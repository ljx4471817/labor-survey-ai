# -*- coding: utf-8 -*-
"""PRD 权限系统改造：whitelist.db 迁移脚本（sys_role 列 + 回填 + 审计表 + 系统管理员）。

用法：
  python scripts/migrate_whitelist_rbac.py --dry-run   # 备份到临时库跑迁移，输出 diff，不碰真实库
  python scripts/migrate_whitelist_rbac.py --apply     # 先备份 backend/data/backups/ 再真实迁移

迁移本身在 whitelist_db 首次连接时自动执行（幂等）；本脚本用于：
- 上线前 dry-run 预览 diff（谁从 普通用户 变成 业务管理员 / 系统管理员）；
- --apply 前自动备份，便于回滚。
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'backend'))

from app.persistence import whitelist_db  # noqa: E402


def _mask(phone: str) -> str:
    return phone[:3] + '****' + phone[-4:]


def _snapshot(path: Path) -> dict:
    """只读快照：phone -> (admin_level, sys_role, active)。"""
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    out = {}
    try:
        cols = {r['name'] for r in conn.execute('PRAGMA table_info(whitelist)')}
        if 'sys_role' not in cols:
            return out
        for r in conn.execute('SELECT phone, admin_level, sys_role, active FROM whitelist'):
            out[r['phone']] = (r['admin_level'], r['sys_role'], r['active'])
    finally:
        conn.close()
    return out


def _copy_db(src: Path, dst: Path) -> None:
    for suffix in ('', '-wal', '-shm'):
        p = Path(str(src) + suffix)
        if p.exists():
            shutil.copy2(p, Path(str(dst) + suffix))


def _run_migration(db_path: Path) -> None:
    """对指定 DB 文件触发 whitelist_db 幂等迁移。"""
    whitelist_db.DB_PATH = db_path
    whitelist_db._conn = None
    _ = whitelist_db.list_active_phones()  # 触发 _get_conn -> _migrate
    whitelist_db._conn = None


def _print_diff(before: dict, after: dict, label: str) -> int:
    changes = 0
    for phone in sorted(set(before) | set(after)):
        b = before.get(phone)
        a = after.get(phone)
        if b == a:
            continue
        changes += 1
        if b is None:
            print(f'  + {_mask(phone)} 新增 {a[1]} (admin_level={a[0]})')
        elif a is None:
            print(f'  - {_mask(phone)} 已删除 (原 {b[1]})')
        else:
            parts = []
            if b[1] != a[1]:
                parts.append(f'sys_role: {b[1]} -> {a[1]}')
            if b[2] != a[2]:
                parts.append(f'active: {b[2]} -> {a[2]}')
            if parts:
                print(f'  ~ {_mask(phone)} (admin_level={b[0]}) {", ".join(parts)}')
    print(f'  {label}: 变更 {changes} 条')
    return changes


def main():
    ap = argparse.ArgumentParser(description='whitelist.db 权限迁移（sys_role + 审计表）')
    ap.add_argument('--dry-run', action='store_true', help='备份到临时库跑迁移，输出 diff，不碰真实库')
    ap.add_argument('--apply', action='store_true', help='先备份 backend/data/backups/ 再真实迁移')
    args = ap.parse_args()

    db = whitelist_db.DB_PATH
    if not db.exists():
        print(f'whitelist.db 不存在：{db}')
        return 1

    print(f'DB: {db}')
    print(f'LSX_SYSTEM_ADMIN_PHONE: {whitelist_db._system_admin_phone() or "(未设置，系统管理员专属功能将 403)"}')

    before = _snapshot(db)

    if args.apply:
        backup_dir = PROJECT_ROOT / 'backend' / 'data' / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d-%H%M%S')
        backup = backup_dir / f'whitelist-{ts}.db'
        _copy_db(db, backup)
        print(f'已备份 -> {backup}')
        _run_migration(db)
        after = _snapshot(db)
        _print_diff(before, after, '真实库迁移')
    else:
        with tempfile.TemporaryDirectory() as td:
            tmp_db = Path(td) / 'whitelist.db'
            _copy_db(db, tmp_db)
            _run_migration(tmp_db)
            after = _snapshot(tmp_db)
            _print_diff(before, after, 'dry-run（临时库）')
        print('（dry-run 未改动真实库）')

    # 审计表检查
    conn = sqlite3.connect(db)
    has_audit = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='whitelist_audit'"
    ).fetchone()[0]
    conn.close()
    print(f'审计表 whitelist_audit: {"存在" if has_audit else "缺失"}')

    if not whitelist_db._system_admin_phone():
        print('\n提醒：.env 尚未配置 LSX_SYSTEM_ADMIN_PHONE，系统管理员专属功能将 403。')
        return 0 if args.dry_run else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
