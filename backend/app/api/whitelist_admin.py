"""白名单 CRUD + CSV 批量导入。"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from loguru import logger

from app.models.schemas import WhitelistEntry
from app.persistence.whitelist_db import (
    bulk_import_csv,
    delete as whitelist_delete,
    list_all as whitelist_list,
    upsert as whitelist_upsert,
)

router = APIRouter()


@router.get("/whitelist")
def list_whitelist() -> dict:
    """全量白名单列表（含 inactive）。"""
    return {"items": whitelist_list(active_only=False)}


@router.post("/whitelist")
def create_whitelist(entry: WhitelistEntry) -> dict:
    """新增或更新一条白名单。"""
    action = whitelist_upsert(entry.model_dump())
    logger.info(f"whitelist: {action} phone={entry.phone[:3]}****")
    return {"ok": True, "action": action}


@router.put("/whitelist/{phone}")
def update_whitelist(phone: str, entry: WhitelistEntry) -> dict:
    """编辑白名单条目。"""
    if phone != entry.phone:
        raise HTTPException(400, "phone 路径参数与 body 不一致")
    action = whitelist_upsert(entry.model_dump())
    logger.info(f"whitelist: {action} phone={phone[:3]}****")
    return {"ok": True, "action": action}


@router.delete("/whitelist/{phone}")
def remove_whitelist(phone: str) -> dict:
    """白名单软删。"""
    ok = whitelist_delete(phone, soft=True)
    if not ok:
        raise HTTPException(404, "条目不存在或已删除")
    logger.info(f"whitelist: soft-deleted phone={phone[:3]}****")
    return {"ok": True}


@router.post("/whitelist/import-csv")
async def import_whitelist_csv(file: UploadFile = File(...)) -> dict:
    """上传 CSV 批量导入白名单。

    必填表头：phone, name, province, city, admin_level
    选填表头：county, township, community, remark
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")  # Excel 导出的 UTF-8-BOM 也兼容
    except UnicodeDecodeError:
        text = raw.decode("gbk")
    result = bulk_import_csv(text)
    logger.info(
        f"whitelist csv import: inserted={result['inserted']} "
        f"updated={result['updated']} errors={len(result['errors'])}"
    )
    return result
