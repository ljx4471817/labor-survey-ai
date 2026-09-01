# -*- coding: utf-8 -*-
"""Standard survey-point catalog used by whitelist entry forms.

The JSON file is a small operational dataset instead of a database table:
it is read-only configuration and only changes when a new workbook is converted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from app.core.constants import AdminLevel


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REGION_POINTS_PATH = PROJECT_ROOT / "backend" / "data" / "region_points.json"
REGION_FIELDS = ("province", "city", "county", "township", "community")

_REQUIRED_BY_LEVEL = {
    AdminLevel.PROVINCE.value: ("province",),
    AdminLevel.CITY.value: ("province", "city"),
    AdminLevel.DISTRICT.value: ("province", "city", "county"),
    AdminLevel.ENUMERATOR.value: REGION_FIELDS,
}
_FORBIDDEN_BY_LEVEL = {
    AdminLevel.PROVINCE.value: ("city", "county", "township", "community"),
    AdminLevel.CITY.value: ("county", "township", "community"),
    AdminLevel.DISTRICT.value: ("township", "community"),
    AdminLevel.ENUMERATOR.value: (),
}


def load_region_points(path: Path = REGION_POINTS_PATH) -> tuple[dict[str, str], ...]:
    """Load and minimally validate the standard survey-point catalog."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("标准调查点数据文件不存在") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("标准调查点数据文件格式错误") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError("标准调查点数据为空")

    points: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"标准调查点第 {index} 行格式错误")
        point: dict[str, str] = {}
        for field in REGION_FIELDS:
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"标准调查点第 {index} 行缺少 {field}")
            point[field] = value.strip()
        key = tuple(point[field] for field in REGION_FIELDS)
        if key in seen:
            raise ValueError(f"标准调查点第 {index} 行重复")
        seen.add(key)
        points.append(point)
    return tuple(points)


def filter_region_points(
    points: Iterable[dict[str, str]], scope: tuple[str, str, str] | None
) -> tuple[dict[str, str], ...]:
    """Filter the catalog by an administrator's region_scope tuple."""
    if scope is None:
        return tuple(points)
    province, city, county = scope
    return tuple(
        point for point in points
        if point["province"] == province
        and (not city or point["city"] == city)
        and (not county or point["county"] == county)
    )


def validate_account_scope(admin_level: str, sys_role: str) -> None:
    """Keep admin_level and sys_role in the semantic combinations confirmed by the product owner."""
    if sys_role == "系统管理员":
        if admin_level != AdminLevel.PROVINCE.value:
            raise ValueError("系统管理员的管理范围只能是省级")
        return
    if sys_role == "业务管理员":
        if admin_level not in (AdminLevel.PROVINCE.value, AdminLevel.CITY.value, AdminLevel.DISTRICT.value):
            raise ValueError("业务管理员的管理范围只能是省级、市级或区县")
        return
    if sys_role == "普通用户":
        if admin_level != AdminLevel.ENUMERATOR.value:
            raise ValueError("普通用户的管理范围只能是调查员")
        return
    raise ValueError(f"未知账号类型：{sys_role}")


def validate_region_selection(
    points: Iterable[dict[str, str]],
    *,
    admin_level: str,
    province: str,
    city: str,
    county: str,
    township: str,
    community: str,
) -> None:
    """Validate region values against the selected management scope."""
    catalog = tuple(points)
    if not catalog:
        raise ValueError("标准调查点数据为空")
    values = {
        "province": province.strip(),
        "city": city.strip(),
        "county": county.strip(),
        "township": township.strip(),
        "community": community.strip(),
    }
    if admin_level not in _REQUIRED_BY_LEVEL:
        raise ValueError(f"未知管理范围：{admin_level}")

    missing = [name for name in _REQUIRED_BY_LEVEL[admin_level] if not values[name]]
    if missing:
        labels = {"province": "省", "city": "市/州", "county": "县/区", "township": "乡镇/街道", "community": "社区/村"}
        raise ValueError(f"请选择{'、'.join(labels[name] for name in missing)}")

    forbidden = [name for name in _FORBIDDEN_BY_LEVEL[admin_level] if values[name]]
    if forbidden:
        labels = {"city": "市/州", "county": "县/区", "township": "乡镇/街道", "community": "社区/村"}
        raise ValueError(f"当前管理范围不应选择{'、'.join(labels[name] for name in forbidden)}")

    provinces = {point["province"] for point in catalog}
    if values["province"] not in provinces:
        raise ValueError("所选省份不在标准调查点数据中")
    if "city" not in _REQUIRED_BY_LEVEL[admin_level]:
        return

    cities = {point["city"] for point in catalog if point["province"] == values["province"]}
    if values["city"] not in cities:
        raise ValueError("所选市/州不在标准调查点数据中")
    if "county" not in _REQUIRED_BY_LEVEL[admin_level]:
        return

    counties = {
        point["county"] for point in catalog
        if point["province"] == values["province"] and point["city"] == values["city"]
    }
    if values["county"] not in counties:
        raise ValueError("所选县/区不在标准调查点数据中")
    if admin_level != AdminLevel.ENUMERATOR.value:
        return

    key = tuple(values[field] for field in REGION_FIELDS)
    catalog_keys = {tuple(point[field] for field in REGION_FIELDS) for point in catalog}
    if key not in catalog_keys:
        raise ValueError("没有对应调查点？反馈给系统管理员")
