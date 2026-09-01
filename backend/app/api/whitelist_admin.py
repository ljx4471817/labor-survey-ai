# -*- coding: utf-8 -*-
"""白名单管理 API（PRD 权限系统改造 M2）。

- whoami / 列表 / 新增 / 更新 / 启用 / 软删除 / 批量停用 / 导出 / 审计
- 所有写操作写审计（whitelist_audit）；手机号日志脱敏 phone[:3]****
- 业务管理员操作受 scope + admin_level 上限 + 保护号 + 系统管理员账号四重约束
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from loguru import logger
from openpyxl import Workbook

from app.core.constants import AdminLevel, SysRole
from app.infra.auth import (
    allowed_admin_levels,
    in_scope,
    is_protected_phone,
    require_system_admin,
    require_whitelist_admin,
)
from app.models.schemas import BatchDisableRequest, WhitelistEntry
from app.persistence import whitelist_db
from app.services.region_points import (
    REGION_FIELDS,
    load_region_points,
    validate_account_scope,
    validate_region_selection,
)

router = APIRouter()


def _mask(phone: str) -> str:
    """脱敏：13985000001 -> 139****0001。"""
    if len(phone) < 7:
        return phone[:3] + "****"
    return phone[:3] + "****" + phone[-4:]


def _derive_sys_role(admin_level: str | None, requested: str | None) -> str:
    """业务层级 -> 系统职能 的默认映射（与迁移回填一致）。"""
    if admin_level in (AdminLevel.PROVINCE.value, AdminLevel.CITY.value, AdminLevel.DISTRICT.value):
        return SysRole.BUSINESS_ADMIN.value
    return SysRole.USER.value


def _effective_sys_role(actor: dict, admin_level: str | None, requested: str | None) -> str:
    """业务管理员无法授予/修改 sys_role：忽略 body，按 admin_level 推导。"""
    if (actor.get("sys_role") or SysRole.USER.value) == SysRole.SYSTEM_ADMIN.value:
        return requested or _derive_sys_role(admin_level, requested)
    return _derive_sys_role(admin_level, requested)


def _is_system_admin(actor: dict) -> bool:
    return (actor.get("sys_role") or SysRole.USER.value) == SysRole.SYSTEM_ADMIN.value


def _ensure_operable(actor: dict, target: dict | None, phone: str) -> None:
    """白名单操作的统一越权校验：404/403。"""
    if target is None:
        raise HTTPException(404, "条目不存在")
    if not _is_system_admin(actor):
        if (target.get("sys_role") or SysRole.USER.value) == SysRole.SYSTEM_ADMIN.value:
            raise HTTPException(403, "不可操作系统管理员账号")
        if is_protected_phone(phone):
            raise HTTPException(403, "保护测试号仅系统管理员可操作")
        if not in_scope(actor, target):
            raise HTTPException(403, "目标不在管辖范围内")


def _ensure_admin_level_allowed(actor: dict, admin_level: str) -> None:
    if admin_level not in allowed_admin_levels(actor):
        raise HTTPException(403, f"不可设置管理员层级：{admin_level}")


def _clean_entry(entry: WhitelistEntry) -> WhitelistEntry:
    """Trim boundary text without mutating the request model in place."""
    text_fields = ("phone", "name", "province", "city", "county", "township", "community", "remark")
    return entry.model_copy(update={field: getattr(entry, field).strip() for field in text_fields})


def _region_changed(before: dict | None, after: dict) -> bool:
    if before is None:
        return True
    return any((before.get(field) or "").strip() != (after.get(field) or "").strip() for field in REGION_FIELDS)


def _region_http_error(exc: ValueError) -> HTTPException:
    return HTTPException(422, str(exc))


def _audit(actor: dict, action: str, phone: str, before: dict | None = None, after: dict | None = None) -> None:
    whitelist_db.log_audit(
        actor_phone=actor["phone"],
        actor_name=(actor.get("name") or "").strip() or None,
        action=action,
        target_phone=phone,
        before=before,
        after=after,
    )
    logger.info(f"whitelist audit: {action} phone={_mask(phone)} by={_mask(actor['phone'])}")


# --- 角色探测 -----------------------------------------------------------------

@router.get("/whitelist/whoami")
def whoami(user: dict = Depends(require_whitelist_admin)) -> dict:
    """返回当前管理员身份信息，前端角色探测。"""
    return {
        "phone": user["phone"],
        "name": user.get("name", ""),
        "admin_level": user.get("admin_level", ""),
        "sys_role": user.get("sys_role", ""),
        "province": user.get("province", ""),
        "city": user.get("city", ""),
        "county": user.get("county", ""),
    }


# --- 列表 ---------------------------------------------------------------------

@router.get("/whitelist")
def list_whitelist(user: dict = Depends(require_whitelist_admin)) -> dict:
    """白名单列表（含 inactive，供恢复入口）；业务管理员按 scope 过滤。"""
    items = [r for r in whitelist_db.list_all(active_only=False) if in_scope(user, r)]
    return {"items": items}


# --- 新增 / 更新 ---------------------------------------------------------------

@router.post("/whitelist")
def create_whitelist(entry: WhitelistEntry, user: dict = Depends(require_whitelist_admin)) -> dict:
    """新增；校验标准调查点、scope、admin_level 上限；业务管理员忽略 body 中 sys_role。"""
    entry = _clean_entry(entry)
    if whitelist_db.get_user_any(entry.phone):
        raise HTTPException(409, "该手机号已存在，请直接编辑")
    _ensure_admin_level_allowed(user, entry.admin_level)
    new_sys_role = _effective_sys_role(user, entry.admin_level, entry.sys_role)
    try:
        validate_account_scope(entry.admin_level, new_sys_role)
        validate_region_selection(
            load_region_points(),
            admin_level=entry.admin_level,
            province=entry.province,
            city=entry.city,
            county=entry.county,
            township=entry.township,
            community=entry.community,
        )
    except ValueError as exc:
        raise _region_http_error(exc) from exc
    new_record = {**entry.model_dump(), "sys_role": new_sys_role}
    if not in_scope(user, new_record):
        raise HTTPException(403, "目标不在管辖范围内")
    if not _is_system_admin(user) and is_protected_phone(entry.phone):
        raise HTTPException(403, "保护测试号仅系统管理员可操作")
    action = whitelist_db.upsert({k: v for k, v in new_record.items() if v is not None})
    _audit(user, "create", entry.phone, after=new_record)
    logger.info(f"whitelist: {action} phone={_mask(entry.phone)}")
    return {"ok": True, "action": action}


@router.put("/whitelist/{phone}")
def update_whitelist(phone: str, entry: WhitelistEntry, user: dict = Depends(require_whitelist_admin)) -> dict:
    """编辑条目；不改变 active（停用后编辑不会复活）；sys_role 仅系统管理员可改。"""
    if phone != entry.phone:
        raise HTTPException(400, "phone 路径参数与 body 不一致")
    entry = _clean_entry(entry)
    before = whitelist_db.get_user_any(phone)
    _ensure_operable(user, before, phone)
    _ensure_admin_level_allowed(user, entry.admin_level)
    new_sys_role = _effective_sys_role(user, entry.admin_level, entry.sys_role)
    after = {**entry.model_dump(), "sys_role": new_sys_role, "active": before["active"]}
    if not in_scope(user, after):
        raise HTTPException(403, "目标不在管辖范围内")

    scope_changed = (before.get("admin_level") or "") != entry.admin_level
    role_changed = (before.get("sys_role") or SysRole.USER.value) != new_sys_role
    try:
        if scope_changed or role_changed:
            validate_account_scope(entry.admin_level, new_sys_role)
        if _region_changed(before, after) or scope_changed:
            validate_region_selection(
                load_region_points(),
                admin_level=entry.admin_level,
                province=entry.province,
                city=entry.city,
                county=entry.county,
                township=entry.township,
                community=entry.community,
            )
    except ValueError as exc:
        raise _region_http_error(exc) from exc

    whitelist_db.upsert({k: v for k, v in after.items() if v is not None})
    _audit(user, "update", phone, before=before, after=after)
    if (before.get("sys_role") or SysRole.USER.value) != new_sys_role:
        _audit(user, "sys_role_change", phone, before=before, after=after)
    logger.info(f"whitelist: updated phone={_mask(phone)}")
    return {"ok": True, "action": "updated"}


# --- 启停 / 批量停用 -------------------------------------------------------------

@router.patch("/whitelist/{phone}/enable")
def enable_whitelist(phone: str, user: dict = Depends(require_whitelist_admin)) -> dict:
    """重新启用（原 UI 缺恢复入口）。"""
    before = whitelist_db.get_user_any(phone)
    _ensure_operable(user, before, phone)
    if not whitelist_db.enable(phone):
        raise HTTPException(404, "条目不存在")
    after = whitelist_db.get_user_any(phone)
    _audit(user, "enable", phone, before=before, after=after)
    logger.info(f"whitelist: enabled phone={_mask(phone)}")
    return {"ok": True}


@router.delete("/whitelist/{phone}")
def remove_whitelist(phone: str, user: dict = Depends(require_whitelist_admin)) -> dict:
    """白名单软删除（停用即时生效）。"""
    before = whitelist_db.get_user_any(phone)
    _ensure_operable(user, before, phone)
    ok = whitelist_db.delete(phone, soft=True)
    if not ok:
        raise HTTPException(404, "条目不存在或已删除")
    after = whitelist_db.get_user_any(phone)
    _audit(user, "disable", phone, before=before, after=after)
    logger.info(f"whitelist: soft-deleted phone={_mask(phone)}")
    return {"ok": True}


@router.post("/whitelist/batch-disable")
def batch_disable(req: BatchDisableRequest, user: dict = Depends(require_whitelist_admin)) -> dict:
    """批量停用；逐条校验 scope / 保护号 / 系统管理员账号；部分跳过不报错。"""
    disabled = 0
    skipped: list[dict] = []
    for phone in req.phones:
        target = whitelist_db.get_user_any(phone)
        if not target:
            skipped.append({"phone": _mask(phone), "reason": "not_found"})
            continue
        if not _is_system_admin(user):
            if is_protected_phone(phone):
                skipped.append({"phone": _mask(phone), "reason": "protected"})
                continue
            if (target.get("sys_role") or SysRole.USER.value) == SysRole.SYSTEM_ADMIN.value:
                skipped.append({"phone": _mask(phone), "reason": "system_admin"})
                continue
            if not in_scope(user, target):
                skipped.append({"phone": _mask(phone), "reason": "out_of_scope"})
                continue
        if whitelist_db.delete(phone, soft=True):
            disabled += 1
            after = whitelist_db.get_user_any(phone)
            _audit(user, "batch_disable", phone, before=target, after=after)
    logger.info(
        f"whitelist batch-disable: disabled={disabled} skipped={len(skipped)} by={_mask(user['phone'])}"
    )
    return {"ok": True, "disabled": disabled, "skipped": skipped}


# --- 导出 ----------------------------------------------------------------------

def _export_rows(user: dict) -> list[dict]:
    return [r for r in whitelist_db.list_all(active_only=True) if in_scope(user, r)]


def _row_values(r: dict) -> list[str]:
    return [
        r.get("province") or "", r.get("city") or "", r.get("county") or "",
        r.get("community") or "", r.get("name") or "", r.get("phone") or "",
        r.get("admin_level") or "调查员", r.get("remark") or "",
    ]


@router.get("/whitelist/export")
def export_whitelist(user: dict = Depends(require_whitelist_admin)) -> Response:
    """导出：区县 -> CSV；市级/省级/系统管理员 -> xlsx 双 sheet（调查员/管理人员）。"""
    rows = _export_rows(user)
    if user.get("admin_level") == AdminLevel.DISTRICT.value and not _is_system_admin(user):
        return _export_csv(rows)

    enumerators = [r for r in rows if (r.get("admin_level") or "调查员") == AdminLevel.ENUMERATOR.value]
    managers = [r for r in rows if (r.get("admin_level") or "调查员") != AdminLevel.ENUMERATOR.value]
    return _export_xlsx(enumerators, managers)


def _export_csv(rows: list[dict]) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["省", "市", "县", "调查小区", "姓名", "联系电话", "管理员层级", "备注"])
    for r in rows:
        writer.writerow(_row_values(r))
    content = "\ufeff" + buf.getvalue()  # Excel 中文兼容
    from urllib.parse import quote
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote("白名单_本县.csv")},
    )


def _export_xlsx(enumerators: list[dict], managers: list[dict]) -> Response:
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "调查员"
    ws1.append(["省", "市", "县", "调查小区", "姓名", "联系电话", "管理员层级", "备注"])
    for r in enumerators:
        ws1.append(_row_values(r))
    ws2 = wb.create_sheet("管理人员")
    ws2.append(["省", "市", "县", "姓名", "联系电话", "管理员层级", "备注"])
    for r in managers:
        ws2.append(_row_values(r)[:5] + _row_values(r)[6:])
    from io import BytesIO
    from urllib.parse import quote

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote("白名单导出.xlsx")},
    )


# --- 审计 ----------------------------------------------------------------------

@router.get("/whitelist/audit")
def whitelist_audit(
    limit: int = Query(100, ge=1, le=500),
    target_phone: str | None = Query(None, max_length=11),
    phone: str = Depends(require_system_admin),
) -> dict:
    """最近审计记录（仅系统管理员），倒序。"""
    items = whitelist_db.list_audit(limit=limit, target_phone=target_phone or None)
    for it in items:
        it["target_phone"] = _mask(it["target_phone"])
        it["actor_phone"] = _mask(it["actor_phone"])
    return {"items": items, "count": len(items)}


# --- CSV 批量导入（已停用） ------------------------------------------------------

@router.post("/whitelist/import-csv")
def import_whitelist_csv(user: dict = Depends(require_whitelist_admin)) -> dict:
    """Disabled endpoint: manual page entry is the supported workflow."""
    raise HTTPException(403, "批量导入功能已停用")
