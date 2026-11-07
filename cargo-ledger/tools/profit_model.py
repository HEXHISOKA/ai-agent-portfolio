"""利润与承受能力估算（只读表，一个字都不写）。

为什么需要它：台账能告诉你"这单亏不亏"，但老板真正要问的是
"一个月下来赚多少、要做到多少单才不亏"。这两个数不能拍脑袋，
得把单件净利、坏件率、运费、固定开销一起算。

先说明口径，别拿估数当账：
   单件净利 = 回收价 − 采购价 − 运费 − 采购价 × 坏件率
   - 回收价：优先取订单上的「回收价」；没有这一列就用商品表当前「行情价」替代。
     替代是有代价的——行情是今天价，订单是历史价，跨几天行情就变了，
     所以输出里会标"用行情价代用 X 单"，这个数越大结论越不可信。
   - 运费：默认按我方每件承担 --freight 元；买家出运费的场景填 0。
   - 坏件率：按采购额全损，不含运费损失（保守起来可以调高）。

用法：
    python tools/profit_model.py                          # 默认假设，跑表里现有数据
    python tools/profit_model.py --freight 0 --loss-rate 0.01
    python tools/profit_model.py --fixed 12000 --units 80 --json out.json
    python tools/profit_model.py --scenario               # 只出敏感性矩阵

所有默认值都是 TODO 待校准，不是结论。
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402

# TODO 待校准：这三个数最影响结论，拿到真实账单后改这里
DEFAULTS = {
    "freight": 8.0,          # 每件我方承担的运费（买家付就填 0）
    "loss_rate": 0.03,       # 坏件/退货/压货损失率
    "fixed_monthly": 8000.0,  # 房租 + 人工 + 水电等月固定开销
}

MISSING_NEEDED = [
    "订单表加一列「回收价」——实际卖出价。没有它，利润只能用商品表今天的行情价代，"
    "跨天就会错",
    "运费谁承担、每件多少（这是个体户最容易漏算的一块）",
    "退货/坏件/压货的比例（拿三个月真实单量回看）",
    "月固定开销：房租、人工、水电、平台服务费",
    "平台优惠券/补贴是否已经体现在「下单价」里（没体现就要单独加回来）",
]


def _match(prods, name):
    """订单的「关联商品」和商品表的「商品名称」写法常常不一样，两步匹配。"""
    key = app._norm_text(name)
    for p in prods:
        if app._norm_text(p["name"]) == key:
            return p
    for p in prods:
        k = app._norm_text(p["name"])
        if k and (k in key or key in k):
            return p
    return None


def build_rows(prods, orders, freight, loss_rate):
    rows, skip = [], {"无下单价": 0, "无关联商品": 0, "匹配不到商品": 0,
                      "商品没行情价": 0, "已退回": 0}
    ignored = set(app.CFG["rules"]["ignore_status"])
    for o in orders:
        if o["status"] == "退回":
            skip["已退回"] += 1
            continue
        if o["price"] is None:
            skip["无下单价"] += 1
            continue
        if not (o["product"] or "").strip():
            skip["无关联商品"] += 1
            continue
        p = _match(prods, o["product"])
        if not p:
            skip["匹配不到商品"] += 1
            continue
        qty = o["quantity"] or 1
        # 优先用订单上的实际回收价（字段存在才算数），否则退回商品表行情价
        sell = o.get("sell_price")
        src = "订单回收价"
        if sell is None:
            sell = p["quote"]
            src = "行情价代用"
        if sell is None:
            skip["商品没行情价"] += 1
            continue
        buy = o["price"]
        unit_net = sell - buy - freight - buy * loss_rate
        rows.append({
            "record_id": o["record_id"],
            "product": o["product"],
            "qty": qty,
            "buy": buy,
            "sell": sell,
            "sell_src": src,
            "freight": freight,
            "unit_gap": round(sell - buy, 2),
            "unit_net": round(unit_net, 2),
            "total_net": round(unit_net * qty, 2),
            "status": o["status"] or "未填状态",
        })
    return rows, skip


def summarize(rows, fixed_monthly, units_month, freight, loss_rate):
    units = sum(r["qty"] for r in rows)
    buy_amt = sum(r["buy"] * r["qty"] for r in rows)
    sell_amt = sum(r["sell"] * r["qty"] for r in rows)
    net = sum(r["total_net"] for r in rows)
    unit_avg = net / units if units else 0.0
    # 月单量：优先用 CLI 给的假设，否则用表里这批的实际件数（代表"一期"而非"一月"）
    per_month = units_month if units_month else units
    month_net = unit_avg * per_month - fixed_monthly
    be_units = (fixed_monthly / unit_avg) if unit_avg > 0 else None
    return {
        "orders": len(rows),
        "units": units,
        "buy_amount": round(buy_amt, 2),
        "sell_amount": round(sell_amt, 2),
        "net_profit_batch": round(net, 2),
        "net_margin_pct": round(net / sell_amt * 100, 2) if sell_amt else None,
        "unit_net_avg": round(unit_avg, 2),
        "units_per_month_assumed": per_month,
        "fixed_monthly": fixed_monthly,
        "net_profit_month": round(month_net, 2),
        "break_even_units": None if be_units is None else round(be_units, 1),
        "verdict": (
            "数据不足，无法判断" if not rows else
            ("按现价亏钱，单量越大亏越多" if unit_avg <= 0 else
             (f"每月做不到 {be_units:.0f} 件就亏" if month_net < 0 else "按当前假设是赚的"))
        ),
    }


def scenario(rows, fixed_monthly, units_month):
    """坏件率 × 运费 的敏感性矩阵。单件净利对这两个参数最敏感。"""
    units = units_month or sum(r["qty"] for r in rows)
    if not rows:
        return []
    out = []
    for lr in (0.0, 0.02, 0.05, 0.10):
        line = {"loss_rate": lr, "by_freight": {}}
        for fr in (0, 5, 8, 12):
            # 每件平均净利 × 月件数 − 固定开销
            avg_unit = sum((r["sell"] - r["buy"] - fr - r["buy"] * lr) * r["qty"] for r in rows) / max(
                1, sum(r["qty"] for r in rows))
            line["by_freight"][str(fr)] = round(avg_unit * units - fixed_monthly, 0)
        out.append(line)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--freight", type=float, default=DEFAULTS["freight"])
    ap.add_argument("--loss-rate", type=float, default=DEFAULTS["loss_rate"])
    ap.add_argument("--fixed", type=float, default=DEFAULTS["fixed_monthly"])
    ap.add_argument("--units", type=float, default=None, help="月件数假设；不给就用表里实际件数")
    ap.add_argument("--scenario", action="store_true")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    prods = app.read_products()
    orders = app.read_orders()
    rows, skip = build_rows(prods, orders, a.freight, a.loss_rate)

    print(f"商品 {len(prods)} 个 / 订单 {len(orders)} 行 → 可算 {len(rows)} 单"
          f"（{sum(r['qty'] for r in rows)} 件）")
    if any(skip.values()):
        print("跳过：" + "，".join(f"{k} {v}" for k, v in skip.items() if v))
    est = sum(1 for r in rows if r["sell_src"] == "行情价代用")
    if est:
        print(f"⚠ 其中 {est} 单的回收价是用商品表「行情价」代的，不是实际卖出价 —— 结论只能当量级看")

    if not rows:
        print("\n没有可算的订单。需要先补的数据：")
        for i, m in enumerate(MISSING_NEEDED, 1):
            print(f"  {i}. {m}")
        return 1

    s = summarize(rows, a.fixed, a.units, a.freight, a.loss_rate)
    print("\n== 单件 ==")
    print(f"  平均采购 {s['buy_amount'] / s['units']:.2f} → 平均回收 {s['sell_amount'] / s['units']:.2f}")
    print(f"  平均单件净利 {s['unit_net_avg']:.2f} 元（已扣运费 {a.freight:g} 和坏件率 {a.loss_rate * 100:g}%）")
    print(f"  净利率 {s['net_margin_pct']}%")

    print("\n== 每月 ==")
    print(f"  假设月件数 {s['units_per_month_assumed']:g}，固定开销 {a.fixed:g} 元 → 净利 {s['net_profit_month']:.0f} 元")
    if s["break_even_units"]:
        print(f"  盈亏平衡：每月至少 {s['break_even_units']:.0f} 件")
    print(f"  判断：{s['verdict']}")

    profit = sorted([r for r in rows if r["unit_net"] > 0], key=lambda r: -r["unit_net"])
    loss = sorted([r for r in rows if r["unit_net"] <= 0], key=lambda r: r["unit_net"])
    if profit:
        print("\n== 最赚的 5 单 ==")
        for r in profit[:5]:
            print(f"  {r['product'][:24]} {r['qty']:g} 件　买 {r['buy']:g} → 卖 {r['sell']:g}"
                  f"　单件净 {r['unit_net']:+.0f}　({r['sell_src']})")
    if loss:
        print("\n== 亏的 / 白干的 ==")
        for r in loss[:8]:
            print(f"  {r['product'][:24]} {r['qty']:g} 件　买 {r['buy']:g} → 卖 {r['sell']:g}"
                  f"　单件净 {r['unit_net']:+.0f}　{r['status']}")

    sc = scenario(rows, a.fixed, a.units)
    if sc:
        print("\n== 敏感性：月净利（行=坏件率，列=每件运费）==")
        print("   坏件率    运费0     5        8        12")
        for line in sc:
            cells = "  ".join(f"{line['by_freight'][k]:>9.0f}" for k in ("0", "5", "8", "12"))
            print(f"   {line['loss_rate'] * 100:>5.0f}%   {cells}")

    payload = {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "assumptions": {
        "freight": a.freight, "loss_rate": a.loss_rate, "fixed_monthly": a.fixed,
        "units_month": a.units}, "summary": s, "skipped": skip, "rows": rows,
        "scenario": sc, "missing_data": MISSING_NEEDED}
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"\n已落盘 {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
