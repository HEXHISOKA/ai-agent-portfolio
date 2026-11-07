"""入库链路的端到端冒烟：不调模型、不连库，验证"什么情况下会停下来问人"。

为什么必须跑这一层
------------------
分诊函数（triage）单独测通过，不代表入库链路是对的——中间还隔着「服务端强制
复核」「按人的处置改值」「重算再判」三步。这三步任何一步接错，表现都是同一种：
**页面看起来正常，库里多了一条没人看过的数据**。这类错最贵，所以用桩把整条
链路跑一遍，断言"入库时到底带了什么审计字段"。

跑法：`python src/test_submit_flow.py`
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import app  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  ← " + str(detail)) if not cond else ""))
    if not cond:
        FAILED.append(name)


# --------------------------------------------------------------------- 桩
EXTRACT = {
    "doc_no": "PO20261024001",
    "supplier": "宁波恒达塑胶制品有限公司",
    "order_date": "2026-10-24",
    "total_amount": 77100,
    "items": [{"name": "塑胶外壳", "qty": 10, "price": 7710, "amount": 77100}],
}
# 一张**真有疑点**的单：行内勾稽不成立（10 × 770 = 7700，但金额写 7710），
# 且明细和 7710 与合计 77100 差一个数量级。用它来验证"该停的时候真的会停"。
EXTRACT_BAD = {
    "doc_no": "PO20261024001",
    "supplier": "宁波恒达塑胶制品有限公司",
    "order_date": "2026-10-24",
    "total_amount": 77100,
    "items": [{"name": "塑胶外壳", "qty": 10, "price": 770, "amount": 7710}],
}
OCR_TEXT = ("采购订单\n供应商：宁波恒达塑胶制品有限公司\n单据编号：PO20261024001\n"
            "下单日期：2026-10-24\n塑胶外壳 10 7710.00 77100.00\n合计：77100.00")
DOC = {
    "text": OCR_TEXT, "image_b64": "x", "filename": "po.pdf",
    "content_risk": {"shadow": [], "numeric": [], "high": 0},
}

INSERTED = {}


# 档案沉淀的调用记录：(doc_no, by)。by 区分"人选过"与"系统自己放行"。
PROFILE_CALLS = []


def fake_cloud(history=None):
    def rest_insert(table, row):
        INSERTED.clear()
        INSERTED.update(row)
        return [{"id": 1, "seq": 7, "record_hash": "h", "hash_ver": 2}]

    def rest_select(table, q=None, **k):
        # history 只喂给 doc_parses（画像学习的来源），档案表仍返回空——
        # 这样得到的是"历史里只有 1 条"的弱画像，而不是"档案已认定"。
        if history and table == "doc_parses":
            return history
        return []

    return types.SimpleNamespace(
        rest_insert=rest_insert,
        rest_select=rest_select,
        rest_update=lambda *a, **k: None,
    )


def run(payload, doc=None, findings=None, findings_error=None, history=None):
    """跑一次 _post_submit，返回 (response, http_code)。"""
    INSERTED.clear()
    PROFILE_CALLS.clear()
    app._PROFILE["at"] = 0.0          # 画像有 10 分钟缓存，跨场景必须清
    app.CLOUD = fake_cloud(history)

    def fake_review(client, image_b64, ocr_text, extract, targets, model, timeout=150.0):
        if findings_error:
            raise findings_error
        return findings or [], {"prompt_tokens": 1, "completion_tokens": 1}

    app.run_review = fake_review
    # 签名带 by + supplier：人确认过的编号记 human，系统放行的记 submit；
    # 并要落到**该供应商**的桶里（编号格式是客户级知识，不能全厂一份）。
    app._profile_record_confirmed = lambda no, by="submit", supplier=None: \
        PROFILE_CALLS.append((no, by))

    class H:
        def _doc(self, p):
            return dict(doc or DOC) if (doc or DOC) else None

        def _json(self, payload, code=200, head_only=False):
            self.out, self.code = payload, code

    h = H()
    app.Handler._post_submit(h, payload)
    return h.out, h.code


# ------------------------------------------------------------------ 场景一
# 三条疑点（编号样本不足 / 供应商写法 / 物料名差一字）看图都逐字一致
# ⇒ 不该停在审核，应该直接入库，并记下"依据是哪些证据"。
print("场景 1 · 看图逐字一致 → 直接入库，不弹审核")
issues_all_match = [
    {"field": "doc_no", "verdict": "match", "dirty_cause": "none", "reason": "逐字一致"},
    {"field": "supplier", "verdict": "match", "dirty_cause": "none", "reason": "OCR 读错一个字"},
    {"field": "items[0].name", "verdict": "match", "dirty_cause": "seal", "reason": "印章外清楚"},
    {"field": "total_amount", "verdict": "match", "dirty_cause": "none", "reason": "一致"},
]
# 画像里只有 1 条、而且写的是 P0：于是这张 PO 过不了 accepts，样本数又不足以
# 让画像自己指方向 —— 这正是"只有 1 条历史样本，不足以断定正确写法"的真实触发
# 条件，也是 O/0 二义唯一必须问人的场合。供应商/物料名仍然靠看图消解。
WEAK_HISTORY = [{"doc_no": "P020261024001", "supplier": "宁波恒达塑胶制品有限公司"}]
resp, code = run({"extract": dict(EXTRACT), "commit": True}, findings=issues_all_match,
                 history=WEAK_HISTORY)
check("编号那一条停下来问写法", code == 409 and resp.get("code") == "audit_required",
      (code, resp.get("code")))
_ask1 = ((resp.get("triage") or {}).get("ask") or [])
check("只有编号要人（其余看图已消解）",
      [a.get("field") for a in _ask1] == ["doc_no"],
      str([a.get("field") for a in _ask1]))
check("给的是 PO / P0 两个写法",
      any(o["how"] == "homoglyph" and o.get("value") == "P020261024001"
          for o in (_ask1[0].get("options") or [])),
      str(_ask1[0].get("options")))
check("没有写库", not INSERTED)

# 人确认"就是 PO" → 入库，且档案记成 human（这是"选一次就记住"的落点）
resp, code = run({"extract": dict(EXTRACT), "commit": True,
                  "audit": {"choices": [{"field": "doc_no", "how": "keep"}]}},
                 findings=issues_all_match, history=WEAK_HISTORY)
check("确认后入库", resp.get("ok") is True and code == 200, (code, resp.get("error")))
check("入库记的是 PO 写法", (INSERTED or {}).get("doc_no") == "PO20261024001",
      (INSERTED or {}).get("doc_no"))
check("档案沉淀标为人确认", ("PO20261024001", "human") in PROFILE_CALLS, str(PROFILE_CALLS))
check("审计里留下这次确认",
      "doc_no" in str(((INSERTED or {}).get("review_json") or {})), "审计缺 doc_no 处置")
resp, code = run({"extract": dict(EXTRACT), "commit": True,
                  "audit": {"choices": [{"field": "doc_no", "how": "homoglyph",
                                         "value": "P020261024001"}]}},
                 findings=issues_all_match, history=WEAK_HISTORY)
check("选另一个写法 → 按选的改值入库", (INSERTED or {}).get("doc_no") == "P020261024001",
      (INSERTED or {}).get("doc_no"))
check("档案记的是人选的写法", ("P020261024001", "human") in PROFILE_CALLS, str(PROFILE_CALLS))

# ------------------------------------------------------------------ 场景二
# 勾稽不成立，且看图读到不同的值 ⇒ 必须停下来让人选，不能一键放行。
print("场景 2 · 复核值与待入库值冲突 → 409，且必须选一个值")
conflict_doc = dict(DOC)
conflict_doc["text"] = OCR_TEXT.replace("77100.00", "77100.00")
resp, code = run(
    {"extract": dict(EXTRACT_BAD), "commit": True},
    doc=conflict_doc,
    findings=[{"field": "total_amount", "verdict": "mismatch", "dirty_cause": "none",
               "image_value": "81000.00", "apply_value": "81000.00", "reason": "照片上是 81000"}],
)
check("未入库", code == 409 and resp.get("ok") is False, code)
check("停下原因是审核", resp.get("code") == "audit_required", resp.get("code"))
ask = ((resp.get("triage") or {}).get("ask") or [])
check("明确要求选值", any(a.get("must_choose") for a in ask), ask)
check("没有写库", not INSERTED)

# ------------------------------------------------------------------ 场景三
# 图上看不清、文字出处也不完整 ⇒ 不存在可选项，只能放行或退回。
print("场景 3 · 图上看不清 → 人放行后入库，审计里留下'经审核放行'")
bad_doc = dict(DOC)
bad_doc["text"] = OCR_TEXT.replace("宁波恒达塑胶制品有限公司", "宁波恒达塑胶")  # 出处不完整
resp, code = run(
    {"extract": dict(EXTRACT), "commit": True},
    doc=bad_doc,
    findings=[{"field": "supplier", "verdict": "unreadable", "dirty_cause": "blur",
               "dirty_label": "模糊/分辨率不足", "reason": "糊了"}],
)
check("未入库", code == 409 and resp.get("code") == "audit_required", code)
ask = ((resp.get("triage") or {}).get("ask") or [])
check("这条例外可以只放行", ask and all(not a.get("must_choose") for a in ask), ask)

resp, code = run(
    {"extract": dict(EXTRACT), "commit": True,
     "audit": {"choices": [{"field": a["field"], "how": "keep"} for a in ask]}},
    doc=bad_doc,
    findings=[{"field": "supplier", "verdict": "unreadable", "dirty_cause": "blur",
               "dirty_label": "模糊/分辨率不足", "reason": "糊了"}],
)
check("放行后入库", resp.get("ok") is True and code == 200, resp.get("error"))
check("标记为经审核放行", resp.get("audited_by_human") is True)
check("审计里留了人的处置", isinstance(INSERTED.get("review_json"), dict)
      and (INSERTED["review_json"].get("audit") or {}).get("decided_by") == "human",
      INSERTED.get("review_json"))

# ------------------------------------------------------------------ 场景四
# 人说退回 ⇒ 不入库。这不是错误，是结论。
print("场景 4 · 人明确退回 → 不入库")
_f4 = [{"field": "total_amount", "verdict": "mismatch", "dirty_cause": "none",
        "image_value": "81000", "apply_value": "81000", "reason": "不一致"}]
_first, _ = run({"extract": dict(EXTRACT_BAD), "commit": True}, findings=_f4)
_asks4 = ((_first.get("triage") or {}).get("ask") or [])
resp, code = run(
    {"extract": dict(EXTRACT_BAD), "commit": True,
     "audit": {"choices": [{"field": a["field"], "how": "reject"} for a in _asks4]}},
    findings=_f4,
)
check("不入库", resp.get("committed") is False and not INSERTED, resp)
check("回报为人工退回", resp.get("code") == "human_rejected", resp.get("code"))

# ------------------------------------------------------------------ 场景五
# 复核跑不起来 ≠ 复核通过：fail-closed。
print("场景 5 · 复核调用失败 → 拒绝入库（不能变成免检通道）")
resp, code = run({"extract": dict(EXTRACT_BAD), "commit": True}, findings_error=RuntimeError("boom"))
check("未入库", code == 502 and resp.get("code") == "review_unavailable", code)
check("标为可重试", resp.get("retryable") is True)
check("没有写库", not INSERTED)

# ------------------------------------------------------------------ 场景六
# 按人选的改值后重算：改了行金额、合计没跟上 → 不能悄悄入库。
print("场景 6 · 改值后重算又冒出疑点 → 继续停下")
ext2 = dict(EXTRACT_BAD)
resp, code = run(
    {"extract": ext2, "commit": True,
     "audit": {"choices": [{"field": "items[0].amount", "how": "manual", "value": "80000"}]}},
    doc=dict(DOC, text=OCR_TEXT.replace("77100.00", "80000.00")),
    findings=[{"field": "items[0].amount", "verdict": "unreadable", "dirty_cause": "none",
               "reason": "看不清"}],
)
check("改值后仍拦下新疑点", code == 409, (code, resp.get("code")))

# ------------------------------------------------------------------ 场景七
# 用户选了一个"实测被单据文字改写过"的模型 ⇒ 这张单必须停一次。
# 这条链的意义：模型的选择权还给了用户，但**后果接在下游**——选它有代价，
# 代价不是"悄悄入库"，而是"多一道人工确认"。
print("场景 7 · 选了有劫持前科的模型 → 必停，放行后如实记下用的是谁")
risky_doc = dict(DOC, extract_model="glm-5v-turbo", extract_model_risk="injection")
resp, code = run({"extract": dict(EXTRACT), "commit": True}, doc=risky_doc,
                 findings=issues_all_match)
check("未入库", code == 409 and resp.get("ok") is False, (code, resp.get("code")))
check("停下原因是审核", resp.get("code") == "audit_required", resp.get("code"))
ask7 = ((resp.get("triage") or {}).get("ask") or [])
check("拦在'模型不可信'这一条", any(a.get("code") == "untrusted_model" for a in ask7),
      str(ask7)[:200])
risk_ask = [a for a in ask7 if a.get("code") == "untrusted_model"]
check("这一条不给'改成什么'的方向",
      bool(risk_ask) and risk_ask[0].get("must_choose") is False,
      str(risk_ask[0].get("must_choose")) if risk_ask else "缺")
check("有可读的字段名（不能是空标签）",
      bool(risk_ask) and risk_ask[0].get("field_label") == "抽取模型",
      str(risk_ask[0].get("field_label")) if risk_ask else "缺")

resp, code = run({"extract": dict(EXTRACT), "commit": True,
                  "audit": {"choices": [{"field": a.get("field"), "how": "keep"} for a in ask7]}},
                 doc=dict(risky_doc), findings=issues_all_match)
check("人放行后入库", code == 200 and resp.get("ok") is True and bool(INSERTED),
      (code, resp.get("code"), resp.get("error")))
check("model_id 记的是实际用的模型", (INSERTED or {}).get("model_id") == "glm-5v-turbo",
      (INSERTED or {}).get("model_id"))
audit7 = ((INSERTED or {}).get("review_json") or {})
check("审计里留下这次人工确认",
      "audit" in (audit7 if isinstance(audit7, dict) else {}), str(audit7)[:120])

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "；".join(FAILED))
    sys.exit(1)
print("全部通过")
