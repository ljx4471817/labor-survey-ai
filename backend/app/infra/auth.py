"""手机号白名单 + HMAC token 鉴权。

- 零外部依赖（只用 stdlib：hmac、hashlib、secrets、base64）
- Token 格式：`<base64url(payload)>.<base64url(hmac_sig)>`
  payload = `f"{phone}|{exp_unix}|{nonce}"`
- 白名单来源：`backend/data/whitelist.db`（按 DB / WAL 文件 mtime 热加载，
  停用立即生效——WAL 模式下写提交只落 -wal 文件，必须把 wal/shm 的 mtime 也纳入缓存键）
- 生产必须设 `LSX_AUTH_SECRET`；缺则启动时随机生成（每次重启 token 失效，仅供 dev）

双维度权限（PRD 权限系统改造）：
- require_user：登录 + 每次请求查 whitelist 确认 active（修复离职 24h 延迟）
- require_whitelist_admin：sys_role ∈ {系统管理员, 业务管理员}，返回完整 user（含区域）
- require_quiz_admin：sys_role ∈ {系统管理员, 业务管理员} 且 admin_level ∈ {省级, 市级}
- require_quiz_stats：sys_role ∈ {系统管理员, 业务管理员}（测验完成率只读，区县可看本县）
- require_system_admin：sys_role = 系统管理员
"""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from fastapi import Header, HTTPException

from app.core.constants import AdminLevel, SysRole
from app.persistence.whitelist_db import DB_PATH, get_user, list_active_phones

_TOKEN_TTL = int(os.environ.get("LSX_TOKEN_TTL_HOURS", "24")) * 3600

# 保护测试号：仅系统管理员可停用/删除/编辑，其余角色 403。
PROTECTED_PHONES = frozenset(
    {"13985000001", "13985000002", "13985000003", "13985000004"}
)

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
def _cached_whitelist(cache_key: tuple[int, ...]) -> frozenset[str]:
    return frozenset(list_active_phones())


def _whitelist_cache_key() -> tuple[int, ...]:
    """DB + WAL + SHM 的 mtime 作为缓存键（WAL 提交只改 -wal，必须一起看）。"""
    keys: list[int] = []
    for suffix in ("", "-wal", "-shm"):
        try:
            keys.append(Path(str(DB_PATH) + suffix).stat().st_mtime_ns)
        except (FileNotFoundError, OSError):
            keys.append(0)
    return tuple(keys)


def load_whitelist() -> frozenset[str]:
    """读白名单 active 列表；DB 文件不存在 → 返回空集。"""
    try:
        return _cached_whitelist(_whitelist_cache_key())
    except (FileNotFoundError, OSError):
        return frozenset()


def require_user(authorization: str | None = Header(None)) -> str:
    """FastAPI Depends：登录 + 每次请求查 whitelist 确认 active；失败抛 401。

    修复离职账号 token 在 TTL 内仍可用的漏洞（停用即时生效）。
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    token = authorization[len("Bearer "):].strip()
    payload = verify_token(token)
    if not payload:
        raise HTTPException(401, "登录已失效，请重新登录")
    if payload["phone"] not in load_whitelist():
        raise HTTPException(401, "账号已停用，请联系管理员")
    return payload["phone"]


def get_current_user(phone: str) -> dict | None:
    """鉴权后取完整用户记录（含 region / sys_role 字段）。返回 None 表示被软删。"""
    return get_user(phone)


# --- 纯函数：区域范围 / 层级上限 / 保护号（必须有单测） ----------------------

def region_scope(user: dict | None) -> tuple[str, str, str] | None:
    """业务管辖范围 (province, city, county)；系统管理员返回 None（无限制）。

    省级 = (province, "", "")；市级 = (province, city, "")；区县 = (province, city, county)。
    """
    if not user:
        return None
    if (user.get("sys_role") or SysRole.USER.value) == SysRole.SYSTEM_ADMIN.value:
        return None
    level = user.get("admin_level") or AdminLevel.ENUMERATOR.value
    province = user.get("province") or ""
    city = user.get("city") or ""
    county = user.get("county") or ""
    if level == AdminLevel.PROVINCE.value:
        return (province, "", "")
    if level == AdminLevel.CITY.value:
        return (province, city, "")
    return (province, city, county)


def in_scope(actor: dict | None, target: dict | None) -> bool:
    """目标记录是否在 actor 管辖范围内；系统管理员 / 无 actor 无限制。"""
    scope = region_scope(actor)
    if scope is None or not target:
        return True
    province, city, county = scope
    if not province:
        return True
    if (target.get("province") or "") != province:
        return False
    if city and (target.get("city") or "") != city:
        return False
    if county and (target.get("county") or "") != county:
        return False
    return True


def allowed_admin_levels(actor: dict | None) -> tuple[str, ...]:
    """actor 可为目标设置的管理员层级上限。系统管理员全部；业务管理员逐级收紧。"""
    if not actor:
        return ()
    if (actor.get("sys_role") or SysRole.USER.value) == SysRole.SYSTEM_ADMIN.value:
        return AdminLevel.values()
    level = actor.get("admin_level") or AdminLevel.ENUMERATOR.value
    if level == AdminLevel.PROVINCE.value:
        return (AdminLevel.CITY.value, AdminLevel.DISTRICT.value, AdminLevel.ENUMERATOR.value)
    if level == AdminLevel.CITY.value:
        return (AdminLevel.DISTRICT.value, AdminLevel.ENUMERATOR.value)
    if level == AdminLevel.DISTRICT.value:
        return (AdminLevel.ENUMERATOR.value,)
    return ()


def is_protected_phone(phone: str) -> bool:
    """保护测试号 13985000001-4：仅系统管理员可操作。"""
    return phone in PROTECTED_PHONES


# --- 角色依赖 ----------------------------------------------------------------

def require_whitelist_admin(authorization: str | None = Header(None)) -> dict:
    """FastAPI Depends：登录 + 白名单管理员（系统/业务），返回完整 user（含区域）。"""
    phone = require_user(authorization)
    user = get_current_user(phone)
    if not user:
        raise HTTPException(401, "登录已失效，请重新登录")
    if user.get("sys_role") not in (
        SysRole.SYSTEM_ADMIN.value, SysRole.BUSINESS_ADMIN.value
    ):
        raise HTTPException(403, "无白名单管理权限")
    return user


def require_quiz_admin(authorization: str | None = Header(None)) -> str:
    """FastAPI Depends：测验管理（系统管理员或省级/市级业务管理员），返回 phone。"""
    phone = require_user(authorization)
    user = get_current_user(phone)
    if not user:
        raise HTTPException(401, "登录已失效，请重新登录")
    role = user.get("sys_role") or SysRole.USER.value
    if role == SysRole.SYSTEM_ADMIN.value:
        return phone
    if role != SysRole.BUSINESS_ADMIN.value:
        raise HTTPException(403, "无测验管理权限")
    if user.get("admin_level") not in (AdminLevel.PROVINCE.value, AdminLevel.CITY.value):
        raise HTTPException(403, "无测验管理权限")
    return phone


def require_quiz_stats(authorization: str | None = Header(None)) -> str:
    """FastAPI Depends：测验完成率只读（系统/业务管理员均可，区县看本县），返回 phone。"""
    phone = require_user(authorization)
    user = get_current_user(phone)
    if not user:
        raise HTTPException(401, "登录已失效，请重新登录")
    if user.get("sys_role") not in (
        SysRole.SYSTEM_ADMIN.value, SysRole.BUSINESS_ADMIN.value
    ):
        raise HTTPException(403, "无统计查看权限")
    return phone


def require_system_admin(authorization: str | None = Header(None)) -> str:
    """FastAPI Depends：系统管理员（反馈/KB/LLM/审计/CSV 导入），返回 phone。"""
    phone = require_user(authorization)
    user = get_current_user(phone)
    if not user:
        raise HTTPException(401, "登录已失效，请重新登录")
    if (user.get("sys_role") or SysRole.USER.value) != SysRole.SYSTEM_ADMIN.value:
        raise HTTPException(403, "仅系统管理员可操作")
    return phone


def require_admin(authorization: str | None = Header(None)) -> str:
    """兼容旧名：等同 require_quiz_admin（PRD v3 6.3 升级后的语义）。"""
    return require_quiz_admin(authorization)
