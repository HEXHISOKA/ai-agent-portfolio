"""清空台账里的测试数据 —— **只能由人在这台机器上手工执行**。

护栏说明（2026-10-29 立的规矩：改文档必须有人工确认）：
- 这是本地运维工具，**不属于 agent 的能力范围**。任何自动化流程、脚本、AI 都不该
  调用它；agent 改表只能走工作台的"核对 → 确认"两段式接口。
- 因此它强制要求**在终端里手工敲 DELETE**。没有交互终端就直接退出，
  这样它天然没法被批处理、被非交互调用。
- 跑之前自动落一份备份到发布目录之外，删完回读校验。

用法（人工）：
    python tools/clear_sheet.py                # 预演：只列出会被删的记录
    python tools/clear_sheet.py --yes          # 真删（仍会要求手工输入 DELETE）
    python tools/clear_sheet.py --sheet order  # 只清订单表
    python tools/clear_sheet.py --restore backups/xxx.json   # 从备份恢复
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import tdoc_oauth  # noqa: E402

# 备份落在**发布目录之外**：发布会把整个项目目录上传，备份没必要跟着上去。
BACKUP_DIR = BASE.parent / "_table_backups"

BATCH = 20          # 单次删多少条。官方没给上限，宁可多打几次。
SHEETS = {
    "product": ("商品信息", "product"),
    "order": ("下单信息", "order"),
}


def sheet_cfg(key: str) -> tuple:
    with open(BASE / "config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    label, ck = SHEETS[key]
    d = cfg["tdoc"][ck]
    return label, d["file_id"], d["sheet_id"], cfg["fields"]


def flat(rec: dict) -> dict:
    """把 field_values 摊平成 {字段名: 值}，只用于打印和备份可读性。"""
    out = {}
    for fv in rec.get("field_values") or []:
        v = fv.get("number_value")
        if v is None:
            tv = fv.get("text_value")
            if isinstance(tv, dict):
                v = " / ".join(x.get("text", "") for x in tv.get("items") or [])
            else:
                v = tv
        if v not in (None, ""):
            out[fv.get("field")] = v
    return out


def dump(key: str) -> dict:
    label, fid, sid, _ = sheet_cfg(key)
    recs = tdoc_oauth.list_records(fid, sid)
    return {"sheet": key, "label": label, "file_id": fid, "sheet_id": sid,
            "count": len(recs), "records": recs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="真的删。不加只是预演")
    ap.add_argument("--sheet", choices=["all", "order", "product"], default="all")
    ap.add_argument("--restore", default="", help="从备份 json 恢复到表里")
    a = ap.parse_args()

    if a.restore:
        return restore(a.restore)

    keys = ["order", "product"] if a.sheet == "all" else [a.sheet]

    # 1) 先全部读一遍并落盘。删之前一定要有一份能倒回去的东西。
    snaps, total = {}, 0
    for k in keys:
        s = dump(k)
        snaps[k] = s
        total += s["count"]

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = BACKUP_DIR / f"ledger-{stamp}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"taken_at": stamp, "sheets": snaps}, f, ensure_ascii=False, indent=1)

    for k in keys:
        s = snaps[k]
        print(f"\n【{s['label']}】{s['file_id']}/{s['sheet_id']}　共 {s['count']} 条")
        for i, r in enumerate(s["records"]):
            f = flat(r)
            # 只打几个能一眼认出货的字段，打在终端上是为了让人核对"要删的是不是这些"
            brief = " | ".join(f"{kk}={f[kk]}" for kk in
                               ("商品名称", "下单价", "快递单号", "到货地址", "商品")
                               if kk in f)
            print(f"  {i + 1:>2}. {r['record_id']}  {brief}")

    print(f"\n备份已写：{path}")

    if not a.yes:
        print(f"\n预演结束，一条都没删。确认上面 {total} 条都是测试数据后，加 --yes 再跑一次。")
        return 0

    # 硬性人工确认：没有交互终端直接拒绝，杜绝被脚本/AI 非交互调用。
    if not sys.stdin.isatty():
        print("\n拒绝执行：删表必须在交互终端里由人手工确认，当前不是交互环境。")
        return 3
    print(f"\n即将删除 {total} 条记录，且不可恢复（有备份，但恢复要手工跑）。")
    print("确认请输入 DELETE（区分大小写）：", end="", flush=True)
    if input().strip() != "DELETE":
        print("输入不匹配，已取消，什么都没删。")
        return 3

    # 2) 真删。订单先删：商品表那边有指向订单的关联字段，反过来删会留悬空引用。
    for k in keys:
        s = snaps[k]
        ids = [r["record_id"] for r in s["records"] if r.get("record_id")]
        for i in range(0, len(ids), BATCH):
            chunk = ids[i:i + BATCH]
            tdoc_oauth.delete_records(s["file_id"], s["sheet_id"], chunk)
            print(f"  已删 {s['label']} {min(i + BATCH, len(ids))}/{len(ids)}")

    # 3) 删完必须回读确认。接口返回 ret=0 不代表真删了，表里剩下的才是事实。
    print("\n回读校验：")
    left = 0
    for k in keys:
        label, fid, sid, _ = sheet_cfg(k)
        n = len(tdoc_oauth.list_records(fid, sid))
        left += n
        print(f"  {label}：剩 {n} 条")
    print("\n清空完成。" if not left else f"\n还有 {left} 条没删掉，需要人工看。")
    print(f"要恢复：python tools/clear_sheet.py --restore {path}")
    return 0 if not left else 1


def restore(path: str) -> int:
    p = Path(path)
    if not p.exists():
        print(f"找不到备份文件：{p}")
        return 2
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    for k, s in (data.get("sheets") or {}).items():
        recs = s.get("records") or []
        if not recs:
            continue
        rows = [{"field_values": r.get("field_values") or []} for r in recs]
        tdoc_oauth.add_records(s["file_id"], s["sheet_id"], rows)
        print(f"  {s['label']}：写回 {len(rows)} 条")
    print("恢复完成。关联字段（商品↔订单）的 recordID 变了，需要在界面上重新挂一次。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
