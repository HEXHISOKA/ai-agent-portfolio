from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column
from sqlalchemy import create_engine, DateTime, event
from app.config import BASE_DIR, settings
from datetime import datetime
from pathlib import Path


def _is_sqlite(url: str) -> bool:
    """按 URL 的 scheme 判断后端，mysql+pymysql 与 sqlite 走不同引擎参数。"""
    return url.split("://", 1)[0].startswith("sqlite")


# 供 SQL 生成与校验使用的方言名，尽量与真实后端一致。
SQL_DIALECT = "sqlite" if _is_sqlite(settings.DATABASE_URL) else "mysql"


def _sqlite_file(url: str) -> Path:
    """把 sqlite:/// 形式的 URL 还原成数据库文件的绝对路径。"""
    raw = url.split(":///", 1)[1] if ":///" in url else url.split("://", 1)[1]
    raw = raw.split("?", 1)[0]
    path = Path(raw)
    # 相对路径统一以 backend 目录为基准，避免从不同工作目录启动写到不同文件。
    return path if path.is_absolute() else (BASE_DIR / path).resolve()


def _sqlite_connect_args() -> dict:
    # check_same_thread=False 让 FastAPI 线程池可以复用连接；
    # timeout 是 sqlite3 的锁等待秒数，并发写时排队而不是立刻报 database is locked。
    return {"check_same_thread": False, "timeout": 15}


def _sqlite_readonly_url(path: Path) -> str:
    """拼出 mode=ro 的 URI 形式。Windows 需要 file:///C:/...，POSIX 是 file:///home/...。"""
    posix = path.as_posix()
    uri = ("file://" + posix) if posix.startswith("/") else ("file:///" + posix)
    return f"sqlite:///{uri}?mode=ro&uri=true"


def _register_sqlite_pragmas(target_engine, read_only: bool = False) -> None:
    """每个新连接都要设置的 pragma。注意 journal_mode 不在这里设置。"""

    @event.listens_for(target_engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        if read_only:
            # 与文件层的 mode=ro 互为双保险，任一失效都仍能拦住写入。
            cursor.execute("PRAGMA query_only=ON")
        cursor.close()


if _is_sqlite(settings.DATABASE_URL):
    _db_file = _sqlite_file(settings.DATABASE_URL)
    _db_file.parent.mkdir(parents=True, exist_ok=True)
    # 用解析后的绝对路径建引擎，避免启动目录不同时连到另一个文件。
    engine = create_engine(
        "sqlite:///" + _db_file.as_posix(), connect_args=_sqlite_connect_args()
    )
    # journal_mode 是写进数据库文件头的持久属性，只在启动时设一次即可。
    # 若在每个连接上都执行 PRAGMA journal_mode=WAL，多处并发建连会随机失败并报
    # "attempt to write a readonly database"，因此必须单独用裸连接执行一次。
    _raw_connection = engine.raw_connection()
    try:
        _raw_connection.cursor().execute("PRAGMA journal_mode=WAL")
    finally:
        _raw_connection.close()
    _register_sqlite_pragmas(engine)
else:
    engine = create_engine(settings.DATABASE_URL)

# 数据库session会话的连接工厂
SessionLocal = sessionmaker(bind=engine, autoflush=False)


def _build_query_engine():
    """查询引擎：MySQL 用只读账号；SQLite 用 mode=ro 文件连接。"""
    url = settings.QUERY_DATABASE_URL
    # 未配置时不建查询引擎，让已有业务页面仍能启动。
    if not url:
        return None
    if not _is_sqlite(url):
        return create_engine(url, pool_pre_ping=True)

    target = _sqlite_file(url)
    if not target.exists():
        # 首次启动时数据库文件还没生成，退回普通连接，避免直接启动失败。
        query_engine = create_engine(
            "sqlite:///" + target.as_posix(), connect_args=_sqlite_connect_args()
        )
        _register_sqlite_pragmas(query_engine, read_only=True)
        return query_engine

    query_engine = create_engine(
        _sqlite_readonly_url(target),
        connect_args=_sqlite_connect_args(),
        pool_pre_ping=True,
    )
    _register_sqlite_pragmas(query_engine, read_only=True)
    return query_engine


query_engine = _build_query_engine()
# pool_pre_ping 在借出连接时检查可用性，不代表 SQL 的执行超时。
QuerySessionLocal = sessionmaker(bind=query_engine, autoflush=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class Base(DeclarativeBase):
    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="主键ID", sort_order=-1
    )
    create_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, comment="创建时间"
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )
