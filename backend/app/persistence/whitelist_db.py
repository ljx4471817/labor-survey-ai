# -*- coding: utf-8 -*-
'''白名单 SQLite 持久化。

schema v2（PRD 权限系统改造 2026-08-13）：
- phone PK
- name / 省 / 市 / 县 / 乡 / 社区 + 姓名 + 手机号(phone, unique)
- admin_level: 省级 / 市级 / 区县 / 调查员（业务管辖范围）
- sys_role: 系统管理员 / 业务管理员 / 普通用户（系统职能，v2 新增）
- remark: 备注
- active 软删除标记
- created_at / updated_at

whitelist_audit 审计表：所有写操作留痕（actor/action/target/before/after），保留 12 个月。

SQLite 在 Python 内部使用 WAL，连接跨线程安全。
迁移逻辑（幂等）：
1. 缺 sys_role 列 -> 加列（默认 普通用户）；
2. 回填：admin_level IN (省级,市级,区县) -> 业务管理员；调查员 -> 普通用户；
3. .env 的 LSX_SYSTEM_ADMIN_PHONE -> 该号码 sys_role=系统管理员 且 active=1（缺失仅启动 warning）；
4. 建审计表与索引。
'''
from __future__ import annotations

import csv
import io
import os
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
    sys_role    TEXT NOT NULL DEFAULT '普通用户',
    remark     TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_whitelist_region
    ON whitelist(province, city, county, township, community);
CREATE INDEX IF NOT EXISTS idx_whitelist_admin_level ON whitelist(admin_level);
"""

_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS whitelist_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_phone  TEXT NOT NULL,
    actor_name   TEXT,
    action       TEXT NOT NULL,   -- create/update/disable/enable/batch_disable/sys_role_change/import
    target_phone TEXT NOT NULL,
    before_json  TEXT,
    after_json   TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_target ON whitelist_audit(target_phone);
CREATE INDEX IF NOT EXISTS idx_audit_created ON whitelist_audit(created_at);
"""

_conn: sqlite3.Connection | None = None

# 旧库迁移：逐一幂等执行（缺列才加）。
_MIGRATIONS = [
    "ALTER TABLE whitelist ADD COLUMN admin_level TEXT NOT NULL DEFAULT '调查员'",
    "ALTER TABLE whitelist ADD COLUMN remark TEXT",
    "ALTER TABLE whitelist ADD COLUMN sys_role TEXT NOT NULL DEFAULT '普通用户'",
]

AUDIT_RETENTION_MONTHS = 12


def _now() -> str:
    return datetime.now(UTC8).isoformat(timespec='seconds')


def _needs_migration(conn: sqlite3.Connection) -> bool:
    cols = {r['name'] for r in conn.execute('PRAGMA table_info(whitelist)')}
    return not {'admin_level', 'remark', 'sys_role'} <= cols


def _system_admin_phone() -> str:
    """从 .env / 环境变量读系统管理员手机号（缺失返回空串）。"""
    return os.environ.get('LSX_SYSTEM_ADMIN_PHONE', '').strip()


def _migrate(conn: sqlite3.Connection) -> None:
    """幂等迁移：加列 + 回填 sys_role + 系统管理员 + 审计表。"""
    if _needs_migration(conn):
        for sql in _MIGRATIONS:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # 列已存在，跳过
        # 回填：已有管理岗（省级/市级/区县）升级为业务管理员；调查员保持普通用户。
        conn.execute(
            "UPDATE whitelist SET sys_role = '业务管理员' "
            "WHERE admin_level IN ('省级', '市级', '区县')"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_whitelist_sys_role ON whitelist(sys_role)"
        )
    sa = _system_admin_phone()
    if sa:
        conn.execute(
            "UPDATE whitelist SET sys_role = '系统管理员', active = 1, updated_at = ? "
            "WHERE phone = ?",
            (_now(), sa),
        )
    conn.executescript(_AUDIT_SCHEMA)
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute('PRAGMA journal_mode=WAL')
        _conn.executescript(_SCHEMA)
        _migrate(_conn)
    return _conn


def list_active_phones() -> list[str]:
    rows = _get_conn().execute('SELECT phone FROM whitelist WHERE active = 1').fetchall()
    return [r['phone'] for r in rows]


def is_whitelisted(phone: str) -> bool:
    row = _get_conn().execute(
        'SELECT 1 FROM whitelist WHERE phone = ? AND active = 1 LIMIT 1', (phone,)
    ).fetchone()
    return row is not None


def get_user(phone: str) -> dict | None:
    """查 active=1 的完整用户记录（含 region / sys_role）。"""
    row = _get_conn().execute(
        'SELECT * FROM whitelist WHERE phone = ? AND active = 1', (phone,)
    ).fetchone()
    return dict(row) if row else None


def get_user_any(phone: str) -> dict | None:
    """查任意状态（含 inactive）的用户记录，用于审计 before/after 快照。"""
    row = _get_conn().execute(
        'SELECT * FROM whitelist WHERE phone = ?', (phone,)
    ).fetchone()
    return dict(row) if row else None


def list_all(active_only: bool = True) -> list[dict]:
    sql = 'SELECT * FROM whitelist'
    if active_only:
        sql += ' WHERE active = 1'
    sql += ' ORDER BY province, city, county, township, community, name'
    return [dict(r) for r in _get_conn().execute(sql)]


def upsert(record: dict) -> str:
    """新增或更新；更新时不改 active（PUT 不复活），sys_role 仅在显式传入时更新。"""
    required = ('phone', 'name', 'province')
    # 省级 / 系统管理员没有下级区域；其他范围仍必须落市。
    if (record.get('admin_level') or '调查员') != '省级':
        required += ('city',)
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
    # 未显式传 sys_role 时按业务层级推导（与迁移回填一致：管理岗=业务管理员）
    sys_role = record.get('sys_role') or (
        '业务管理员' if admin_level in ('省级', '市级', '区县') else '普通用户'
    )
    # .env 指定系统管理员：身份恒为系统管理员且 active（防业务层级推导覆盖）
    if _system_admin_phone() and record['phone'] == _system_admin_phone():
        sys_role = '系统管理员'
    remark = record.get('remark') or ''

    if existing:
        conn.execute(
            '''
            UPDATE whitelist SET
                name = ?, province = ?, city = ?, county = ?,
                township = ?, community = ?, admin_level = ?,
                sys_role = COALESCE(?, sys_role),
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
                sys_role,
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
                 admin_level, sys_role, remark, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
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
                sys_role or '普通用户',
                remark,
                now,
                now,
            ),
        )
        action = 'inserted'

    if _system_admin_phone() and record['phone'] == _system_admin_phone():
        conn.execute(
            "UPDATE whitelist SET sys_role = '系统管理员', active = 1 WHERE phone = ?",
            (record['phone'],),
        )

    conn.commit()
    return action


def set_active(phone: str, active: bool) -> bool:
    """启停用户；返回是否命中。"""
    cur = _get_conn().execute(
        'UPDATE whitelist SET active = ?, updated_at = ? WHERE phone = ?',
        (1 if active else 0, _now(), phone),
    )
    _get_conn().commit()
    return cur.rowcount > 0


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


def enable(phone: str) -> bool:
    """重新启用（原 UI 缺恢复入口）。"""
    return set_active(phone, True)


def _json(v: dict | None) -> str | None:
    if v is None:
        return None
    import json
    return json.dumps(v, ensure_ascii=False)


def log_audit(
    actor_phone: str,
    action: str,
    target_phone: str,
    actor_name: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    """写一条白名单审计记录（所有写操作必须调用）。"""
    conn = _get_conn()
    conn.execute(
        '''
        INSERT INTO whitelist_audit
            (actor_phone, actor_name, action, target_phone, before_json, after_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            _mask_phone(actor_phone),
            actor_name,
            action,
            _mask_phone(target_phone),
            _json(_mask_pii(before)),
            _json(_mask_pii(after)),
            _now(),
        ),
    )
    conn.commit()


def _mask_phone(phone: str) -> str:
    """手机号脱敏：13985000001 -> 139****0001。"""
    if not phone or len(phone) < 7:
        return phone[:3] + "****" if phone else ""
    return phone[:3] + "****" + phone[-4:]


def _mask_pii(record: dict | None) -> dict | None:
    """对记录中的 PII 字段脱敏。"""
    if record is None:
        return None
    masked = dict(record)
    for key in ("phone", "target_phone", "actor_phone"):
        if key in masked and masked[key]:
            masked[key] = _mask_phone(str(masked[key]))
    return masked


def list_audit(limit: int = 100, target_phone: str | None = None) -> list[dict]:
    """最近审计记录，倒序。"""
    if target_phone:
        rows = _get_conn().execute(
            'SELECT * FROM whitelist_audit WHERE target_phone = ? ORDER BY id DESC LIMIT ?',
            (target_phone, limit),
        ).fetchall()
    else:
        rows = _get_conn().execute(
            'SELECT * FROM whitelist_audit ORDER BY id DESC LIMIT ?', (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def cleanup_audit(months: int = AUDIT_RETENTION_MONTHS) -> int:
    """清理超过 months 个月的审计记录，返回删除行数。"""
    from datetime import datetime as _dt
    cutoff = (_dt.now(UTC8) - timedelta(days=months * 30)).isoformat(timespec='seconds')
    cur = _get_conn().execute(
        'DELETE FROM whitelist_audit WHERE created_at < ?', (cutoff,)
    )
    _get_conn().commit()
    return cur.rowcount


def bulk_import_csv(csv_text: str) -> dict:
    reader = csv.DictReader(io.StringIO(csv_text))
    required = ('phone', 'name', 'province', 'city', 'admin_level')
    missing_cols = [c for c in required if c not in (reader.fieldnames or [])]
    if missing_cols:
        raise ValueError(f'CSV 缺少必要列：{missing_cols}')

    inserted = updated = 0
    errors = []
    phones: list[str] = []
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
            phones.append((row.get('phone') or '').strip())
            if action == 'inserted':
                inserted += 1
            else:
                updated += 1
        except Exception as e:
            errors.append({'line': line_no, 'error': str(e), 'row': dict(row)})
    return {'inserted': inserted, 'updated': updated, 'errors': errors, 'phones': phones}
