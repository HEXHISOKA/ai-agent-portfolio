"""单据解析服务 —— OCR / 复核 / 校验 / 入库。

责任分工（这一点在面试里比代码本身更重要）：
    OCR 留在后端：确定性、可复现（同一张图连跑三次结果逐字节相同）、
        可存档、可做回归测试。这是把它放后端的理由，不是"省 token"。
    模型抽取在后端（2026-10-25 从浏览器搬来）：搬家的理由是**可归因**，不是性能。
        抽取在浏览器时，服务端只收到一个 JSON，分不清它是"模型读图得到的"还是
        "人手工写的"——一份算术自洽的伪造数据同样会被判为 validate_ok=true 并入库。
        搬到后端后，extract 只能由服务端自己调模型产生，来源可证。顺带两件事：
        ① 提示词不再发到浏览器；② 模型 id 由服务端固定（前端可选 33 个模型，
        实测其中 2 个会被图上文字劫持出错误的供应商名）。
    校验与入库在后端：这一层不能交给浏览器——否则 `validate_ok` 就只是客户端
        提交的一个布尔值，前端随手就能改成 true。现在由服务端复算校验、服务端
        写库，前端那份只用于显示。
    看图复核在后端：三层校验查不了"字段与照片是否一致"（供应商名读错就是这种情况，
        字形近、格式合法、不参与勾稽）。复核提示词与仲裁规则留在服务端，客户端拿不到。

数据流（2026-10-26 抽取迁移为图片直读后）：
    正常单据  图片直读抽取 → 服务端校验（出处层以 OCR 文本为参照，零 token）
              → 入库（不调用模型复核，零额外成本）
    有疑点    图片直读抽取 → 校验发现疑点 → 原图+OCR 文本发给模型复核
              → 页面标黄"图上似为 X，请确认" → 人点一下 → 入库

路由：
    GET  /             单页界面（从 web/index.html 读取）
    GET  /api/config   下发云服务 publicConfig（前端据此初始化 SDK）
    GET  /api/health   健康检查
    GET  /api/eval     评测摘要（读 eval/report.json，页面不手抄数字）
    GET  /api/models   可选抽取模型清单（平台目录 ∩ 支持图片；附实测标注）
    POST /api/ocr      接收图片，返回识别文本 + 逐词坐标 + 版面重建结果 + doc_id
    POST /api/extract  按 doc_id 取服务端缓存的原图，由服务端调视觉模型直读抽取。
                       只收 doc_id —— 抽取来源可证。OCR 文本不再进抽取 prompt，
                       只作为出处层参照与复核证据（本地 RapidOCR，零 token）。
                       可带 model 指定抽取模型；只接受平台目录里声明能读图的模型，
                       不认的 id 直接 400 并回可选清单。不带则用 EXTRACT_MODEL。
    POST /api/submit   commit=false 只校验并返回疑点；commit=true 校验后写库
    POST /api/review   对指定 doc_id 的原图做看图复核，返回逐字段核对结果
    POST /api/apply    人工选定方向后，由模型把方向执行成一组具体字段改动（patch），
                       再经服务端闸门核对；只返回结果，不写库（写库仍走 /api/submit）
    POST /api/correct  对已入库记录追加更正（红字冲销：原记录不动，更正进独立哈希链；
                       支持批量 changes，表格模式一次提交多个格）
    GET  /api/corrections?corrects_id=N   某条记录的全部更正记录
    GET  /api/profile  编号格式档案现状（来源 / 样例 / 画像结构）
    POST /api/profile/examples  实施方直接设定标准编号样例（工厂级，最高优先级证据）
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import http.server
import json
import os
import re
import sys
import time
import traceback
import urllib.parse
import uuid
from collections import OrderedDict
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR / "src"))

from apply import apply_patches, explain as explain_patches, run_apply, verify_patches  # noqa: E402
from cloud import CloudClient, CloudError  # noqa: E402
from contentrisk import detect as detect_content_risk  # noqa: E402
from grounding import check as check_grounding, summary as grounding_summary  # noqa: E402
from guard import detect  # noqa: E402
from layout import rebuild, to_markdown_table  # noqa: E402
from ocr import backend_name, run_ocr_array  # noqa: E402
from preprocess import decode_image, suppress_red  # noqa: E402
from prompt import build_messages_direct  # noqa: E402
from review import encode_for_vision, run_review  # noqa: E402
import patterns  # noqa: E402
import extract_models  # noqa: E402
import triage as triage_mod  # noqa: E402
from validate import is_ok, review_targets, validate as validate_extract  # noqa: E402

MAX_BYTES = 12 * 1024 * 1024

# ---------------------------------------------------------------------------
# 云服务公开配置
#
# 这四个值来自 workbuddy_cloud_service 的 activate 返回（publicConfig），是唯一
# 允许下发到前端的凭据。publishableKey 只表明"这是哪个应用"，本身不带权限，
# 服务端仍强制 Origin 精确匹配，所以它可以出现在前端代码里（它最终一定会出现在
# 浏览器里），但不应写进日志。
#
# endpoint 之所以在服务端持有而不是写死在前端：前端一旦把域名写死，应用换域名
# 或换环境就必然静默失效；由服务端统一提供，改一处即可。
# 注意：应用发布域名变更后，这里的 endpoint 必须同步更新，否则前端初始化会
# 因 Origin 不匹配而失败（表现为模型调用报 auth_ 前缀的错误）。
# ---------------------------------------------------------------------------
PUBLIC_CONFIG = {
    "resourceId": "wbcs_om13gymsKUOt5qewwIVMMf",
    "endpoint": "https://doc-ocr-probe.app.workbuddy.host",
    "oauthRelayBaseUrl": "https://www.workbuddy.cn/v2/as/genie-baas/oauth",
    "publishableKey": "wbpk_Kwk1z9NDBdtWoFAmazMpMO_ftdeVjutejpktDQRfMCSL4iiwBjKBpZb",
}

WEB_DIR = BASE_DIR / "web"
FALLBACK_PAGE = (
    "<!doctype html><meta charset='utf-8'>"
    "<h1>页面文件缺失</h1><p>web/index.html 未随项目一同部署。</p>"
)

# ---------------------------------------------------------------------------
# 服务端持有的云通道客户端
#
# 为什么服务端也需要它：校验要由服务端复算、入库要由服务端执行、复核要由服务端
# 发图，这三件事都必须有一个"客户端改不了"的执行者。publishableKey 本身是公开
# 标识（它一定会出现在浏览器里），真正的约束在平台侧（Origin 白名单与配额）。
# ---------------------------------------------------------------------------
CLOUD = CloudClient(PUBLIC_CONFIG["endpoint"], PUBLIC_CONFIG["publishableKey"])
# 抽取模型：默认使用 EXTRACT_MODEL，页面可从"平台目录里声明支持图片"的模型中选择。
#
# 2026-10-26 之前是**写死一个模型、不接受客户端指定**。当时的理由现在仍然成立
# 一半：前端曾把 33 个模型列成下拉框，其中 glm-5v-turbo / hunyuan-chat 会被图上
# 文字劫持，把供应商改成清单外的公司名。但结论不该是"永久禁止选择"——
# 真正挡住那次改写的是出处层（L4）：改出来的公司名在原文里找不到出处。
# 控制点在下游，就有条件把选择权还回去。约束见 src/extract_models.py：
# 只认平台声明能读图的模型、prompt 仍在服务端、model_id 由服务端记录。
EXTRACT_MODEL = os.environ.get("EXTRACT_MODEL", "deepseek-v4.1-flash")
# 复核模型**不开放选择**：复核是判据，不是被测对象。同一个模型既抽取又复核，
# 等于只有一条证据链——两个"独立来源"变成同一个来源，交叉验证就没了。
REVIEW_MODEL = os.environ.get("REVIEW_MODEL", "deepseek-v4.1-flash")
TABLE = "doc_parses"

# 上传的原图只在内存里短暂保留，够复核那一次调用用即可。
# 为什么不落磁盘：单据可能含真实客户信息，进程退出就应当什么都不剩。
#
# 为什么要按会话隔离（原先是全局一个 FIFO，DOC_MAX=6）：
#   所有人共用一个队列 ⇒ 第 7 个人上传，第 1 个人的图被挤掉，他点「看图复核」
#   得到「原图已失效」。单用户演示发现不了，两个人同时用就出事。
#   现在每会话独立 FIFO + 全局上限兜底，一个人连传 20 张也挤不掉别人的。
#
# 为什么还要一个 doc_id → 会话 的全局索引：
#   cookie 在 iframe 里可能被浏览器按第三方 cookie 拦掉（平台若用 iframe 嵌页面
#   就会这样）。那种情况下 cookie 拿不到、会话每次都是新的，doc_id 就永远找不回。
#   所以查找顺序是「本会话 → 全局索引」：隔离用于淘汰、不用于鉴权。
#   doc_id 是 128 bit 随机值，知道它本身就是能力，跨会话查到不等于越权。
DOC_TTL = 15 * 60
DOC_MAX = 8            # 每个会话同时保留几张
DOC_MAX_TOTAL = 64     # 全局上限，防止无人清理时内存无限增长
SID_COOKIE = "wb_sid"
_SESS: "OrderedDict[str, OrderedDict[str, dict]]" = OrderedDict()
_ALL: "OrderedDict[str, str]" = OrderedDict()   # doc_id -> sid，按插入顺序


def _doc_at(doc_id: str) -> float:
    sid = _ALL.get(doc_id)
    if not sid:
        return 0.0
    return (_SESS.get(sid, {}).get(doc_id) or {}).get("at") or 0.0


def _drop_doc(doc_id: str) -> None:
    sid = _ALL.pop(doc_id, None)
    if sid and sid in _SESS:
        _SESS[sid].pop(doc_id, None)
        if not _SESS[sid]:
            _SESS.pop(sid, None)

# 历史供应商名用于"疑似形近字错误"比对，60 秒缓存足够，避免每张单都打数据库
_SUPPLIERS: dict = {"at": 0.0, "names": []}
_SUPPLIER_TTL = 60.0


def _model_json(raw: str) -> dict | None:
    """把模型返回收成一个 dict。json_mode 不保证不裹 ```，仍要容错。"""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    for cand in (s, s[s.find("{"):s.rfind("}") + 1] if "{" in s and "}" in s else ""):
        if not cand:
            continue
        try:
            v = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(v, dict):
            return v
    return None


def load_page() -> bytes:
    """每次请求现读，改前端不必重启服务。"""
    p = WEB_DIR / "index.html"
    if p.exists():
        return p.read_bytes()
    return FALLBACK_PAGE.encode("utf-8")


def _load_eval_summary() -> dict:
    """把评测摘要交给前端，避免在页面里手抄评测数字。

    2026-10-26 起优先读 `report_live.json`：那是打线上真实链路（/api/ocr →
    /api/extract → /api/submit → 存疑 /api/review）跑出来的，与页面正在服务的
    这一份代码是同一个版本。`report.json` 是旧脚本在本地跑的结果，其中的 A 列
    还是"纯 OCR 文本抽取"——那个方案已经不存在了，留着只作历史对照，
    不能再拿它当页面的评测结论。
    """
    p = None
    for name in ("report_live.json", "report.json"):
        cand = BASE_DIR / "eval" / name
        if cand.exists():
            p = cand
            break
    if p is None:
        return {"available": False, "error": "评测报告未随项目部署（eval/report_live.json 缺失）"}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    out = {
        "available": True,
        "model": d.get("model"),
        "n": d.get("n"),
        "summary": d.get("summary") or {},
        "fallback": d.get("fallback_stats") or {},
        "mode": d.get("mode") or "offline",
        "note": d.get("note") or "",
        "generated_at": d.get("generated_at")
        or time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
    }
    # 难例集（退化字体/低质扫描）的报告一并下发：页面上"正常字体"与"退化字体"
    # 两个场景的对比都必须来自实测文件，不能在 HTML 里手抄数字。
    hard = BASE_DIR / "eval" / "report_live_simsun_abc.json"
    if hard.exists():
        try:
            hd = json.loads(hard.read_text(encoding="utf-8"))
            out["hard"] = {
                "label": "退化字体 / 低质扫描（编号宋体 20px，O 与 0 同字形）",
                "n": hd.get("n"),
                "summary": hd.get("summary") or {},
                "fallback": hd.get("fallback_stats") or {},
                "generated_at": hd.get("generated_at"),
            }
        except (OSError, json.JSONDecodeError):
            pass  # 难例集报告坏了不影响主报告的展示
    return out


def cache_doc(blob: bytes, filename: str, text: str, content_risk: dict, meta: dict,
              sid: str = "") -> str:
    """把原图（压成适合模型看的 JPEG）与 OCR 结果暂存在内存，返回 doc_id。

    复核发生在抽取之后，那时请求已经结束；用 doc_id 找回原图，避免让浏览器把
    同一张图再传一遍（也避免图片在客户端与服务端之间来回搬）。
    """
    now = time.time()
    for k in [k for k in list(_ALL) if now - _doc_at(k) > DOC_TTL]:
        _drop_doc(k)
    try:
        image_b64 = encode_for_vision(blob)
    except Exception:  # noqa: BLE001 - 压图失败不该让整条链路失败，退回不带图
        image_b64 = ""
    doc_id = uuid.uuid4().hex
    _SESS.setdefault(sid, OrderedDict())[doc_id] = {
        "at": now,
        "sid": sid,
        "filename": filename,
        "image_b64": image_b64,
        "text": text,
        "content_risk": content_risk or {},
        "words": meta.get("words"),
        "ocr_ms": meta.get("ocr_ms"),
        "skew_deg": meta.get("skew_deg"),
    }
    _ALL[doc_id] = sid
    # 先淘汰本会话最老的，再按全局上限淘汰全局最老的
    while len(_SESS[sid]) > DOC_MAX:
        _drop_doc(next(iter(_SESS[sid])))
    while len(_ALL) > DOC_MAX_TOTAL:
        _drop_doc(next(iter(_ALL)))
    return doc_id


def get_doc(doc_id: str, sid: str = "") -> dict | None:
    """按 doc_id 取回落暂存的原图。

    先查本会话，查不到再查全局索引（cookie 被拦时的兜底）。
    doc_id 猜不到，所以跨会话命中不是越权，只是让 iframe 场景能正常工作。
    """
    doc_id = doc_id or ""
    bucket = _SESS.get(sid) if sid else None
    v = bucket.get(doc_id) if bucket else None
    if v is None:
        owner = _ALL.get(doc_id)
        v = _SESS.get(owner, {}).get(doc_id) if owner else None
    if not v:
        return None
    if time.time() - v["at"] > DOC_TTL:
        _drop_doc(doc_id)
        return None
    _ALL.move_to_end(doc_id)
    return v


def supplier_history() -> list[str]:
    """取库内出现过的供应商名，用于形近字比对。失败返回空表——比对是增强项，
    不能因为读不到历史就把入库整条堵死。"""
    now = time.time()
    if now - _SUPPLIERS["at"] < _SUPPLIER_TTL:
        return _SUPPLIERS["names"]
    try:
        rows = CLOUD.rest_select(TABLE, "select=supplier&supplier=not.is.null&limit=500")
        names = sorted({r.get("supplier") for r in rows if r.get("supplier")})
    except (CloudError, json.JSONDecodeError):
        names = _SUPPLIERS["names"]
    _SUPPLIERS.update({"at": now, "names": names})
    return names


_PROFILE = {"at": 0.0, "by_supplier": {}, "global": patterns.EMPTY}
_PROFILE_TTL = 600


_SUPPLIER_PREFIX = "supplier:"


def _bucket_of(factory_key) -> str:
    """`doc_no_profiles.factory_key` → 画像桶键（空串表示全局兜底）。

    库里的键写成 `supplier:<归一化供应商名>`，之所以带前缀，是为了让老的单行
    `default` 仍然有意义（降级为兜底桶），同时人能一眼看出这行属于哪个客户。
    取画像时要剥掉前缀，才能和 `patterns.learn` 按 `normalize_name` 建的桶对齐。
    """
    key = str(factory_key or "default")
    if key == "default":
        return ""
    return key[len(_SUPPLIER_PREFIX):] if key.startswith(_SUPPLIER_PREFIX) else key


def doc_profiles():
    """各客户的单据编号格式画像（见 patterns.py）。

    来源优先级：配置文件（环境变量 DOC_RULES）> 档案认定 > 历史统计 > 空画像。
    空画像是**安全默认值**：校验层据此只说"无法判断格式"，而不会拿别家客户的
    规律去套这张单——宁可少一条提示，也不要多一条误报。

    分桶维度是**供应商**，不是"全厂一份"。原因见 _profile_rows 的注释。
    合并规则有讲究（2026-10-26 用户实测后确定的）：
      · 权威档案（实施方样例 / 人确认过）→ 强制覆盖同桶，一条就够；
      · 非权威档案（系统自己放行时沉淀的 submit 记录）→ 只补历史没有的空桶。
        否则一条 submit 样例会把几十条历史统计顶掉，等于让系统自己给自己发证。
    """
    now = time.time()
    if now - _PROFILE["at"] < _PROFILE_TTL:
        return _PROFILE["by_supplier"], _PROFILE["global"]

    conf = patterns.from_config(os.environ.get("DOC_RULES"))
    if conf is not None:
        _PROFILE.update({"at": now, "by_supplier": {}, "global": conf})
        return {}, conf

    # ① 档案（认定）：一行一个客户桶
    arch_by: dict[str, patterns.DocProfile] = {}
    arch_global = patterns.EMPTY
    for r in _profile_rows():
        vals = r.get("examples")
        vals = [str(x) for x in vals if str(x).strip()] if isinstance(vals, list) else []
        if not vals:
            continue
        prof = patterns.from_examples(vals, str(r.get("source") or "confirmed"),
                                      str(r.get("confirmed_by") or ""))
        if prof is None or prof.empty:
            continue
        bucket = _bucket_of(r.get("factory_key"))
        if bucket:
            arch_by[bucket] = prof
        else:
            arch_global = prof

    # ② 历史统计（按供应商分桶；读不到不算错误，画像是增强项）
    try:
        rows = CLOUD.rest_select(TABLE, "select=doc_no,supplier&doc_no=not.is.null&limit=1000")
        hist_by, hist_global = patterns.learn(
            [(r.get("doc_no"), r.get("supplier")) for r in rows])
    except (CloudError, json.JSONDecodeError):
        hist_by, hist_global = {}, patterns.EMPTY

    # ③ 合并：权威档案覆盖，非权威只补空
    by_supplier = dict(hist_by)
    for bucket, prof in arch_by.items():
        if prof.authoritative or bucket not in by_supplier:
            by_supplier[bucket] = prof
    if arch_global.empty:
        glob = hist_global
    elif arch_global.authoritative or hist_global.empty:
        glob = arch_global
    else:
        glob = hist_global

    _PROFILE.update({"at": now, "by_supplier": by_supplier, "global": glob})
    return by_supplier, glob


def validate_with_profile(extract: dict) -> list[dict]:
    """按这张单的供应商挑画像再校验。画像缺失时自动退化成通用层。"""
    by_supplier, global_profile = doc_profiles()
    prof = patterns.pick(by_supplier, global_profile, (extract or {}).get("supplier"))
    return validate_extract(extract, profile=prof)


PROFILE_TABLE = "doc_no_profiles"
CORRECTIONS_TABLE = "corrections"
# 与库层 CHECK 约束同款白名单：更正只允许落在这四个业务字段上。
# item_count 是明细行的派生值（len(items)），单独更正没有意义。
CORRECTABLE_FIELDS = ("doc_no", "supplier", "order_date", "total_amount")

# 明细更正：命名与入库前表格编辑/校验层完全一致（items[0].qty，0 基）。
# 白名单三处同源：DB CHECK 约束、这里、前端渲染——谁也不比谁多。
_ITEM_FIELD_RE = re.compile(r"^items\[(\d+)\]\.(name|qty|price|amount)$")
_ITEM_NUMERIC = {"qty", "price", "amount"}
MAX_CHANGES = 16  # 单次批量上限：整单表头 4 格 + 明细若干格，够用且防滥用

# 有效版本视图：业务侧"现在值是多少"的唯一定义点。更正的 old_value 从这里读，
# 保证同字段多次更正时 old→new 链条连贯（原表读会把旧值读重）。
EFFECTIVE_VIEW = "v_doc_parses_effective"
_DATE_RE = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}$")


def _profile_rows() -> list[dict]:
    """读**全部**编号档案行（`doc_no_profiles` 一行一个桶）。失败返回空。

    原来是"只读 factory_key=default 那一行"，等于承认了"全厂一份档案"。
    编号格式是客户级知识（同一集团一套、换客户一套），全厂一份会出现两种错：
      · 别家真是 `P0` 开头，本家档案的 `PO` 会把人家的真编号"纠正"错；
      · 一家的样例混进另一家的统计，前缀出现次数互相污染。
    表上 `factory_key` 本来就有唯一索引，天然支持"一桶一行"，无需改表结构。
    """
    try:
        return CLOUD.rest_select(
            PROFILE_TABLE,
            "select=factory_key,examples,source,confirmed_by,confirmed_at,updated_at"
            "&order=updated_at.desc&limit=200")
    except (CloudError, json.JSONDecodeError):
        return []


def _profile_examples(key: str | None = None) -> list[dict]:
    """读指定桶的档案行；不传则读全局兜底桶（default）。"""
    want = key or "default"
    return [r for r in _profile_rows()
            if str(r.get("factory_key") or "default") == want]


def profile_summary(supplier=None) -> dict:
    """档案现状（GET /api/profile?supplier=…）。给维护界面看：证据来源、样例清单、画像结构。

    分桶之后必须能回答两个问题，缺一个界面就会误导人：
      ① **这张单**用的是哪份档案（本供应商桶 / 全局兜底）——否则用户会拿全局
         样例去核对别家的单，误以为"我存的它没记住"；
      ② 库里到底有几份档案、分别属于谁 —— 否则"换客户后格式互相覆盖"这种
         事故在界面上完全看不见。
    """
    # 展示层强制读库：页面是"人去看档案现状"的地方，它必须与库里一致。
    # 校验路径保留 600 秒缓存（性能），但界面若跟着缓存走，人刚改完样例、
    # 刷新一看还是旧值，只会得出"保存没生效"的结论——这是纯误导。
    _PROFILE["at"] = 0.0
    by_supplier, glob = doc_profiles()
    key = patterns.normalize_name(supplier)
    prof = patterns.pick(by_supplier, glob, supplier)
    want = patterns.supplier_key(supplier)
    rows = _profile_rows()
    row = next((r for r in rows if str(r.get("factory_key") or "default") == want), {})

    def _vals(r) -> list[str]:
        v = r.get("examples")
        return [str(x) for x in v if str(x).strip()] if isinstance(v, list) else []

    manual = _vals(row)
    if os.environ.get("DOC_RULES"):
        src = "config"
    elif manual:
        src = "profiles"
    else:
        src = getattr(prof, "source", "") or "history"

    # 样本口径必须写明，否则"我存了好几条它还让我核对"会被当成系统没记住。
    # 口径：按**不同编号**计。现实里单据编号不会重复，同一编号进库多次只可能是
    # 同一张单被重传/重测——它不增加对"格式"的认知，按 1 条计才诚实。
    repeat = 0
    distinct_seen = 0
    try:
        hist = CLOUD.rest_select(TABLE, "select=doc_no&doc_no=not.is.null&limit=1000")
        counter: dict[str, int] = {}
        for r in hist:
            k = str(r.get("doc_no") or "").strip()
            if k:
                counter[k] = counter.get(k, 0) + 1
        distinct_seen = len(counter)
        repeat = sum(counter.values()) - distinct_seen
    except (CloudError, json.JSONDecodeError):
        pass

    buckets = []
    for r in rows:
        b = _bucket_of(r.get("factory_key"))
        vals = _vals(r)
        if not vals and not b:
            continue
        buckets.append({"key": b or "default",
                        "label": b or "全局兜底（所有客户）",
                        "examples": vals, "source": r.get("source"),
                        "confirmed_by": r.get("confirmed_by"),
                        "updated_at": r.get("updated_at")})
    return {"ok": True, "source": src, "examples": manual,
            "bucket": key or "default",
            "bucket_label": ("供应商：" + key) if key else "全局兜底（所有客户）",
            "buckets": buckets,
            "distinct": len(manual), "ledger_distinct": distinct_seen,
            "repeat_entries": repeat,
            "confirmed_by": row.get("confirmed_by"),
            "confirmed_at": row.get("confirmed_at"),
            "authoritative": bool(getattr(prof, "authoritative", False)),
            "describe": (prof.describe() if prof is not None and not prof.empty else "暂无可用画像"),
            "profile": (prof.to_dict() if prof is not None and not prof.empty else None)}


def _profile_record_confirmed(doc_no, by: str = "submit", supplier=None) -> None:
    """人工确认入库的编号沉淀进**该客户**的编号档案——"只问一次，之后记住"的沉淀侧。

    与画像学习的关系：doc_profiles() 本来就会定期从库内历史重学；这里的价值是
    ① 把"人确认过的写法"显式留痕（source/confirmed_by/confirmed_at 可查可反证），
    ② 档案证据不依赖学习窗口和样本量阈值。重复写法不重复记。全程失败静默：
    档案是增强项，绝不能影响入库主流程。

    分桶键是**供应商**（`supplier:<归一化名>`），不再是全厂一份。否则两家的
    编号规律互相覆盖：本家 `PO`+11 位会把别家真的 `P0` 开头"纠正"成自己的写法。

    `by` 区分两种"确认"，这个区分不能省：
      · "human"  —— 人在处置面板上明确选过这个写法（PO 还是 P0）。
        这是**显式认定**，档案里必须能查到是谁定的。
      · "submit" —— 整单没有任何需要人定的疑点，编号是系统自己的结论。
        它可信（整单四道闸门都过了），但不能和"人签过字"混成同一条证据：
        出了问题要能分清"那是人选的"还是"系统自己放行的"。
        正因如此，submit 沉淀的桶在 patterns 里 `authoritative=False`：
        提供格式样例，但**不享受"一条即可归一"的免门槛待遇**——否则系统
        归一错一次就把它写进档案，下次更确信，错得越来越稳。
    """
    no = "" if doc_no is None else str(doc_no).strip()
    if not no or len(no) > 60:
        return
    key = patterns.supplier_key(supplier)
    # 桶键里带供应商中文名，必须编码后再进 query string。
    # （urllib 在发送时按 ascii 编码 URL，未编码的中文会直接抛 UnicodeEncodeError，
    #  这个异常不在下面的 except 里，会把入库后半段整条打断——所以必须编码。）
    q_key = urllib.parse.quote(key, safe="")
    try:
        rows = CLOUD.rest_select(PROFILE_TABLE,
                                 f"select=id,examples,source,confirmed_by"
                                 f"&factory_key=eq.{q_key}&limit=1")
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if rows:
            row = rows[0]
            examples = row.get("examples") if isinstance(row.get("examples"), list) else []
            examples = [str(x) for x in examples if str(x).strip()]
            if no in examples:
                # 同一写法又出现了一次 —— **不重复追加**。
                # 现实中单据编号不会重复，同一编号进库多次只可能是同一张单被重传/
                # 重测，它不增加"格式认知"，按 1 条计才是诚实的口径。
                # 但有一件事必须补：如果这次是**人**在处置面板上定的这个写法，
                # 而档案里记的还是之前"系统自己放行"（submit），证据等级要升级。
                # 原来的写法在这里直接 return，人的认定会被永久挡在门外。
                if by == "human" and row.get("confirmed_by") != "human":
                    CLOUD.rest_update(PROFILE_TABLE, {
                        "source": "confirmed", "confirmed_by": "human",
                        "confirmed_at": now_iso, "updated_at": now_iso,
                    }, query=f"id=eq.{row.get('id')}")
                return
            # 系统放行的编号若与该客户已知格式不符，**不要塞进这个桶**：
            # 那多半是另一套编号（另一个客户、另一类单据），塞进来会让
            # 该客户的前缀/位数统计多出第二种形状，之后每张单都判"格式不符"。
            # 人明确选了就是另一回事——人的认定优先于系统的格式判断。
            if by != "human" and examples:
                cur = patterns.from_examples(examples, str(row.get("source") or "confirmed"),
                                             str(row.get("confirmed_by") or ""))
                if cur is not None and not cur.empty and not cur.accepts(no):
                    return
            examples = (examples + [no])[-50:]  # 上限 50 条防膨胀
            patch = {"examples": examples, "updated_at": now_iso}
            if by == "human":
                # 人确认过的写法要盖住之前"系统自己放行"留下的痕迹：
                # 同一份档案里，人工认定的效力高于自动沉淀。
                patch.update({"source": "confirmed", "confirmed_by": "human",
                              "confirmed_at": now_iso})
            CLOUD.rest_update(PROFILE_TABLE, patch, query=f"id=eq.{row.get('id')}")
        else:
            CLOUD.rest_insert(PROFILE_TABLE, {
                "factory_key": key, "examples": [no],
                "source": "confirmed", "confirmed_by": by,
                "confirmed_at": now_iso})
    except (CloudError, json.JSONDecodeError):
        pass


def handle_profile_examples(payload: dict) -> dict:
    """POST /api/profile/examples：实施方直接设定**指定客户**的标准编号样例。

    覆盖式更新——给的是"标准写法清单"，不是往历史里掺样本；覆盖式是刻意的：
    实施方给清单这个动作本身就意味着"以这份为准"。写完立刻作废画像缓存，
    下一次校验马上用新档案。

    `supplier` 决定写哪个桶（不传 = 全局兜底 default）。不按供应商分桶就会出现
    "给甲客户配的规则把乙客户的单判成格式不符"，而界面上还看不出是哪里来的。
    """
    raw = payload.get("examples")
    if not isinstance(raw, list) or not raw:
        return {"ok": False, "error": "examples 必须是非空数组"}
    vals: list[str] = []
    for x in raw[:20]:
        t = str(x).strip()
        if not (4 <= len(t) <= 60):
            return {"ok": False, "error": f"样例「{t[:20]}」长度须在 4-60 个字符之间"}
        vals.append(t)
    by = str(payload.get("by") or "").strip()[:60] or "实施方"
    supplier = payload.get("supplier")
    key = patterns.supplier_key(supplier)
    q_key = urllib.parse.quote(key, safe="")
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        rows = CLOUD.rest_select(PROFILE_TABLE,
                                 f"select=id&factory_key=eq.{q_key}&limit=1")
        if rows:
            CLOUD.rest_update(PROFILE_TABLE,
                              {"examples": vals, "source": "manual", "confirmed_by": by,
                               "confirmed_at": now_iso, "updated_at": now_iso},
                              query=f"id=eq.{rows[0].get('id')}")
        else:
            CLOUD.rest_insert(PROFILE_TABLE,
                              {"factory_key": key, "examples": vals, "source": "manual",
                               "confirmed_by": by, "confirmed_at": now_iso})
    except CloudError as exc:
        return {"ok": False, "upstream": True, "error": f"写档案失败：{exc}"}
    _PROFILE["at"] = 0.0
    return {"ok": True, "examples": vals, "bucket": _bucket_of(key) or "default",
            "summary": profile_summary(supplier)}


def _validate_change(field: str, new_value) -> tuple:
    """校验单个更正项。返回 (规范化后的新值, 错误信息)；合法时错误信息为 None。

    与单字段版一致的约束，另补 order_date 格式检查：更正值会进有效版本视图
    被下游当日期用，垃圾格式进去比拒绝用户一次的代价高。
    表头字段与明细字段（items[i].key）分开校验：明细数值格与表头金额同规。
    """
    m = _ITEM_FIELD_RE.match(field)
    if not m and field not in CORRECTABLE_FIELDS:
        return None, (f"field 只允许 {'/'.join(CORRECTABLE_FIELDS)} 或 items[行号].name/qty/price/amount")
    if new_value is None or str(new_value).strip() == "":
        return None, "更正值不能为空（不支持用更正清空字段）"
    new_value = str(new_value).strip()
    if len(new_value) > 200:
        return None, "new_value 超长（上限 200）"
    if field == "total_amount":
        try:
            v = float(new_value)
        except ValueError:
            return None, "金额必须是数字"
        if v < 0 or v > 10 ** 12:
            return None, "金额超出合理范围"
    elif field == "order_date" and not _DATE_RE.match(new_value):
        return None, "日期格式应为 YYYY-MM-DD"
    elif m and m.group(2) in _ITEM_NUMERIC:
        try:
            v = float(new_value)
        except ValueError:
            return None, f"明细的 {m.group(2)} 必须是数字"
        if v < 0 or v > 10 ** 12:
            return None, f"明细的 {m.group(2)} 超出合理范围"
    return new_value, None


def handle_correct(payload: dict) -> dict:
    """POST /api/correct：对已入库记录追加更正（会计红字冲销）。

    支持批量：payload 带 changes=[{field,new_value},...]（表格模式一次改多个格），
    或兼容旧版单字段 field/new_value。批量时先全部校验再落库——要么整批合法，
    要么一条都不写，不会出现"改了一半"的中间态留在界面上。

    约束（每一条都有理由）：
    - 字段白名单 = 表头四字段 + 明细 items[i].name/qty/price/amount（0 基行号），
      与 DB CHECK 约束、前端渲染三处同源；item_count 是派生值，单独更正无意义；
    - old_value 由服务端从有效版本现读（表头查视图、明细从 extract_json 叠加
      已有更正）——不是原表，也不是客户端说了算；同格第二次更正的 old_value
      应是"上一次更正后的值"，否则更正链的 old→new 叙事就断了；
    - 与现值相同的项目直接跳过（不是错误）——表格模式天然会带上没改的格子；
    - 原记录 doc_parses 一字不动：UPDATE 会断主链哈希。这不是缺陷是设计，
      纠错 = 追加，两条链各自完整。
    """
    cid = payload.get("corrects_id")
    reason = str(payload.get("reason") or "").strip()
    by = str(payload.get("corrected_by") or "").strip()[:60] or "web"
    if isinstance(cid, bool) or not isinstance(cid, int):
        return {"ok": False, "error": "corrects_id 必须是整数"}
    if len(reason) > 500:
        return {"ok": False, "error": "reason 超长（上限 500）"}

    raw_changes = payload.get("changes")
    if raw_changes is None:
        raw_changes = [{"field": payload.get("field"), "new_value": payload.get("new_value")}]
    if not isinstance(raw_changes, list) or not raw_changes:
        return {"ok": False, "error": "changes 必须是非空数组"}
    if len(raw_changes) > MAX_CHANGES:
        return {"ok": False, "error": f"一次更正的项目数超过上限（{MAX_CHANGES}）"}

    seen: set = set()
    changes: list[tuple[str, str]] = []
    for ch in raw_changes:
        if not isinstance(ch, dict):
            return {"ok": False, "error": "changes 每项必须是 {field, new_value}"}
        field = str(ch.get("field") or "").strip()
        if field in seen:
            return {"ok": False, "error": f"字段 {field} 在一次更正里出现多次"}
        seen.add(field)
        val, err = _validate_change(field, ch.get("new_value"))
        if err:
            return {"ok": False, "error": f"{field}: {err}"}
        changes.append((field, val))

    # 旧值从有效版本读：表头字段查有效版本视图（视图已叠加历史更正）；
    # 明细字段视图管不到（值在 extract_json 里），在此单独叠加——
    # extract_json 原值按 id 序应用该记录已有的 items[i] 更正，得到的才是
    # 「当前有效值」。同格第二次更正的 old_value 必须是上一次改后的值，
    # 否则更正链的 old→new 叙事同样会断。
    try:
        rows = CLOUD.rest_select(
            EFFECTIVE_VIEW,
            "select=id,doc_no,supplier,order_date,total_amount,extract_json"
            f"&id=eq.{cid}&limit=1")
    except CloudError as exc:
        return {"ok": False, "upstream": True, "error": f"读取原记录失败：{exc}"}
    if not rows:
        return {"ok": False, "error": f"记录 id={cid} 不存在"}
    cur = rows[0]

    item_fields = [f for f, _v in changes if _ITEM_FIELD_RE.match(f)]
    eff_items = None
    if item_fields:
        try:
            ex = cur.get("extract_json")
            ex = json.loads(ex) if isinstance(ex, str) else ex
            items = ex.get("items") if isinstance(ex, dict) else None
            if isinstance(items, list):
                eff_items = [dict(it) if isinstance(it, dict) else {} for it in items]
        except (json.JSONDecodeError, TypeError):
            eff_items = None
        if eff_items is None:
            return {"ok": False, "error": "该记录没有可读的明细数据，无法更正明细"}
        # 叠加已有明细更正（按 id 升序，与链顺序一致）
        try:
            prior = CLOUD.rest_select(
                CORRECTIONS_TABLE,
                "select=field,new_value&corrects_id=eq."
                f"{cid}&field=like.items%5B*&order=id.asc")
        except CloudError as exc:
            return {"ok": False, "upstream": True, "error": f"读取已有更正失败：{exc}"}
        for p in prior:
            m = _ITEM_FIELD_RE.match(str(p.get("field") or ""))
            if not m:
                continue
            idx, key = int(m.group(1)), m.group(2)
            if 0 <= idx < len(eff_items):
                eff_items[idx][key] = p.get("new_value")

    def old_of(field: str):
        m = _ITEM_FIELD_RE.match(field)
        if not m:
            v = cur.get(field)
        else:
            idx, key = int(m.group(1)), m.group(2)
            if idx >= len(eff_items):
                return None, f"明细只有 {len(eff_items)} 行，没有第 {idx + 1} 行"
            v = eff_items[idx].get(key)
        return ("" if v is None else str(v)), None

    pending: list[tuple[str, str, str]] = []  # (field, old_s, new_value)
    for field, val in changes:
        old_s, err = old_of(field)
        if err:
            return {"ok": False, "error": err}
        if old_s == val:
            continue  # 没变的格子跳过，不是错误
        pending.append((field, old_s, val))
    if not pending:
        return {"ok": False, "error": "没有实际变更：提交的值与现值全部相同，无需更正"}

    applied: list[dict] = []
    for field, old_s, val in pending:
        try:
            inserted = CLOUD.rest_insert(CORRECTIONS_TABLE, {
                "corrects_id": cid, "field": field,
                "old_value": old_s, "new_value": val,
                "reason": reason[:500], "corrected_by": by})
        except CloudError as exc:
            # 中途失败如实上报：已落链的更正不回滚（链上记录不可撤销），
            # 未写的不再继续。界面会展示"成功 N 条 / 失败原因"，用户重试剩余的。
            resp = {"ok": False, "upstream": True,
                    "error": f"写更正失败（已成功 {len(applied)} 条，剩余未写）：{exc}"}
            if applied:
                resp["partial"] = True
                resp["corrections"] = applied
            return resp
        rec = inserted[0] if isinstance(inserted, list) and inserted else {}
        applied.append(rec)
    return {"ok": True, "corrections": applied,
            "correction": applied[0] if applied else None}


def corrections_for(cid) -> dict:
    """某条入库记录的全部更正（GET /api/corrections?corrects_id=N）。"""
    if isinstance(cid, bool) or not isinstance(cid, int):
        return {"ok": False, "error": "corrects_id 必须是整数"}
    try:
        rows = CLOUD.rest_select(
            CORRECTIONS_TABLE,
            "select=id,seq,record_hash,field,old_value,new_value,reason,corrected_by,created_at"
            f"&corrects_id=eq.{cid}&order=id.asc")
    except (CloudError, json.JSONDecodeError):
        return {"ok": False, "upstream": True, "error": "读取更正记录失败"}
    return {"ok": True, "corrections": rows}


def extract_sig(extract: dict) -> str:
    """抽取结果的内容指纹。用来判断"这次要入库的，是不是刚才复核过的那一份"。

    它不是安全边界（客户端拿不到它也伪造不了复核结果），只是一个复用的依据：
    复核结果存在服务端 doc 缓存里，只有内容指纹一致才复用，内容一变就重跑。
    """
    try:
        raw = json.dumps(extract, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raw = repr(extract)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def validate_with_source(extract: dict, doc: dict | None) -> tuple[list[dict], list[dict]]:
    """服务端权威校验 = 规则层（L1~L3）+ 出处层（L4）。

    为什么出处层必须挂在这里、而不是只在前端显示：
        L1~L3 全部围绕数字，而注入的最优形态是「只改名字不动数字」——三行金额
        一字不改，勾稽照样平。出处层是唯一会去看「这个值在不在图上」的判据。
        它一旦只在浏览器里跑，攻击者跳过页面直接 POST 就能绕开。

    没有 doc（原图已失效）时只跑规则层，并在返回里明确标注出处层未执行——
        「没验」和「验过并通过」是两件事，不能让它俩看起来一样。
    """
    issues = validate_with_profile(extract)
    details: list[dict] = []
    # 编号被 L2 归一（见 validate 的 info 事件）时，出处校验对**归一前的原值**做：
    # 归一后的值（PO…）在原文里没有连续出处——原文写的是 P0…，拿归一值对原文
    # 会得到 ~0.917 的覆盖率、落 warn 档，把"写法被归一"误报成"值没有出处"，
    # 再把看图复核触发回来，归一就白做了。出处层要回答的问题始终是
    # "这个值是不是来自这张图"，而对编号来说图上的字面就是 OCR 原值；
    # 归一的依据（历史画像）已由 L2 info 事件记录，两者互不越界。
    g_extract = extract
    for _i in issues:
        if _i.get("field") == "doc_no" and isinstance(_i.get("normalized"), dict):
            g_extract = dict(extract)
            g_extract["doc_no"] = _i["normalized"]["from"]
            break
    if doc and (doc.get("text") or "").strip():
        g_issues, details = check_grounding(g_extract, doc["text"])
        issues = issues + g_issues

    # 内容风险层的高危命中也要进 issues，而且必须是**疑点**而不是只做展示。
    #
    # 起因：「单据上出现两个合计」原来只写进 doc.content_risk，用于触发一次看图复核，
    # 但 issues 里没有对应条目 → 前端看不到、分诊看不到 → 复核说"当前值一致"就
    # 整单放行了，而那张单上明明有两个互斥的总额。这正是用户定义的疑点
    # "同一性质的数据出现多个互相矛盾的取值"，它必须停在人面前。
    #
    # 只收 shadow（影子字段）的 high：numeric 类（1.200.00、千分位错位）是**写法**
    # 不规范，票面就这么印的，看图也只能读回同一个值，把它当疑点会让人被迫为
    # 排版问题反复点确认；它仍然照旧触发复核与展示，只是不阻断。
    if doc:
        cr = doc.get("content_risk") or {}
        for hit in (cr.get("shadow") or []):
            if not isinstance(hit, dict) or hit.get("level") != "high":
                continue
            # token 里带着这一组互斥的数值（`[77100.0] vs [88000.0]`）。把它们解析出来，
            # 处置面板才能给出"改用另一个总额"这个选项——**只报"有多个总额"却不给
            # 候选值，人还得自己回原文找那个数**，这正是"方向选择"要解决的问题。
            vals: list[float] = []
            for tok in re.findall(r"\d+(?:\.\d+)?", str(hit.get("token") or "")):
                try:
                    v = float(tok)
                except ValueError:
                    continue
                if v > 0 and v not in vals:
                    vals.append(v)
            iss = {
                "level": "warn", "layer": "L5", "field": "total_amount",
                "code": "multi_total",
                "value": g_extract.get("total_amount"),
                "msg": hit.get("desc") or "单据上出现多个总额",
            }
            if 1 < len(vals) <= 6:
                iss["multi_values"] = sorted(vals)
            issues = issues + [iss]

    # 选了"实测被单据文字改写过"的模型 → 整份结果按不可信处理，入库前必须人看一次。
    #
    # 为什么不一禁了之：禁止和"允许但必须人看"是两种态度。禁掉等于宣称这个模型
    # 不存在，而它确实存在（也便宜）；允许它参与比较、但要求人选完之后看一眼
    # 结果，是把风险摆在明处。这里给不出任何"该改成什么"的方向——整份结果的来源
    # 有前科，不是某个字段的矛盾——所以只有"认可/退回"两条路，不编候选值。
    if doc and (doc.get("extract_model_risk") or "") == "injection":
        issues = issues + [{
            "level": "warn", "layer": "L0", "field": "",
            # field 留空：它不是"某个字段的值不对"，而是整份结果的来源有问题。
            # 但界面上不能因此显示成空标签，所以单独给一个名字。
            "field_label": "抽取模型",
            "code": "untrusted_model",
            "value": doc.get("extract_model"),
            "msg": (f"本单由「{doc.get('extract_model')}」读出，该模型实测曾被单据上的"
                    f"文字改写（供应商被换成清单外的公司）。整份结果按不可信处理，"
                    f"需人工对照原图确认后才能入库。"),
        }]
    return issues, details


def _set_path(extract: dict, field: str, value) -> bool:
    """把一个人选定值写回 extract 的对应位置。返回是否真的改了。

    只认两种路径：顶层字段（doc_no/supplier/order_date/total_amount/currency）
    与明细行字段（items[3].price）。审核面板能产生的就这两种，多余的路径一律
    不认——放开任意路径等于给了一个可以通过审核面板改任意结构的口子。
    """
    m = re.fullmatch(r"items\[(\d+)\]\.(qty|price|amount|name)", field or "")
    if m:
        rows = extract.get("items")
        if not isinstance(rows, list):
            return False
        i = int(m.group(1))
        if i >= len(rows) or not isinstance(rows[i], dict):
            return False
        key = m.group(2)
        old = rows[i].get(key)
        rows[i][key] = _coerce(key, value)
        return str(old) != str(rows[i][key])
    if field in ("doc_no", "supplier", "order_date", "total_amount", "currency"):
        old = extract.get(field)
        extract[field] = _coerce(field, value)
        return str(old) != str(extract[field])
    return False


def _coerce(field: str, value):
    """把人在页面上填的字符串尽量还原成原来的类型。

    这一步不是洁癖：`total_amount` 从 77100 变成 "77100.00" 之后，下游按数值
    比较的地方（勾稽、报表）都得各自再转一次，而按字符串比较的地方会突然不相等。
    在入口一次转干净，后面就只有一个类型。
    """
    if value is None:
        return None
    if field == "qty":
        try:
            f = float(str(value).strip())
            return int(f) if f.is_integer() else f
        except (TypeError, ValueError):
            return value
    if field in ("total_amount", "price", "amount"):
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return value
    return value


def _with_values(issues: list[dict], extract: dict) -> list[dict]:
    """给每条疑点补上"当前值"。

    为什么这一步要单独做：validate.py 只产出"哪里有问题"，不产出"值是多少"——
    它刻意不认识完整 extract 的形状。但审核面板上你必须让人看到"当前值是 A、
    看图读到 B"，否则他得自己回表格里找哪个格。只在分诊这一步补，不写进库。
    """
    out = []
    for iss in issues or []:
        if not isinstance(iss, dict):
            continue
        f = iss.get("field") or ""
        m = re.fullmatch(r"items\[(\d+)\]\.(\w+)", f)
        v = None
        if m:
            rows = extract.get("items")
            i = int(m.group(1))
            if isinstance(rows, list) and i < len(rows) and isinstance(rows[i], dict):
                v = rows[i].get(m.group(2))
        elif f:
            v = extract.get(f)
        out.append({**iss, "value": v})
    return out


def _audit_choices(payload: dict) -> dict | None:
    """取客户端提交的人工处置。结构 {"choices":[{"field","how","value"}]}。

    `how` 的合法取值由分诊层定义（`triage.AUDIT_HOWS`），**不在这里手抄**。
    手抄过一次，代价是：后来新增的 `alt_total`（单据上另一个总额）和
    `homoglyph`（PO / P0 另一个写法）两个方向，人选完提交后被静默丢弃——
    页面显示"已选"，服务端却认为"没处置"，人只会觉得系统坏了，查不出原因。

    合法方向大致是：
        keep      保持当前值（人看过原件后认为当前值对）
        image     改为看图复核读到的值
        manual    改为人工填写的值
        reject    这张单不入库
        homoglyph 编号交界处换成另一种写法（PO ↔ P0）
        alt_total 改用单据上印着的另一个总额
        rule / recompute / profile / merge  按规则层算出的方向
    """
    a = payload.get("audit")
    if not isinstance(a, dict):
        return None
    raw = a.get("choices")
    if not isinstance(raw, list):
        return None
    allowed = triage_mod.AUDIT_HOWS
    out = {}
    for c in raw:
        if not isinstance(c, dict):
            continue
        f = c.get("field")
        how = c.get("how")
        if not isinstance(f, str) or how not in allowed:
            continue
        out[f] = {"field": f, "how": how, "value": c.get("value")}
    return out or None


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "docagent/0.3"

    def _session(self) -> str:
        """本次请求的会话 id。没有就现造一个，并在响应里种 cookie。

        为什么用 cookie 而不是别的：它天然跨请求、由浏览器自动带回，不需要前端
        改一行代码。但它只是**分桶的依据**，不是权限——见 get_doc 的说明。
        """
        if getattr(self, "_sid", ""):
            return self._sid
        sid = ""
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == SID_COOKIE and 16 <= len(v) <= 64:
                sid = v
                break
        if not sid:
            sid = uuid.uuid4().hex[:24]
            self._sid_new = True
        self._sid = sid
        return sid

    def _doc(self, payload: dict) -> dict | None:
        """按 doc_id 取原图，带上本会话（先本会话、后全局索引）。"""
        return get_doc(str(payload.get("doc_id") or ""), self._session())

    def _send(
        self,
        body: bytes,
        code: int = 200,
        ctype: str = "application/json; charset=utf-8",
        head_only: bool = False,
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "_sid_new", False):
            # HttpOnly：前端不需要读它，也就不该让它被脚本碰到
            self.send_header(
                "Set-Cookie",
                f"{SID_COOKIE}={self._sid}; Path=/; Max-Age=3600; SameSite=Lax; HttpOnly",
            )
            self._sid_new = False
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200, head_only: bool = False) -> None:
        self._send(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            code,
            head_only=head_only,
        )

    def do_GET(self) -> None:  # noqa: N802
        self._serve_get(HEAD_ONLY=False)

    def do_HEAD(self) -> None:  # noqa: N802
        # 链接预览、聊天工具缩略图、部分浏览器打开前会先发 HEAD。
        # 不实现的话默认回 501，探活方会判定"站点不可用"。
        self._serve_get(HEAD_ONLY=True)

    def _serve_get(self, HEAD_ONLY: bool) -> None:  # noqa: N803
        path = self.path.split("?")[0]
        # 打开页面的同时把会话 cookie 种下去。不这么做的话，浏览器第一次并发发两个
        # /api/ocr 时两个请求都没有 cookie，服务端会各造一个会话，于是同一用户的两张图
        # 落在两个桶里——上限就是 8+8 而不是 8。
        self._session()
        if path.endswith("/favicon.ico"):
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.endswith("/api/health"):
            self._json(
                {"ok": True, "backend": backend_name(), "python": sys.version.split()[0]},
                head_only=HEAD_ONLY,
            )
            return
        if path.endswith("/api/config"):
            # 不含任何服务端密钥；publishableKey 本就会被浏览器持有。
            # extractModel 只是"告诉页面服务端用哪个模型"用于展示，
            # 抽取请求不携带它 —— 带了也不会被采纳。
            self._json({"ok": True, **PUBLIC_CONFIG, "extractModel": EXTRACT_MODEL},
                       head_only=HEAD_ONLY)
            return
        if path.endswith("/api/eval"):
            # 评测数字必须由脚本从 report.json 读出来，不能在页面里手抄——
            # 手抄的数字改一次数据就对不上了，而"可复现"正是这个项目的卖点之一。
            self._json({"ok": True, **_load_eval_summary()}, head_only=HEAD_ONLY)
            return
        if path.endswith("/api/models"):
            # 可选抽取模型。能力以平台目录的 supportsImages 为准，实测标注来自
            # src/model_measured.json（由注入扫描结果生成，不手抄）。
            # 目录读不到时 degraded=True 且只留默认模型——不放行任意 id。
            cat = extract_models.catalog(CLOUD, EXTRACT_MODEL)
            self._json({
                "ok": True,
                "default": EXTRACT_MODEL,
                "degraded": bool(cat.get("degraded")),
                "error": cat.get("error"),
                "models": cat["models"],
                "note": ("只有平台目录里声明支持图片输入的模型能读单据图；"
                         "复核模型固定，不在此列。"),
            }, head_only=HEAD_ONLY)
            return
        if path.endswith("/api/profile"):
            # 画像按供应商分桶后，"当前这张单用的是哪份档案"必须能查：
            # 否则用户会拿全局兜底样例去核对别家的单，误以为系统没记住。
            qstr = self.path.split("?", 1)[1] if "?" in self.path else ""
            q = dict(pair.split("=", 1) for pair in qstr.split("&") if "=" in pair)
            supplier = urllib.parse.unquote_plus(q.get("supplier", "") or "")
            self._json(profile_summary(supplier or None), head_only=HEAD_ONLY)
            return
        if path.endswith("/api/corrections"):
            qstr = self.path.split("?", 1)[1] if "?" in self.path else ""
            q = dict(pair.split("=", 1) for pair in qstr.split("&") if "=" in pair)
            try:
                cid = int(q.get("corrects_id", ""))
            except ValueError:
                self._json({"ok": False, "error": "corrects_id 必须是整数"},
                           400, head_only=HEAD_ONLY)
                return
            self._json(corrections_for(cid), head_only=HEAD_ONLY)
            return
        # 兜底：除 /api/* 外的一切 GET 都返回页面。
        # 平台可能用任意前缀代理（/preview、/p/xxx、多层子路径），
        # 精确匹配 "/" 只在"路径恰好是根"时成立，前缀一变用户就看到 not found。
        if path.startswith("/api/"):
            self._json({"ok": False, "error": "not found"}, 404, head_only=HEAD_ONLY)
            return
        self._send(
            load_page(),
            ctype="text/html; charset=utf-8",
            head_only=HEAD_ONLY,
        )

    # ---------------------------------------------------------------- POST
    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        try:
            payload = self._read_json()
            if payload is None:
                return
            if path.endswith("/api/ocr"):
                self._post_ocr(payload)
            elif path.endswith("/api/extract"):
                self._post_extract(payload)
            elif path.endswith("/api/submit"):
                self._post_submit(payload)
            elif path.endswith("/api/review"):
                self._post_review(payload)
            elif path.endswith("/api/apply"):
                self._post_apply(payload)
            elif path.endswith("/api/correct"):
                self._json(handle_correct(payload))
            elif path.endswith("/api/profile/examples"):
                self._json(handle_profile_examples(payload))
            else:
                self._json({"ok": False, "error": "not found"}, 404)
        except CloudError as exc:
            # 上游（数据库/模型通道）失败与本地逻辑错误必须分开报：
            # 前者可重试且不影响已算好的结果，后者是缺陷。
            sys.stderr.write(f"[docagent] cloud error: {exc}\n")
            self._json({"ok": False, "upstream": True, "retryable": exc.retryable,
                        "error": str(exc)}, 502)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(traceback.format_exc())
            self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    def _read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._json({"ok": False, "error": "请求体为空"}, 400)
            return None
        if length > MAX_BYTES:
            self._json({"ok": False, "error": "文件超过 12MB 上限"}, 413)
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._json({"ok": False, "error": f"请求体不是合法 JSON：{exc}"}, 400)
            return None
        if not isinstance(data, dict):
            self._json({"ok": False, "error": "请求体必须是 JSON 对象"}, 400)
            return None
        return data

    # ------------------------------------------------------------------ OCR
    def _post_ocr(self, payload: dict) -> None:
        raw = payload.get("data_b64") or ""
        if "," in raw[:64]:
            raw = raw.split(",", 1)[1]
        try:
            blob = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            self._json({"ok": False, "error": "base64 解码失败"}, 400)
            return

        # 内存内解码，不落临时文件：用户单据不在磁盘留痕
        # 解码失败是"这张图不行"，不是服务出错——必须是 400 且给一句人话。
        # 之前这里直接抛，走到 do_POST 的兜底 except，用户看到的是 500 +
        # "RuntimeError: 无法解码图片"，看起来像系统挂了。
        try:
            bgr = decode_image(blob)
        except RuntimeError as exc:
            self._json({
                "ok": False,
                "error": "图片无法解码：文件可能不完整或格式不支持，请重新上传原图（不要用截图工具二次压缩）",
                "detail": str(exc)[:200],
            }, 400)
            return

        t_pre = time.perf_counter()
        # ① 去红章：必须在 OCR 之前。红油墨与黑印刷在 RGB 里可分，
        #    放到 OCR 之后就无法区分"印章碎片"和"真实的一个字"。
        clean, pre_meta = suppress_red(bgr)
        pre_ms = (time.perf_counter() - t_pre) * 1000

        t0 = time.perf_counter()
        items = run_ocr_array(clean)
        ocr_ms = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        # ② 版面重建（内含补漏规则：剔除倾斜异常的词，如灰度章弧排字）
        layout = rebuild(items)
        layout_ms = (time.perf_counter() - t1) * 1000

        # ③ 注入检测：必须在抽取之前跑，这样告警能和「模型是否真的被劫持」
        #    形成对照（检测到了 → 但金额仍是真实值 → 证明两条防线都生效）
        injection = detect(layout.text)

        # ④ 内容风险：数字字面量异常 / 影子字段 / 币种。
        #    和注入检测并列但意图不同——注入问「有人在指挥模型吗」，
        #    这一层问「这张单据的数字本身可信吗」，两者都要在抽取前拿到原文。
        content_risk = detect_content_risk(layout.text)

        # ⑤ 原图暂存：后面看图复核要用（那时请求早已结束），只留在内存。
        filename = str(payload.get("filename") or "")[:120]
        doc_id = cache_doc(
            blob, filename, layout.text, content_risk,
            {"words": len(items), "ocr_ms": round(ocr_ms), "skew_deg": round(layout.skew_deg, 2)},
            sid=self._session(),
        )

        self._json(
            {
                "ok": True,
                "doc_id": doc_id,
                "filename": filename,
                "bytes": len(blob),
                "words": len(items),
                "preprocess": pre_meta,
                "pre_ms": round(pre_ms, 1),
                "dropped": layout.dropped,
                "ocr_ms": round(ocr_ms),
                "layout_ms": round(layout_ms, 1),
                "skew_deg": round(layout.skew_deg, 2),
                "lines": len(layout.lines),
                "cols": layout.col_count,
                "markdown": to_markdown_table(layout),
                "text": layout.text,
                "items": items,
                "injection": injection,
                "content_risk": content_risk,
            }
        )

    # --------------------------------------------------------------- 字段抽取
    def _post_extract(self, payload: dict) -> None:
        """按 doc_id 抽取（2026-10-26 迁移为图片直读）。**只认 doc_id**。

        迁移的理由（评测实证，见 eval/report_live_v2.json 与报告补记 2/3/4）：
        OCR 串联抽取把 OCR 的系统性偏差（O/0、表格错位）直接喂给模型，明细
        准确率反而低于直读。迁移后抽取看原图（信息无损），OCR 降级为出处层
        的参照文本（本地跑、零 token）与复核裁决的第二证据源——它不再生产
        抽取结果，只提供"独立于视觉模型"的第二读数。

        来源可证的约束不变：抽取只能由服务端拿自己缓存的原图、用自己固定的
        模型算出来。客户端连"怎么问"都决定不了。
        """
        doc_id = str(payload.get("doc_id") or "")
        doc = get_doc(doc_id, self._session())
        if not doc:
            self._json({
                "ok": False,
                "error": "原图已失效（服务端每会话保留 15 分钟、最多 8 张），请重新上传",
            }, 410)
            return

        image_b64 = doc.get("image_b64") or ""
        if not image_b64:
            self._json({
                "ok": False,
                "error": "这张图的原图缓存不可用，请重新上传后再抽取",
            }, 400)
            return

        # 用哪个模型：客户端可以选，但只能从"平台声明支持图片"的清单里选。
        # 不合格的 id 不静默换成默认（那会让人以为自己的选择生效了），直接拒绝
        # 并回可选清单。顺便把该模型的实测风险取出来——是否入得了库由下游闸门
        # 决定，但选择的那一刻就该知道它有没有前科。
        used_model, model_err = extract_models.resolve(
            CLOUD, payload.get("model"), EXTRACT_MODEL
        )
        if model_err:
            self._json({"ok": False, **model_err}, 400)
            return
        mrisk = extract_models.risk_of(used_model)

        text = (doc.get("text") or "").strip()
        messages, nonce = build_messages_direct(image_b64)
        t0 = time.perf_counter()
        raw, usage = CLOUD.chat(messages, model=used_model,
                                temperature=0.1, json_mode=True, timeout=120.0)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        extract = _model_json(raw)
        if extract is None:
            # 不把原文整段回显：那等于把「换个说法就能套出内容」的探测面留给对方
            self._json({
                "ok": False,
                "error": "模型未返回可解析的 JSON，请重试或换一张更清晰的原图",
                "model": used_model,
                "raw_head": (raw or "")[:200],
            }, 502)
            return

        # 记在 doc 上：入库时 model_id 从这里取，不采信客户端自报的 model_id。
        # 这张单是谁读的，只有真正发过这次调用的人知道。
        doc["extract_model"] = used_model
        doc["extract_model_risk"] = mrisk["risk"]

        # 出处层在抽取返回时就跑一次，让人在页面上直接看到「每个值在原文哪里」。
        # 注意这只是展示：真正决定能不能入库的那次在 /api/submit 里重算，
        # 客户端拿到的这个结果改不动服务端的判定。
        # OCR 文本为空的图（识别不出的手写单等）不再拒绝抽取——直读不需要
        # 文本；此时出处层没有参照物可对，如实降级为仅规则层校验。
        if text:
            g_issues, g_details = check_grounding(extract, text)
            grounding_note = ""
        else:
            g_issues, g_details = [], []
            grounding_note = "本图无 OCR 文本，出处层无参照物未运行，仅规则层校验"

        self._json({
            "ok": True,
            "doc_id": doc_id,
            "extract": extract,
            "model": used_model,
            "model_requested": (str(payload.get("model") or "").strip() or None),
            "model_risk": mrisk["risk"],
            "model_risk_note": mrisk["risk_note"],
            "extract_mode": "direct",
            "nonce": nonce,
            "usage": usage,
            "extract_ms": round(elapsed_ms),
            "chars": len(text),
            "injection": detect(text),
            "grounding": grounding_summary(g_details),
            "grounding_details": g_details,
            "grounding_issues": g_issues,
            "grounding_note": grounding_note,
        })

    # --------------------------------------------------------------- 校验/入库
    def _post_submit(self, payload: dict) -> None:
        """两层语义：commit=false 只校验，commit=true 校验后写库。

        刻意忽略客户端传来的 validate_ok / issues —— 这两个值如果由客户端给，
        整个改动就白做了。这里永远用自己的计算结果，客户端那份只用于显示。

        commit 分支的四道闸门（2026-10-25 加固）：
          ① 必须有 doc —— 没有原图就无法验证抽取值来自这张图，直接拒绝；
          ② 出处层（L4）参与判定 —— 名字类的错，勾稽查不出来；
          ③ needs_review 时**服务端自己**跑一次看图复核，不接受客户端"我已经复核过了"；
          ④ 复核结果与待入库值冲突 → 拒绝入库，把冲突原样退回。
        第 ③ 条每单多一次视觉模型调用。贵，但"入库"是终态操作：
        脏数据一旦进库，后面所有对账、付款、评级都以它为准。
        """
        extract = payload.get("extract")
        if not isinstance(extract, dict):
            self._json({"ok": False, "error": "缺少抽取结果 extract"}, 400)
            return
        doc = self._doc(payload)
        issues, g_details = validate_with_source(extract, doc)
        ok = is_ok(issues)
        grounding = grounding_summary(g_details)
        # 「没验」与「验过并通过」必须能区分：前者 grounding.checked==0
        grounding["ran"] = bool(doc and (doc.get("text") or "").strip())

        if not payload.get("commit"):
            needs, targets, triggers = review_targets(
                extract, issues, (doc or {}).get("content_risk"), supplier_history()
            )
            # 没有复核结论时的初判：只用来告诉页面"先别看这些，等看图回来再定"。
            # 页面拿到 /api/review 的 audit 后会覆盖它，因为到那时才有逐字段结论。
            pre_triage = triage_mod.triage(_with_values(issues, extract), None, g_details)
            self._json({
                "ok": True,
                "validated_by": "server",
                "validate_ok": ok,
                "issues": issues,
                "needs_review": needs,
                "review_targets": targets,
                "review_triggers": triggers,
                "triage": triage_mod.summary(pre_triage),
                # 校验**之后**的值，单独给一个字段名，不与入参 extract 混淆。
                # 编号形近归一就发生在这里（P0… → PO…），调用方据此核对
                # "服务端最后认定的到底是什么"，而不是拿自己提交的原值去猜。
                "validated_extract": extract,
                "grounding": grounding,
                "grounding_details": g_details,
                # 原图还在不在，必须显式告诉前端：commit=false 这一路在 doc 失效时
                # 不会报错（只是不跑出处层），界面需要据此提示"早点重新上传"，
                # 否则用户点入库时才会撞上 410。
                "source_available": bool(doc),
            })
            return

        # ① 原图必须还在：否则这份 extract 的来源不可证，不能入库
        if not doc:
            self._json({
                "ok": False,
                "code": "source_unavailable",
                "error": "原图已失效（服务端只保留 15 分钟、最多 8 张），"
                         "无法确认这份数据来自这张图，已拒绝入库。请重新上传后再提交。",
            }, 410)
            return

        # ③ needs_review ⇒ 服务端强制复核一次，不采信客户端自报的复核结果
        needs, targets, triggers = review_targets(
            extract, issues, doc.get("content_risk"), supplier_history()
        )
        forced = None
        findings: list[dict] = []
        if needs:
            if not doc.get("image_b64"):
                self._json({
                    "ok": False,
                    "code": "image_unavailable",
                    "error": "这笔单据需要看图复核，但服务端没有保住原图（压缩失败），"
                             "无法完成复核，已拒绝入库。请重新上传。",
                }, 409)
                return
            cache = doc.get("review_cache") or {}
            reuse = cache.get("sig") == extract_sig(extract) and cache.get("findings") is not None
            try:
                if reuse:
                    # 复用的是**服务端自己**刚跑出来的结果，不是客户端交上来的。
                    # 客户端那份 review 字段只作记录，从不参与判定。
                    findings = cache["findings"]
                    usage = cache.get("usage") or {}
                else:
                    findings, usage = run_review(
                        CLOUD, doc["image_b64"], doc.get("text") or "",
                        extract, targets, REVIEW_MODEL,
                    )
            except Exception as exc:  # noqa: BLE001
                # 复核跑不起来 ≠ 复核通过。拒绝入库并标可重试，
                # 否则"让复核失败"就成了一条免检通道。
                sys.stderr.write(f"[docagent] forced review failed: {exc}\n")
                self._json({
                    "ok": False,
                    "code": "review_unavailable",
                    "retryable": True,
                    "error": f"这笔单据有疑点，需要看图复核，但复核调用失败（{type(exc).__name__}）。"
                             f"数据尚未入库，请稍后重试。",
                }, 502)
                return
            forced = {
                "auto": True,
                "reused": bool(reuse),
                "model": REVIEW_MODEL,
                "findings": findings,
                "reviewed_fields": [t["field"] for t in targets],
                "usage": {"prompt_tokens": usage.get("prompt_tokens"),
                          "completion_tokens": usage.get("completion_tokens")},
            }

        # ④ 分诊：复核已有结论的疑点就地消解，只把**没有结论**的交给人工。
        #
        #    这一步是"全对直接入库"的落点。原来这里的判据是"复核值与待入库值
        #    不一致就 409 退回"，结果是：看图已经逐字确认正确的单子，也必须由人
        #    再点一次"通过"；而看图什么都没读出来的单子，反倒只要点一次就过。
        #    现在改成：能证明对的自动消解（记录依据），证不了的才要人的处置。
        t = triage_mod.triage(_with_values(issues, extract), findings, g_details)
        asks = t["ask"]
        audit_record = None
        if asks:
            choices = _audit_choices(payload)
            if not choices:
                self._json({
                    "ok": False,
                    "code": "audit_required",
                    "committed": False,
                    "error": f"有 {len(asks)} 条疑点机器给不出结论，需要你来定。"
                             f"数据尚未入库。",
                    "issues": issues,
                    "triage": triage_mod.summary(t),
                    "review_triggers": triggers[:6],
                }, 409)
                return
            missing = [a.get("field") for a in asks if a.get("field") not in choices]
            if missing:
                self._json({
                    "ok": False,
                    "code": "audit_incomplete",
                    "committed": False,
                    "error": f"还有 {len(missing)} 条疑点没有给出处置，数据尚未入库。",
                    "pending_fields": missing,
                    "triage": triage_mod.summary(t),
                }, 409)
                return

            applied, kept, rejected = [], [], []
            for a in asks:
                f = a.get("field")
                c = choices[f]
                if c["how"] == "reject":
                    rejected.append({"field": f, "msg": a.get("msg")})
                    continue
                if c["how"] == "keep":
                    # 人看过之后决定不改。记录他当时看到的值与证据，事后可追。
                    kept.append({"field": f, "value": a.get("value"),
                                 "level": a.get("level"), "evidence": a.get("evidence"),
                                 "image_value": a.get("image_value")})
                    continue
                val = c.get("value")
                if val is None or str(val).strip() == "":
                    self._json({
                        "ok": False,
                        "code": "audit_bad_value",
                        "committed": False,
                        "error": f"「{f}」选择了填写新值，但值是空的，数据尚未入库。",
                    }, 400)
                    return
                if _set_path(extract, f, val):
                    applied.append({"field": f, "to": val, "how": c["how"]})
            if rejected:
                # 人明确说了这张不入库。这不是错误，是他的结论——按成功返回，
                # 但 committed=false，页面据此显示"已退回"。
                self._json({
                    "ok": True, "committed": False, "code": "human_rejected",
                    "rejected": rejected,
                    "issues": issues,
                    "triage": triage_mod.summary(t),
                    "review_triggers": triggers[:6],
                })
                return
            if applied:
                # 值被改过，勾稽关系和出处都变了，必须用服务端规则重算一遍。
                # 重算后若又冒出没人处置过的疑点，就再停下让人看——不要用
                # "他已经点过一次通过"来掩盖新出现的问题。
                issues, g_details = validate_with_source(extract, doc)
                ok = is_ok(issues)
                grounding = grounding_summary(g_details)
                grounding["ran"] = bool(doc and (doc.get("text") or "").strip())
                t2 = triage_mod.triage(_with_values(issues, extract), findings, g_details)
                left = [a for a in t2["ask"] if a.get("field") not in choices]
                if left:
                    self._json({
                        "ok": False,
                        "code": "audit_required",
                        "committed": False,
                        "error": f"按你选的改完后，又出现 {len(left)} 条新疑点，仍需你确认。"
                                 f"数据尚未入库。",
                        "applied": applied,
                        "issues": issues,
                        "triage": triage_mod.summary(t2),
                    }, 409)
                    return
                t = t2
            audit_record = {
                "decided_by": "human",
                "choices": [{"field": f, "how": choices[f]["how"], "value": choices[f].get("value")}
                            for f in choices],
                "applied": applied,
                "kept": kept,
                "resolved_by_evidence": [
                    {"field": i.get("field"), "evidence": i.get("evidence")}
                    for i in t["resolved"]],
            }
            ok = is_ok(issues)

        stats = payload.get("ocr") if isinstance(payload.get("ocr"), dict) else {}
        items = extract.get("items") if isinstance(extract.get("items"), list) else []
        total = extract.get("total_amount")
        try:
            total_num = float(total) if total is not None and str(total) != "" else None
        except (TypeError, ValueError):
            total_num = None

        client_review = payload.get("review") if isinstance(payload.get("review"), dict) else None
        # 复核记录必须如实：哪次是服务端强制跑的、客户端是否也提交过一份。
        # 这个字段进 hash（v2 起），改它会被链查出。
        #
        # 审核结论（audit）也放进来，且**由服务端自己写**：客户端只说"我选了哪个",
        # 服务端记下"当时有哪些疑点、依据是什么、最后改成了什么"。否则事后无法
        # 区分"人看过之后决定不改"与"根本没人看过"。
        review = ({"server_forced": forced, "client": client_review,
                   "triage": triage_mod.summary(t), "audit": audit_record}
                  if (forced or audit_record) else client_review)
        row = {
            # owner_id 由数据库 DEFAULT 填充，服务端绝不代填
            "filename": doc.get("filename") or str(payload.get("filename") or "")[:120] or None,
            "doc_no": extract.get("doc_no"),
            "supplier": extract.get("supplier"),
            "order_date": extract.get("order_date"),
            "total_amount": total_num,
            "item_count": len(items),
            "extract_json": extract,
            "validate_ok": ok,
            "issues": issues,
            "ocr_words": doc.get("words") or stats.get("words"),
            "ocr_ms": doc.get("ocr_ms") or stats.get("ocr_ms"),
            "skew_deg": doc.get("skew_deg") or stats.get("skew_deg"),
            # 抽取模型以服务端记录的为准，**不采信客户端自报的 model_id**：
            # 这张单是谁读的，只有真发过那次调用的人知道。客户端能选模型，
            # 但选了什么由 /api/extract 记在 doc 上，这里只负责抄下来。
            "model_id": (str(doc.get("extract_model") or "")[:60] or None),
            "review_json": review,
            "validated_by": "server",
        }
        inserted = CLOUD.rest_insert(TABLE, row)
        # 编号写法沉淀进工厂档案（失败静默，见函数内注释）。
        # 有人在处置面板上定过这个编号（PO 还是 P0）就记成 human，
        # 否则记成 submit——两种证据必须能分清，见 _profile_record_confirmed。
        _no_by = "submit"
        if audit_record:
            for _c in (audit_record.get("choices") or []):
                if _c.get("field") == "doc_no" and _c.get("how") != "reject":
                    _no_by = "human"
                    break
        _profile_record_confirmed(extract.get("doc_no"), _no_by,
                                  extract.get("supplier"))
        rec = inserted[0] if isinstance(inserted, list) and inserted else {}
        self._json({
            "ok": True,
            "validated_by": "server",
            "id": rec.get("id"),
            "seq": rec.get("seq"),
            "record_hash": rec.get("record_hash"),
            "hash_ver": rec.get("hash_ver"),
            "validate_ok": ok,
            "issues": issues,
            "triage": triage_mod.summary(t),
            "audited_by_human": bool(audit_record),
            "grounding": grounding,
            "grounding_details": g_details,
            "forced_review": bool(forced),
            "reviewed_fields": (forced or {}).get("reviewed_fields") or [],
        })

    # ----------------------------------------------------------------- 复核
    def _post_review(self, payload: dict) -> None:
        doc = self._doc(payload)
        if not doc or not doc.get("image_b64"):
            self._json({"ok": False, "expired": True,
                        "error": "原图已失效（超过 15 分钟或服务重启），请重新上传单据"}, 400)
            return
        extract = payload.get("extract") if isinstance(payload.get("extract"), dict) else {}
        # 复核目标一律由服务端重算，不采用客户端传来的 targets。
        # 原因：target 里带 rule_fix（规则建议值），而"看图结果是否与规则一致"决定
        # 要不要放行形近字符的修改建议。这个值若由浏览器给，双源互证就等于自己跟
        # 自己印证，仲裁规则当场失效。客户端那份只影响它自己的显示。
        # 与 /api/submit 用同一套判定（规则层 + 出处层）。两边若不一致，
        # 页面复核过的字段和入库时要求复核的字段就会对不上。
        issues, g_details = validate_with_source(extract, doc)
        _, targets, _ = review_targets(extract, issues, doc.get("content_risk"), supplier_history())
        if not targets:
            self._json({"ok": True, "findings": [], "skipped": True, "reason": "没有需要复核的疑点",
                        "triage": triage_mod.summary(
                            triage_mod.triage(_with_values(issues, extract), None, g_details))})
            return
        # 模型不接受客户端指定：换弱模型可以降低复核判据的质量，
        # 换贵模型可以烧配额。两条都不该由请求方决定。
        model = REVIEW_MODEL
        findings, usage = run_review(
            CLOUD, doc["image_b64"], doc.get("text") or "", extract, targets, model
        )
        # 复核一回来就分诊：哪些疑点已被这条结论消解、哪些还得人定，一次算清。
        # 页面据此决定"直接入库"还是"弹出带选项的审核"——而不是看到任何一条
        # 非 info 就弹窗，逼人对看图已经确认过的事再点一次通过。
        t = triage_mod.triage(_with_values(issues, extract), findings, g_details)
        # 存一份到 doc 缓存：commit 时若待入库内容与现在完全一致，直接复用这次结果，
        # 不再多花一次视觉调用。内容一变（人改过值），指纹就对不上，必然重跑。
        doc["review_cache"] = {
            "sig": extract_sig(extract),
            "findings": findings,
            "reviewed_fields": [t2["field"] for t2 in targets],
            "usage": {"prompt_tokens": usage.get("prompt_tokens"),
                      "completion_tokens": usage.get("completion_tokens")},
        }
        self._json({
            "ok": True,
            "model": model,
            "findings": findings,
            "triage": triage_mod.summary(t),
            "reviewed_fields": [t2["field"] for t2 in targets],
            "usage": {"prompt_tokens": usage.get("prompt_tokens"),
                      "completion_tokens": usage.get("completion_tokens")},
        })

    # ------------------------------------------------- 按人工决定执行修改
    def _post_apply(self, payload: dict) -> None:
        """人给方向，模型执行，服务端核对——三步缺一不可，顺序也不能换。

        `decisions` 是人在页面上做完的选择，每条 {field, choice, value, text}。
        模型只负责把它展开成具体字段改动（连带重算行金额、合计、合并重复行），
        它给出的每一条改动都要过 verify_patches，不合规的丢掉并回报原因。

        这个接口**不写库**。写库仍然只有 /api/submit 一条路，且那条路会用自己的
        规则再校验一遍——执行说"改成这样"，存储说"这样合不合规"，两件事分开。
        """
        extract = payload.get("extract")
        if not isinstance(extract, dict):
            self._json({"ok": False, "error": "缺少抽取结果 extract"}, 400)
            return
        raw = payload.get("decisions")
        decisions = [d for d in raw if isinstance(d, dict) and isinstance(d.get("field"), str)] \
            if isinstance(raw, list) else []
        if not decisions:
            self._json({"ok": False, "error": "没有提供任何修改决定"}, 400)
            return
        if len(decisions) > 40:
            self._json({"ok": False, "error": "一次提交的修改项过多"}, 400)
            return

        # 只有"真的要改"的才送进模型。全是「保持当前值」时不该花一次调用，
        # 也不该让模型有机会在这些字段上做任何事。
        active = [d for d in decisions
                  if d.get("value") is not None or str(d.get("choice")) == "merge"]
        if not active:
            issues, _ = validate_with_source(extract, self._doc(payload))
            self._json({"ok": True, "model": None, "degraded": None, "patches": [],
                        "rejected": [], "notes": "没有选中任何修改，数据保持原样",
                        "extract": extract, "issues": issues,
                        "validate_ok": is_ok(issues), "to_confirm": [], "usage": {}})
            return

        doc = self._doc(payload)
        image = None
        if doc and doc.get("image_b64"):
            try:
                image = encode_for_vision(doc["image_b64"])
            except Exception:  # noqa: BLE001
                image = None  # 图读不出来就只按文字执行，不因此让整次操作失败
        # 模型不接受客户端指定：换弱模型可以降低复核判据的质量，
        # 换贵模型可以烧配额。两条都不该由请求方决定。
        model = REVIEW_MODEL

        degraded = None
        # 编号是**纯字符串替换**：改一个编号不牵动行金额、合计、任何勾稽关系，
        # 所以不进模型，理由两条：
        #   ① 模型没见过 "homoglyph" 这个选项名，容易顺手改到别的字段上去；
        #   ② 这里根本没有"要联动的算术"，交给模型只多一次出错的机会。
        # 与表格模式（mode=direct）同一原则：确定性操作交给确定性代码。
        det = [d for d in active
               if str(d.get("field")) == "doc_no" and d.get("value") is not None]
        rest = [d for d in active if d not in det]
        det_patches = _local_patches(det) if det else []

        merge_wanted = sum(1 for d in rest if str(d.get("choice")) == "merge")
        if not rest:
            # 只有编号要改：不花一次模型调用
            patches_raw, notes, usage = det_patches, "单据编号按你选定的写法直接写入", {}
        elif payload.get("mode") == "direct":
            # 表格模式：纯人工修改，人工给的值就是终值。不进模型——确定性操作
            # 不该引入不可控的"帮忙"，也不该为此花一次调用。merge 需要模型判断
            # 两行是否同一物料，direct 下没有模型，遇到就如实降级说明。
            patches_raw, notes, usage = det_patches + _local_patches(rest), "人工表格直接修改", {}
            if merge_wanted:
                degraded = f"表格模式不执行「合并重复行」（需模型判断两行是否同一物料），{merge_wanted} 项已忽略"
        else:
            try:
                patches_raw, notes, usage = run_apply(
                    CLOUD, image, (doc or {}).get("text") or "", extract, rest, model
                )
                if not patches_raw:
                    # 模型什么都没给：不是错误，但不能就这么过去——否则人点了"改"
                    # 却没改，还以为改了。这里回落成本地确定性改动，并在结果里说明。
                    degraded = "模型没有返回任何改动，已按你选定的值直接执行"
                    patches_raw, notes = _local_patches(rest), "按人工选定的值执行"
            except Exception as exc:  # noqa: BLE001
                # 模型挂了不能卡住入库：改成"照人选的值直接写"。这也是降级路径，
                # 必须让界面知道这次没有模型参与，不能假装两种判据都用过。
                degraded = f"模型不可用（{type(exc).__name__}），已按你选定的值直接执行"
                patches_raw, notes, usage = _local_patches(rest), "按人工选定的值执行", {}
            patches_raw = det_patches + patches_raw
            if degraded and merge_wanted:
                degraded += f"；其中 {merge_wanted} 项「合并重复行」需要模型判断两行是否同一物料，本次未执行"

        accepted, rejected = verify_patches(patches_raw, extract, active)

        # 人的决定是**硬约束**，不是给模型的参考。模型漏掉某个被点名的字段时
        # （它经常只处理自己觉得有问题的那几个），必须按人选的值补上——
        # 否则会出现"页面提示已执行、数据其实没变"这种最难解释的状态。
        covered = {p["field"] for p in accepted if p["op"] == "set"}
        gaps = [d for d in active
                if d.get("value") is not None and str(d.get("choice")) != "keep"
                and str(d.get("field")) not in covered]
        if gaps:
            fill, rejected_fill = verify_patches(_local_patches(gaps), extract, active)
            accepted += fill
            rejected += rejected_fill
            gap_note = f"模型未覆盖你指定的 {len(gaps)} 个字段，已按你选定的值直接写入"
            notes = f"{notes}；{gap_note}" if notes else gap_note

        after = apply_patches(extract, accepted)
        # 补丁改完之后要重跑**出处层**：如果模型把供应商改成了原文里没有的名字，
        # 这里就该立刻看出来，而不是等入库时才被拦。
        issues, g_details = validate_with_source(after, doc)
        self._json({
            "ok": True,
            "model": model if not degraded else None,
            "degraded": degraded,
            "patches": accepted,
            "rejected": rejected,
            "notes": notes,
            "extract": after,
            "issues": issues,
            "validate_ok": is_ok(issues),
            "grounding": grounding_summary(g_details),
            "grounding_details": g_details,
            "to_confirm": explain_patches(accepted, after, active),
            "usage": {"prompt_tokens": usage.get("prompt_tokens"),
                      "completion_tokens": usage.get("completion_tokens")},
        })

    def log_message(self, fmt: str, *args) -> None:
        # 只记方法/路径/状态码，不记请求体——单据内容不进日志
        sys.stderr.write("[docagent] " + fmt % args + "\n")


def _local_patches(decisions: list[dict]) -> list[dict]:
    """模型不可用时的降级：只把"人已经选定的那个值"写进去，不做任何联动推导。

    为什么降级不做联动：重算行金额、合计、合并重复行都属于"语义→算术"的推导，
    本地硬算等于替人做决定，而且极易算错（改单价后哪几行受影响，本地没有依据）。
    宁可不联动，让 /api/submit 的校验把算不平的地方如实报出来，也不要悄悄
    改出一个"看起来对"的结果。
    """
    out: list[dict] = []
    for d in decisions:
        if str(d.get("choice")) == "merge" and d.get("value") is None:
            continue  # 合并行必须由模型判断两行是不是同一个物料，本地不猜
        out.append({"op": "set", "field": d.get("field"), "to": d.get("value"),
                    "why": "按人工选定的值直接执行（本次未使用模型）"})
    return out


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[docagent] listening on 0.0.0.0:{port}", flush=True)
    # OCR 后端缺失不该让整个服务起不来。除了上传识别，还有一串功能不依赖 OCR：
    # 查询已入库记录、看审计链、追加更正、看编号档案。原来在这里无保护地调
    # backend_name()，缺 rapidocr 时进程直接退出，等于把这几件事一起判死。
    # 现在只在启动日志里如实说"OCR 不可用"，等到真正要识别时由 /api/ocr 报错。
    try:
        print(f"[docagent] ocr backend = {backend_name()}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[docagent] OCR 不可用（上传识别会返回错误，其余接口正常）：{exc}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
