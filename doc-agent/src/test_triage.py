"""疑点分诊的回归测试：哪些疑点该问人、哪些不该问。

为什么要有这份测试
------------------
"要不要问人"这条判据被改过两次，两次都改错了方向：
  · 第一次按 level 判（warn 就弹窗）→ 看图已逐字确认正确的单子，还要人空点一次通过；
  · 第二次把处置面板整个换成"通过/退回"→ 连 PO/P0 这种本该给选项的疑点也没有选项了。
这两次都不是算错，而是**判据的语义错了**。所以把语义固化成用例：
  问人 = 多值冲突 / 勾稽不成立
  不问 = 写法归一、看图逐字一致、图上看不清但文字出处完整

跑法：`python src/test_triage.py`（无第三方依赖，不调模型，不连库）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import triage  # noqa: E402

FAILED = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + name + (("  ← " + detail) if not cond and detail else ""))
    if not cond:
        FAILED.append(name)


def counts(issues, findings=None, g=None):
    return triage.triage(issues, findings, g)


# ---------------------------------------------------------------- 场景 1
# 截图里那一屏：三条疑点，看图逐字一致。
#
# 但"一致"在三条上不是同一件事：
#   · 供应商、物料名 —— 读错通常是字形差异（"塑胶"vs"塑料"），两个模型不会一
#     致地错，所以"一致"是真的两条独立证据 ⇒ 消解；
#   · 单据编号 —— 字母段末位的 O 与数字 0 在无衬线字体下包围盒几乎一样，抽取和
#     复核读的是同一张图，会一致地读成同一个字符。"一致"在这里不是两条证据，
#     是同一条证据重复了一次 ⇒ 必须人定写法（PO 还是 P0）。
print("场景 1 · 看图逐字一致：供应商/物料名消解，编号仍要人定写法")
t = counts(
    [
        {"level": "warn", "layer": "L2", "field": "doc_no", "value": "PO20261024001",
         "msg": "单据编号「PO20261024001」只有 1 条历史样本，不足以断定正确写法，请人工核对"},
        {"level": "warn", "layer": "L4", "field": "supplier", "code": "label_line_fuzzy",
         "value": "宁波恒达塑胶制品有限公司", "msg": "供应商与单据上的写法略有出入，需核对原图"},
        {"level": "warn", "layer": "L4", "field": "items[0].name", "code": "value_partial_in_source",
         "value": "塑胶外壳", "msg": "明细 1 的物料名与原文差 1 个字，需核对原图"},
    ],
    [
        {"field": "doc_no", "verdict": "match", "dirty_cause": "none", "reason": "逐字一致"},
        {"field": "supplier", "verdict": "match", "dirty_cause": "none", "reason": "OCR 把「望」读错"},
        {"field": "items[0].name", "verdict": "match", "dirty_cause": "seal", "reason": "印章外清楚"},
    ],
    [{"field": "supplier", "coverage": 1.0}, {"field": "doc_no", "coverage": 1.0},
     {"field": "items[0].name", "coverage": 1.0}],
)
check("供应商与物料名消解", t["counts"]["resolved"] == 2, str(t["counts"]))
check("编号那一条没被消解", any(a.get("field") == "doc_no" for a in t["ask"]), str(t["counts"]))
check("消解理由可读", all(i.get("evidence") for i in t["resolved"]))
check("编号给出的是写法二选一，不是放行/退回",
      any(o["how"] == "homoglyph" for o in (t["ask"] or [{}])[0].get("options") or []),
      str([o["how"] for o in (t["ask"] or [{}])[0].get("options") or []]))

# ---------------------------------------------------------------- 场景 2
# 勾稽不成立 + 看图读到不同的值：这是真疑点（多个同性质数据），必须人定，且要给选项。
print("场景 2 · 勾稽不过 + 看图读到不同值 → 必须给选项")
t = counts(
    [{"level": "err", "layer": "L3", "field": "items[1].amount", "value": 465.0,
      "msg": "第 2 行勾稽不通过：30 × 16.5 = 495.00，但金额记为 465.00",
      "fixes": [{"value": 495.0, "how": "recompute", "why": "按 30×16.5 重算"}]}],
    [{"field": "items[1].amount", "verdict": "mismatch", "dirty_cause": "none",
      "image_value": "495.00", "apply_value": "495.00", "reason": "照片上是 495.00"}],
)
check("需要人", t["need_human"] is True)
a = (t["ask"] or [{}])[0]
check("选项里有看图值", any(o["how"] == "image" for o in a.get("options") or []))
check("选项里能保持原值", any(o["how"] == "keep" for o in a.get("options") or []))
check("选项里能手动填", any(o["how"] == "manual" for o in a.get("options") or []))
check("证据写明两个候选值", "465" in (a.get("evidence") or "") and "495" in (a.get("evidence") or ""),
      a.get("evidence") or "")
# 看图读出不同的值 ⇒ 必须选一个值，不能用"审核通过"整单放行
# （否则四道闸门第 ④ 道"复核冲突不得入库"就形同不存在）。
check("冲突必须选值", a.get("must_choose") is True)

# ---------------------------------------------------------------- 场景 3
# 印章压住了字，看图读不出，但 OCR 原文里有完整出处 —— 用户的原话：
# "除去印章以后文字识别很清楚的也不算疑点"。两条判据合起来才算数。
print("场景 3 · 印章遮挡：文字有出处 ≠ 疑点；文字也没出处 = 疑点")
iss = [{"level": "err", "layer": "L4", "field": "supplier", "code": "value_not_in_source",
        "value": "宁波恒达塑胶制品有限公司", "msg": "供应商在原文里找不到，可能是读错或填错"}]
f = [{"field": "supplier", "verdict": "unreadable", "dirty_cause": "seal",
      "dirty_label": "印章覆盖", "reason": "红章压死"}]
check("有出处 → 消解", counts(iss, f, [{"field": "supplier", "coverage": 1.0}])["counts"]["resolved"] == 1)
check("出处 0.5 → 问人", counts(iss, f, [{"field": "supplier", "coverage": 0.5}])["need_human"] is True)
check("无出处记录 → 问人", counts(iss, f, [])["need_human"] is True)
check("非遮挡原因读不出 → 问人",
      counts(iss, [dict(f[0], dirty_cause="layout")], [{"field": "supplier", "coverage": 1.0}])["need_human"] is True)
# 图上看不清、也没有第二条判据 ⇒ 只能"放行/退回"，没有可选项（这一屏本就不该有选项）
_t = counts(iss, f, [{"field": "supplier", "coverage": 0.5}])
check("读不清这类可以只放行", (_t["ask"] or [{}])[0].get("must_choose") is False)

# ---------------------------------------------------------------- 场景 4
# 写法已按档案归一是 info：已经处理完了，不该再问。
print("场景 4 · 写法归一 / 说明类提示 → 不过问")
t = counts([
    {"level": "info", "layer": "L2", "field": "doc_no", "msg": "已归一为 PO20261024001",
     "normalized": {"from": "P020261024001", "to": "PO20261024001"}},
    {"level": "warn", "layer": "L2", "field": "currency", "code": "currency_inferred",
     "msg": "本单没有任何金额，币种却填了 CNY"},
])
check("两条都不问人", t["counts"]["auto"] == 2 and t["need_human"] is False, str(t["counts"]))

# ---------------------------------------------------------------- 场景 5
# 重复行不同单价：每条自己自洽、合起来矛盾。方向有三条，人挑一个。
print("场景 5 · 同一物料两行不同单价 → 三个方向都要给")
t = counts([{
    "level": "warn", "layer": "L3", "field": "items[1].price", "code": "duplicate_price",
    "value": 105.0, "msg": "第 2 行与第 1 行是同一物料「纸箱」，单价却不同（105 vs 100）",
    "fixes": [{"value": 100.0, "how": "rule", "why": "统一为第 1 行的单价"},
              {"value": 105.0, "how": "rule", "why": "这一行在后，可能是更正后的价"},
              {"value": None, "how": "merge", "why": "合并为一行"}],
}])
a = (t["ask"] or [{}])[0]
check("需要人", t["need_human"] is True)
check("三个方向都在", len([o for o in a.get("options") or [] if o["how"] in ("rule", "merge")]) >= 3,
      str([o["how"] for o in a.get("options") or []]))

# ---------------------------------------------------------------- 场景 6
# 看图说一致，但它旁边还有一条勾稽疑点没被覆盖 —— 消解只作用于被复核过的字段，
# 不能因为"这张单复核过了"就整体放行。
print("场景 6 · 消解是逐字段的，不整体放行")
t = counts(
    [{"level": "warn", "layer": "L4", "field": "supplier", "msg": "写法略有出入"},
     {"level": "err", "layer": "L3", "field": "total_amount", "value": 77100,
      "msg": "明细金额之和 85000.00 与合计 77100.00 不一致，差额 -7900.00"}],
    [{"field": "supplier", "verdict": "match", "dirty_cause": "none", "reason": "一致"}],
    [{"field": "supplier", "coverage": 1.0}],
)
check("供应商消解", t["counts"]["resolved"] == 1)
check("合计仍要人", t["need_human"] is True and (t["ask"] or [{}])[0].get("field") == "total_amount",
      str(t["counts"]))

# ---------------------------------------------------------------- 场景 7
# 空输入：没有疑点就是没有疑点，不要凭"没数据"给出需要人的结论。
print("场景 7 · 空输入")
t = counts([], None, None)
check("空 issues → 不需要人", t["need_human"] is False and t["counts"]["ask"] == 0)

# ---------------------------------------------------------------- 场景 8
# 用户指出的第一件事：PO 还是 P0，要让人选，不能推成"看图一致"或"请人工复核"。
print("场景 8 · 编号 P0 / PO 二义 → 两个写法都摆出来让人挑")
t = counts(
    [{"level": "warn", "layer": "L2", "field": "doc_no", "value": "PO20261024001",
      "msg": "单据编号「PO20261024001」只有 1 条历史样本，不足以断定正确写法，请人工核对"}],
    [{"field": "doc_no", "verdict": "match", "dirty_cause": "none", "reason": "逐字一致"}],
    [{"field": "doc_no", "coverage": 1.0}],
)
a = (t["ask"] or [{}])[0]
vals = [o.get("value") for o in a.get("options") or [] if o["how"] == "homoglyph"]
check("两个写法都在", vals == ["P020261024001"], str(vals))
check("当前写法也能确认", any(o["how"] == "keep" and o.get("value") == "PO20261024001"
                          for o in a.get("options") or []))
check("必须选一个值，不能一键放行", a.get("must_choose") is True)
check("理由说清为什么不能靠看图", "同" in (a.get("evidence") or ""), a.get("evidence") or "")

# 反方向也要对称：模型读成 P0 时，PO 同样要作为候选给出来
t = counts(
    [{"level": "warn", "layer": "L2", "field": "doc_no", "value": "P020261024001", "msg": "样本不足"}],
    [{"field": "doc_no", "verdict": "match"}], [{"field": "doc_no", "coverage": 1.0}])
vals = [o.get("value") for o in (t["ask"] or [{}])[0].get("options") or []
        if o["how"] == "homoglyph"]
check("反方向也对称（P0 → 给出 PO）", vals == ["PO20261024001"], str(vals))

# ---------------------------------------------------------------- 场景 9
# 编号格式本身没有交界二义（XK-2026-0042）时不许硬问：不然每张单都多一屏。
print("场景 9 · 编号没有 O/0 二义 → 不额外打断")
t = counts(
    [{"level": "warn", "layer": "L2", "field": "doc_no", "value": "XK-2026-0042", "msg": "样本不足"}],
    [{"field": "doc_no", "verdict": "match", "reason": "逐字一致"}], [])
check("照常消解", t["counts"]["resolved"] == 1 and t["need_human"] is False, str(t["counts"]))

# 画像已经认定过（validate 报 info / 已归一）时，这一屏根本不该再出现——
# 这就是"选一次以后有画像了，之后就不用再问"。
t = counts([
    {"level": "info", "layer": "L2", "field": "doc_no", "value": "PO20261024001",
     "msg": "已归一为 PO20261024001",
     "normalized": {"from": "P020261024001", "to": "PO20261024001"}},
], [{"field": "doc_no", "verdict": "match"}], [])
check("画像认过 → 不再问写法", t["need_human"] is False, str(t["counts"]))

# ---------------------------------------------------------------- 场景 10
# 用户指出的第二件事：纸箱本来就是 4100，被印章压成了 4000。
# 这类勾稽矛盾**不能**因为"看图说一致"就放过——看图读到的 4000 只是把票面上
# 那个被压住的读数重复了一遍，而 500 × 8.2 = 4100 是算出来的事实。
print("场景 10 · 勾稽矛盾 + 看图说一致 → 仍要人定（印章会把真值压成错的）")
t = counts(
    [{"level": "err", "layer": "L3", "field": "items[2].amount", "value": 4000.0,
      "msg": "第 3 行勾稽不通过：500 × 8.2 = 4100.00，但金额记为 4000.00",
      "fixes": [{"value": 4100.0, "how": "recompute", "why": "按 500 × 8.2 重算"}]}],
    [{"field": "items[2].amount", "verdict": "match", "dirty_cause": "seal",
      "dirty_label": "印章覆盖", "reason": "章下读作 4000"}],
    [{"field": "items[2].amount", "coverage": 1.0}],
)
a = (t["ask"] or [{}])[0]
check("没有被'看图一致'消解", t["counts"]["resolved"] == 0 and t["need_human"] is True,
      str(t["counts"]))
check("按算术重算的选项在", any(o["how"] == "recompute" and float(o["value"]) == 4100.0
                          for o in a.get("options") or []),
      str(a.get("options")))
check("理由点明算术不因看图而改变", "算术" in (a.get("evidence") or ""), a.get("evidence") or "")
check("必须选值", a.get("must_choose") is True)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "；".join(FAILED))
    sys.exit(1)
print("全部通过")
