"""数据访问层。

设计目标：**零三方依赖也能跑**。
- 默认 SQLite（Python 标准库自带），单文件 `data/difu.db`，开箱即用；
- 设置 `DB_ENGINE=mysql` 且装有 pymysql 时自动切到 MySQL 8（utf8mb4），业务代码零改动；
- 所有 SQL 只写一份，`adapt()` 负责 `?` → `%s` 与自增主键方言差异。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterable

from . import config
from .schema import INDEXES, SCHEMA_SQL

_lock = threading.RLock()
_engine = "sqlite"
_conn_factory = None
_ready = False


# ------------------------------------------------------------------ 工具函数
def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def days_later(days: int) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(s: str | None) -> datetime:
    if not s:
        return datetime.now()
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return datetime.now()


def jdumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def jloads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default if default is not None else {}
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default if default is not None else {}


# ------------------------------------------------------------------ 连接管理
def adapt(sql: str) -> str:
    """把统一写法的 SQL 适配到当前引擎。"""
    if _engine == "mysql":
        sql = sql.replace("?", "%s")
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "INT AUTO_INCREMENT PRIMARY KEY")
    return sql


def _sqlite_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.SQLITE_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def _mysql_connect():
    import pymysql  # type: ignore

    conn = pymysql.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT, user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD, database=config.MYSQL_DB, charset="utf8mb4",
        autocommit=True, cursorclass=pymysql.cursors.DictCursor,
    )
    return conn


# 后加的列：老库（已经建过表）不会因为 CREATE TABLE IF NOT EXISTS 补列，
# 这里做一次幂等的「缺列就补」，避免升级后 SELECT 报 no such column。
_EXTRA_COLUMNS = {
    "judgment": [("sentence_days", "INTEGER DEFAULT 0")],
}


def _migrate(conn, engine: str) -> None:
    for table, cols in _EXTRA_COLUMNS.items():
        if engine == "sqlite":
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        else:
            cur = conn.cursor()
            cur.execute(f"SHOW COLUMNS FROM {table}")
            have = {r[0] for r in cur.fetchall()}
        for name, ddl in cols:
            if name in have:
                continue
            stmt = f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
            if engine == "sqlite":
                conn.execute(stmt)
            else:
                conn.cursor().execute(stmt)


def init() -> str:
    """初始化引擎与表结构，返回实际使用的引擎名。"""
    global _engine, _conn_factory, _ready
    with _lock:
        if _ready:
            return _engine
        if config.DB_ENGINE.lower() == "mysql":
            try:
                import pymysql  # noqa: F401

                _engine, _conn_factory = "mysql", _mysql_connect
            except Exception:                     # 降级：没装 pymysql 就回落到 sqlite
                _engine, _conn_factory = "sqlite", _sqlite_connect
        else:
            _engine, _conn_factory = "sqlite", _sqlite_connect

        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = _conn_factory()
        try:
            for ddl in SCHEMA_SQL + INDEXES:
                if _engine == "mysql":
                    ddl = ddl.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "INT AUTO_INCREMENT PRIMARY KEY")
                if _engine == "sqlite":
                    conn.execute(ddl)
                else:
                    conn.cursor().execute(ddl)
            _migrate(conn, _engine)
            if _engine != "sqlite":
                conn.commit()
        finally:
            conn.close()
        _ready = True
        return _engine


def engine() -> str:
    return _engine


def _cursor(conn):
    return conn.cursor()


# ------------------------------------------------------------------ 基本读写
def query(sql: str, params: Iterable | None = None) -> list[dict]:
    sql = adapt(sql)
    conn = _conn_factory()
    try:
        if _engine == "sqlite":
            cur = conn.execute(sql, tuple(params or ()))
            rows = [dict(r) for r in cur.fetchall()]
        else:
            cur = conn.cursor()
            cur.execute(sql, tuple(params or ()))
            rows = list(cur.fetchall())
        return rows
    finally:
        conn.close()


def query_one(sql: str, params: Iterable | None = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable | None = None) -> int:
    """返回受影响行数；INSERT 时返回新主键（sqlite 用 lastrowid，mysql 用 lastrowid）。"""
    sql = adapt(sql)
    conn = _conn_factory()
    try:
        cur = conn.execute(sql, tuple(params or ())) if _engine == "sqlite" else conn.cursor()
        if _engine != "sqlite":
            cur.execute(sql, tuple(params or ()))
        rowcount = cur.rowcount
        lastid = getattr(cur, "lastrowid", None)
        conn.commit()                     # SQLite 默认隐式事务，必须显式提交
        return int(lastid or rowcount or 0)
    finally:
        conn.close()


def count(table: str, where: str = "", params: Iterable | None = None) -> int:
    sql = f"SELECT COUNT(*) AS c FROM {table}" + (f" WHERE {where}" if where else "")
    row = query_one(sql, params)
    return int(row["c"]) if row else 0


def insert(table: str, data: dict) -> int:
    cols = list(data.keys())
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    return execute(sql, [data[c] for c in cols])


def update(table: str, data: dict, where: str, params: Iterable | None = None) -> int:
    sets = ",".join(f"{k}=?" for k in data)
    sql = f"UPDATE {table} SET {sets} WHERE {where}"
    return execute(sql, list(data.values()) + list(params or ()))


def delete(table: str, where: str, params: Iterable | None = None) -> int:
    return execute(f"DELETE FROM {table} WHERE {where}", params)


def get(table: str, rid: int) -> dict | None:
    return query_one(f"SELECT * FROM {table} WHERE id=?", (rid,))


def all_rows(table: str, where: str = "", params: Iterable | None = None,
             order: str = "id DESC", limit: int = 0, offset: int = 0) -> list[dict]:
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    if order:
        sql += f" ORDER BY {order}"
    if limit:
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    return query(sql, params)


@contextmanager
def tx():
    """事务上下文（SQLite 下为真事务，MySQL 下为连接级事务）。"""
    conn = _conn_factory()
    try:
        if _engine != "sqlite":
            conn.begin()
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


BUSINESS_TABLES = (
    "case_file", "deceased", "evidence", "judgment", "execution", "reincarnation",
    "approval", "agent_log", "audit_log", "notification", "knowledge_chunk",
    "knowledge_doc", "chat_message", "case_note", "tool_call", "checkpoint",
)


def shutdown() -> None:
    """优雅退出收尾：把 WAL 里的内容 checkpoint 回主库文件。

    🔴 原来 stop.sh 发 SIGTERM 时 Python 直接被杀，finally 不执行，
       WAL 没落盘（数据没丢，但 `data/difu.db` 单文件拷走会是旧快照）。
    """
    if _engine != "sqlite":
        return
    try:
        conn = sqlite3.connect(config.SQLITE_PATH, timeout=15)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    except Exception as exc:
        print(f"[停止] WAL checkpoint 失败：{exc}", flush=True)


def backup(dest_path: str) -> str:
    """在线备份 SQLite（用官方 backup API，WAL 模式下也保证一致），返回路径。

    /api/demo/reset 这种「一键清 16 张表」的操作，执行前必须先留一份可回滚的快照。
    """
    if _engine != "sqlite":
        return ""
    from pathlib import Path
    target = Path(dest_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(config.SQLITE_PATH, timeout=15)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)                 # 官方在线备份 API，WAL 下也是一致快照
        dst.commit()
    finally:
        dst.close()
        src.close()
    return str(target)


def reset_db() -> None:
    """演示用：只清业务数据，保留账号 / 角色 / 字典 / 模型配置。

    保留 user 表是有意的：账号 id 不变，重置后当前登录态仍然有效，
    不会把人踢回登录页；而 role/jurisdiction/sys_dict/model_config
    属于基础配置，重建一次反而会冲掉用户改过的配置。

    🔴 原来逐条 `execute(DELETE)` 各自开连接、各自提交，中途失败就是半清状态。
       现在整套删除放进一个事务，要么全清、要么全不清。
    """
    with tx() as conn:
        for table in BUSINESS_TABLES:
            if _engine == "sqlite":
                conn.execute(f"DELETE FROM {table}")
            else:
                conn.cursor().execute(f"DELETE FROM {table}")
