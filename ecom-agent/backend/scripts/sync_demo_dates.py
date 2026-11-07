"""将课程演示数据的历史日期平移到本机当天；默认仅预览。"""
import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text

from app.config import settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TABLES = ("products", "orders", "reviews")


def build_plan(tables, today):
    # 订单和评价决定演示时间范围，商品的人工复核更新时间不能成为日期基准。
    times = [row["create_time"] for table in ("orders", "reviews") for row in tables[table]]
    if not times:
        raise ValueError("没有订单或评价可同步，请先导入课程演示数据")
    anchor = max(times).date()
    days = (today - anchor).days
    offset = timedelta(days=days)
    changes = {}
    for table in TABLES:
        changes[table] = []
        for row in tables[table]:
            # 晚于演示时间范围的新商品不跟随历史数据移动，避免把新数据推到未来。
            if row["create_time"].date() > anchor:
                continue
            created = row["create_time"] + offset
            updated = row["update_time"]
            if updated.date() <= anchor:
                updated += offset
            else:
                # 后续操作的更新时间保留；至少不能早于平移后的创建时间。
                updated = max(updated, created)
            if (created, updated) != (row["create_time"], row["update_time"]):
                changes[table].append({
                    "id": row["id"],
                    "before_create_time": row["create_time"],
                    "before_update_time": row["update_time"],
                    "create_time": created,
                    "update_time": updated,
                })
    return {"reference_day": anchor, "target_day": today, "offset_days": days, "changes": changes}


def sync_dates(engine, *, apply=False, today=None, backup_dir=None):
    today = today or date.today()
    backup_dir = Path(backup_dir or BACKEND_ROOT / "data" / "demo-date-backups")
    backup = None
    # 三张表共用一个事务；任意更新失败都会回滚，不能只移动半组日期。
    with engine.begin() as conn:
        tables = {}
        for table in TABLES:
            # 写入前锁定待同步记录，保证备份与后续更新对应同一份数据。
            suffix = " FOR UPDATE" if apply else ""
            tables[table] = [dict(row) for row in conn.execute(text(
                f"SELECT id, create_time, update_time FROM `{table}` ORDER BY id{suffix}"
            )).mappings()]
        plan = build_plan(tables, today)
        if apply and any(plan["changes"].values()):
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.json"
            # 备份只保存库名、编号和日期；备份写入失败时不执行数据库更新。
            payload = {"database": engine.url.database, **plan}
            with backup.open("x", encoding="utf8") as file:
                json.dump(payload, file, ensure_ascii=False, default=str, indent=2)
            for table, rows in plan["changes"].items():
                if not rows:
                    continue
                conn.execute(text(
                    f"UPDATE `{table}` SET create_time=:create_time, "
                    "update_time=:update_time WHERE id=:id"
                ), [{key: row[key] for key in ("id", "create_time", "update_time")} for row in rows])
    return {**plan, "applied": apply, "backup": str(backup) if backup else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="备份原日期后写入；不加时只预览")
    args = parser.parse_args()

    engine = create_engine(settings.DATABASE_URL)
    try:
        result = sync_dates(engine, apply=args.apply)
    finally:
        engine.dispose()
    print(f"演示日期基准：{result['reference_day']} → {result['target_day']}")
    print(f"统一平移：{result['offset_days']:+d} 天")
    for table, rows in result["changes"].items():
        print(f"{table}：{'已更新' if args.apply else '待更新'} {len(rows)} 条")
    if result["backup"]:
        print(f"原日期备份：{result['backup']}")
    elif not any(result["changes"].values()):
        print("日期已对齐，无需更新。")
    if not args.apply:
        print("当前仅预览；确认使用课程演示库后，加 --apply 执行。")


if __name__ == "__main__":
    main()
