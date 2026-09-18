# -*- coding: utf-8 -*-
"""SQLite → PostgreSQL 一次性数据迁移脚本（服务器停机窗口内执行）。

用法（服务器侧）：
    python backend/scripts/migrate_sqlite_to_pg.py --dry-run        # 默认，只读预演
    python backend/scripts/migrate_sqlite_to_pg.py --apply          # 真实迁移

DSN 来源（二选一，缺一报错）：
    --dsn-map '{"whitelist": "postgresql://...", ...}'
    或环境变量 LSX_DB_WHITELIST / LSX_DB_CONVERSATIONS / LSX_DB_QUERY_LOG / LSX_DB_QUIZ

硬约束：
- 只读 SQLite（mode=ro 打开，绝不写入/删除/重命名任何 backend/data 文件）
- 默认 dry-run；不加 --apply 绝不写 PG
- 逐表行数 + 校验和双比对，任一不一致退出码非 0
- 幂等：--apply 先 TRUNCATE 目标表再全量插入，重复执行结果一致
- 迁移完成后 setval 重置所有 IDENTITY 序列到 max(id)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.persistence import (  # noqa: E402
    conversations,
    db,
    query_log,
    quiz_db,
    whitelist_db,
)

DB_KEYS = ("whitelist", "conversations", "query_log", "quiz")
SQLITE_FILES = {
    "whitelist": "whitelist.db",
    "conversations": "conversations.db",
    "query_log": "query_log.db",
    "quiz": "quiz.db",
}
ENV_VARS = {
    "whitelist": "LSX_DB_WHITELIST",
    "conversations": "LSX_DB_CONVERSATIONS",
    "query_log": "LSX_DB_QUERY_LOG",
    "quiz": "LSX_DB_QUIZ",
}
PG_SCHEMAS = {
    "whitelist": [whitelist_db._SCHEMA_PG, whitelist_db._AUDIT_SCHEMA_PG],
    "conversations": [conversations._SCHEMA_PG],
    "query_log": [query_log._SCHEMA_PG],
    "quiz": [quiz_db._SCHEMA_PG],
}


def _sqlite_readonly(path: Path) -> sqlite3.Connection:
    """只读打开 SQLite，从物理上保证脚本不可能写坏源库。"""
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_tables(conn: sqlite3.Connection) -> list:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def _pg_tables(pg) -> set:
    rows = pg.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = current_schema()"
    ).fetchall()
    return {r["table_name"] for r in rows}


def _canon(v) -> str:
    """行值规范化：NULL / int / float / str 稳定序列化，供校验和比对。"""
    if v is None:
        return "\\N"
    if isinstance(v, bool):
        return "i1" if v else "i0"
    if isinstance(v, int):
        return "i%d" % v
    if isinstance(v, float):
        return "f%r" % v
    return "s:" + str(v)


def _checksum(rows) -> str:
    """顺序无关校验和：逐行规范化后排序再 md5，两端排序规则无需一致。"""
    lines = sorted("|".join(_canon(v) for v in row) for row in rows)
    return hashlib.md5("\n".join(lines).encode("utf-8")).hexdigest()


def _resolve_dsns(args) -> dict:
    dsn_map: dict = {}
    if args.dsn_map:
        dsn_map = json.loads(args.dsn_map)
    for key in DB_KEYS:
        if not dsn_map.get(key):
            env = os.environ.get(ENV_VARS[key])
            if env:
                dsn_map[key] = env
    missing = [k for k in DB_KEYS if not dsn_map.get(k)]
    if missing:
        raise SystemExit(
            "缺少 DSN：%s（用 --dsn-map JSON 或环境变量 %s）"
            % (", ".join(missing), ", ".join(ENV_VARS[k] for k in missing))
        )
    for key, dsn in dsn_map.items():
        if not db.is_pg_target(dsn):
            raise SystemExit("%s 的 DSN 不是 PostgreSQL：%s" % (key, dsn))
    return dsn_map


def migrate_one(key: str, sqlite_path: Path, dsn: str, args, report: list) -> bool:
    """迁移单个库；返回是否全部校验通过。"""
    started = time.monotonic()
    ok = True
    sq = _sqlite_readonly(sqlite_path)
    try:
        tables = _sqlite_tables(sq)
        if args.only:
            tables = [t for t in tables if t in args.only]
        pg = db.connect(dsn)
        pg.executescript(";\n".join(PG_SCHEMAS[key]))
        pg.commit()
        pg_existing = _pg_tables(pg)

        plan = []
        for table in tables:
            if table not in pg_existing:
                report.append("| %s | %s | - | - | 跳过（PG 无对应表） |" % (key, table))
                continue
            cols = [r["name"] for r in sq.execute("PRAGMA table_info(%s)" % table)]
            rows = sq.execute(
                'SELECT %s FROM "%s" ORDER BY rowid'
                % (", ".join('"%s"' % c for c in cols), table)
            ).fetchall()
            plan.append((table, cols, [tuple(r) for r in rows]))

        if args.apply:
            if plan:
                # 幂等策略：先 TRUNCATE 再全量插入（PG 允许逗号列表；无外键，顺序无关）
                table_list = ", ".join('"%s"' % t for t, _, _ in plan)
                pg.execute("TRUNCATE TABLE %s" % table_list)
                pg.commit()
            for table, cols, rows in plan:
                sql = 'INSERT INTO "%s" (%s) VALUES (%s)' % (
                    table,
                    ", ".join('"%s"' % c for c in cols),
                    ", ".join(["%s"] * len(cols)),
                )
                for row in rows:
                    pg.execute(sql, row)
                pg.commit()

        # 双比对：行数 + 校验和（顺序无关，NULL / 数值类型规范化）
        # dry-run 只报迁移计划（PG 尚无数据，比对必然不一致，不作为失败）
        if not args.apply:
            for table, cols, rows in plan:
                report.append(
                    "| %s | %s | %d | - | dry-run（未写入，待 --apply） |"
                    % (key, table, len(rows))
                )
            report.append("")
            report.append(
                "*%s 耗时 %.1f 秒（dry-run）*" % (key, time.monotonic() - started)
            )
            report.append("")
            return True
        for table, cols, rows in plan:
            sq_count = len(rows)
            sq_md5 = _checksum(rows)
            pg_rows = pg.execute(
                'SELECT %s FROM "%s"'
                % (", ".join('"%s"' % c for c in cols), table)
            ).fetchall()
            pg_values = [tuple(r) for r in pg_rows]
            pg_count = len(pg_values)
            pg_md5 = _checksum(pg_values)
            match = sq_count == pg_count and sq_md5 == pg_md5
            ok = ok and match
            if match:
                conclusion = "一致 (%s)" % sq_md5[:12]
            else:
                conclusion = "不一致 (sqlite=%s pg=%s)" % (sq_md5[:12], pg_md5[:12])
            report.append(
                "| %s | %s | %d | %d | %s |" % (key, table, sq_count, pg_count, conclusion)
            )
    finally:
        sq.close()

    if args.apply:
        _reset_identity_sequences(dsn, report, key)
    report.append("")
    report.append(
        "*%s 耗时 %.1f 秒（%s）*"
        % (key, time.monotonic() - started, "apply" if args.apply else "dry-run")
    )
    report.append("")
    return ok


def _reset_identity_sequences(dsn: str, report: list, key: str) -> None:
    """把所有 IDENTITY 序列重置到 max(id)，避免迁移后新写入主键冲突。"""
    pg = db.connect(dsn)
    rows = pg.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND is_identity = 'YES'"
    ).fetchall()
    for r in rows:
        table, col = r["table_name"], r["column_name"]
        max_row = pg.execute(
            'SELECT COALESCE(MAX("%s"), 0) AS m FROM "%s"' % (col, table)
        ).fetchone()
        max_id = max_row["m"] or 0
        if max_id == 0:
            pg.execute(
                "SELECT setval(pg_get_serial_sequence(%s, %s), 1, false)", (table, col)
            )
        else:
            pg.execute(
                "SELECT setval(pg_get_serial_sequence(%s, %s), %s, true)",
                (table, col, max_id),
            )
        report.append("| %s | setval %s.%s | - | - | -> %s |" % (key, table, col, max_id))
    pg.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite -> PostgreSQL 迁移（默认 dry-run）")
    parser.add_argument(
        "--sqlite-dir",
        default=str(BACKEND_ROOT / "data"),
        help="SQLite 文件所在目录（默认 backend/data）",
    )
    parser.add_argument(
        "--dsn-map",
        help='4 个库的 PG DSN，JSON：{"whitelist": "...", "conversations": "...", '
        '"query_log": "...", "quiz": "..."}；缺省回落 LSX_DB_* 环境变量',
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="只读预演（默认）")
    mode.add_argument("--apply", action="store_true", help="真实迁移（写 PG，先 TRUNCATE 目标表）")
    parser.add_argument("--report-out", help="报告输出路径（markdown）；缺省打印 stdout")
    parser.add_argument("--only", action="append", help="只迁移指定表（可多次）")
    args = parser.parse_args()
    args.apply = bool(args.apply)

    dsn_map = _resolve_dsns(args)

    sqlite_dir = Path(args.sqlite_dir)
    lines = [
        "# SQLite -> PostgreSQL 迁移报告",
        "",
        "- 模式：%s" % ("APPLY（已写入 PG）" if args.apply else "DRY-RUN（未写任何数据）"),
        "- SQLite 目录：%s" % sqlite_dir.resolve(),
        "- 时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "| 库 | 表 | 预期行数(SQLite) | 实际行数(PG) | 校验和 / 结论 |",
        "|---|---|---|---|---|",
    ]
    all_ok = True
    overall_started = time.monotonic()
    for key in DB_KEYS:
        sqlite_path = sqlite_dir / SQLITE_FILES[key]
        if not sqlite_path.exists():
            lines.append("| %s | - | - | - | 源文件缺失：%s |" % (key, sqlite_path))
            all_ok = False
            continue
        ok = migrate_one(key, sqlite_path, dsn_map[key], args, lines)
        all_ok = all_ok and ok

    lines.append("---")
    lines.append("总耗时：%.1f 秒" % (time.monotonic() - overall_started))
    lines.append("")
    lines.append(
        "结论：%s"
        % (
            "全部一致，迁移成功"
            if all_ok
            else "存在不一致项 —— 立即回滚（.env 指回 SQLite 并重启）"
        )
    )

    text = "\n".join(lines)
    if args.report_out:
        Path(args.report_out).write_text(text, encoding="utf-8")
        print("报告已写入 %s" % args.report_out)
    print(text)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())