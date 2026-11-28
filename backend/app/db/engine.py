import threading
from functools import wraps
from pathlib import Path

from sqlalchemy import Engine, event
from sqlmodel import SQLModel, create_engine

from app.db import models  # noqa: F401  导入后表定义才会注册到 SQLModel.metadata

_write_lock = threading.Lock()


def serialized(function):
    """让「先查有没有、没有再写」的操作排队执行。

    接口和工具都可能同时收到两个一样的请求（重复点击、Agent 并行调用同一个工具）。
    不排队的话，两边都查到「没有」，然后各写一条。迁跃只有一个后端进程，进程内的锁就够了。
    被装饰的函数要在返回前提交。
    """

    @wraps(function)
    def wrapper(*args, **kwargs):
        with _write_lock:
            return function(*args, **kwargs)

    return wrapper


def create_db_engine(db_path: Path) -> Engine:
    """打开数据库并建表。表已存在时不做任何改动。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 同步路由在线程池里执行，同一个连接会被不同线程使用，所以关掉同线程检查。
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")  # SQLite 默认不检查外键
        cursor.execute("PRAGMA journal_mode=WAL")  # 读写互不阻塞
        cursor.close()

    SQLModel.metadata.create_all(engine)
    # create_all 只建新表。给已有工作区补上本次设置项，保留原有配置和材料。
    with engine.begin() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(settings)")}
        if "hide_personal_info" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE settings ADD COLUMN hide_personal_info BOOLEAN NOT NULL DEFAULT 1"
            )
        if "privacy_terms" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE settings ADD COLUMN privacy_terms TEXT NOT NULL DEFAULT ''"
            )
    return engine
