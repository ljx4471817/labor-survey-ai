#!/usr/bin/env python3
"""白名单变更专用入口（运维 + Agent 共用）。

设计目标：
  - 所有变更走 backup + audit + 校验，禁止 raw SQL
  - Agent 改白名单只能调本脚本，不允许直接 sqlite3
  - 操作有详细日志，便于事后追溯

支持操作：
  --action change-phone --old <phone> --new <phone> [--reason <text>]
  --action add          --entry-json <json>
  --action update-field --phone <phone> --field <name> --value <v>
  --action disable      --phone <phone> [--reason <text>]
  --action enable       --phone <phone> [--reason <text>]

所有操作均要求 --actor <系统管理员手机号>，并在 audit 中记录。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

DB = "/opt/labor-survey-ai/backend/data/whitelist.db"
BACKUP_DIR = "/opt/labor-survey-ai/backend/data/backups"
os.makedirs(BACKUP_DIR, exist_ok=True)

PROTECTED_PHONES = frozenset({"13985000001", "13985000002", "13985000003", "13985000004"})

ALLOWED_FIELDS = frozenset({
    "name", "province", "city", "county", "township", "community",
    "admin_level", "remark", "active",
})
PHONE_RE = re.compile(r"^1\d{10}$")
NOW = lambda: datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def mask_phone(p: str) -> str:
    return p[:3] + "****" + p[-4:] if len(p) >= 7 else p[:3] + "****"


def err(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def backup_db(tag: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"whitelist-{tag}-{ts}.db")
    shutil.copy2(DB, dst)
    for s in ("-wal", "-shm"):
        src = DB + s
        if os.path.exists(src):
            shutil.copy2(src, dst + s)
    return dst


def get_conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def log_audit(c, actor_phone: str, actor_name: str, action: str,
              target_phone: str, before: dict | None, after: dict | None) -> None:
    def mask_pii(d):
        if not d: return None
        out = dict(d)
        if "phone" in out: out["phone"] = mask_phone(out["phone"])
        return out
    c.execute("""
        INSERT INTO whitelist_audit
          (actor_phone, actor_name, action, target_phone, before_json, after_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        mask_phone(actor_phone), actor_name, action, mask_phone(target_phone),
        json.dumps(mask_pii(before), ensure_ascii=False),
        json.dumps(mask_pii(after), ensure_ascii=False),
        NOW(),
    ))


def cmd_change_phone(args):
    if not PHONE_RE.match(args.old): err("旧号格式错误")
    if not PHONE_RE.match(args.new): err("新号格式错误")
    if args.new in PROTECTED_PHONES and not args.allow_protected:
        err(f"新号 {args.new} 是保护测试号，需 --allow-protected")
    c = get_conn()
    before = c.execute("SELECT * FROM whitelist WHERE phone=?", (args.old,)).fetchone()
    if not before: err(f"旧号 {args.old} 不存在")
    before = dict(before)
    if (before.get("sys_role") or "普通用户") == "系统管理员" and not args.allow_sysadmin:
        err("目标是系统管理员账号，需 --allow-sysadmin")
    if c.execute("SELECT 1 FROM whitelist WHERE phone=?", (args.new,)).fetchone():
        err(f"新号 {args.new} 已存在，请先处理冲突")
    bkf = backup_db("phone-change")
    c.execute("UPDATE whitelist SET phone=?, updated_at=? WHERE phone=?",
              (args.new, NOW(), args.old))
    after = dict(c.execute("SELECT * FROM whitelist WHERE phone=?", (args.new,)).fetchone())
    log_audit(c, args.actor, args.actor_name, "phone_change", args.new, before, after)
    c.commit(); c.close()
    print(json.dumps({"ok": True, "action": "change-phone",
                      "old": args.old, "new": args.new,
                      "backup": bkf, "after": after},
                     ensure_ascii=False, indent=2))


def cmd_add(args):
    entry = json.loads(args.entry_json)
    if "phone" not in entry: err("缺少 phone")
    if not PHONE_RE.match(entry["phone"]): err("phone 格式错误")
    for req in ("name", "province", "city", "county", "community"):
        if req not in entry: err(f"缺少 {req}")
    c = get_conn()
    if c.execute("SELECT 1 FROM whitelist WHERE phone=?", (entry["phone"],)).fetchone():
        err(f"phone {entry['phone']} 已存在")
    bkf = backup_db("add")
    entry.setdefault("admin_level", "调查员")
    entry.setdefault("sys_role", "普通用户")
    entry.setdefault("remark", "")
    entry["active"] = 1
    entry["created_at"] = NOW(); entry["updated_at"] = NOW()
    c.execute("""
        INSERT INTO whitelist (phone,name,province,city,county,township,community,
            admin_level,sys_role,remark,active,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        entry["phone"], entry["name"], entry["province"], entry["city"],
        entry["county"], entry.get("township", ""), entry["community"],
        entry["admin_level"], entry["sys_role"], entry["remark"],
        entry["active"], entry["created_at"], entry["updated_at"],
    ))
    after = dict(c.execute("SELECT * FROM whitelist WHERE phone=?", (entry["phone"],)).fetchone())
    log_audit(c, args.actor, args.actor_name, "create", entry["phone"], None, after)
    c.commit(); c.close()
    print(json.dumps({"ok": True, "action": "add", "phone": entry["phone"],
                      "backup": bkf}, ensure_ascii=False, indent=2))


def cmd_update_field(args):
    if args.field not in ALLOWED_FIELDS:
        err(f"field 必须是 {sorted(ALLOWED_FIELDS)} 之一；不允许改 phone/sys_role/created_at")
    if not PHONE_RE.match(args.phone): err("phone 格式错误")
    c = get_conn()
    before = c.execute("SELECT * FROM whitelist WHERE phone=?", (args.phone,)).fetchone()
    if not before: err(f"phone {args.phone} 不存在")
    before = dict(before)
    bkf = backup_db("update-field")
    c.execute(f"UPDATE whitelist SET {args.field}=?, updated_at=? WHERE phone=?",
              (args.value, NOW(), args.phone))
    after = dict(c.execute("SELECT * FROM whitelist WHERE phone=?", (args.phone,)).fetchone())
    log_audit(c, args.actor, args.actor_name, "update_field", args.phone, before, after)
    c.commit(); c.close()
    print(json.dumps({"ok": True, "action": "update-field",
                      "phone": args.phone, "field": args.field,
                      "backup": bkf}, ensure_ascii=False, indent=2))


def cmd_disable(args):
    if not PHONE_RE.match(args.phone): err("phone 格式错误")
    c = get_conn()
    before = c.execute("SELECT * FROM whitelist WHERE phone=?", (args.phone,)).fetchone()
    if not before: err(f"phone {args.phone} 不存在")
    before = dict(before)
    if (before.get("sys_role") or "普通用户") == "系统管理员" and not args.allow_sysadmin:
        err("目标是系统管理员账号，需 --allow-sysadmin")
    bkf = backup_db("disable")
    c.execute("UPDATE whitelist SET active=0, updated_at=? WHERE phone=?", (NOW(), args.phone))
    after = dict(c.execute("SELECT * FROM whitelist WHERE phone=?", (args.phone,)).fetchone())
    log_audit(c, args.actor, args.actor_name, "disable", args.phone, before, after)
    c.commit(); c.close()
    print(json.dumps({"ok": True, "action": "disable", "phone": args.phone,
                      "backup": bkf}, ensure_ascii=False, indent=2))


def cmd_enable(args):
    if not PHONE_RE.match(args.phone): err("phone 格式错误")
    c = get_conn()
    before = c.execute("SELECT * FROM whitelist WHERE phone=?", (args.phone,)).fetchone()
    if not before: err(f"phone {args.phone} 不存在")
    before = dict(before)
    bkf = backup_db("enable")
    c.execute("UPDATE whitelist SET active=1, updated_at=? WHERE phone=?", (NOW(), args.phone))
    after = dict(c.execute("SELECT * FROM whitelist WHERE phone=?", (args.phone,)).fetchone())
    log_audit(c, args.actor, args.actor_name, "enable", args.phone, before, after)
    c.commit(); c.close()
    print(json.dumps({"ok": True, "action": "enable", "phone": args.phone,
                      "backup": bkf}, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(description="白名单变更专用入口（禁止 raw SQL）")
    p.add_argument("--action", required=True,
                   choices=["change-phone", "add", "update-field", "disable", "enable"])
    p.add_argument("--actor", required=True, help="操作者手机号（系统管理员）")
    p.add_argument("--actor-name", default="系统管理员")
    p.add_argument("--old"); p.add_argument("--new")
    p.add_argument("--entry-json")
    p.add_argument("--phone"); p.add_argument("--field"); p.add_argument("--value")
    p.add_argument("--reason", default="")
    p.add_argument("--allow-protected", action="store_true")
    p.add_argument("--allow-sysadmin", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.dry_run:
        print("DRY-RUN:", json.dumps({"action": args.action, "args": vars(args)},
                                     ensure_ascii=False))
        return

    {"change-phone": cmd_change_phone, "add": cmd_add,
     "update-field": cmd_update_field, "disable": cmd_disable,
     "enable": cmd_enable}[args.action](args)


if __name__ == "__main__":
    main()
