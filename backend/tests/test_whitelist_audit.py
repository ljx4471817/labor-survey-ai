# -*- coding: utf-8 -*-
"""whitelist_audit 审计表：写入字段完整 / 倒序 / 过滤 / API 写操作留痕 / 12 个月清理。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.api import whitelist_admin as wl_api
from app.core.constants import AdminLevel, SysRole
from app.models.schemas import WhitelistEntry
from app.persistence import whitelist_db as wl

UTC8 = timezone(timedelta(hours=8))


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    db_file = tmp_path / "whitelist.db"
    monkeypatch.setattr(wl, "DB_PATH", db_file)
    monkeypatch.setattr(wl, "_conn", None)
    yield db_file
    monkeypatch.setattr(wl, "_conn", None)


def _user(role, level, county="南明区"):
    return {
        "phone": "13900000001", "name": "操作员", "sys_role": role,
        "admin_level": level, "province": "贵州省", "city": "贵阳市", "county": county,
        "active": 1,
    }


def test_audit_row_fields_complete(tmp_db):
    before = {"phone": "13800000001", "name": "旧名"}
    after = {"phone": "13800000001", "name": "新名"}
    wl.log_audit(
        actor_phone="13900000001", actor_name="操作员", action="update",
        target_phone="13800000001", before=before, after=after,
    )
    rows = wl.list_audit()
    assert len(rows) == 1
    r = rows[0]
    assert r["actor_phone"] == "139****0001"
    assert r["actor_name"] == "操作员"
    assert r["action"] == "update"
    assert r["target_phone"] == "138****0001"
    assert json.loads(r["before_json"]) == {"phone": "138****0001", "name": "旧名"}
    assert json.loads(r["after_json"]) == {"phone": "138****0001", "name": "新名"}
    assert r["created_at"]


def test_audit_desc_order_and_target_filter(tmp_db):
    for i in range(3):
        wl.log_audit(actor_phone="13900000001", action="create", target_phone=f"1380000000{i}")
    rows = wl.list_audit(limit=10)
    assert [r["target_phone"] for r in rows] == ["138****0002", "138****0001", "138****0000"]
    one = wl.list_audit(target_phone="138****0001")
    assert len(one) == 1 and one[0]["target_phone"] == "138****0001"


def test_api_write_operations_log_audit(tmp_db):
    actor = _user(SysRole.BUSINESS_ADMIN.value, AdminLevel.CITY.value)
    wl_api.create_whitelist(
        WhitelistEntry(phone="13800000001", name="甲", province="贵州省", city="贵阳市", county="南明区"),
        user=actor,
    )
    wl_api.remove_whitelist("13800000001", user=actor)
    wl_api.enable_whitelist("13800000001", user=actor)
    actions = [r["action"] for r in wl.list_audit(limit=10)]
    assert actions == ["enable", "disable", "create"]
    create_row = wl.list_audit(limit=10)[-1]
    assert json.loads(create_row["after_json"])["name"] == "甲"


def test_api_update_sys_role_change_logged(tmp_db):
    actor = _user(SysRole.SYSTEM_ADMIN.value, AdminLevel.ENUMERATOR.value)
    wl_api.create_whitelist(
        WhitelistEntry(phone="13800000001", name="甲", province="贵州省", city="贵阳市",
                              county="南明区", admin_level="调查员", sys_role="普通用户"),
        user=actor,
    )
    wl_api.update_whitelist(
        "13800000001",
        WhitelistEntry(phone="13800000001", name="甲", province="贵州省", city="贵阳市",
                              county="南明区", admin_level="调查员", sys_role="业务管理员"),
        user=actor,
    )
    actions = [r["action"] for r in wl.list_audit(limit=10)]
    assert "sys_role_change" in actions


def test_cleanup_audit_retention(tmp_db):
    wl.log_audit(actor_phone="13900000001", action="create", target_phone="13800000001")
    # 直接插入一条 13 个月前的旧记录（手机号已脱敏存储）
    old_ts = (datetime.now(UTC8) - timedelta(days=13 * 30)).isoformat(timespec="seconds")
    conn = wl._get_conn()
    conn.execute(
        "INSERT INTO whitelist_audit (actor_phone, action, target_phone, created_at) VALUES (?, ?, ?, ?)",
        ("139****0001", "create", "138****0002", old_ts),
    )
    conn.commit()
    assert wl.cleanup_audit() == 1
    assert [r["target_phone"] for r in wl.list_audit()] == ["138****0001"]
