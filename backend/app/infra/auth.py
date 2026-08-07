"""手机号白名单 + HMAC token 鉴权。

- 零外部依赖（只用 stdlib：hmac、hashlib、secrets、base64）
- Token 格式：`<base64url(payload)>.<base64url(hmac_sig)>`
  payload = `f"{phone}|{exp_unix}|{nonce}"`
- 白名单来源：`backend/data/whitelist.db`（按 DB 文件 mtime 热加载）
- 生产必须设 `LSX_AUTH_SECRET`；缺则启动时随机生成（每次重启 token 失效，仅供 dev）

DB 细节见 `whitelist_db.py`；本文件只做 token + 鉴权编排。
"""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import os
import secrets
import time

from fastapi import Header, HTTPException

from app.persistence.whitelist_db import DB_PATH, get_user, list_active_phones

_TOKEN_TTL = int(os.environ.get("LSX_TOKEN_TTL_HOURS", "24")) * 3600

# Per-process 随机密钥兜底（仅 dev）。生产必须设 LSX_AUTH_SECRET。
_DEV_SECRET: str | None = None


def _secret() -> str:
    global _DEV_SECRET
    s = os.environ.get("LSX_AUTH_SECRET", "")
    if s:
        return s
    if not _DEV_SECRET:
        _DEV_SECRET = secrets.token_hex(32)
    return _DEV_SECRET


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_token(phone: str) -> tuple[str, int]:
    """签发 token；返回 (token, exp_unix)。"""
    exp = int(time.time()) + _TOKEN_TTL
    nonce = secrets.token_hex(8)
    payload = f"{phone}|{exp}|{nonce}".encode("utf-8")
    sig = hmac.new(_secret().encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64url(payload)}.{_b64url(sig)}", exp


def verify_token(token: str) -> dict | None:
    """校验 token；成功返回 {phone, exp}，失败或过期返回 None。"""
    if not token or token.count(".") != 1:
        return None
    p, s = token.split(".", 1)
    try:
        payload = _b64url_decode(p)
        sig = _b64url_decode(s)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(
        _secret().encode("utf-8"), payload, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        phone, exp_str, _nonce = payload.decode("utf-8").split("|", 2)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        return None
    if exp < int(time.time()):
        return None
    return {"phone": phone, "exp": exp}


@functools.lru_cache(maxsize=1)
def _cached_whitelist(mtime_ns: int) -> frozenset[str]:
    return frozenset(list_active_phones())


def load_whitelist() -> frozenset[str]:
    """读白名单 active 列表；DB 文件不存在 → 返回空集。"""
    try:
        return _cached_whitelist(DB_PATH.stat().st_mtime_ns)
    except (FileNotFoundError, OSError):
        return frozenset()


def require_user(authorization: str | None = Header(None)) -> str:
    """FastAPI Depends：校验 Bearer token，返回 phone；失败抛 401。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    token = authorization[len("Bearer "):].strip()
    payload = verify_token(token)
    if not payload:
        raise HTTPException(401, "登录已失效，请重新登录")
    return payload["phone"]


def get_current_user(phone: str) -> dict | None:
    """鉴权后取完整用户记录（含 region 字段）。返回 None 表示被软删。"""
    return get_user(phone)


def require_admin(authorization: str | None = Header(None)) -> str:
    """FastAPI Depends：登录 + 管理员校验（admin_level ∈ 市级/省级），返回 phone。

    quiz 管理端专用（PRD v3 6.3）：复用白名单既有 admin_level 字段，
    不改 whitelist schema。区县 / 调查员返回 403。
    """
    phone = require_user(authorization)
    user = get_current_user(phone)
    if not user:
        raise HTTPException(401, "登录已失效，请重新登录")
    if user.get("admin_level") not in ("市级", "省级"):
        raise HTTPException(403, "无管理员权限")
    return phone
