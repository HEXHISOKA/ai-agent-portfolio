"""编号档案"按供应商分桶" + "归一不得跨单"的回归测试（stub 掉 CloudClient）。

为什么要有这个测试文件：
    真实事故形态不是"归一算错了"，而是**归一改对了格式、改错了编号**——
    A 单的流水号被"纠正"成 B 单的，入库后两条记录看起来都合法，事后无法分辨。
    以及：两家客户共用一份档案，甲家的 `PO`+11 位把乙家真的 `P0` 开头改掉。

覆盖：
- patterns.same_skeleton：同骨架/不同流水号/不同长度/完全相同；
- validate 归一闸门：跨编号候选一律作废，doc_no 原值不动；
- patterns.supplier_key / normalize 桶键；
- patterns.learn + pick：两家客户的画像互不干扰；
- app._bucket_of：factory_key ↔ 桶键；
- app._profile_record_confirmed：写入正确桶、不把陌生格式塞进别人的桶；
- app.doc_profiles：权威档案覆盖、submit 档案只补空。

跑法：python src/test_profile_supplier.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))          # app.py
sys.path.insert(0, str(ROOT / "src"))  # src 内模块

import patterns  # noqa: E402
import app        # noqa: E402
from validate import validate  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


class FakeCloud:
    def __init__(self, select_results=None):
        self.select_results = list(select_results or [])
        self.select_calls, self.insert_calls, self.update_calls = [], [], []

    def rest_select(self, table, query="", timeout=20.0):
        self.select_calls.append((table, query))
        return self.select_results.pop(0) if self.select_results else []

    def rest_insert(self, table, row, timeout=25.0):
        self.insert_calls.append((table, row))
        return []

    def rest_update(self, table, row, query="", timeout=25.0):
        self.update_calls.append((table, row, query))
        return []


def base_extract(doc_no, supplier="宁波恒达塑胶制品有限公司"):
    return {"doc_no": doc_no, "supplier": supplier, "order_date": "2026-10-24",
            "total_amount": 77100.0, "currency": "CNY",
            "items": [{"name": "纸箱", "qty": 500, "price": 8.2, "amount": 4100.0}]}


def doc_no_issue(d, level=None):
    return [i for i in validate(d, profile=None)
            if i.get("field") == "doc_no" and (level is None or i.get("level") == level)]


print("== patterns.same_skeleton ==")
check("O / 0 单点差异 = 同骨架", patterns.same_skeleton("PO20261024001", "P020261024001"))
check("流水号末位不同 ≠ 同骨架", not patterns.same_skeleton("PO20261024001", "PO20261024002"))
check("长度不同 ≠ 同骨架", not patterns.same_skeleton("PO20261024001", "PO2026082113"))
check("完全相同 → False（没有差异就没有归一）",
      not patterns.same_skeleton("PO20261024001", "PO20261024001"))
check("两处差异 → False", not patterns.same_skeleton("PO20261024001", "P02026102400I"))
check("非形近字符差异 → False", not patterns.same_skeleton("PO20261024001", "PQ20261024001"))

print("== 归一闸门：不许跨单改写 ==")
AUTH = patterns.from_examples(["PO20261024001"], "manual")   # 认定型：一条即可
check("认定型画像 authoritative", AUTH.authoritative)
d1 = base_extract("P020261024001")
iss1 = validate(d1, profile=AUTH)
check("同骨架形近写法被归一", d1["doc_no"] == "PO20261024001", d1["doc_no"])
check("归一记 info 且保留原值",
      any(i.get("normalized", {}).get("from") == "P020261024001" for i in iss1))
d2 = base_extract("PO20261024002")
iss2 = validate(d2, profile=AUTH)
check("★不同流水号不被改写", d2["doc_no"] == "PO20261024002", d2["doc_no"])
check("★不同流水号不给出指向他人编号的 fixes",
      not [f for i in iss2 if i.get("field") == "doc_no"
           for f in (i.get("fixes") or []) if f.get("value") == "PO20261024001"])
d3 = base_extract("PO2026082113")           # 10 位数字，另一张单
iss3 = validate(d3, profile=AUTH)
check("★位数不同的编号不被补成认定样例", d3["doc_no"] == "PO2026082113", d3["doc_no"])
check("位数不同时给出提示而不是静默",
      any("格式" in (i.get("msg") or "") for i in iss3 if i.get("field") == "doc_no"))

print("== submit 沉淀不算认定 ==")
WEAK = patterns.from_examples(["PO20261024001"], "confirmed", "submit")
check("submit 档案 authoritative=False", not WEAK.authoritative)
cand, why = WEAK.suggest("P020261024001")
check("submit 单条样例不给归一候选（仍守 3 条门槛）", cand is None, cand)
check("提示里写明是系统沉淀而不是人工确认",
      why is None or "系统自动沉淀" in why, why)
HUMAN = patterns.from_examples(["PO20261024001"], "confirmed", "human")
check("human 档案 authoritative=True", HUMAN.authoritative)

print("== 供应商分桶 ==")
NB = "宁波恒达塑胶制品有限公司"
WZ = "温州龙湾宏利纸品有限公司"
check("空供应商 → default 兜底", patterns.supplier_key(None) == "default")
check("有供应商 → supplier:<归一化名>",
      patterns.supplier_key(NB) == "supplier:" + patterns.normalize_name(NB))
by, glob = patterns.learn([("PO20261024001", NB), ("PO20261024002", NB),
                           ("PO20261024003", NB),
                           ("PO2026081512", WZ), ("PO2026081513", WZ),
                           ("PO2026081514", WZ)])
nb_prof = patterns.pick(by, glob, NB)
wz_prof = patterns.pick(by, glob, WZ)
check("宁波桶接受 11 位编号", nb_prof.accepts("PO20261024009"))
check("宁波桶不接受温州的 10 位编号", not nb_prof.accepts("PO2026081512"))
check("温州桶接受 10 位编号", wz_prof.accepts("PO2026081512"))
# 位数区间对 +1 位是刻意宽容的（流水号跨年/进位会让位数涨一位），
# 这里断言的是"+2 位就不认"，而不是"必须严格 10 位"。
check("温州桶对 +1 位有进位宽容", wz_prof.accepts("PO20260815121"))
check("温州桶不接受 +2 位", not wz_prof.accepts("PO202608151211"))
check("两家桶互相独立", nb_prof is not wz_prof and nb_prof.dmin != wz_prof.dmin)

print("== app._bucket_of ==")
check("default → 全局", app._bucket_of("default") == "")
check("supplier: 前缀被剥掉",
      app._bucket_of("supplier:" + patterns.normalize_name(NB)) == patterns.normalize_name(NB))
check("无前缀的历史行当桶键用", app._bucket_of("acme") == "acme")
check("空值当全局", app._bucket_of(None) == "")

print("== _profile_record_confirmed 写入正确的桶 ==")
fake = FakeCloud(select_results=[[]])       # 该桶还没有行 → insert
app.CLOUD = fake
app._profile_record_confirmed("PO20261024001", "submit", NB)
check("插入行存在", len(fake.insert_calls) == 1)
row = fake.insert_calls[0][1] if fake.insert_calls else {}
check("★factory_key 是供应商桶", row.get("factory_key") == patterns.supplier_key(NB),
      row.get("factory_key"))
check("submit 标记保留", row.get("confirmed_by") == "submit")

# 另一家客户走另一个桶，不会追加到宁波那行
fake = FakeCloud(select_results=[[]])
app.CLOUD = fake
app._profile_record_confirmed("PO2026081512", "submit", WZ)
row2 = fake.insert_calls[0][1] if fake.insert_calls else {}
check("★另一家客户写自己的桶", row2.get("factory_key") == patterns.supplier_key(WZ))
check("两家桶键不同", row.get("factory_key") != row2.get("factory_key"))

# 系统放行的编号与桶内已知格式不符 → 不写进该桶（防串格式）
fake = FakeCloud(select_results=[[{"id": 9, "examples": ["PO20261024001"],
                                   "source": "confirmed", "confirmed_by": "human"}]])
app.CLOUD = fake
app._profile_record_confirmed("PO2026081512", "submit", NB)
check("★陌生格式不塞进别人桶（update 未被调用）", not fake.update_calls,
      fake.update_calls)

# 人明确选的写法 → 即便格式不同也要落库（人的认定优先）
fake = FakeCloud(select_results=[[{"id": 9, "examples": ["PO20261024001"],
                                   "source": "confirmed", "confirmed_by": "human"}]])
app.CLOUD = fake
app._profile_record_confirmed("PO2026081512", "human", NB)
check("人的认定可以扩展该桶格式", bool(fake.update_calls))

print("== doc_profiles 合并规则 ==")
# 档案：宁波桶为人工认定（权威），全局桶为 submit（不权威）
rows = [
    {"factory_key": "supplier:" + patterns.normalize_name(NB), "examples": ["PO20261024001"],
     "source": "confirmed", "confirmed_by": "human", "updated_at": "2026-10-26T00:00:00Z"},
    {"factory_key": "default", "examples": ["XX20990101001"],
     "source": "confirmed", "confirmed_by": "submit", "updated_at": "2026-10-20T00:00:00Z"},
]
hist = [{"doc_no": "PO20261024001", "supplier": NB},
        {"doc_no": "PO2026081512", "supplier": WZ},
        {"doc_no": "PO2026081513", "supplier": WZ},
        {"doc_no": "PO2026081514", "supplier": WZ}]
fake = FakeCloud(select_results=[rows, hist])
app.CLOUD = fake
app._PROFILE["at"] = 0.0
by_p, glob_p = app.doc_profiles()
nb_p = patterns.pick(by_p, glob_p, NB)
check("权威档案覆盖宁波桶", nb_p.accepts("PO20261024001") and nb_p.authoritative)
wz_p = patterns.pick(by_p, glob_p, WZ)
check("未配档案的客户用历史统计", wz_p.accepts("PO2026081512"))
check("★submit 全局档案没有顶掉历史统计",
      glob_p.source != "confirmed" or not glob_p.authoritative,
      f"source={glob_p.source} auth={glob_p.authoritative}")

print(f"\n{'=' * 46}\nPASS {PASS} / FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
