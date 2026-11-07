"""把 MySQL 业务库整库搬到 SQLite。

用法（在 backend 目录下）：

    # 先看会做什么，不写盘
    ./.venv/Scripts/python.exe -m scripts.migrate_to_sqlite

    # 真正执行，目标文件已存在时需加 --replace
    ./.venv/Scripts/python.exe -m scripts.migrate_to_sqlite --yes --replace

源库默认读环境变量 SOURCE_DATABASE_URL，没配则用本地 MySQL 演示库；
目标默认写 sqlite:///./data/ecom.db。两边都可以用参数覆盖。
"""

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text

# 让脚本可以脱离 uvicorn 单独运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import Base  # noqa: E402
from app.models.alert_notification import AlertNotification  # noqa: E402,F401
from app.models.diagnosis_report import DiagnosisReport  # noqa: E402,F401
from app.models.order import Order  # noqa: E402,F401
from app.models.product import Product  # noqa: E402,F401
from app.models.review import Review  # noqa: E402,F401
from app.models.user import User  # noqa: E402,F401
from app.observability import models as observability_models  # noqa: E402,F401

# 外键依赖顺序：被引用的表必须先写。
TABLES = [
    "users",
    "products",
    "orders",
    "reviews",
    "diagnosis_reports",
    "alert_notifications",
    "agent_runs",
    "agent_run_steps",
    "agent_feedbacks",
]

DEFAULT_SOURCE = "mysql+pymysql://root:mysql@127.0.0.1:3306/ecom-analysis-agent?charset=utf8mb4"
DEFAULT_TARGET = "sqlite:///./data/ecom.db"


def _sqlite_path(url: str) -> Path:
    raw = url.split(":///", 1)[1] if ":///" in url else url.split("://", 1)[1]
    raw = raw.split("?", 1)[0]
    path = Path(raw)
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def _money_total(engine, table: str, column: str) -> Decimal:
    with engine.connect() as conn:
        value = conn.execute(text(f"SELECT SUM({column}) FROM {table}")).scalar()
    if value is None:
        return Decimal("0")
    return Decimal(str(value)).quantize(Decimal("0.01"))


def main() -> int:
    parser = argparse.ArgumentParser(description="MySQL -> SQLite 数据迁移")
    parser.add_argument("--source", default=os.environ.get("SOURCE_DATABASE_URL", DEFAULT_SOURCE))
    parser.add_argument("--target", default=os.environ.get("TARGET_DATABASE_URL", DEFAULT_TARGET))
    parser.add_argument("--yes", action="store_true", help="真正写盘，不加只做试运行")
    parser.add_argument("--replace", action="store_true", help="目标文件已存在时先删除")
    args = parser.parse_args()

    target_file = _sqlite_path(args.target)
    print(f"源库  : {args.source.split('@')[-1]}")
    print(f"目标  : {target_file}")

    if target_file.exists() and not args.replace:
        print("\n目标文件已存在。确认要覆盖请加 --replace（原文件会被删除）。")
        return 2

    if not args.yes:
        print("\n[试运行] 未加 --yes，不做任何写入。")

    source = create_engine(args.source, pool_pre_ping=True)
    try:
        with source.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        print(f"\n连不上源库：{type(exc).__name__}: {str(exc)[:160]}")
        return 1

    if args.yes:
        if target_file.exists():
            for suffix in ("", "-wal", "-shm"):
                path = Path(str(target_file) + suffix)
                if path.exists():
                    path.unlink()
                    print(f"已删除旧文件 {path.name}")
        target_file.parent.mkdir(parents=True, exist_ok=True)

    target = create_engine(
        args.target, connect_args={"check_same_thread": False, "timeout": 15}
    )

    if args.yes:
        Base.metadata.create_all(bind=target)
        raw = target.raw_connection()
        try:
            raw.cursor().execute("PRAGMA journal_mode=WAL")
            raw.commit()
        finally:
            raw.close()

    counts: dict[str, int] = {}
    with source.connect() as src:
        if args.yes:
            with target.begin() as dst:
                for table in TABLES:
                    rows = [dict(r) for r in src.execute(text(f"SELECT * FROM `{table}`")).mappings()]
                    if rows:
                        dst.execute(Base.metadata.tables[table].insert(), rows)
                    counts[table] = len(rows)
                    print(f"  {table:22s} {len(rows):6d} 行")
        else:
            for table in TABLES:
                n = src.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar()
                counts[table] = int(n or 0)
                print(f"  {table:22s} {counts[table]:6d} 行（试运行，仅计数）")

    print(f"\n合计 {sum(counts.values())} 行")
    if not args.yes:
        print("加 --yes 才会真正写入。")
        return 0

    # 对账：行数 + 金额合计，两边必须一致才算迁移成功
    print("\n=== 对账 ===")
    failed = False
    for table in TABLES:
        with target.connect() as conn:
            new_n = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        flag = "OK" if new_n == counts[table] else "不一致"
        if new_n != counts[table]:
            failed = True
        print(f"  {table:22s} 源 {counts[table]:6d}  目标 {new_n:6d}  {flag}")

    for table, column in (("products", "price"), ("orders", "amount")):
        old = _money_total(source, table, column)
        new = _money_total(target, table, column)
        flag = "OK" if old == new else "不一致"
        if old != new:
            failed = True
        print(f"  {table}.{column} 合计   源 {old}  目标 {new}  {flag}")

    print("\n迁移完成。" if not failed else "\n对账有差异，请先排查再使用。")
    print(f"数据库文件大小：{target_file.stat().st_size / 1024:.0f} KB")
    return 0 if not failed else 3


if __name__ == "__main__":
    raise SystemExit(main())
