"""货物台账管家 —— 给运营用的工作台（云端单进程，标准库实现，零第三方依赖）。

设计上的三个取舍：

1. **零依赖**。只要 python 3，不需要 pip install。云端冷启动从几分钟降到几秒，
   而"装依赖失败"是这类小工具最常见的上线事故。

2. **服务端持密钥**。腾讯文档的 client_secret 只能待在这里。浏览器里放 secret
   等于公开，任何人 F12 就能拿到并改台账。

3. **写入前必须回显**。解析结果先返回给页面看，运营点确认才落库。台账脏了
   以后，你连"哪一行是错的"都找不回来。
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sys
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import auth_gate  # noqa: E402
import carriers  # noqa: E402
import parse_order  # noqa: E402
import quote_parse  # noqa: E402
import tdoc_oauth  # noqa: E402
from cloud import CloudClient, CloudError  # noqa: E402

WEB_DIR = BASE_DIR / "web"

# 令牌落在项目目录**之外**。项目目录每次重新发布都会被覆盖上传，
# token 放里面等于"一发新版本就要重新授权一次"。
TOKEN_DIR = Path(os.environ.get("TDOC_TOKEN_DIR", "/workspace/.cargo-ledger"))
try:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    tdoc_oauth.TOKEN_PATH = TOKEN_DIR / "tdoc_tokens.json"
except OSError:
    pass  # 本地开发时 /workspace 不存在，退回项目目录


def load_config() -> dict:
    with open(BASE_DIR / "config.json", encoding="utf-8") as f:
        return json.load(f)


CFG = load_config()
_cloud_client = None

# 口令失败计数。单进程服务，内存够用；重启即清空，而重启本身就打断了爆破。
LOGIN_LIMITER = auth_gate.RateLimiter(limit=5, window=600, lock=900)

# 不需要口令的路径前缀。公开补单页只能摸到这些，且每个接口各自再做限制。
# /oauth/ 放行是因为腾讯回调带着一次性 code + 我们内存里的 state，
# 本身不含任何台账数据；挡住它反而会在登录态失效时把授权流程卡死。
PUBLIC_PREFIXES = ("/f/", "/api/pub/", "/oauth/")

# 一次授权流程的 state 集合。内存持有即可：这是单进程服务，
# 且 state 只活到回调那一刻（腾讯的 code 也只有 10 分钟）。
_PENDING_STATE: dict[str, float] = {}
_STATE_TTL = 600


def cloud() -> CloudClient:
    global _cloud_client
    if _cloud_client is None:
        c = CFG["cloud"]
        _cloud_client = CloudClient(c["endpoint"], c["publishableKey"])
    return _cloud_client


# --------------------------------------------------------------------- 业务

def _fv(rec: dict, title: str):
    for fv in rec.get("field_values") or []:
        if fv.get("field") == title:
            # 不能用 or 串联：0 是合法值（成本 0、毛利 0），
            # `0 or 下一个` 会跳过去，把"真的是 0"读成"空"。
            v = fv.get("text_value")
            if v is None:
                v = fv.get("number_value")
            if v is None:
                v = fv.get("string_value")
            if v is None:
                # 复选框（复核状态）落在 bool_value；不认它的话永远读成"没勾"
                v = fv.get("bool_value")
            if isinstance(v, dict):
                items = v.get("items") or []
                return items[0].get("text") if items else None
            return v
    return None


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("元", "").strip())
    except ValueError:
        return None


_PROD_NAME_CACHE: dict = {"at": 0.0, "map": {}}


def _product_names(ttl: float = 5.0) -> dict:
    """商品表 recordID → 商品名称。

    「关联商品」是**关联记录**字段（field_type 21），接口只在 text_value 里
    放被引用行的 recordID（比如 rSkinS），不放名字。直接拿它当商品名，
    页面上就是一串 ID，比价和利润估算永远匹配不上——这不是数据脏，
    是字段类型决定的，只能自己反查。5 秒缓存，避免一次请求里反复拉表。
    """
    now = time.time()
    if _PROD_NAME_CACHE["map"] and now - _PROD_NAME_CACHE["at"] < ttl:
        return _PROD_NAME_CACHE["map"]
    f = CFG["fields"]["product"]
    pid = CFG["tdoc"]["product"]
    m = {}
    for r in tdoc_oauth.list_records(pid["file_id"], pid["sheet_id"]):
        nm = (_fv(r, f["name"]) or "").strip()
        if r.get("record_id") and nm:
            m[r["record_id"]] = nm
    _PROD_NAME_CACHE.update(at=now, map=m)
    return m


def read_orders() -> list:
    f = CFG["fields"]["order"]
    recs = tdoc_oauth.list_records(CFG["tdoc"]["order"]["file_id"],
                                   CFG["tdoc"]["order"]["sheet_id"])
    names = _product_names()
    out = []
    for r in recs:
        raw = (_fv(r, f["product"]) or "")
        raw = raw.strip() if isinstance(raw, str) else str(raw)
        out.append({
            "record_id": r.get("record_id"),
            # 关联记录字段给的是 ID，换成名字；对不上就原样留着（可能是手填文本）
            "product": names.get(raw, raw),
            "product_id": raw,
            "quantity": _num(_fv(r, f["quantity"])),
            "price": _num(_fv(r, f["price"])),
            # 下单时间：补单页要靠它让客户认出"是哪一单"（同一尾号可能有多单）
            "order_time": str(_fv(r, f.get("order_time") or "") or ""),
            "buyer": str(_fv(r, f.get("buyer") or "") or ""),
            "address": _fv(r, f["address"]) or "",
            "tracking_no": _fv(r, f["tracking_no"]) or "",
            "phone_tail": _fv(r, f["phone_tail"]) or "",
            "carrier": _fv(r, f["carrier"]) or "",
            "status": _fv(r, f["status"]) or "",
            "last_trace": _fv(r, f["last_trace"]) or "",
            "eta": _fv(r, f["eta"]) or "",
            # 实际卖出价。这一列还没建，读到 None 是正常的——建了之后
            # 利润估算就自动从"拿行情价代"切换成"用真实成交价"。
            "sell_price": _num(_fv(r, f.get("sell_price") or "回收价")),
            "reviewed": bool(_fv(r, f.get("review") or "")),
        })
    return out


def _is_history(o: dict) -> bool:
    """是不是"历史单"——下单日期早于 rules.track_since 的订单。

    历史单一律不再查物流轨迹。这条规矩不是为了省事，是为了不乱花钱：
    查一次就是一次快递鸟额度，而 10-30 之前的单早就该到了，反复查也
    查不出新东西。省下来的额度留给真正在路上的货。

    但"不查"不等于"当作已经完成"：它们仍然留在表里，只是不计入在途、
    不进到货提醒。数量会单独报出来（total_history），不允许悄悄少一个数。

    没填下单时间的**不算**历史单：不能因为字段空着就免掉跟踪，
    那样漏填一列就等于把这单从视野里删掉。
    """
    since = str((CFG.get("rules") or {}).get("track_since") or "").strip()
    if not since:
        return False
    d = _date_cn(o.get("order_time"))
    if not d:
        return False
    return d < since


def _track_key(no: str) -> str:
    """单号的比较键：只留字母数字，统一大写。

    单号是小写录入的、或者带了分隔符（SF-5142-7330），不该因此查不到同一个
    包裹；反过来，拿原始字符串直接比，差一个空格就当成两个单号。
    """
    return "".join(ch for ch in str(no or "").upper() if ch.isalnum())


def _order_by_tracking(no: str, orders: list | None = None):
    """按快递单号在台账里找那一行。找不到返回 None。

    「查物流」页上手动粘一个单号时，页面只有单号，没有 record_id。顺丰和
    京东必须核收件人手机尾号，而尾号明明就在台账里——让人手抄一遍，既多
    一步操作，又是最容易抄错的地方（抄错了接口回"尾号不正确"，看起来像
    单号有问题）。所以这里反查一次，把尾号顺着取出来。
    """
    key = _track_key(no)
    if not key:
        return None
    for o in (orders if orders is not None else read_orders()):
        if _track_key(o.get("tracking_no")) == key:
            return o
    return None


def arrivals(date_key: str) -> dict:
    """今天/明天到货。返回里每条都带 basis，区分"轨迹说的"和"我们推的"。

    快递查询接口基本不返回预计到达时间，eta 只能由规则推。给一个看起来
    很确定、其实没依据的日期，比留空更坏。
    """
    today = time.strftime("%Y-%m-%d")
    tomorrow = time.strftime("%Y-%m-%d", time.localtime(time.time() + 86400))
    target = {"today": today, "tomorrow": tomorrow}.get(date_key, today)

    orders = read_orders()
    # 空行不算在途。表里常见"新建了一行还没填"的空记录（只有 recordID），
    # 不过滤掉的话"在途 N 单"会凭空多出几单，调度会被假数骗。
    orders = [o for o in orders if (o["product"] or o["tracking_no"])]
    ignored = set(CFG["rules"]["ignore_status"])

    # 历史单（下单早于 rules.track_since）不参与在途和到货：它们不再查轨迹，
    # 状态会永远停在录入那一刻，计入在途等于把一堆查不动的货挂在清单上。
    history = [o for o in orders if _is_history(o)]
    live = [o for o in orders if not _is_history(o)]

    # ⭐「在途」必须有证据：有快递单号，或者已经被查过物流状态。
    # 只填了商品、没单号的只是"已下单"，它不是在路上。两者混成一个数字，
    # 会让人以为几十单在路上，实际一个包裹都没发出去。
    shipped = [o for o in live if (o["tracking_no"] or o["status"])]
    in_transit = [o for o in shipped if o["status"] not in ignored]
    noship = [o for o in live if not (o["tracking_no"] or o["status"])]

    hit = []
    for o in in_transit:
        eta = (o["eta"] or "")[:10]
        if eta == target:
            hit.append({**o, "basis": "轨迹判断"})
        elif date_key == "today" and o["status"] in ("派送中", "已到达") and not eta:
            # 已到达 = 货已经在驿站/快递柜，今天不取就压在那儿了。
            # 派送中今天必到，已到达今天必须取——两者都该出现在今天的清单里。
            hit.append({**o, "basis": "已到达，待取件" if o["status"] == "已到达"
                        else "派送中（今日必到）"})
    return {
        "ok": True,
        "date_key": date_key,
        "date": target,
        "count": len(hit),
        # 手机尾号不往下发：查轨迹改为服务端按 record_id 取，页面只拿到
        # "这单能不能查" 的结论。能少一个字段就少一个。
        "items": [{k: v for k, v in o.items() if k != "phone_tail"} for o in hit],
        "total_in_transit": len(in_transit),
        "total_order": len(orders),
        # 单独报：这些还没物流信息，不是在路上
        "total_pending_ship": len(noship),
        # 单独报：这些是历史单，不查轨迹也不计入在途。宁可多一个数，不许
        # 让"少了 38 单"变成一个没人能解释的差值。
        "total_history": len(history),
        "track_since": str((CFG.get("rules") or {}).get("track_since") or ""),
        "note": ("在途 = 已录快递单号或已有物流状态，且未签收/未退回。"
                 "只填了商品没单号的不算在途，单独报 total_pending_ship。"
                 f" 下单早于 {CFG['rules'].get('track_since') or '（未设）'} 的历史单不查轨迹、"
                 "不计入在途，数量见 total_history。"),
    }


# ---------------------------------------------------------- 公开补单页
#
# 客户下单时还没有快递单号，等他拿到单号要能自己补回来，而不是运营拿两
# 张表手工对。这一节只服务那一个页面，规则和台账内部接口完全不同：
#
#   1. 不给页面下发 recordID。页面拿到的是 HMAC 句柄，服务端提交时用它
#      **配合尾号**重算比对——只有"尾号确实属于这一行"才可能匹配上。
#      这样即使有人猜到 recordID，也改不了别人的单。
#   2. 只回商品名/数量/下单日期。地址、金额、下单人、手机号一律不出这个口。
#   3. 提交不直接落库，进待确认队列，运营点确认才写。客户能提交，不能改表。
#
# 第 3 条不是过度设计：这个页面在公网上、无口令，唯一挡在前面的是 4 位数字。
# 只要写权限不在客户手里，最坏结果是运营的待确认列表里多几条垃圾，而不是台账被改脏。


class PubError(RuntimeError):
    """公开页面的输入错误。统一 400，文案要给客户看得懂的话。"""


class _Window:
    """按 key（IP）的滑动窗口计数，超了就拒绝。

    单进程内存足够；重启即清空，而重启本身已经打断了枚举。
    """

    def __init__(self, limit: int, window: int):
        self.limit, self.window = limit, window
        self._hits: dict[str, list] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True


# 查一次看一堆订单骨架，所以查得比提交限制更紧：正常客户一次只会输错一两次。
PUB_LOOKUP_RATE = _Window(limit=12, window=60)
PUB_SUBMIT_RATE = _Window(limit=6, window=60)

# 客户补单：按「手机尾号 + 商品」定位到那一行，**直接写进「下单信息」表**。
#
# 这块绕过三次弯路，都记下来别再犯：先是进程内存（重启即丢，客户看到
# "提交成功"而运营那边什么都没有），再是本地 JSON（线上多实例各读各的，
# 客户提交落到实例 A、运营确认落到实例 B，B 说"这条补单已经不在了"），
# 再是另开一张"补单待确认"子表（同样的问题：数据在订单表、状态在别处，
# 两边要靠代码对齐，对不齐就互相看不见）。
#
# 一句话结论：台账已经在腾讯文档里，就别再引入第二套存储。数据在哪，
# 状态就该在哪。补单不排队、不中转——定位到行，直接写那一行的「快递单号」。
# 写错的风险靠三件事兜住：
#   ① 双因子定位（尾号 + 商品句柄），不是随便一个 recordID 就能写；
#   ② 已有单号的行不覆盖，客户重复提交/运营手填过都不会被冲掉；
#   ③ 写后读回校验，接口说成功但表里没变就报错，绝不假装成功。
# 而且运营在腾讯文档里一眼能看到、能直接改——比让人去另一个地方点"确认"
# 更快，也比任何中转表都可靠。


_CN_TZ = datetime.timezone(datetime.timedelta(hours=8))


def _date_cn(v) -> str:
    """腾讯文档的日期值 → 'YYYY-MM-DD'。

    接口给的是时间戳（秒或毫秒），直接截前 10 位会显示成 `1793289600`。
    时区写死 +8：线上容器多半是 UTC，用服务器本地时区会让整列日期差一天，
    而这种错在页面上看不出来——每个值都是合法日期，只是错了一天。
    """
    s = str(v or "").strip()
    if not s:
        return ""
    if re.fullmatch(r"\d{13}", s):
        ts = int(s) / 1000
    elif re.fullmatch(r"\d{10}", s):
        ts = int(s)
    else:
        return s[:10]
    try:
        return datetime.datetime.fromtimestamp(ts, _CN_TZ).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return s[:10]


def _hid(record_id: str, tail: str) -> str:
    """对外句柄 = HMAC(服务端密钥, record_id|尾号)。

    双向不需要存映射：提交时把该尾号下每一行都算一遍，能对上就是这一行。
    所以句柄离开尾号就没有意义，抄走也没用。
    """
    key = str(CFG["auth"].get("session_secret") or "").encode()
    return hmac.new(key, f"patch|{record_id}|{tail}".encode(),
                    hashlib.sha256).hexdigest()[:16]


def _tail_ok(tail: str) -> bool:
    return bool(re.fullmatch(r"\d{4}", tail or ""))


def _norm_no(no: str) -> str:
    # 单号里常有空格、全角字符，还有人手打进去的星号。不洗一遍，写进表里
    # 就是一串带空格的字符串，后面查轨迹会莫名其妙查不到。
    s = str(no or "").strip().upper().replace(" ", "").replace("　", "")
    return re.sub(r"[^0-9A-Z\-]", "", s)


def pub_lookup(tail: str) -> dict:
    """客户输尾号 → 列出"还缺单号"的单。

    只列缺单号的：已经填过的不再出现在列表里，客户不会误以为要重填一遍，
    也不会顺手把别人的单号覆盖掉。
    """
    tail = (tail or "").strip()
    if not _tail_ok(tail):
        raise PubError("请输入手机号后 4 位数字")

    orders = [o for o in read_orders() if str(o.get("phone_tail") or "").strip() == tail]
    todo = [o for o in orders
            if o.get("product") and not str(o.get("tracking_no") or "").strip()]
    done = len(orders) - len(todo)

    items = []
    for o in todo:
        items.append({
            "hid": _hid(o["record_id"], tail),
            "product": o.get("product") or "",
            "qty": int(o.get("quantity") or 1),
            "date": _date_cn(o.get("order_time")),
            # 状态也回：客户看到"运输中却没单号"会知道这行是运营先手填的
            "status": o.get("status") or "",
        })

    if items:
        tip = f"您有 {len(items)} 单还没填快递单号，填上后运营核对一遍就进台账。"
    elif done:
        tip = "您这些单的快递单号都已经登记过了，不用再填。"
    else:
        tip = "没找到这个尾号下的订单。核对一下是不是收货人手机号的最后 4 位。"
    return {
        "ok": True,
        "count": len(items),
        "done": done,
        "items": items,
        "tip": tip,
        # 别再让客户试：枚举的边界是"每 60 秒 12 次"，说清楚比让他反复撞好
        "notice": "只能查收货人手机号后 4 位。下单人号码不算，顺丰/京东都只认收件人或寄件人。",
    }


# 客户补单的待确认队列：服务端文件。客户提交只进这里，运营点「通过」才写表。
#
# 这块走过四次弯路，每次都是同一个病根：以为"中转数据不是正式数据"，
# 就可以随手安置。把代价记在这儿，别再犯：
#   ① 只放内存 —— 进程一重启（每发布一次就重启一次）队列就空。客户看到
#      "提交成功"，运营那边什么都没有，两边都以为对方没做。
#   ② 落盘但 `except OSError: traceback.print_exc()` —— 文件没写成也照样回
#      "提交成功"，比 ① 更坏：日志里躺着一行 traceback，没人会去看。
#   ③ 读内存缓存而不是每次读文件 —— 线上若不止一个实例，各看各的队列。
#   ④ 另起一张云库表 / 另开一张子表 —— 多一套存储就多一份要对齐的状态。
#
# 现在：每次请求都从磁盘读（不吃内存缓存）；写失败直接抛错，页面回"系统忙"，
# 绝不回"提交成功"；数据目录和 token 共用 /workspace，容器重建也不会丢。
_BOOT_AT = time.time()   # 本进程启动时刻，体检接口用
_QUEUE_FILE = TOKEN_DIR / "patch_queue.json"

# 这里原来有个 _PATCH_TTL = 7 天，超时的条目在读待办时被跳过、在下次提交时被
# 物理删掉。已经去掉，而且是**故意的**，不留开关。
#
# 理由：这队列里放的不是缓存，是客户已经看到"提交成功"的待办事项。过期跳过
# 等于运营永远不知道有这一条，客户那边却一直显示已提交——跟前面几轮的经历
# 完全同源：数据消失的样子长得跟"本来就没事"一模一样。时间没有任何资格替人
# 做"这条不重要、可以扔"这个判断。
#
# 代价上限是算得清的：一条不到 200 字节，一千条也就两百来 KB，`_queue_read`
# 读一次几毫秒。真正让队列变小的唯一方式，是运营点「通过」或「退回」。


def _queue_read() -> dict:
    """每次都从磁盘读。

    不用模块级缓存：缓存是"每个进程一份"，多实例时客户提交落在 A 的缓存里，
    运营在 B 上永远看不到。文件本身就是唯一的真相，读一次不到 1ms。
    """
    try:
        data = json.loads(_QUEUE_FILE.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        traceback.print_exc()
        return {}
    return data if isinstance(data, dict) else {}


def _queue_write(data: dict) -> None:
    """写失败必须抛出去，不许吞。

    tmp + os.replace：读到的要么是旧的完整内容，要么是新的完整内容，
    不会出现写一半断电留下的半截 JSON。
    """
    _QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _QUEUE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    os.replace(tmp, _QUEUE_FILE)


def pub_submit(tail: str, hid: str, tracking_no: str) -> dict:
    """客户提交单号 → 只进待确认队列，**不写表**。

    表在运营点「通过」之前一个字都不会变。这是审核环节的位置：客户能提交，
    但没有任何路径能绕开人直接改台账。
    """
    tail = (tail or "").strip()
    if not _tail_ok(tail):
        raise PubError("请输入手机号后 4 位数字")

    no = _norm_no(tracking_no)
    if len(no) < 8 or len(no) > 24:
        raise PubError("单号看着不对，通常是一串 10~20 位的数字或字母，核对一下再提交")

    orders = [o for o in read_orders() if str(o.get("phone_tail") or "").strip() == tail]
    if not orders:
        raise PubError("这个尾号下没有订单，核对一下手机号后 4 位")

    row = next((o for o in orders if _hid(o["record_id"], tail) == (hid or "").strip()), None)
    if not row:
        # 句柄对不上有两种可能：页面放久了（表格被改过），或者有人构造请求。
        # 两种都回同一句，不区分——区分了就是在告诉对方"你猜的方向对了"。
        raise PubError("这一单对不上了，刷新页面重新查一次")
    if str(row.get("tracking_no") or "").strip():
        raise PubError("这一单已经有快递单号了，不用重复提交")

    # 只有唯一候选才写快递公司。纯数字单号（中通/圆通/韵达都长这样）会给
    # 出一堆候选，挑第一个填进去等于替运营猜，猜错了后面查轨迹的承运商
    # 编码就是错的——留空比猜错好，反正运营确认时一眼能看出没填。
    cands = carriers.guess_carriers(no)
    carrier = cands[0][0] if len(cands) == 1 else ""

    q = _queue_read()
    # 同一行只留最新一次提交：客户连点两下、或者填错重填，运营都不该看到
    # 两条互相矛盾的单号。只按 record_id 去重，**不按时间**——旧的没处理就
    # 一直留着，什么时候处理完什么时候消失。
    now = time.time()
    for k, v in list(q.items()):
        if v.get("record_id") == row["record_id"]:
            q.pop(k, None)
    item_id = secrets.token_urlsafe(9)
    q[item_id] = {
        "record_id": row["record_id"],
        "tracking_no": no,
        "tail": tail,
        "product": row.get("product") or "",
        "carrier": carrier,
        "at": now,
    }
    try:
        _queue_write(q)   # 先存下来再回话
    except OSError:
        traceback.print_exc()
        # 存不下就说存不下。"提交成功"这四个字只要说出口，运营那边就必须有东西。
        raise PubError("系统现在存不下这条记录，稍等一分钟再提交一次")
    return {
        "ok": True,
        "submitted": True,
        "queued": True,
        "tracking_no": no,
        "carrier": carrier,
        "product": row.get("product") or "",
        "msg": ("已提交，运营核对后才会写进台账。" + (f"识别为{carrier}。" if carrier else "")
                + "填错的话跟运营说一声。"),
    }


def patch_pending() -> dict:
    """运营待办：客户提交了哪些单号还没确认。

    不做任何时间过滤：只要没被「通过」或「退回」，就一直在这儿。放久了会
    排在前面并带上 age_sec，页面按它标红——让旧的下沉是排序问题，不是
    删除问题。删除只有两个入口：运营点通过、运营点退回。
    """
    q = _queue_read()
    now = time.time()
    items = []
    for k, v in q.items():
        age = max(0.0, now - float(v.get("at") or 0))
        items.append({
            "id": k,
            "record_id": v.get("record_id") or "",
            "tracking_no": v.get("tracking_no") or "",
            "product": v.get("product") or "",
            "carrier": v.get("carrier") or "",
            "tail": v.get("tail") or "",
            "minutes_ago": int(age // 60),
            "age_sec": int(age),
        })
    # 最久的排最上：运营从上往下处理，先来的先办，没有任何一条会被新提交的
    # 挤到列表底部再也看不见（那只是把"过期消失"换成"过期沉底"，同一个病）。
    items.sort(key=lambda x: -x["age_sec"])
    return {"ok": True, "count": len(items), "items": items}


def patch_confirm(item_id: str, reject: bool = False) -> dict:
    """运营点「通过」/「退回」。这是唯一真正把单号写进订单表的地方。"""
    key = (item_id or "").strip()
    q = _queue_read()
    it = q.get(key)
    if not it:
        raise ConfirmError("这条补单已经不在了（可能刚确认过，或已超过 7 天）")

    if reject:
        q.pop(key, None)
        _queue_write(q)
        return {"ok": True, "rejected": True, "tracking_no": it["tracking_no"]}

    f = CFG["fields"]["order"]
    oid = CFG["tdoc"]["order"]
    # 写之前重读：运营可能刚在表格里手工填过同一行，那就别拿旧的覆盖新的
    row = next((o for o in read_orders() if o["record_id"] == it["record_id"]), None)
    if row is None:
        q.pop(key, None)
        _queue_write(q)
        raise ConfirmError("这一行在表格里找不到了（可能被删），这条补单已作废")
    if str(row.get("tracking_no") or "").strip():
        q.pop(key, None)
        _queue_write(q)
        return {"ok": True, "skipped": True, "tracking_no": row["tracking_no"],
                "msg": "表里这一行已经有单号了，没有覆盖"}

    fields = [{"field": f["tracking_no"], "string_value": it["tracking_no"]}]
    if it.get("carrier") and f.get("carrier"):
        fields.append({"field": f["carrier"], "string_value": it["carrier"]})
    # verify=True：腾讯的 updateRecords 在值格式错时照样回 ret=0 并把 values
    # 原样回显，看起来完全成功、实际一个字没落库。不读回来就等于没验证。
    tdoc_oauth.update_records(oid["file_id"], oid["sheet_id"],
                              [{"record_id": it["record_id"], "field_values": fields}],
                              verify=True)
    # 一定"写表成功之后"才从队列删。反过来的话，写表抛错（token 过期、限流）
    # 这一条就永久消失了，客户那端却显示过"提交成功"。
    q.pop(key, None)
    _queue_write(q)
    return {"ok": True, "written": True, "tracking_no": it["tracking_no"],
            "product": it.get("product") or "", "carrier": it.get("carrier") or ""}


# ---------------------------------------------------------- 行情监测

# 阈值只写在 config.watch 里，这里是没配时的默认值。
# 给的是业务判断的起点，不是标准答案——跑两周真实数据回来调，
# 别把 50 块、5% 当成真理。
_WATCH_DEFAULTS = {
    "min_profit": 50,      # 毛利低于这个数就提醒（扣运费前）
    "drop_pct": 0.05,      # 单次跌幅 ≥ 5% 提醒"先别收同类"
    "jump_pct": 0.08,      # 单次涨幅 ≥ 8% 提醒"可以补货"
    "stale_days": 3,       # 行情超过 N 天没更新，比价结论不可信
}


def read_products() -> list:
    f = CFG["fields"]["product"]
    pid = CFG["tdoc"]["product"]
    out = []
    for r in tdoc_oauth.list_records(pid["file_id"], pid["sheet_id"]):
        name = (_fv(r, f["name"]) or "").strip()
        if not name:
            continue
        out.append({
            "record_id": r.get("record_id"),
            "name": name,
            "config": _fv(r, f["config"]) or "",
            "quote": _num(_fv(r, f["quote"])),
            "cost": _num(_fv(r, f["cost"])),
            "suggest_price": _num(_fv(r, f["suggest_price"])),
            "has_gift": _fv(r, f["has_gift"]) or "",
            "gift": _fv(r, f["gift"]) or "",
            "quote_time": _fv(r, f["quote_time"]),
        })
    return out


def _quote_age_days(v):
    """行情价距今天数。认不出格式就返回 None —— 宁可不说，也别拿错数报"过期"。"""
    n = _num(v)
    if n is not None:
        sec = n / 1000.0 if n > 1e11 else n
        return max(0.0, (time.time() - sec) / 86400.0)
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return max(0.0, (time.time() - time.mktime(time.strptime(s[:len(fmt) + 2].strip(), fmt))) / 86400.0)
        except (ValueError, TypeError):
            continue
    return None


def quote_watch(changed=None) -> dict:
    """行情体检：每次更新完行情跑一遍。

    检查的不是"数据填得对不对"，是"按现在这个价还能不能赚钱"：
      1. 亏损   行情 < 成本 —— 这个价收进来卖出去就亏
      2. 薄利   毛利不够覆盖运费损耗
      3. 收贵了 在途单的下单价已经高于当前行情（唯一还能止损的一类）
      4. 异动   单次跌幅过大，先别急着收同类
      5. 过期   拿几天前的价做决定，比没价更危险
    """
    w = {**_WATCH_DEFAULTS, **(CFG.get("watch") or {})}
    prods = read_products()
    orders = read_orders()
    ignored = set(CFG["rules"]["ignore_status"])
    alerts = []

    def add(level, kind, text, product=""):
        alerts.append({"level": level, "kind": kind, "text": text, "product": product})

    index, no_quote, stale = {}, 0, []
    for p in prods:
        q, c = p["quote"], p["cost"]
        if q is None:
            no_quote += 1
            continue
        index.setdefault(_norm_text(p["name"]), p)
        if c is not None:
            margin = q - c
            if margin < 0:
                add("red", "loss",
                    f'{p["name"]}：行情 {q:g} − 成本 {c:g} = {margin:+g}，'
                    f'这个价收进来卖出去就亏 {abs(margin):g}', p["name"])
            elif margin < w["min_profit"]:
                add("warn", "thin",
                    f'{p["name"]}：毛利只剩 {margin:g}（低于 {w["min_profit"]:g}），'
                    f'扣掉运费基本白干', p["name"])
        age = _quote_age_days(p.get("quote_time"))
        if age is not None and age > w["stale_days"]:
            stale.append(f'{p["name"]}（{age:.0f} 天）')

    # 只看还没走完的单：已签收的改不了，算进 total_loss 就够，不占告警位
    over, settled_loss, settle_cnt = [], 0.0, 0
    for o in orders:
        price, name = o["price"], (o["product"] or "")
        if price is None or not name:
            continue
        key = _norm_text(name)
        p = index.get(key)
        if not p:
            # 订单写「红米K90 12+256混色」，商品表是「红米 K90」：归一化只吃空格和
            # 连字符，配置写法不同就匹配不上，退一步做包含匹配。
            p = next((v for k, v in index.items() if k and (k in key or key in k)), None)
        if not p or p["quote"] is None:
            continue
        gap = price - p["quote"]
        done = o["status"] in ignored
        if gap <= 0:
            continue
        if done:
            settle_cnt += 1
            settled_loss += gap * (o["quantity"] or 1)
        else:
            over.append({"product": name, "price": price, "quote": p["quote"],
                         "gap": gap, "qty": o["quantity"],
                         "status": o["status"] or "未发货",
                         "record_id": o["record_id"]})

    over.sort(key=lambda x: -x["gap"] * (x["qty"] or 1))
    for o in over:
        qty = o["qty"] or 1
        # 一件和一批是两件事：单价差 170 看着不多，两件就是 340。
        extra = f'，{qty} 件共差 {o["gap"] * qty:g}' if qty > 1 else ''
        add("red", "overbuy",
            f'{o["product"]}：下单价 {o["price"]:g} 已高于行情 {o["quote"]:g}，'
            f'每件差 {o["gap"]:g}{extra}；当前{o["status"]}，还能谈就谈、能退就退',
            o["product"])

    for c in (changed or []):
        old, new = c.get("old"), c.get("new")
        if not old or new is None or old <= 0:
            continue
        pct = (new - old) / old
        if pct <= -w["drop_pct"]:
            add("warn", "drop",
                f'{c["name"]}：行情 {old:g} → {new:g}（{pct * 100:+.1f}%），'
                f'跌幅超过 {w["drop_pct"] * 100:g}%，同类先别收', c["name"])
        elif pct >= w["jump_pct"]:
            add("info", "jump",
                f'{c["name"]}：行情 {old:g} → {new:g}（{pct * 100:+.1f}%），'
                f'涨过 {w["jump_pct"] * 100:g}%，可以补货', c["name"])

    if stale:
        add("warn", "stale",
            f'{len(stale)} 个商品行情超过 {w["stale_days"]:g} 天没更新，'
            f'按它比价可能已经错了：' + "、".join(stale[:8])
            + ("…" if len(stale) > 8 else ""))
    # 一个价格都没有的时候，"0 告警"是不诚实的——它长得像"很健康"，
    # 实际是体检根本没跑起来。这种必须说出来。
    if prods and no_quote == len(prods):
        add("warn", "blind",
            f'{len(prods)} 个商品一个行情价都没有，这次体检等于空转。'
            f'先导入一张报价单，或者手工把行情价填上。')

    order = {"red": 0, "warn": 1, "info": 2}
    alerts.sort(key=lambda a: order.get(a["level"], 3))
    counts = {lv: sum(1 for a in alerts if a["level"] == lv) for lv in ("red", "warn", "info")}
    return {
        "ok": True,
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "checked_products": len(prods),
        "checked_orders": len(orders),
        "counts": counts,
        "alerts": alerts,
        "overbuy_orders": over,
        "settled": {"count": settle_cnt, "loss": round(settled_loss, 2)},
        "no_quote_products": no_quote,
        "thresholds": w,
        "note": ("阈值在 config.watch，改了立刻生效。"
                 "已签收单的下单-行情差只累计金额、不单条提醒——改不了了。"),
    }


# ------------------------------------------------------- 写入：两阶段确认

# 待确认的写入。**这是"改文档必须人工确认"的落点**：
# 第一次请求只算差异、不发写请求；把差异和一张一次性票据返回给页面，
# 人在页面上看过、再点一次确认，才拿票据来换真正的写入。
# 票据绑内容、一次性、5 分钟过期——即使有人直接打接口，没有票据就改不动表。
_PENDING_WRITE: dict = {}
_WRITE_TTL = 300


class ConfirmError(RuntimeError):
    """票据问题：过期、用过、对不上。属于 400（操作有误），不是系统故障。"""


class BodyTooLarge(RuntimeError):
    """请求体超限。单进程服务不设上限，一个超大 POST 就能把内存打满。"""


def _stage_write(kind: str, payload: dict, preview: list) -> dict:
    now = time.time()
    for k, v in list(_PENDING_WRITE.items()):
        if v["exp"] < now:
            _PENDING_WRITE.pop(k, None)
    tok = secrets.token_urlsafe(18)
    _PENDING_WRITE[tok] = {"kind": kind, "payload": payload,
                           "preview": preview, "exp": now + _WRITE_TTL}
    will = [p for p in preview if p["action"] == "update"]
    return {
        "ok": True,
        "need_confirm": True,
        "confirm_token": tok,
        "expires_in": _WRITE_TTL,
        "will_update": len(will),
        "unchanged": len([p for p in preview if p["action"] == "unchanged"]),
        "unmatched": [p["name"] for p in preview if p["action"] == "unmatched"],
        "preview": preview,
        "notice": "还没写。核对下面的差异，确认无误再点「确认写入」。",
    }


def _take_write(kind: str, token: str) -> dict:
    """取票据并作废。一次性：同一张票不能被用第二次。"""
    it = _PENDING_WRITE.pop((token or "").strip(), None)
    if not it or it["kind"] != kind:
        raise ConfirmError("确认票据无效或已用过，请重新核对后再来一次。")
    if it["exp"] < time.time():
        raise ConfirmError("确认超时（超过 5 分钟），请重新识别并核对。")
    return it


def preview_quote_rows(rows: list) -> list:
    """只算"会变成什么"，一个字都不写。

    只更新已存在的商品，不新建：报价单上的型号写法（K90 / Mate80）和台账里的
    商品名（红米 K90 12+256 混色）不一定对得上，自动新建会造出一堆重复商品，
    后面比价全乱。匹配不上的原样返回给运营，让他决定。
    """
    f = CFG["fields"]["product"]
    pid = CFG["tdoc"]["product"]
    recs = tdoc_oauth.list_records(pid["file_id"], pid["sheet_id"])
    index = {}
    for r in recs:
        name = (_fv(r, f["name"]) or "").strip()
        if name:
            index[name] = r

    items = []
    for row in rows:
        name = (row.get("name") or "").strip()
        price = _num(row.get("price"))
        if not name or price is None:
            continue
        r = index.get(name)
        if not r:
            items.append({"name": name, "old": None, "new": price,
                          "action": "unmatched", "record_id": None})
            continue
        old = _num(_fv(r, f["quote"]))
        same = old is not None and abs(old - price) < 1e-9
        items.append({"name": name, "old": old, "new": price,
                      "action": "unchanged" if same else "update",
                      "record_id": r["record_id"]})
    return items


def commit_quote_rows(preview: list, date_str: str) -> dict:
    f = CFG["fields"]["product"]
    pid = CFG["tdoc"]["product"]
    ts = str(int(time.time() * 1000))
    updates = [{
        "record_id": p["record_id"],
        "field_values": [
            {"field": f["quote"], "number_value": float(p["new"])},
            {"field": f["quote_time"], "string_value": ts},
        ],
    } for p in preview if p["action"] == "update"]

    if updates:
        tdoc_oauth.update_records(pid["file_id"], pid["sheet_id"], updates, verify=True)
    res = {"ok": True, "written": True, "updated": len(updates),
           "quote_date": date_str}
    # 行情一落表就体检一遍。失败要单独报，不能因为体检出错就说"没写进去"——
    # 那会让人重复提交，反而多改一次表。
    try:
        res["watch"] = quote_watch([p for p in preview if p["action"] == "update"])
    except Exception as exc:  # noqa: BLE001
        res["watch"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return res


# ------------------------------------------------- 录一单：识别结果 → 商品信息表

# 商品表的字段清单只写一次，预览和写入共用，避免两处不一致写错列。
# 第三项是写入形态：num = 货币/数字列，text = 文本列。
# 第一项同时是 config.fields.product 的键和写入值的键，所以只写一次。
_PRODUCT_COLS = [
    ("name", "text"),
    ("config", "text"),
    ("platform", "text"),
    ("has_gift", "text"),
    ("gift", "text"),
    ("suggest_price", "num"),
    ("cost", "num"),
    ("quote", "num"),
    ("profit", "num"),
]

# 能被接口写进去的列类型。公式、查找引用这类列由表格自己算——写它要么报错，
# 要么把公式覆盖掉，所以一律跳过。认不出的类型（unknown_*）按不可写处理，
# 宁可少写一列，也不要把别人的公式列写坏。
_WRITABLE_TYPES = {"text", "number", "currency", "dateTime",
                   "singleSelect", "checkbox", "url"}


def _writable(file_id: str, sheet_id: str) -> dict:
    """字段名 → 这个列能不能由接口写。"""
    return {f["field_title"]: (f["field_type"] in _WRITABLE_TYPES)
            for f in tdoc_oauth.list_fields(file_id, sheet_id)}


def _norm_text(s) -> str:
    """匹配用的归一化：全角转半角、去空格与常见分隔符、统一小写。

    运营在表里手写的是「华为mate80ProMax」「vivoX300卫星版」，识别出来的是
    「HUAWEI Mate80 Pro Max」「vivo X300 卫星版」。不做归一化就永远对不上，
    每录一单都会新建一条重复商品，后面比价全乱。
    """
    s = "" if s is None else str(s).strip()
    for a, b in (("＋", "+"), ("（", "("), ("）", ")"), ("　", " ")):
        s = s.replace(a, b)
    return re.sub(r"[\s\-_·、,，。/／\\|]+", "", s).lower()


def _product_key(name, config) -> str:
    return _norm_text(name) + "|" + _norm_text(config)


def _row_from_parsed(p: dict) -> dict:
    """把识别结果（可能已被人工改过）整理成一条商品记录的目标值。

    毛利**在服务端重算**，不采信页面传来的值：页面上那个是给人看的，
    一旦行情或成本被改过就过期了，写进表里就是错的账。
    """
    name = (p.get("product") or "").strip() or (p.get("canonical_name") or "").strip()
    config = (p.get("spec") or "").strip()
    quote = _num(p.get("quote"))
    if quote is None:
        quote = _num(p.get("quote_low"))
    cost = _num(p.get("cost"))
    gift = (p.get("gift") or "").strip()
    profit = round(quote - cost, 2) if (quote is not None and cost is not None) else None
    return {
        "name": name,
        "config": config,
        "platform": (p.get("platform") or "").strip(),
        # 赠品没填就留空，表示"这次没提供"。不能直接推成"无"——
        # 表里本来记着「有」，一次没填就抹成「无」是丢数据。
        "has_gift": "有" if gift else None,
        "gift": gift,
        "suggest_price": _num(p.get("order_price")),
        "cost": cost,
        "quote": quote,
        "profit": profit,
    }


def preview_product(p: dict) -> dict:
    """只算"表里会变成什么"，一个写请求都不发。"""
    f = CFG["fields"]["product"]
    pid = CFG["tdoc"]["product"]
    tgt = _row_from_parsed(p)
    if not tgt["name"]:
        raise ConfirmError("「商品」是空的，不知道该写成什么。先在上面把商品名填上。")

    ok_cols = _writable(pid["file_id"], pid["sheet_id"])
    idx = {}
    for r in tdoc_oauth.list_records(pid["file_id"], pid["sheet_id"]):
        k = _product_key(_fv(r, f["name"]), _fv(r, f["config"]))
        if k.strip("|"):
            idx.setdefault(k, r)
    hit = idx.get(_product_key(tgt["name"], tgt["config"]))

    out = {
        "name": tgt["name"], "config": tgt["config"],
        "record_id": hit["record_id"] if hit else None,
        "target": tgt, "changes": [], "skipped": [],
    }

    if not hit:
        out["action"] = "create"
        for key, kind in _PRODUCT_COLS:
            v = tgt.get(key)
            if key == "has_gift" and v in (None, ""):
                v = "无"                 # 新建的记录总得有个是非，"无"比空格好读
                tgt["has_gift"] = v      # 同步进 target，否则 commit 会跳过这一列
            if v in (None, ""):
                continue
            if not ok_cols.get(f[key], False):
                out["skipped"].append({"field": f[key], "old": None,
                                       "reason": "这列由表格自己算（公式/查找引用），不写入"})
                continue
            out["changes"].append({"field": f[key], "old": None,
                                   "new": v, "kind": kind})
        return out

    for key, kind in _PRODUCT_COLS:
        if not ok_cols.get(f[key], False):
            # 公式列在表里自己算，写它等于把公式覆盖掉，直接让开
            out["skipped"].append({"field": f[key], "old": _fv(hit, f[key]),
                                   "reason": "这列由表格自己算（公式/查找引用），不写入"})
            continue
        new = tgt.get(key)
        if new in (None, ""):
            # 空值不写。表里已有的内容不能被一次"没填"抹掉——那是丢数据，
            # 不是修改。确要清空请到表里手动删。
            old0 = _fv(hit, f[key])
            if old0 not in (None, ""):
                out["skipped"].append({"field": f[key], "old": old0,
                                       "reason": "这次没填，保留表里原值"})
            continue
        old = _fv(hit, f[key])
        if key in ("name", "config"):
            # 商品名/配置是匹配键，写法差异（空格、大小写）不构成"要改"。
            # 否则识别结果里的「HUAWEI Mate 80」会把表里的「华为mate80」
            # 覆盖掉，改一次名就再也匹配不上了。
            same = _norm_text(old) == _norm_text(new)
        elif kind == "num" and _num(old) is not None and _num(new) is not None:
            same = abs(_num(old) - float(new)) < 1e-9
        else:
            same = str(old).strip() == str(new).strip()
        if not same:
            out["changes"].append({"field": f[key], "old": old,
                                   "new": new, "kind": kind})
    out["action"] = "update" if out["changes"] else "unchanged"
    return out


def commit_product(preview: dict) -> dict:
    """真正写。只有拿到一次性票据才会走到这里。"""
    f = CFG["fields"]["product"]
    pid = CFG["tdoc"]["product"]
    tgt = preview["target"]
    ok_cols = _writable(pid["file_id"], pid["sheet_id"])   # 两次请求之间列类型可能被改过
    values = []
    for key, kind in _PRODUCT_COLS:
        v = tgt.get(key)
        if v in (None, ""):
            continue
        if not ok_cols.get(f[key], False):
            continue                # 公式列一律不写
        if kind == "num":
            values.append({"field": f[key], "number_value": float(v)})
        else:
            values.append({"field": f[key],
                           "text_value": {"items": [{"text": str(v)}]}})
    if tgt.get("quote") is not None:
        values.append({"field": f["quote_time"],
                       "string_value": str(int(time.time() * 1000))})

    if preview["action"] == "create":
        tdoc_oauth.add_records(pid["file_id"], pid["sheet_id"], [{"field_values": values}])
        return {"ok": True, "written": True, "action": "created",
                "name": tgt["name"], "fields": len(values)}
    tdoc_oauth.update_records(pid["file_id"], pid["sheet_id"],
                              [{"record_id": preview["record_id"], "field_values": values}],
                              verify=True)
    return {"ok": True, "written": True, "action": "updated",
            "name": tgt["name"], "fields": len(values),
            "changed": len(preview["changes"])}


def stage_product(preview: dict) -> dict:
    now = time.time()
    for k, v in list(_PENDING_WRITE.items()):
        if v["exp"] < now:
            _PENDING_WRITE.pop(k, None)
    tok = secrets.token_urlsafe(18)
    _PENDING_WRITE[tok] = {"kind": "product", "payload": preview,
                           "preview": [preview], "exp": now + _WRITE_TTL}
    verb = {"create": "新建一条商品记录", "update": "更新一条已有商品记录",
            "unchanged": "表里已经是这个值，不用改"}[preview["action"]]
    return {
        "ok": True, "need_confirm": True, "confirm_token": tok,
        "expires_in": _WRITE_TTL, "preview": preview, "action": preview["action"],
        "notice": f"还没写。核对下面的内容——确认后将{verb}。",
    }


# --------------------------------------------------------------------- 路由

def _whereami() -> dict:
    """部署环境体检：这个进程是谁、数据目录能不能写、线上有几个实例。

    加这个接口是因为我先前把"运营点确认时提示补单不在了"直接判成"线上多实例"，
    其实没有证据——真跑一次比推理快，也不至于把错误结论写进代码。
    """
    out = {
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "host": socket.gethostname(),
        "boot_at": _BOOT_AT,
        "uptime_sec": int(time.time() - _BOOT_AT),
        "data_dir": str(TOKEN_DIR),
        "data_dir_exists": TOKEN_DIR.is_dir(),
    }
    # ① 可写探针：真写一个文件再读回来。不是 os.access() 那种"理论上能写"。
    probe = TOKEN_DIR / "probe.txt"
    stamp = f"{os.getpid()}|{time.time():.0f}"
    try:
        TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        probe.write_text(stamp, encoding="utf-8")
        out["write_ok"] = probe.read_text(encoding="utf-8") == stamp
    except OSError as exc:
        out["write_ok"] = False
        out["write_error"] = str(exc)
    # ② 计数器：每次请求 +1。单实例严格递增；多实例会跳号（各自一把）。
    cnt = TOKEN_DIR / "hit_count.txt"
    try:
        n = int(cnt.read_text().strip() or "0") + 1 if cnt.exists() else 1
    except (OSError, ValueError):
        n = 1
    try:
        cnt.write_text(str(n), encoding="utf-8")
        out["hit"] = n
    except OSError as exc:
        out["hit"] = "FAIL: " + str(exc)
    # ③ 队列文件现状
    try:
        out["queue_exists"] = _QUEUE_FILE.exists()
        out["queue_items"] = len(_queue_read())
    except OSError as exc:
        out["queue_error"] = str(exc)
    return out


def _status() -> dict:
    tok = tdoc_oauth.load_token()
    now = int(time.time())
    manual = (tok.get("source") == "manual-adopt")
    # 手填 token 的到期时间未知，不能按假定值把有效凭证判死；
    # 真的失效时接口会返回 10301，页面那边会带出明确错误。
    authed = bool(tok.get("access_token")) and (
        manual or tok.get("expires_at", 0) > now
    )
    oconf = tdoc_oauth.oauth_conf()
    return {
        "ok": True,
        "tdoc": {
            "authorized": authed,
            "expires_in": 0 if manual else (max(0, tok.get("expires_at", 0) - now) if authed else 0),
            "open_id": tok.get("open_id", ""),
            "source": tok.get("source") or "oauth",
            "can_refresh": bool(tok.get("refresh_token")),
            "has_client_id": bool(oconf["client_id"]),
            "has_client_secret": bool(oconf["client_secret"]),
            "has_redirect_uri": bool(oconf["redirect_uri"]),
            "redirect_uri": oconf["redirect_uri"],
        },
        "kuaidi": {"provider": CFG["kuaidi"].get("provider", "mock")},
        "llm": {"vision_model": CFG["llm"]["vision_model"]},
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _auth_url() -> dict:
    state = secrets.token_urlsafe(16)
    _PENDING_STATE[state] = time.time() + _STATE_TTL
    for k, exp in list(_PENDING_STATE.items()):
        if exp < time.time():
            _PENDING_STATE.pop(k, None)
    return {"ok": True, "url": tdoc_oauth.build_auth_url(state), "state": state}


OK_PAGE = """<!doctype html><meta charset="utf-8"><title>授权完成</title>
<body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:520px;margin:80px auto;padding:0 20px;color:#111">
<h2 style="margin:0 0 8px">腾讯文档授权成功</h2>
<p style="color:#555;line-height:1.7">令牌已保存在服务端，可以直接回到工作台使用了。</p>
<p><a href="/" style="color:#0b7a75;font-weight:600">← 回到工作台</a></p></body>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "cargo-ledger/1.0"

    def log_message(self, fmt, *args):  # 静音默认访问日志，保留异常
        pass

    # ---------------------------------------------------------- 基础工具
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              headers: list | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data: dict, code: int = 200, headers: list | None = None):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), headers=headers)

    MAX_BODY = 16 * 1024 * 1024

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        # 先看长度再读：读进来再判断等于已经把内存吃了。
        if n > self.MAX_BODY:
            raise BodyTooLarge(f"请求体 {n // 1024 // 1024}MB 超过上限 {self.MAX_BODY // 1024 // 1024}MB")
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # ---------------------------------------------------------------- 口令
    #
    # 只有一个口令，没有用户名。挡的是"拿到链接就能看全部订单"这一个风险，
    # 不是要建身份体系——两三个人的内部工具，账号体系的开销远大于收益。

    def _is_https(self) -> bool:
        """要不要给 cookie 打 Secure。打在 http 上浏览器会直接不存，
        本地调试就登不进去了，所以按协议判断而不是写死。"""
        return ("https" in (self.headers.get("X-Forwarded-Proto") or "").lower()
                or "workbuddy.host" in (self.headers.get("Host") or ""))

    def _cookie(self, name: str) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def _sess_header(self, value: str, max_age: int) -> list:
        bits = [f"{auth_gate.cookie_name()}={value}", "Path=/",
                "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
        if self._is_https():
            bits.append("Secure")
        return [("Set-Cookie", "; ".join(bits))]

    def _authed(self) -> bool:
        a = CFG.get("auth") or {}
        secret = a.get("session_secret") or ""
        # fail closed：没配口令就谁也不放进来。反过来（没配就放行）等于
        # 部署完忘了配口令 = 台账公开，而这个错误没人会发现。
        if not secret or not a.get("password_hash"):
            return False
        return auth_gate.check_session(secret, self._cookie(auth_gate.cookie_name()))

    def _is_public(self, path: str) -> bool:
        # "/f" 不带斜杠也得算公开，否则客户手打少一个斜杠会跳到登录页
        # ——他会以为要账号，然后放弃补单。
        if path in ("/f", "/f/index.html"):
            return True
        return any(path.startswith(p) for p in PUBLIC_PREFIXES)

    def _client_ip(self) -> str:
        """限流用的客户端标识。

        部署在反向代理后面时 client_address 全是代理的 IP，按它限流等于
        把所有人算成一个人——一个人刷爆就把所有客户挡在门外。所以只在
        请求确实来自内网/本机（说明前面有一层我们自己的代理）时才看
        X-Forwarded-For，并且取**最后一跳**（第一跳是客户端自己填的，可伪造）。
        """
        peer = self.client_address[0]
        priv = (peer.startswith("127.") or peer.startswith("10.")
                or peer.startswith("192.168.") or peer.startswith("172.16.")
                or peer in ("::1", "localhost"))
        if priv:
            xff = (self.headers.get("X-Forwarded-For") or "").strip()
            if xff:
                parts = [p.strip() for p in xff.split(",") if p.strip()]
                if parts:
                    return parts[-1]
        return peer

    def _unauth(self):
        if self.path.startswith("/api/"):
            return self._json({"ok": False, "need_login": True,
                               "error": "需要口令，请先登录"}, 401)
        loc = "/login?next=" + urllib.parse.quote(self.path, safe="")
        self._send(302, b"", "text/plain; charset=utf-8", [("Location", loc)])

    def _login_page(self, next_url: str = "/"):
        a = CFG.get("auth") or {}
        ready = bool(a.get("password_hash") and a.get("session_secret"))
        warn = "" if ready else (
            '<div class="warn">服务端还没设置口令，任何人都进不来。<br>'
            '在服务器上跑 <code>python -m auth_gate set-password</code> 后重启即可。</div>')
        # 只接受站内相对路径。`//evil.com` 也是以 / 开头，但浏览器会当成
        # 协议相对 URL 跳出去——这是登录页最经典的开放重定向。
        safe_next = next_url if (next_url.startswith("/") and not next_url.startswith("//")
                                 and not next_url.startswith("/\\")) else "/"
        page = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>货物台账 · 登录</title><style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
 background:#f6f7f9;font:15px/1.6 -apple-system,"Segoe UI",system-ui,"PingFang SC","Microsoft YaHei",sans-serif;color:#111}
.card{width:min(360px,92vw);background:#fff;border:1px solid #e6e8eb;border-radius:14px;
 padding:26px 24px 22px;box-shadow:0 1px 3px rgba(16,24,40,.06),0 12px 32px -12px rgba(16,24,40,.12)}
h1{margin:0 0 4px;font-size:19px;font-weight:650;letter-spacing:.2px}
.sub{color:#6b7280;font-size:13px;margin-bottom:18px}
input{width:100%;padding:11px 12px;border:1px solid #d6dae0;border-radius:9px;font-size:15px;
 outline:none;transition:border-color .15s,box-shadow .15s}
input:focus{border-color:#0b7a75;box-shadow:0 0 0 3px rgba(11,122,117,.12)}
button{width:100%;margin-top:12px;padding:11px;border:0;border-radius:9px;background:#0b7a75;
 color:#fff;font-size:15px;font-weight:600;cursor:pointer}
button:disabled{opacity:.6;cursor:default}
.err{color:#b42318;font-size:13px;margin-top:10px;min-height:18px}
.warn{background:#fffaeb;border:1px solid #fedf89;color:#7a2e0e;border-radius:9px;
 padding:10px 12px;font-size:13px;margin-bottom:16px}
code{background:#f2f4f6;padding:1px 5px;border-radius:4px;font-size:12.5px}
.guest{margin-top:16px;padding-top:14px;border-top:1px solid #eef0f3;color:#6b7280;font-size:13px}
.guest a{color:#0b7a75;text-decoration:none}
</style></head><body><div class="card">
<h1>货物台账</h1><div class="sub">内部工具，需要口令</div>
__WARN__
<input id="pw" type="password" placeholder="口令" autocomplete="current-password" autofocus>
<button id="btn">进入</button>
<div class="err" id="msg"></div>
<div class="guest">只是来补快递单号？<a href="/f/">走这边</a>，不用口令。</div>
</div><script>
var NEXT=__NEXT__;
function go(){
  var b=document.getElementById('btn'), m=document.getElementById('msg');
  var pw=document.getElementById('pw').value;
  if(!pw){ m.textContent='先输口令'; return; }
  b.disabled=true; m.textContent='';
  fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({password:pw})})
   .then(function(r){return r.json()})
   .then(function(d){
     if(d.ok){ location.href=NEXT; return; }
     m.textContent=d.error||'登录失败'; b.disabled=false;
     document.getElementById('pw').select();
   })
   .catch(function(e){ m.textContent='网络错误：'+e.message; b.disabled=false; });
}
document.getElementById('btn').onclick=go;
document.getElementById('pw').addEventListener('keydown',function(e){ if(e.key==='Enter') go(); });
</script></body></html>"""
        page = (page.replace("__WARN__", warn)
                .replace("__NEXT__", json.dumps(safe_next)))
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def _login(self, body: dict):
        ip = self.client_address[0]
        left = LOGIN_LIMITER.locked(ip)
        if left:
            return self._json({"ok": False,
                               "error": f"错太多次，{max(1, left // 60 + 1)} 分钟后再试"}, 429)
        a = CFG.get("auth") or {}
        if not (a.get("password_hash") and a.get("session_secret")):
            return self._json({"ok": False,
                               "error": "服务端还没设置口令，先在服务器上跑 python -m auth_gate set-password"}, 503)
        pw = str(body.get("password") or "")
        if not auth_gate.verify_password(pw, a["password_hash"]):
            n = LOGIN_LIMITER.fail(ip)
            remain = max(0, LOGIN_LIMITER.limit - n)
            tip = f"，再错 {remain} 次就锁 15 分钟" if remain else "，已锁定 15 分钟"
            return self._json({"ok": False, "error": f"口令不对{tip}"}, 401)
        LOGIN_LIMITER.reset(ip)
        days = int(a.get("session_days") or 30)
        return self._json({"ok": True, "days": days},
                          headers=self._sess_header(
                              auth_gate.make_session(a["session_secret"], days), days * 86400))

    # ----------------------------------------------------------------- GET
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        q = urllib.parse.parse_qs(u.query)
        try:
            if path in ("/login", "/login.html"):
                return self._login_page((q.get("next") or ["/"])[0])

            if path == "/api/health":
                return self._json({"ok": True, "python": sys.version.split()[0]})

            # 闸门放在最前面：后面每个 if 都是在"已经确认是内部人"的前提下写的。
            # 顺序反了（先匹配路由再鉴权）就会出现某个分支忘了判断的漏网。
            if not self._is_public(path) and not self._authed():
                return self._unauth()

            if path in ("/", "/index.html"):
                page = (WEB_DIR / "index.html").read_bytes()
                return self._send(200, page, "text/html; charset=utf-8")

            if path in ("/f", "/f/", "/f/index.html"):
                page = (WEB_DIR / "patch.html").read_bytes()
                return self._send(200, page, "text/html; charset=utf-8")

            if path == "/api/patch_pending":
                # 运营侧待办：有单号但没勾复核的行。读，不需要二次确认。
                try:
                    return self._json(patch_pending())
                except tdoc_oauth.OAuthError as exc:
                    return self._json({"ok": False, "need_auth": True,
                                       "error": f"还没连上腾讯文档：{exc}"}, 401)

            if path == "/api/status":
                return self._json(_status())

            if path == "/api/probe":
                # 排障用，不在页面上暴露。必须带 admin_key，且统一回 404：
                # 不告诉扫描者"这里有个接口，只是你 key 不对"。
                want = str(CFG.get("admin_key") or "")
                got = (q.get("key") or [""])[0].strip()
                if not want or got != want:
                    return self._json({"ok": False, "error": "not found"}, 404)
                tgt = dict(CFG["tdoc"].get("product") or {})
                fid = (q.get("file_id") or [""])[0].strip() or tgt.get("file_id", "")
                sid = (q.get("sheet_id") or [""])[0].strip() or tgt.get("sheet_id", "")
                if not (fid and sid):
                    return self._json({"ok": False, "error": "config 里没有可诊断的表"}, 400)
                try:
                    r = tdoc_oauth.probe(fid, sid)
                except tdoc_oauth.OAuthError as exc:
                    return self._json({"ok": False, "need_auth": True,
                                       "error": f"还没拿到可用凭证：{exc}"}, 401)
                # open_id 属于账号标识，排障够用就行，不整串吐出来
                oid = r.get("open_id") or ""
                r["open_id"] = (oid[:4] + "…" + oid[-4:]) if len(oid) > 10 else oid
                return self._json(r)

            if path == "/api/whereami":
                # 部署环境体检。同样要 admin_key、同样回 404：这是排障口，不是功能。
                want = str(CFG.get("admin_key") or "")
                got = (q.get("key") or [""])[0].strip()
                if not want or got != want:
                    return self._json({"ok": False, "error": "not found"}, 404)
                return self._json({"ok": True, **_whereami()})

            if path == "/api/auth_url":
                return self._json(_auth_url())

            if path == "/oauth/callback":
                return self._callback(q)

            if path == "/api/arrivals":
                # 未授权时不是 500（服务没坏），而是 401 + 明确原因。
                # 这个区别对运营很重要：500 会让人以为系统故障，401 会让人去授权。
                try:
                    return self._json(arrivals((q.get("date") or ["today"])[0]))
                except tdoc_oauth.OAuthError as exc:
                    return self._json({"ok": False, "need_auth": True,
                                       "error": f"还没连上腾讯文档：{exc}"}, 401)

            if path == "/api/quote_watch":
                # 随手看一遍行情体检。只读表，不改任何东西，也不需要确认。
                try:
                    return self._json(quote_watch())
                except tdoc_oauth.OAuthError as exc:
                    return self._json({"ok": False, "need_auth": True,
                                       "error": f"还没连上腾讯文档：{exc}"}, 401)

            if path == "/api/track":
                # 两种用法：
                #   1) record_id=台账里那一行 → 单号/尾号/快递公司都从表里取，
                #      运营不用手抄尾号（这也是尾号最容易抄错的地方）。
                #   2) no=...&tail=... → 临时查一个表外的单号。
                rid = (q.get("record_id") or [""])[0].strip()
                no = (q.get("no") or [""])[0].strip()
                tail = (q.get("tail") or [""])[0].strip()
                # 手填的尾号优先于台账：台账里那格可能是下单人手机号，
                # 而承运商要的是收件人/寄件人在它自己系统里登记的那个号。
                tail_src = "input" if tail else ""
                hint = ""
                row = None
                if rid or not tail:
                    # 要定位行（rid）或者要从台账取尾号，都得读一次表。
                    try:
                        orders = read_orders()
                    except tdoc_oauth.OAuthError as exc:
                        return self._json({"ok": False, "need_auth": True,
                                           "error": f"还没连上腾讯文档：{exc}"}, 401)
                    if rid:
                        row = next((o for o in orders if o["record_id"] == rid), None)
                        if not row:
                            return self._json({"ok": False, "error": "台账里找不到这一行（可能已被删除）"}, 404)
                        no = row["tracking_no"] or no
                        hint = row["carrier"] or ""
                    # 只有单号、没带 record_id 时（手动粘单号），也反查台账那一行。
                    # 以前这里只认 record_id，手动查就永远没尾号，顺丰/京东必报
                    # "请提供手机尾号"——而尾号本来就在表里躺着。
                    if not tail and no:
                        if row is None:
                            row = _order_by_tracking(no, orders)
                        if row:
                            tail = str(row.get("phone_tail") or "").strip()
                            tail_src = "ledger" if tail else ""
                            hint = hint or str(row.get("carrier") or "")
                if not no:
                    return self._json({"ok": False,
                                       "error": "这单还没填快递单号，先补单号再查轨迹"}, 400)
                # 历史单直接拒查。放在调用接口之前——这个判断的全部意义就是
                # 不让它花掉一次快递鸟额度；查完再提示等于什么都没省。
                if rid and _is_history(row):
                    since = str((CFG.get("rules") or {}).get("track_since") or "")
                    return self._json({"ok": False,
                                       "error": f"这单下单日期早于 {since}，按规则不再查物流轨迹。"
                                                f"（历史单：不查轨迹、不计入在途）"}, 400)
                cands = carriers.guess_carriers(no)
                res = carriers.query(no, tail, hint or (cands[0][0] if cands else ""),
                                     CFG["kuaidi"])
                # 尾号被承运商拒了，得说清是"哪个尾号"被拒——不然运营会拿着
                # 台账那个数反复重试，查十次拒十次，每次都以为是系统坏了。
                if (not res.get("ok") and tail_src == "ledger"
                        and ("尾号不正确" in str(res.get("error") or "")
                             or "尾号对不上" in str(res.get("error") or ""))):
                    res["error"] = (str(res.get("error") or "")
                                    + f"\n\n（刚才用的是台账里这一行的手机尾号 {tail}。"
                                      f"它被承运商拒了，说明这一格填的多半是下单人手机号，"
                                      f"不是收件人/寄件人在快递系统里登记的那个。"
                                      f"去下单平台看订单页的收件人手机号（隐私面单是一串虚拟号），"
                                      f"把后四位填到上面的「手机尾号」再查一次。）")
                return self._json({"ok": True, "tracking_no": no,
                                   "phone_tail": tail, "tail_source": tail_src,
                                   "has_phone_tail": bool(str(tail).strip()),
                                   "result": res})

            return self._json({"ok": False, "error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001 - 顶层兜底，绝不让进程崩
            traceback.print_exc()
            return self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    def _callback(self, q: dict):
        code = (q.get("code") or [""])[0].strip()
        state = (q.get("state") or [""])[0].strip()
        err = (q.get("error") or [""])[0].strip()
        if err:
            desc = (q.get("error_description") or [""])[0]
            return self._send(400, f"授权失败：{err} {desc}".encode(), "text/plain; charset=utf-8")
        if not code:
            return self._send(400, b"missing code", "text/plain; charset=utf-8")
        exp = _PENDING_STATE.pop(state, None)
        # state 对不上就拒绝：否则别人构造一个带 code 的链接就能让服务端换 token，
        # 挂到他自己控制的文档授权上。
        if exp is None or exp < time.time():
            return self._send(400, "state 无效或已过期，请回到工作台重新发起授权".encode(),
                              "text/plain; charset=utf-8")
        try:
            tdoc_oauth.exchange_code(code)
        except Exception as exc:  # noqa: BLE001
            return self._send(500, f"换取令牌失败：{exc}".encode(), "text/plain; charset=utf-8")
        return self._send(200, OK_PAGE.encode(), "text/html; charset=utf-8")

    # ---------------------------------------------------------------- POST
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        try:
            body = self._body()

            if path == "/api/login":
                return self._login(body)

            if path == "/api/logout":
                return self._json({"ok": True}, headers=self._sess_header("", 0))

            if not self._is_public(path) and not self._authed():
                return self._unauth()

            # ---------------------------------------------- 公开补单（无需口令）
            if path in ("/api/pub/lookup", "/api/pub/submit"):
                ip = self._client_ip()
                lim = PUB_LOOKUP_RATE if path.endswith("lookup") else PUB_SUBMIT_RATE
                if not lim.allow(ip):
                    return self._json({"ok": False,
                                       "error": "操作太频繁，等 1 分钟再试"}, 429)
                try:
                    if path.endswith("lookup"):
                        return self._json(pub_lookup(body.get("tail") or ""))
                    return self._json(pub_submit(body.get("tail") or "",
                                                 body.get("hid") or "",
                                                 body.get("tracking_no") or ""))
                except PubError as exc:
                    return self._json({"ok": False, "error": str(exc)}, 400)
                except tdoc_oauth.OAuthError:
                    # 不把腾讯的原始报错吐给客户看：那是一堆内部细节，
                    # 客户看不懂，也轮不到他处理。
                    return self._json({"ok": False,
                                       "error": "系统暂时读不到台账，请稍后再试"}, 503)

            if path == "/api/patch_confirm":
                # 运营核对完点这里：通过 → 写表；退回 → 只清队列。
                #
                # 这里踩过一次，代价是三轮排查全错方向：函数改成收「队列条目
                # id」了，路由还在这儿读 body["record_id"]，前端发的又是
                # {"id": ...}，于是 key 永远为空字符串，永远命中"这条补单
                # 已经不在了"。看起来像"队列丢了""多实例各看各的"，其实是
                # 参数名没对上。同一个错误还骗过我自己一次，让我把结论写成
                # 了"线上多实例"——体检接口实测 pid 恒定、计数器递增，单实例。
                # 教训：报错文案描述的是症状，不是原因，别拿它当结论。
                item_id = (body.get("id") or body.get("record_id") or "").strip()
                if not item_id:
                    return self._json({"ok": False,
                                       "error": "没收到要确认哪一条，刷新页面重试"}, 400)
                try:
                    return self._json(patch_confirm(item_id, bool(body.get("reject"))))
                except ConfirmError as exc:
                    return self._json({"ok": False, "error": str(exc)}, 400)
                except tdoc_oauth.OAuthError as exc:
                    return self._json({"ok": False, "need_auth": True,
                                       "error": f"还没连上腾讯文档：{exc}"}, 401)

            if path == "/api/parse_text":
                text = (body.get("text") or "").strip()
                if not text:
                    return self._json({"ok": False, "error": "粘贴内容为空"}, 400)
                r = parse_order.parse_order_text(text)
                return self._json({"ok": True, "parsed": r})

            if path == "/api/parse_quote":
                b64 = body.get("image_base64") or ""
                if not b64:
                    return self._json({"ok": False, "error": "没有收到图片"}, 400)
                if len(b64) > 12 * 1024 * 1024:
                    return self._json({"ok": False, "error": "图片太大（>9MB），先压缩"}, 413)
                mime = body.get("mime") or "image/jpeg"
                try:
                    r = quote_parse.parse_quote_image(
                        cloud(), b64, mime, CFG["llm"]["vision_model"],
                        timeout=float(CFG["llm"]["timeout_sec"]),
                    )
                except CloudError as exc:
                    return self._json({"ok": False, "error": f"模型调用失败：{exc}"}, 502)
                return self._json(r)

            if path == "/api/adopt":
                # 已下线：这个页面是给运营用的，任何拿到链接的人都能贴 token
                # 就等于把台账的写入权公开。凭证只从服务端（配置文件 / 环境变量）
                # 读取，页面不留录入入口。
                return self._json({"ok": False,
                                   "error": "该入口已关闭，凭证请在服务端配置"}, 410)

            if path == "/api/write_quote":
                # 两阶段：带 rows 只出差异（不写），带 confirm_token 才真写。
                # 这个顺序不能反——反了就变成"点一下直接改表"，没有确认环节。
                token = (body.get("confirm_token") or "").strip()
                try:
                    if token:
                        it = _take_write("quote", token)
                        return self._json(commit_quote_rows(it["preview"],
                                                            it["payload"].get("quote_date", "")))
                    rows = body.get("rows") or []
                    if not isinstance(rows, list) or not rows:
                        return self._json({"ok": False, "error": "没有要写入的行"}, 400)
                    preview = preview_quote_rows(rows)
                    return self._json(_stage_write(
                        "quote", {"quote_date": body.get("quote_date") or ""}, preview))
                except ConfirmError as exc:
                    return self._json({"ok": False, "error": str(exc)}, 400)
                except tdoc_oauth.OAuthError as exc:
                    return self._json({"ok": False, "need_auth": True,
                                       "error": f"还没连上腾讯文档：{exc}"}, 401)

            if path == "/api/write_product":
                # 录一单 → 商品信息表。同样是两阶段：先出「会新建/会改动什么」，
                # 确认票据换真写。读表不需要确认，改表一律要。
                token = (body.get("confirm_token") or "").strip()
                try:
                    if token:
                        it = _take_write("product", token)
                        return self._json(commit_product(it["payload"]))
                    parsed = body.get("parsed") or {}
                    if not isinstance(parsed, dict) or not parsed:
                        return self._json({"ok": False,
                                           "error": "没有识别结果可写"}, 400)
                    return self._json(stage_product(preview_product(parsed)))
                except ConfirmError as exc:
                    return self._json({"ok": False, "error": str(exc)}, 400)
                except tdoc_oauth.OAuthError as exc:
                    return self._json({"ok": False, "need_auth": True,
                                       "error": f"还没连上腾讯文档：{exc}"}, 401)

            return self._json({"ok": False, "error": "not found"}, 404)
        except BodyTooLarge as exc:
            return self._json({"ok": False, "error": str(exc)}, 413)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)


def main():
    port = int(os.environ.get("PORT", "8000"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[cargo-ledger] listening on 0.0.0.0:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
