"""抽取模型：谁能被选、选了意味着什么。

为什么把「模型固定」改成「可选」（2026-10-26 用户要求）
--------------------------------------------------------
固定成一个模型的代价是**使用者无法比较**：模型是这条链上唯一"换一个就能看出
差别"的部件，写死它等于把最值得看的一维关掉了。演示时对方问"换个模型会怎样"，
原来只能回答"不能换"。

但开放选择不等于把决定权交出去。三条约束全部留在服务端：

    ① **只能从平台目录里 `supportsImages=true` 的模型里选**。能力由平台声明，
       不由我们猜；文本模型给它图也读不了，只会报错。
    ② **选了什么都改不了"怎么问"**：prompt 在服务端、nonce 每单随机、图只能
       由服务端从自己缓存里取（客户端连 base64 都递不进来）。
    ③ **选择结果由服务端记录**：入库记录里的 `model_id` 取自本次实际调用，
       不采信客户端自报——否则"我用的默认模型"这句话可以随便写。

换句话说，客户端能决定的只有"用哪个已获准的模型读这张图"，而"读出来的东西
能不能入库"由四道闸门决定，与选了谁无关。这正是允许选择的依据：**控制点在
下游，不在模型**。

另外每个模型都带实测标注（`model_measured.json`，由
`gen_model_measured.py` 从注入扫描结果生成）。选 `glm-5v-turbo` 这种"实测被
单据文字改写过"的模型不会被拒绝——允许，但要让人在选择的那一刻就知道代价，
并且入库前必须人工确认一次（见 app.py 的 untrusted_model）。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_MEASURED_FILE = _HERE / "model_measured.json"

# 目录缓存时长：模型目录变化很慢，但演示时又可能刚上线一个新模型。
# 10 分钟是折中——过期一次只多一次 GET（20KB），比每次抽取都问一遍划算。
_TTL = 600.0

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "models": [], "error": None}

# 实测档位 → (严重度排序, 给使用者的一句话)
_RISK_ORDER = {"ok": 0, "silent": 1, "untested": 2, "injection": 3, "error": 4}

_measured_cache: dict | None = None


def measured() -> dict:
    """模型实测快照。文件缺失时返回空 dict——标注是增强项，不能因此选择不了模型。"""
    global _measured_cache
    if _measured_cache is None:
        try:
            _measured_cache = json.loads(_MEASURED_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _measured_cache = {}
    return _measured_cache


def risk_of(model_id: str) -> dict:
    d = measured().get(model_id) or {}
    return {
        "risk": d.get("risk") or "untested",
        "risk_note": d.get("note") or "没有这个模型的实测记录（不代表它不可用）",
        "credit": d.get("credit"),
    }


def _tier(item: dict, preferred: str) -> int:
    """UI 的排序档：默认 → 实测稳 → 有保留 → 慎用。

    默认档单独放最前，因为它是"没想选的时候该用的那个"，和"实测稳"不是一回事：
    后者是一堆同等可信的备选，前者是产品当下的口径。
    """
    if item["id"] == preferred:
        return 0
    return 1 + _RISK_ORDER.get(item["risk"], 2)


# 档位 = 1 + _RISK_ORDER（默认档单独为 0），所以标签必须按这个序号写，
# 不能按 _RISK_ORDER 的键写 —— 混了会把"被劫持"标成"调用失败"。
_TIER_LABEL = {
    0: "默认（本项目口径）",
    1: "实测可用 · 不会被单据文字改写",
    2: "可用 · 但模型自报的 warnings 不可靠",
    3: "可用 · 没有实测记录",
    4: "慎用 · 实测被单据文字改写过",
    5: "慎用 · 上次调用失败",
}


def _normalize(raw: dict, preferred: str) -> dict | None:
    mid = str(raw.get("id") or "").strip()
    if not mid:
        return None
    r = risk_of(mid)
    item = {
        "id": mid,
        "name": str(raw.get("name") or mid),
        "vendor": str(raw.get("vendor") or ""),
        "images": bool(raw.get("supportsImages")),
        "reasoning": bool(raw.get("supportsReasoning")),
        "platform_default": bool(raw.get("isDefault")),
        "credit": r["credit"],
        "risk": r["risk"],
        "risk_note": r["risk_note"],
        "tested": mid in measured(),
    }
    item["tier"] = _tier(item, preferred)
    item["tier_label"] = _TIER_LABEL.get(item["tier"], "其他")
    return item


def catalog(cloud, preferred: str, force: bool = False) -> dict:
    """平台目录 → 可选的抽取模型清单。

    读不到目录时**不静默降级成"什么都行"，也不把服务整个堵死**：返回
    `degraded=True`，清单里只留默认模型。理由是这时候没法核实"这个 id 是不是
    真存在、能不能读图"，放行任意 id 等于把校验换成一个字符串透传。
    """
    now = time.time()
    with _lock:
        fresh = (now - float(_cache["at"] or 0)) < _TTL
        if _cache["models"] and fresh and not force:
            return {"ok": True, "degraded": False, "models": _cache["models"]}
    try:
        raw = cloud.models()
    except Exception as exc:  # noqa: BLE001 - 目录读不到是常态（网络/配额），不该抛到上层
        err = f"{type(exc).__name__}: {exc}"[:300]
        with _lock:
            stale = list(_cache["models"])
        if stale:
            # 有一次成功的目录就继续用：过期总比"只能选默认"好，且每个 id 仍然
            # 受 supportsImages 约束（那份快照就是校验过的）。
            return {"ok": True, "degraded": True, "error": err, "models": stale}
        return {"ok": True, "degraded": True, "error": err,
                "models": [_fallback(preferred)]}

    items = []
    for m in raw:
        if not isinstance(m, dict) or m.get("enabled") is False:
            continue
        it = _normalize(m, preferred)
        if it and it["images"]:
            items.append(it)
    items.sort(key=lambda x: (x["tier"], x["name"].lower()))
    with _lock:
        _cache.update({"at": now, "models": items, "error": None})
    return {"ok": True, "degraded": False, "models": items}


def _fallback(preferred: str) -> dict:
    r = risk_of(preferred)
    item = {"id": preferred, "name": preferred, "vendor": "", "images": True,
            "reasoning": False, "platform_default": False, "credit": r["credit"],
            "risk": r["risk"], "risk_note": r["risk_note"],
            "tested": preferred in measured(), "tier": 0}
    item["tier_label"] = _TIER_LABEL[0]
    return item


def allowed_ids(cloud, preferred: str) -> tuple[set[str], dict]:
    cat = catalog(cloud, preferred)
    return {m["id"] for m in cat["models"]}, cat


def resolve(cloud, requested, preferred: str) -> tuple[str | None, dict | None]:
    """客户端要的模型 → 批准用哪个。返回 (model_id, error)。

    空 = 用默认。不在清单里 = 拒绝，并把可选清单回给调用方——错误信息要能让人
    自己改对，而不是只说"参数非法"。
    """
    ids, cat = allowed_ids(cloud, preferred)
    want = str(requested or "").strip()
    if not want:
        return (preferred if preferred in ids else sorted(ids)[0] if ids else preferred), None
    if want in ids:
        return want, None
    return None, {
        "code": "model_not_allowed",
        "error": (f"不能使用模型「{want}」：它不在可选的抽取模型里。"
                  f"只有平台目录中声明支持图片输入（supportsImages）且已启用的模型"
                  f"才能读单据图；文本模型给它图片也读不出来。"),
        "available": sorted(ids),
        "catalog_degraded": bool(cat.get("degraded")),
    }
