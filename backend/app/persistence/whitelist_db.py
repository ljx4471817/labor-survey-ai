# -*- coding: utf-8 -*-
'''白名单 SQLite 持久化。

schema v1:
- phone PK
- name / 省 / 市 / 县 / 乡 / 社区 + 姓名 + 手机号(phone, unique)
- admin_level: 省级 / 市级 / 区县 / 调查员
- remark: 备注
- active 软删除标记
- created_at / updated_at

SQLite 在 Python 内部使用 WAL，连接跨线程安全。
下列 DDL 顺序执行：建表 + 区域复合索引 + admin_level 索引。
'''

from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.core.config import PROJECT_ROOT


DB_PATH = PROJECT_ROOT / 'backend' / 'data' / 'whitelist.db'
UTC8 = timezone(timedelta(hours=8))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS whitelist (
    phone      TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    province   TEXT NOT NULL,
    city       TEXT NOT NULL,
    county     TEXT,
    township   TEXT,
    community  TEXT,
    admin_level TEXT NOT NULL DEFAULT '调查员',
    remark     TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_whitelist_region
    ON whitelist(province, city, county, township, community);
"""

_conn: sqlite3.Connection | None = None

_MIGRATIONS = [
    "ALTER TABLE whitelist ADD COLUMN admin_level TEXT NOT NULL DEFAULT '调查员'",
    "ALTER TABLE whitelist ADD COLUMN remark TEXT",
    "CREATE INDEX IF NOT EXISTS idx_whitelist_admin_level ON whitelist(admin_level)",
]


def _needs_migration(conn: sqlite3.Connection) -> bool:
    cols = {r['name'] for r in conn.execute('PRAGMA table_info(whitelist)')}
    return 'admin_level' not in cols


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute('PRAGMA journal_mode=WAL')
        _conn.executescript(_SCHEMA)
        if _needs_migration(_conn):
            for sql in _MIGRATIONS:
                try:
                    _conn.execute(sql)
                except sqlite3.OperationalError:
                    pass
            _conn.commit()
    return _conn


def _now() -> str:
    return datetime.now(UTC8).isoformat(timespec='seconds')


def list_active_phones() -> list[str]:
    rows = _get_conn().execute('SELECT phone FROM whitelist WHERE active = 1').fetchall()
    return [r['phone'] for r in rows]


def is_whitelisted(phone: str) -> bool:
    row = _get_conn().execute(
        'SELECT 1 FROM whitelist WHERE phone = ? AND active = 1 LIMIT 1', (phone,)
    ).fetchone()
    return row is not None


def get_user(phone: str) -> dict | None:
    row = _get_conn().execute(
        'SELECT * FROM whitelist WHERE phone = ? AND active = 1', (phone,)
    ).fetchone()
    if row:
        return dict(row)
    return None


def list_all(active_only: bool = True) -> list[dict]:
    sql = 'SELECT * FROM whitelist'
    if active_only:
        sql += ' WHERE active = 1'
    sql += ' ORDER BY province, city, county, township, community, name'
    return [dict(r) for r in _get_conn().execute(sql)]


def upsert(record: dict) -> str:
    required = ('phone', 'name', 'province', 'city')
    for k in required:
        if not record.get(k):
            raise ValueError(f'缺少必填字段：{k}')

    now = _now()
    conn = _get_conn()
    existing = conn.execute(
        'SELECT phone FROM whitelist WHERE phone = ?', (record['phone'],)
    ).fetchone()

    county = record.get('county') or ''
    community = record.get('community') or ''
    admin_level = record.get('admin_level') or '调查员'
    remark = record.get('remark') or ''

    if existing:
        conn.execute(
            '''
            UPDATE whitelist SET
                name = ?, province = ?, city = ?, county = ?,
                township = ?, community = ?, admin_level = ?,
                remark = ?, updated_at = ?
            WHERE phone = ?
            ''',
            (
                record['name'],
                record['province'],
                record['city'],
                county,
                record.get('township') or '',
                community,
                admin_level,
                remark,
                now,
                record['phone'],
            ),
        )
        action = 'updated'
    else:
        conn.execute(
            '''
            INSERT INTO whitelist
                (phone, name, province, city, county, township, community,
                 admin_level, remark, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ''',
            (
                record['phone'],
                record['name'],
                record['province'],
                record['city'],
                county,
                record.get('township') or '',
                community,
                admin_level,
                remark,
                now,
                now,
            ),
        )
        action = 'inserted'

    conn.commit()
    return action


def delete(phone: str, soft: bool = True) -> bool:
    conn = _get_conn()
    if soft:
        cur = conn.execute(
            'UPDATE whitelist SET active = 0, updated_at = ? WHERE phone = ?',
            (_now(), phone),
        )
    else:
        cur = conn.execute('DELETE FROM whitelist WHERE phone = ?', (phone,))
    conn.commit()
    return cur.rowcount > 0


def bulk_import_csv(csv_text: str) -> dict:
    reader = csv.DictReader(io.StringIO(csv_text))
    required = ('phone', 'name', 'province', 'city', 'admin_level')
    missing_cols = [c for c in required if c not in (reader.fieldnames or [])]
    if missing_cols:
        raise ValueError(f'CSV 缺少必要列：{missing_cols}')

    inserted = updated = 0
    errors = []
    for line_no, row in enumerate(reader, start=2):
        try:
            action = upsert(
                {
                    k: (row.get(k) or '').strip()
                    for k in (
                        'phone', 'name', 'province', 'city', 'county',
                        'township', 'community', 'admin_level', 'remark',
                    )
                }
            )
            if action == 'inserted':
                inserted += 1
            else:
                updated += 1
        except Exception as e:
            errors.append({'line': line_no, 'error': str(e), 'row': dict(row)})
    return {'inserted': inserted, 'updated': updated, 'errors': errors}
