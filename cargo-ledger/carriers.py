"""快递单号识别 + 物流查询适配层。

统一把各家的返回折算成同一个结构，上层不用关心是哪家物流。
注意：kdniao / kuaidi100 两个真实通道尚未用真实 key 跑通过，属于按官方文档实现的待验证代码，
mock 通道可用，用来跑通链路和演示。
"""

import base64
import hashlib
import json
import random
import re
import time
import urllib.parse
import urllib.request

# ---------- 单号识别 ----------

# 字母前缀：可以唯一确定
PREFIX_MAP = [
    (r"^SF\d{10,}$", "顺丰", "SF"),
    (r"^JD[A-Z]{0,2}\d{6,}$", "京东", "JD"),
    (r"^YT\d{10,}$", "圆通", "YTO"),
    (r"^JT\d{10,}$", "极兔", "JTSD"),
    (r"^DPK\d+$", "德邦", "DBL"),
    (r"^[A-Z]{2}\d{9}CN$", "EMS", "EMS"),
]

# 纯数字：只能给候选，按业务实际优先级排（数码品类以顺丰/京东为主）
DIGIT_CANDIDATES = [("顺丰", "SF"), ("京东", "JD"), ("极兔", "JTSD"), ("中通", "ZTO"),
                    ("圆通", "YTO"), ("韵达", "YD"), ("申通", "STO"), ("EMS", "EMS")]

CITY_RE = re.compile(r"([\u4e00-\u9fa5]{2,8}?(?:市|自治州|地区|盟))")


def guess_carriers(tracking_no: str) -> list:
    """返回候选 [(名称, 代码)]，第一个是最可能的。"""
    s = (tracking_no or "").strip().upper().replace(" ", "").replace("-", "")
    if not s:
        return []
    for pat, name, code in PREFIX_MAP:
        if re.match(pat, s):
            return [(name, code)]
    if s.isdigit():
        return list(DIGIT_CANDIDATES)
    return []


def extract_city(address: str) -> str:
    if not address:
        return ""
    m = CITY_RE.search(address)
    if m:
        return m.group(1)
    # 直辖市
    for d in ("北京", "上海", "天津", "重庆"):
        if d in address:
            return d + "市"
    return ""


# ---------- 状态归一 ----------

DELIVERING_WORDS = ("派送中", "派件中", "正在派送", "派送", "投递中")
# ⚠️ 这里原来写的是 "代收"，后果是「快件已存放至【XX超市代收点】」被判成已签收
# ——"代收点"是个地名，不是有人签收。加了「已到达」之后这个错才暴露出来：
# 判成已签收的货不进在途、也不进到货提醒，等于货压在驿站没人管。
# 所以只认"人代收"的说法，不认地点。
SIGNED_WORDS = ("已签收", "签收", "已妥投", "已代收", "代收人",
                "他人代收", "物业代收", "家人代收", "同事代收", "门卫代收")
ABNORMAL_WORDS = ("拒收", "退回", "疑难", "破损", "丢失", "失败", "滞留")
ARRIVED_CITY_WORDS = ("分拨", "转运", "集散", "中转", "到达")

# 「已到达」= 货已经到了末端（驿站/快递柜/网点/配送站），但还没被人取走。
# 和「运输中」的区别在于"还要不要等物流车"，和「已签收」的区别在于
# "这单还得有人去拿"——运营最需要知道的就是这个状态。
#
# ⚠️ 只认末端地点，**不能**只匹配"到达"两个字：中转轨迹里全是
# "快件已到达【深圳转运中心】"，那种还在路上，判成已到达会把一堆在路上
# 的货误报成"到货待取"，比不判更糟。
ARRIVED_WORDS = ("驿站", "快递柜", "丰巢", "自提柜", "自提点", "代收点",
                 "便民服务点", "配送站", "末端网点", "到达目的地",
                 "已到件", "待取件", "取件码", "已存放", "已暂存",
                 "已放入", "请及时取件")


def normalize_status(text: str, signed_flag=False) -> str:
    t = text or ""
    if signed_flag or any(w in t for w in SIGNED_WORDS):
        return "已签收"
    if any(w in t for w in ABNORMAL_WORDS):
        return "退回" if ("退回" in t or "拒收" in t) else "异常"
    if any(w in t for w in DELIVERING_WORDS):
        return "派送中"
    # 放在"派送中"之后：轨迹写"配送员正在派件"时是更靠后的状态，别被
    # 同一句里的"...已到达XX配送站"抢先判成已到达。
    if any(w in t for w in ARRIVED_WORDS):
        return "已到达"
    if t:
        return "运输中"
    return "待发货"


def _parse_time(s: str) -> str:
    return (s or "").strip()


def _eta_from_traces(traces: list, dest_city: str):
    """按轨迹推断到达日；返回 (日期字符串, 依据)。依据是 trace=有轨迹证据，guess=纯推测。"""
    if not traces:
        return "", ""
    last = traces[0]
    desc = last.get("desc") or ""
    today = time.strftime("%Y-%m-%d")
    tomorrow = time.strftime("%Y-%m-%d", time.localtime(time.time() + 86400))
    if any(w in desc for w in DELIVERING_WORDS):
        return today, "trace"
    # 已经到了驿站/快递柜：货就在目的地，今天必须有人去取，不用等明天。
    if any(w in desc for w in ARRIVED_WORDS):
        return today, "trace"
    if dest_city and dest_city in desc and any(w in desc for w in ARRIVED_CITY_WORDS):
        return tomorrow, "trace"
    return "", ""


# ---------- 查询通道 ----------

def query(tracking_no: str, phone_tail: str, carrier_hint: str, cfg: dict) -> dict:
    provider = (cfg or {}).get("provider", "mock")
    candidates = guess_carriers(tracking_no)
    if carrier_hint:
        hint = carrier_hint.strip()
        for name, code in candidates:
            if name == hint:
                candidates = [(name, code)] + [c for c in candidates if c[0] != hint]
                break
        else:
            candidates = [(hint, hint)] + candidates

    if not candidates:
        return {"ok": False, "error": "单号格式无法识别", "traces": []}

    if provider == "mock":
        return _mock(tracking_no, candidates[0], phone_tail)

    last = None
    for name, code in candidates:
        try:
            if provider == "kdniao":
                res = _kdniao(tracking_no, phone_tail, code, cfg)
            elif provider == "kuaidi100":
                res = _kuaidi100(tracking_no, phone_tail, code, cfg)
            else:
                return {"ok": False, "error": f"未知 provider: {provider}", "traces": []}
            if res.get("ok"):
                res["carrier"] = name
                return res
            last = res
        except Exception as e:  # 网络/签名问题不中断整批
            last = {"ok": False, "error": f"{name}: {e}", "traces": []}

    if not last:
        return {"ok": False, "error": "所有候选承运商均查询失败", "traces": []}
    # 把错误原样带上去：diagnose 面板要靠 detail 区分"账号没套餐"和"单号查不到"
    out = {"ok": False, "error": last.get("error") or "查询失败", "traces": []}
    if last.get("detail"):
        out["detail"] = last["detail"]
    return out


def _kdniao_reason(reason: str) -> str:
    """把快递鸟的原始 Reason 翻成能直接行动的话。

    "没有可用套餐" 是最容易误判的一个：它看着像"接口不给我数据"，
    实际是账号没开通/没买查询次数。不翻译的话人会一直去查单号对不对。
    """
    r = (reason or "").strip()
    if not r:
        return "快递鸟返回失败，但没给原因"
    if "没有可用套餐" in r or "套餐" in r and "可用" in r:
        # 这一句能出来，说明 EBusinessID + DataSign 已经过了校验：
        # 凭证错会先报「请核实ID和key」，签名错会报「签名」，
        # 走不到业务层。所以别让人再去翻 key。
        # 三种原因共用这一句：没开通、套餐到期、当天额度用完。
        return ("快递鸟账号还没有可用的查询套餐。\n"
                "  凭证是对的（签名已通过校验，才会走到业务层报这句），不用改 key。\n"
                "  按官方流程：登录 www.kdniao.com → 我的会员中心 完成实名认证\n"
                "  → 产品服务订购，开通「物流查询（免费版）」会员套餐。\n"
                "  如果已经开通过，就是套餐到期或当天查询量用完了，\n"
                "  去「产品服务管理 / 产品仪表盘」看剩余次数，次日恢复或续订。\n"
                "  开通后再点一次查询即可，代码不用改。")
    if "CustomerName" in r or "客户名称" in r:
        return ("这家快递必须带「收件人或寄件人」手机号后四位"
                "（下单人手机号不算）。补上尾号再查。")
    if "尾号不正确" in r or "手机号不正确" in r:
        # 快递鸟官方文档（快递查询 8002 / 即时查询 8001 的 CustomerName 说明）明确写了：
        #   顺丰/跨越/中通 必填「寄件人 or 收件人 手机号后四位」
        #   ●尽量使用寄件人尾号
        #   ●顺丰可能用虚拟号下单录入系统，作为验证标准
        #   ●顺丰/中通开启隐私保护的订单，需要用寄件人手机尾号或隐私号码验证
        # 所以"收件人真实尾号"被拒是这个 API 的固有行为，不是填错。
        return ("尾号对不上。顺丰/跨越/中通只认「寄件人或收件人」手机号后四位，"
                "官方建议优先用【寄件人】尾号。\n"
                "  开了隐私保护的单（拼多多/抖音/淘宝常见）只认寄件人尾号或虚拟号，"
                "收货人的真实尾号会被拒。\n"
                "  先用【顺丰速运+】小程序输单号+尾号验一次：能进说明尾号对、是接口侧问题；"
                "进不去就是尾号确实不匹配。")
    if "不支持当前快递公司" in r or "不支持的快递公司" in r:
        return "这个单号对应的快递公司不在你套餐的覆盖范围里，需在后台加开该公司查询权限。"
    if "核实ID和key" in r or "请核实ID" in r:
        return ("凭证不对：用户ID 或 API Key 至少一个错（实测两个都错时报的是同一句话，"
                "所以分不出是哪个，两个都重新复制一遍）。\n"
                "  位置：www.kdniao.com → 用户中心 → 我的会员中心。")
    if "无效的EBusinessID" in r or "EBusinessID" in r:
        return "快递鸟 用户ID 不对，去用户中心核对后改 config.json 的 kdniao_ebusiness_id。"
    if "签名" in r or "DataSign" in r or "Sign" in r:
        return "快递鸟 API Key 不对（签名校验失败），核对 kdniao_app_key。"
    if "暂无轨迹" in r or "无轨迹" in r or "没有轨迹" in r:
        return "单号查不到轨迹：可能刚发出还没揽收，或单号/承运商填错。"
    if "运单号不匹配" in r or "单号格式" in r:
        return "快递鸟认为这个单号和所选快递公司不匹配，核对单号与承运商。"
    if "单号" in r and ("错" in r or "无效" in r or "不合法" in r):
        return f"单号看起来不对：{_unwrap(r)}"
    return _unwrap(r)


def _kdniao_level(reason: str) -> str:
    """判断这条错误是"没到快递公司"还是"快递公司自己拒了"。

    区别很重要：
      account  = 凭证/套餐问题，换 RequestType 有可能好，值得再试下一个端点；
      business = 已经进了承运商系统（尾号、单号、承运商编码被拒），
                 再问 8001/1002 只会拿到"没有可用套餐"，把真因埋掉。
    """
    r = reason or ""
    if "套餐" in r or "次数" in r or "余额" in r:
        return "account"
    if "核实ID" in r or "请核实ID" in r or "签名" in r or "EBusinessID" in r or "特殊字符" in r:
        return "account"
    # 通道能力问题：这个接口不覆盖这家快递。换下一个端点可能就有了，
    # 所以不能当 business 提前返回。
    if ("不支持当前快递公司" in r or "不支持的快递公司" in r
            or "渠道升级" in r or "物流公司编号不正确" in r):
        return "channel"
    return "business"


def _unwrap(reason: str) -> str:
    """剥掉快递鸟返回里层层嵌套的包装词，只留人能读的那一句。

    原始形态长这样：业务错误[无效的输入[运单号不匹配，请核实]]
    """
    s = reason or ""
    for _ in range(4):
        m = re.match(r"^[^\[\]]{0,8}\[(.+)\]$", s)
        if not m:
            break
        s = m.group(1)
    return s.strip()


# 三个查询接口报文结构一样，差别在 RequestType：
#   8002 = 快递查询（统一接口，可不传承运商，接口自己识别）——本账号实测可用
#   8001 = 付费即时查询 / 1002 = 免费即时查询——本账号实测都报「没有可用套餐」
# 所以 8002 排第一：它能进业务层，前两个连门都进不去。留着是给
# 别的账号（有付费配额）用，不是为了兜底同一个错误。
_KDNIAO_ENDPOINTS = [
    ("8002 快递查询", "https://api.kdniao.com/api/dist", "8002"),
    ("8001 付费即时查询", "https://api.kdniao.com/api/dist", "8001"),
    ("1002 免费即时查询", "https://api.kdniao.com/Ebusiness/EbusinessOrderHandle.aspx", "1002"),
]


def _kdniao(tracking_no: str, phone_tail: str, code: str, cfg: dict) -> dict:
    """快递鸟即时查询。文档：https://www.kdniao.com

    code 传空 = 不指定承运商，交给 8002 自己识别（8002 支持）。
    """
    eid = cfg.get("kdniao_ebusiness_id", "")
    key = cfg.get("kdniao_app_key", "")
    if not eid or not key:
        return {"ok": False, "error": "未配置快递鸟 EBusinessID / AppKey"}

    tail = str(phone_tail or "").strip()
    payload = {"LogisticCode": tracking_no}
    if code:
        payload["ShipperCode"] = code
    # 顺丰/京东必须带「收件人 或 寄件人」手机号后四位，缺了接口直接报
    # CustomerName不能为空（8002 实测）。这不是可选项，缺的时候别发请求——
    # 发了只会拿到一句含糊的"暂无轨迹"，人会以为是单号错了。
    if code in ("SF", "JD"):
        if not tail:
            nm = "顺丰" if code == "SF" else "京东"
            return {"ok": False,
                    "error": f"{nm}必须提供「收件人或寄件人」手机号后四位，"
                             f"下单人手机号不算。补上尾号再查。"}
        payload["CustomerName"] = tail
    elif tail:
        # 不知承运商时也带上：万一是顺丰/京东，缺它必失败；其余公司会忽略这个字段
        payload["CustomerName"] = tail

    request_data = json.dumps(payload, ensure_ascii=False)
    sign = base64.b64encode(
        hashlib.md5((request_data + key).encode("utf-8")).hexdigest().encode("utf-8")
    ).decode("utf-8")

    errs = []
    empty = None
    for label, url, req_type in _KDNIAO_ENDPOINTS:
        body = urllib.parse.urlencode({
            "RequestData": request_data,
            "EBusinessID": eid,
            "RequestType": req_type,
            "DataSign": sign,
            "DataType": "2",
        }).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=body)
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:                       # 网络问题换下个端点没意义
            errs.append(f"{label}: {type(e).__name__}: {e}")
            continue

        if not data.get("Success"):
            reason = data.get("Reason") or ""
            errs.append(f"{label}: {_kdniao_reason(reason)}")
            # 承运商已经受理并拒绝了（尾号/单号/编码），换端点结果不会变。
            # 继续问 8001/1002 只会多一条"没有可用套餐"，把真正的原因淹掉。
            if _kdniao_level(reason) == "business":
                return {"ok": False, "error": errs[0], "detail": errs}
            continue

        traces = [{"time": (t.get("AcceptTime") or "").strip(),
                   "desc": (t.get("AcceptStation") or "").strip()}
                  for t in (data.get("Traces") or [])]
        traces.sort(key=lambda x: x["time"], reverse=True)  # 不信任接口给的顺序

        if not traces and empty is None:
            # 免费通道有时"成功但零轨迹"（该通道不覆盖这家快递），
            # 直接当"没物流"会误判。先记下来，再看付费通道有没有。
            empty = label
            continue

        # State 有的通道返回数字 3，有的返回字符串 "3"，两边都认，否则签收永远判不出来
        out = _pack(traces, str(data.get("State", "")).strip() == "3", code)
        out["via"] = label
        if empty:
            out["note"] = f"{empty} 未返回轨迹，本结果来自 {label}"
        return out

    if empty:
        # 各通道都没轨迹：这是"查不到"，不是"查询失败"
        return {"ok": True, "traces": [], "status": "待发货",
                "last_trace": "", "signed": False, "via": empty,
                "note": "接口连通但没有轨迹，多为尚未揽收或单号/尾号不符"}

    # 所有端点都失败：只报第一条，但把"是账号问题还是单号问题"写在话里
    return {"ok": False, "error": errs[0] if errs else "快递鸟查询失败",
            "detail": errs}


def probe_kdniao(cfg: dict) -> dict:
    """诊断快递鸟账号，把三件常被混为一谈的事分开：

      1. 凭证/签名对不对（错了就得改配置）
      2. 账号有没有查询套餐（错了得去后台开通/充值，改代码没用）
      3. 某个具体单号查不查得到（这才和单号本身有关）

    做法：发一条语法合法、但不存在的单号。它必然查不到轨迹，
    但会不会被拒、以什么理由被拒，恰好暴露前两层。
    """
    eid = (cfg.get("kdniao_ebusiness_id") or "").strip()
    key = (cfg.get("kdniao_app_key") or "").strip()
    out = {"ebusiness_id": eid, "app_key_len": len(key), "steps": []}
    if not eid or not key:
        out["verdict"] = "missing_credentials"
        out["advise"] = "config.json 的 kuaidi.kdniao_ebusiness_id / kdniao_app_key 没填全。"
        return out

    probe_no = "SF0000000000000"     # 格式合法但不可能存在
    payload = {"ShipperCode": "SF", "LogisticCode": probe_no, "CustomerName": "0000"}
    rd = json.dumps(payload, ensure_ascii=False)
    sign = base64.b64encode(
        hashlib.md5((rd + key).encode("utf-8")).hexdigest().encode("utf-8")
    ).decode("utf-8")

    for label, url, req_type in _KDNIAO_ENDPOINTS:
        body = urllib.parse.urlencode({
            "RequestData": rd, "EBusinessID": eid, "RequestType": req_type,
            "DataSign": sign, "DataType": "2",
        }).encode("utf-8")
        step = {"endpoint": label, "request_type": req_type}
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, data=body), timeout=20) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            data = json.loads(raw)
        except Exception as e:
            step.update(ok=False, category="network", reason=f"{type(e).__name__}: {e}")
            out["steps"].append(step)
            continue

        reason = (data.get("Reason") or "").strip()
        step["raw_reason"] = reason
        step["success"] = bool(data.get("Success"))
        if data.get("Success"):
            # 探针单号不存在，理论上不该到这里；真到了说明账号完全可用
            step.update(ok=True, category="working")
        elif "套餐" in reason or "次数" in reason or "余额" in reason:
            step.update(ok=False, category="no_package",
                        reason="账号没有可用查询套餐/次数")
        elif "核实ID和key" in reason or "核实ID" in reason:
            # 实测：key 错、用户ID 错，返回的都是这一句，区分不出来
            step.update(ok=False, category="bad_credentials", reason=reason)
        elif "EBusinessID" in reason:
            step.update(ok=False, category="bad_ebusiness_id", reason=reason)
        elif "Sign" in reason or "签名" in reason:
            step.update(ok=False, category="bad_signature", reason=reason)
        elif "CustomerName" in reason or "尾号不正确" in reason or "暂无轨迹" in reason:
            # 到了参数校验 / 查不到轨迹，说明凭证、套餐、承运商都过了
            step.update(ok=True, category="working")
        else:
            step.update(ok=False, category="other", reason=reason)
        out["steps"].append(step)

    cats = [s.get("category") for s in out["steps"]]
    if "working" in cats:
        out["verdict"] = "working"
        out["advise"] = "凭证、签名、套餐都通，可以直接查真实单号了。"
    elif "no_package" in cats:
        out["verdict"] = "no_package"
        out["advise"] = ("凭证和签名是有效的（否则会报 EBusinessID / 签名错误），"
                         "卡在账号没有可用查询套餐。官方流程：www.kdniao.com 登录 → "
                         "我的会员中心完成实名认证 → 产品服务订购，开通「物流查询（免费版）」会员套餐；"
                         "已开通过就是套餐到期或当天额度用完，去「产品服务管理」看剩余次数。")
    elif "bad_credentials" in cats:
        out["verdict"] = "bad_credentials"
        out["advise"] = ("用户ID 或 API Key 不对（快递鸟对这两种错报同一句话，分不出是哪个），"
                         "两个都去用户中心重新复制一遍。")
    elif "bad_ebusiness_id" in cats:
        out["verdict"] = "bad_ebusiness_id"
        out["advise"] = "用户ID 不对，去用户中心复制「用户ID」。"
    elif "bad_signature" in cats:
        out["verdict"] = "bad_signature"
        out["advise"] = "API Key 不对，去用户中心重新复制。"
    elif all(c == "network" for c in cats):
        out["verdict"] = "network"
        out["advise"] = "连不上 api.kdniao.com，检查本机网络/代理。"
    else:
        out["verdict"] = "unknown"
        out["advise"] = "没识别出的返回，把 steps 里的 raw_reason 发出来。"
    return out


def _kuaidi100(tracking_no: str, phone_tail: str, code: str, cfg: dict) -> dict:
    """快递100 实时查询。文档：https://api.kuaidi100.com"""
    customer = cfg.get("kuaidi100_customer", "")
    key = cfg.get("kuaidi100_key", "")
    if not customer or not key:
        return {"ok": False, "error": "未配置快递100 customer / key"}
    com_map = {"SF": "shunfeng", "JD": "jd", "YTO": "yuantong", "ZTO": "zhongtong",
               "YD": "yunda", "STO": "shentong", "EMS": "ems", "JTSD": "jtexpress"}
    param = {"com": com_map.get(code, code.lower()), "num": tracking_no, "resultv2": "4"}
    if phone_tail and code in ("SF", "JD"):
        param["phone"] = str(phone_tail)
    param_json = json.dumps(param, ensure_ascii=False)
    sign = hashlib.md5((param_json + customer + key).encode("utf-8")).hexdigest().upper()
    body = urllib.parse.urlencode({
        "customer": customer, "sign": sign, "param": param_json,
    }).encode("utf-8")
    req = urllib.request.Request("https://poll.kuaidi100.com/poll/query.do", data=body)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("returnCode") not in ("200", 200):
        return {"ok": False, "error": data.get("message") or "快递100返回失败"}
    traces = [{"time": t.get("ftime", ""), "desc": t.get("context", "")}
              for t in (data.get("data") or [])]
    state = str(data.get("state", ""))
    return _pack(traces, state == "3", code)


def _pack(traces: list, signed: bool, code: str) -> dict:
    if not traces:
        return {"ok": True, "traces": [], "status": "待发货", "last_trace": "", "signed": False}
    head = traces[0]
    status = normalize_status(head["desc"], signed)
    return {"ok": True, "traces": traces, "status": status,
            "last_trace": f'{head["time"]} {head["desc"]}'.strip(), "signed": signed}


# ---------- mock 通道 ----------

MOCK_NODES = {
    "顺丰": ["快件已揽收", "快件已到达【{o}集散点】", "快件已发往【{d}】",
             "快件已到达【{d}转运中心】", "快件正在派送途中，请保持电话畅通"],
    "京东": ["您的订单已出库", "货物已到达【{o}分拣中心】", "货物已发往【{d}】",
             "货物已到达【{d}分拨中心】", "配送员正在为您派件"],
}


def _mock(tracking_no: str, cand: tuple, phone_tail: str) -> dict:
    name, code = cand
    seed = sum(ord(c) for c in tracking_no)
    rnd = random.Random(seed)
    origin, dest = "深圳", "杭州"
    nodes = MOCK_NODES.get(name, MOCK_NODES["顺丰"])
    # 按单号哈希分布状态，保证同一单号结果稳定
    bucket = seed % 10
    if bucket < 3:
        depth = 5  # 派送中
    elif bucket < 5:
        depth = 4  # 到达本市
    elif bucket < 8:
        depth = 2  # 在途
    else:
        depth = 6  # 已签收
    depth = min(depth, len(nodes))
    now = time.time()
    traces = []
    for i in range(depth):
        ts = now - (depth - i) * rnd.randint(4, 14) * 3600
        desc = nodes[i].format(o=origin, d=dest)
        if i == depth - 1 and depth == 6:
            desc = "快件已签收，签收人：本人"
        traces.append({"time": time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)), "desc": desc})
    traces.reverse()
    head = traces[0]
    status = normalize_status(head["desc"], depth == 6)
    return {"ok": True, "traces": traces, "status": status,
            "last_trace": f'{head["time"]} {head["desc"]}', "signed": depth == 6,
            "carrier": name, "_mock": True,
            "warning": "这是演示用的假轨迹，不是真实物流。台账不得据此改物流状态。"}
