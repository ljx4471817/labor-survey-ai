# -*- coding: utf-8 -*-
"""标准调查点数据、白名单区域校验与批量导入停用逻辑测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from openpyxl import Workbook

from app.api import whitelist_admin as wl_api
from app.api import whitelist_regions as region_api
from app.core.constants import AdminLevel, SysRole
from app.models.schemas import WhitelistEntry
from app.persistence import whitelist_db as wl
from app.services import region_points as rp

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts.convert_region_points import convert_region_points


FULL_POINT = {
    "city": "贵阳市",
    "county": "南明区",
    "township": "新华路街道",
    "community": "神奇路社区",
}


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    db_file = tmp_path / "whitelist.db"
    monkeypatch.setattr(wl, "DB_PATH", db_file)
    monkeypatch.setattr(wl, "_conn", None)
    yield db_file
    monkeypatch.setattr(wl, "_conn", None)


def _user(role=SysRole.SYSTEM_ADMIN.value, level=AdminLevel.PROVINCE.value, city="", county=""):
    return {
        "phone": "13900000001", "name": "操作员", "sys_role": role,
        "admin_level": level, "province": "贵州省", "city": city, "county": county,
        "active": 1,
    }


def _entry(phone="13800000001", name="测试", admin_level=AdminLevel.ENUMERATOR.value,
           city=FULL_POINT["city"], county=FULL_POINT["county"], township=FULL_POINT["township"],
           community=FULL_POINT["community"], sys_role=None):
    return WhitelistEntry(
        phone=phone, name=name, province="贵州省", city=city, county=county,
        township=township, community=community, admin_level=admin_level, sys_role=sys_role,
    )


def test_standard_region_file_loads_and_is_unique():
    points = rp.load_region_points()
    assert len(points) == 719
    assert all(point["province"] == "贵州省" for point in points)
    assert len({tuple(point[field] for field in rp.REGION_FIELDS) for point in points}) == len(points)


def test_validate_account_scope_combinations():
    rp.validate_account_scope("省级", "系统管理员")
    rp.validate_account_scope("省级", "业务管理员")
    rp.validate_account_scope("市级", "业务管理员")
    rp.validate_account_scope("调查员", "普通用户")
    with pytest.raises(ValueError, match="系统管理员"):
        rp.validate_account_scope("调查员", "系统管理员")
    with pytest.raises(ValueError, match="业务管理员"):
        rp.validate_account_scope("调查员", "业务管理员")
    with pytest.raises(ValueError, match="普通用户"):
        rp.validate_account_scope("市级", "普通用户")


def test_validate_region_selection_by_scope():
    points = rp.load_region_points()
    rp.validate_region_selection(points, admin_level="省级", province="贵州省", city="", county="", township="", community="")
    rp.validate_region_selection(points, admin_level="市级", province="贵州省", city="贵阳市", county="", township="", community="")
    rp.validate_region_selection(points, admin_level="区县", province="贵州省", city="贵阳市", county="南明区", township="", community="")
    rp.validate_region_selection(points, admin_level="调查员", province="贵州省", **FULL_POINT)

    with pytest.raises(ValueError, match="乡镇"):
        rp.validate_region_selection(
            points, admin_level="调查员", province="贵州省",
            city=FULL_POINT["city"], county=FULL_POINT["county"], township="", community="",
        )
    with pytest.raises(ValueError, match="没有对应调查点"):
        rp.validate_region_selection(
            points, admin_level="调查员", province="贵州省",
            city=FULL_POINT["city"], county=FULL_POINT["county"],
            township="不存在街道", community="不存在社区",
        )


def test_filter_region_points_by_scope():
    points = rp.load_region_points()
    scoped = rp.filter_region_points(points, ("贵州省", "贵阳市", "南明区"))
    assert scoped
    assert all(point["city"] == "贵阳市" and point["county"] == "南明区" for point in scoped)
    assert rp.filter_region_points(points, None) == points


def test_convert_region_points_adds_default_province_and_rejects_duplicates(tmp_path):
    source = tmp_path / "points.xlsx"
    output = tmp_path / "points.json"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["市", "县", "乡镇", "村居"])
    worksheet.append(["贵阳市", "南明区", "新华路街道", "神奇路社区"])
    workbook.save(source)

    assert convert_region_points(source, output) == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload[0]["province"] == "贵州省"

    worksheet.append(["贵阳市", "南明区", "新华路街道", "神奇路社区"])
    workbook.save(source)
    with pytest.raises(ValueError, match="重复"):
        convert_region_points(source, output)


def test_create_surveyor_requires_standard_point(tmp_db):
    actor = _user()
    assert wl_api.create_whitelist(_entry(), user=actor)["ok"]
    with pytest.raises(HTTPException) as exc:
        wl_api.create_whitelist(_entry(phone="13800000002", township="不存在街道", community="不存在社区"), user=actor)
    assert exc.value.status_code == 422
    assert "没有对应调查点" in exc.value.detail


def test_create_system_admin_is_province_and_unscoped(tmp_db):
    actor = _user()
    entry = _entry(admin_level=AdminLevel.PROVINCE.value, city="", county="", township="", community="",
                   sys_role=SysRole.SYSTEM_ADMIN.value)
    assert wl_api.create_whitelist(entry, user=actor)["ok"]
    record = wl.get_user_any("13800000001")
    assert record["admin_level"] == "省级"
    assert record["sys_role"] == "系统管理员"
    assert record["city"] == ""


def test_create_system_admin_with_city_is_rejected(tmp_db):
    actor = _user()
    entry = _entry(admin_level=AdminLevel.CITY.value, county="", township="", community="",
                   sys_role=SysRole.SYSTEM_ADMIN.value)
    with pytest.raises(HTTPException) as exc:
        wl_api.create_whitelist(entry, user=actor)
    assert exc.value.status_code == 422


def test_update_unchanged_legacy_region_is_allowed_but_changed_region_is_validated(tmp_db):
    actor = _user()
    wl.upsert({
        "phone": "13800000001", "name": "旧数据", "province": "贵州省", "city": "贵阳市",
        "county": "南明区", "township": "", "community": "旧社区",
        "admin_level": "调查员", "sys_role": "普通用户",
    })
    legacy = _entry(name="改名", township="", community="旧社区")
    assert wl_api.update_whitelist("13800000001", legacy, user=actor)["ok"]

    changed = _entry(name="改名", township="不存在街道", community="不存在社区")
    with pytest.raises(HTTPException) as exc:
        wl_api.update_whitelist("13800000001", changed, user=actor)
    assert exc.value.status_code == 422


def test_update_scope_change_requires_new_region_shape(tmp_db):
    actor = _user()
    wl_api.create_whitelist(_entry(), user=actor)
    promoted = _entry(
        admin_level=AdminLevel.DISTRICT.value, township="", community="",
        sys_role=SysRole.BUSINESS_ADMIN.value,
    )
    assert wl_api.update_whitelist("13800000001", promoted, user=actor)["ok"]
    assert wl.get_user_any("13800000001")["community"] == ""


def test_import_csv_endpoint_is_disabled(tmp_db):
    actor = _user()
    with pytest.raises(HTTPException) as exc:
        wl_api.import_whitelist_csv(user=actor)
    assert exc.value.status_code == 403
    assert "已停用" in exc.value.detail


def test_region_points_endpoint_scopes_catalog():
    assert region_api.list_region_points(user=_user())["count"] == 719
    district_user = _user(SysRole.BUSINESS_ADMIN.value, AdminLevel.DISTRICT.value, "贵阳市", "南明区")
    scoped = region_api.list_region_points(user=district_user)
    assert scoped["count"] > 0
    assert all(point["county"] == "南明区" for point in scoped["points"])
