# -*- coding: utf-8 -*-
"""双后端数据库适配层（SQLite / PostgreSQL）。

阶段二（2026-09）：把 4 个持久化模块从"直连 sqlite3"改为经由本模块连接，
使同一份业务代码可以通过环境变量指向 SQLite 文件或 PostgreSQL DSN：

- LSX_DB_WHITELIST / LSX_DB_CONVERSATIONS / LSX_DB_QUERY_LOG / LSX_DB_QUIZ
- 值以 postgres:// 或 postgresql:// 开头 → PostgreSQL（psycopg 3）
- 其它值 → SQLite 文件路径
- 未设置时回落到各模块现有的 SQLite 默认路径（行为与切换前一致）

适配层只做三件事（不做 ORM、不做方言翻译魔法）：
1. 连接创建（集中在此，便于将来换连接池）
2. 占位符（PG 侧 ? → %s）
3. 行对象（PG 侧同时支持按列名 / 按序号取值）

并发模型：每个线程一份底层连接（threading.local）。
app/ 里有大量 sync def 端点，FastAPI 会把它们丢进线程池；
psycopg 连接不支持多线程并发使用，SQLite 侧保持同样的线程局部模型，
配合 check_same_thread=False / WAL / busy_timeout=5000 / timeout=10。
PG 侧不引入 ORM / SQLAlchemy / 连接池（阶段二边界）。
"""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator, Sequence

# 关于「死锁重试兜底」的结论（2026-09-23 复核后移除）：
# PG 对 40P01（deadlock_detected）/ 40001（serialization_failure）的处理是
# 整个事务被 abort（savepoint 一并销毁），无法用 ROLLBACK TO SAVEPOINT 恢复
# 后重放单条语句；语句级重试必然以 25P02 告终。防死锁只能靠根因修复
# （conversations.py 等的 DDL 双检锁），这里不再保留无效的语句级重试。

try:  # psycopg 为可选依赖：纯 SQLite 部署 / 测试环境无需安装
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - 无 PG 时仅禁用 PG 分支
    psycopg = None
    dict_row = None

__all__ = [
    "Error", "OperationalError", "IntegrityError", "ProgrammingError",
    "connect", "is_pg_target", "table_columns", "Row",
]

# --- 统一异常（两种后端都翻译到这一组，业务侧只 import 这里） ------------------


class Error(Exception):
    """数据库操作错误基类。"""


class OperationalError(Error):
    """执行期错误（列已存在 / 表已存在 / 连接问题等）。"""


class IntegrityError(Error):
    """约束冲突（主键 / 唯一键）。"""


class ProgrammingError(Error):
    """SQL 语法 / 用法错误。"""


def _translate_sqlite_error(exc: sqlite3.Error) -> Error:
    if isinstance(exc, sqlite3.OperationalError):
        return OperationalError(str(exc))
    if isinstance(exc, sqlite3.IntegrityError):
        return IntegrityError(str(exc))
    if isinstance(exc, sqlite3.ProgrammingError):
        return ProgrammingError(str(exc))
    return Error(str(exc))


def _translate_pg_error(exc: Any) -> Error:
    """psycopg 异常 → 统一异常。按 SQLSTATE 类别对齐 SQLite 语义：

    - 23 类（约束冲突）→ IntegrityError
    - 42 类（缺表/缺列/重复列/语法错等）→ OperationalError
      （SQLite 对这些场景一律抛 OperationalError，业务迁移依赖此语义）
    """
    # psycopg3 异常类型自带 sqlstate 类属性（pgcode 在部分路径下为 None）
    code = str(getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", "") or "")
    if code.startswith("23"):
        return IntegrityError(str(exc))
    if code.startswith("42"):
        return OperationalError(str(exc))
    op = getattr(psycopg, "OperationalError", None)
    integrity = getattr(psycopg, "IntegrityError", None)
    prog = getattr(psycopg, "ProgrammingError", None)
    if op is not None and isinstance(exc, op):
        return OperationalError(str(exc))
    if integrity is not None and isinstance(exc, integrity):
        return IntegrityError(str(exc))
    if prog is not None and isinstance(exc, prog):
        return ProgrammingError(str(exc))
    return Error(str(exc))


# --- 行对象（PG 侧）：按列名 / 按序号取值，兼容 dict(row) --------------------


class Row:
    """PG 查询行：sqlite3.Row 的等价物（dict_row + 序号访问）。"""

    __slots__ = ("_data", "_keys")

    def __init__(self, data: dict):
        self._data = dict(data)
        self._keys = tuple(self._data.keys())

    def keys(self) -> tuple:
        return self._keys

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._data[self._keys[key]]
        return self._data[key]

    def __iter__(self) -> Iterator[Any]:
        # 与 sqlite3.Row 对齐：迭代产出值（不是列名）
        return iter(self._data.values())

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Row):
            return self._data == other._data
        if isinstance(other, dict):
            return self._data == other
        return NotImplemented

    def __repr__(self) -> str:
        return f"Row({self._data!r})"


# --- PG 游标包装 -------------------------------------------------------------


class _PGCursor:
    def __init__(self, cur: Any):
        self._cur = cur

    def fetchone(self) -> Row | None:
        row = self._cur.fetchone()
        return Row(row) if row is not None else None

    def fetchall(self) -> list[Row]:
        return [Row(r) for r in self._cur.fetchall() or []]

    def __iter__(self) -> Iterator[Row]:
        for row in self._cur:
            yield Row(row)

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def close(self) -> None:
        self._cur.close()


def _to_pg_sql(sql: str) -> str:
    """占位符 ? → %s。

    本项目 SQL 中 ? 只作占位符使用（已全量核查，无字符串字面量含 ?），
    因此全局替换安全。禁止拼接 SQL 的约定不变。
    """
    return sql.replace("?", "%s")


def _split_statements(script: str) -> list[str]:
    """按 ; 拆分 SQL 脚本（本仓库 schema 无字符串内分号，直接拆分安全）。"""
    return [s.strip() for s in script.split(";") if s.strip()]


class _PGConnection:
    """PG 连接包装：线程局部底层连接，对外表现为可随意传递的单个连接对象。"""

    backend = "postgres"

    def __init__(self, target: str):
        if psycopg is None:
            raise Error(
                "psycopg 未安装，无法连接 PostgreSQL（pip install 'psycopg[binary]'）"
            )
        self._target = target
        self._local = threading.local()

    def _conn(self) -> Any:
        conn = getattr(self._local, "conn", None)
        if conn is None or conn.closed:
            conn = psycopg.connect(self._target, row_factory=dict_row)
            self._local.conn = conn
        return conn

    def execute(self, sql: str, params: Sequence | None = None) -> _PGCursor:
        """语句级 savepoint 执行：单条失败只回滚该语句，不毒化整个事务。

        SQLite 里单条语句失败不影响同事务的前后语句；PG 事务一旦 abort
        后续全部拒绝。为对齐两边的迁移/幂等语义（如"列已存在则跳过"），
        每条语句包一层 SAVEPOINT，失败时 ROLLBACK TO SAVEPOINT 再抛统一异常。
        """
        conn = self._conn()
        sp_no = getattr(self._local, "sp_no", 0) + 1
        self._local.sp_no = sp_no
        sp = "_lsx_sp_%d" % sp_no
        try:
            conn.execute("SAVEPOINT " + sp)
        except psycopg.Error as exc:  # type: ignore[union-attr]
            raise _translate_pg_error(exc) from exc
        try:
            cur = conn.execute(_to_pg_sql(sql), params if params else None)
        except psycopg.Error as exc:  # type: ignore[union-attr]
            try:
                conn.execute("ROLLBACK TO SAVEPOINT " + sp)
            except psycopg.Error:  # pragma: no cover - 回滚失败则让事务自然中止
                pass
            raise _translate_pg_error(exc) from exc
        try:
            conn.execute("RELEASE SAVEPOINT " + sp)
        except psycopg.Error as exc:  # type: ignore[union-attr]
            raise _translate_pg_error(exc) from exc
        return _PGCursor(cur)

    def executescript(self, script: str) -> None:
        for stmt in _split_statements(script):
            self.execute(stmt)

    def commit(self) -> None:
        try:
            self._conn().commit()
        except psycopg.Error as exc:  # type: ignore[union-attr]
            raise _translate_pg_error(exc) from exc

    def rollback(self) -> None:
        try:
            conn = getattr(self._local, "conn", None)
            if conn is not None and not conn.closed:
                conn.rollback()
        except psycopg.Error as exc:  # type: ignore[union-attr]
            raise _translate_pg_error(exc) from exc

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None and not conn.closed:
            conn.close()
        self._local.conn = None

# --- SQLite 包装：逐字保留现有连接行为 ----------------------------------------


class _SQLiteConnection:
    """SQLite 连接包装：线程局部底层连接 + 现有 PRAGMA 行为原样保留。"""

    backend = "sqlite"

    def __init__(self, target: str):
        self._target = target
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            try:
                path = Path(self._target)
                path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
            except sqlite3.Error as exc:
                # 建连/PRAGMA 失败同样走统一异常，避免裸 sqlite3.OperationalError 泄漏
                raise _translate_sqlite_error(exc) from exc
            self._local.conn = conn
        return conn

    def execute(self, sql: str, params: Sequence | None = None) -> Any:
        try:
            if params is None:
                return self._conn().execute(sql)
            return self._conn().execute(sql, params)
        except sqlite3.Error as exc:
            raise _translate_sqlite_error(exc) from exc

    def executescript(self, script: str) -> None:
        try:
            self._conn().executescript(script)
        except sqlite3.Error as exc:
            raise _translate_sqlite_error(exc) from exc

    def commit(self) -> None:
        try:
            self._conn().commit()
        except sqlite3.Error as exc:
            raise _translate_sqlite_error(exc) from exc

    def rollback(self) -> None:
        try:
            conn = getattr(self._local, "conn", None)
            if conn is not None:
                conn.rollback()
        except sqlite3.Error as exc:
            raise _translate_sqlite_error(exc) from exc

    def close(self) -> None:
        try:
            conn = getattr(self._local, "conn", None)
            if conn is not None:
                conn.close()
        except sqlite3.Error as exc:
            raise _translate_sqlite_error(exc) from exc
        finally:
            self._local.conn = None


# 同一 target 复用同一个包装（内部已线程局部化），避免每线程重复建 wrapper。
_registry: dict = {}
_registry_lock = threading.Lock()


def is_pg_target(target: object) -> bool:
    """postgres:// / postgresql:// 开头 → PG；其余按 SQLite 文件路径处理。"""
    value = str(target or "").strip()
    return value.startswith("postgres://") or value.startswith("postgresql://")


def connect(target: object) -> "_SQLiteConnection | _PGConnection":
    """按 target 自动选择后端，返回可跨线程传递的连接包装。"""
    key = str(target).strip()  # 归一空白，防 DSN 带尾随空格被误判为文件路径
    with _registry_lock:
        conn = _registry.get(key)
        if conn is None:
            conn = _PGConnection(key) if is_pg_target(key) else _SQLiteConnection(key)
            _registry[key] = conn
        return conn


def table_columns(conn: "_SQLiteConnection | _PGConnection", table: str) -> list:
    """等价 PRAGMA table_info：返回 [{name, notnull}]（notnull: 1/0）。

    SQLite 直接查 pragma；PG 查 information_schema（限定当前 schema）。
    只取本仓库用到的两个字段，避免把两套目录结构暴露给业务模块。
    """
    if conn.backend == "sqlite":
        ident = re.sub(r"[^A-Za-z0-9_]", "", table)
        rows = conn.execute("PRAGMA table_info(%s)" % ident).fetchall()
        return [{"name": r["name"], "notnull": r["notnull"]} for r in rows]
    rows = conn.execute(
        "SELECT column_name, CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull "
        "FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s "
        "ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    return [{"name": r["column_name"], "notnull": r["notnull"]} for r in rows]