"""POST /api/auth/login + GET /api/auth/check。

login：手机号白名单校验 → HMAC 签发 token。
check：探测当前 token 有效性（前端探测 localStorage 残留 token）。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.infra.auth import load_whitelist, require_user, sign_token
from app.persistence import whitelist_db

router = APIRouter()


class LoginRequest(BaseModel):
    # 11 位中国大陆手机号（1[3-9] 起头），不校验号段真实性。
    phone: str = Field(..., min_length=11, max_length=11, pattern=r"^1[3-9]\d{9}$")


@router.post("/login")
def login(req: LoginRequest) -> dict:
    whitelist = load_whitelist()
    if req.phone not in whitelist:
        logger.warning(f"login: 未授权手机号 {req.phone[:3]}****")
        raise HTTPException(401, "手机号未授权")
    token, exp = sign_token(req.phone)
    u = whitelist_db.get_user(req.phone)
    name = (u.get("name") or "").strip() if u else ""
    logger.info(f"login: phone={req.phone[:3]}**** exp={exp} name={name}")
    return {"token": token, "expires_at": exp, "phone": req.phone, "name": name}


@router.get("/check")
def check(phone: str = Depends(require_user)) -> dict:
    """前端探测 localStorage 残留 token 有效性。"""
    return {"ok": True, "phone": phone, "now": int(time.time())}
