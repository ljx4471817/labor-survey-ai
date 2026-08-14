"""backend/app/services/quiz_llm.py — 测验模块独立 LLM 配置（与对话三级路由完全隔离）。

- 配置：backend/data/quiz_llm_config.json（运行时文件，默认 qwen-flash/dashscope）。
- 切换：仅系统管理员（require_system_admin 在 API 层）；切换前 probe 一次可用性。
- 调用：直接按 provider 直连（复用 llm_router.provider_config 的 URL/Key/模型名），
  不经过 llm_route.json，因此不影响文档程序（对话）的模型。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from loguru import logger

from app.core.config import PROJECT_ROOT
from app.services import llm_router

CONFIG_FILE = PROJECT_ROOT / "backend" / "data" / "quiz_llm_config.json"
DEFAULT_PROVIDER = "dashscope"  # qwen-flash：批量任务成本敏感，默认不占 MiniMax 套餐额度
ALLOWED_PROVIDERS = ("minimax", "dashscope", "deepseek")
DISPLAY = {
    "minimax": "MiniMax M2.7-highspeed",
    "dashscope": "qwen-flash",
    "deepseek": "DeepSeek flash",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _default() -> dict:
    return {
        "provider": DEFAULT_PROVIDER,
        "model": DISPLAY[DEFAULT_PROVIDER],
        "updated_at": None,
        "updated_by": None,
        "updated_by_name": None,
    }


def load_config() -> dict:
    """读测验 LLM 配置；文件缺失/损坏时返回默认（qwen-flash）。"""
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        provider = data.get("provider")
        if provider not in ALLOWED_PROVIDERS:
            return _default()
        cfg = _default()
        cfg["provider"] = provider
        for k in ("updated_at", "updated_by", "updated_by_name"):
            if k in data:
                cfg[k] = data[k]
        cfg["model"] = _resolve_model(provider)
        return cfg
    except FileNotFoundError:
        return _default()
    except Exception:
        logger.exception("quiz_llm config unreadable; using default")
        return _default()


def _resolve_model(provider: str) -> str:
    """取 provider 当前实际模型名（来自 .env / llm_router 默认值）。"""
    cfg = llm_router.provider_config(provider)
    return (cfg or {}).get("model") or DISPLAY.get(provider, provider)


def save_config(provider: str, actor_phone: str, actor_name: str = "") -> dict:
    """保存测验 LLM 配置（原子写 tmp+replace），并返回新配置。"""
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError(f"不支持的 provider: {provider}")
    cfg = {
        "provider": provider,
        "model": _resolve_model(provider),
        "updated_at": _now_iso(),
        "updated_by": actor_phone,
        "updated_by_name": actor_name,
    }
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)
    logger.info("quiz LLM config -> {} (by {} {})", provider, actor_phone, actor_name)
    return cfg


def _call(cfg: dict, messages: list[dict], max_tokens: int, temperature: float, timeout: float) -> str:
    resp = requests.post(
        cfg["url"],
        headers={"Authorization": "Bearer " + cfg["api_key"], "Content-Type": "application/json"},
        json={
            "model": cfg["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"].get("content") or ""
    return llm_router.strip_thinking(content)


def probe(provider: str, timeout: float = 30) -> str:
    """切换前探测：发 1 行消息验证 Key/模型可用；成功返回模型名，失败抛异常。"""
    cfg = llm_router.provider_config(provider)
    if cfg is None:
        raise RuntimeError(f"{provider} 未配置 API Key（检查 .env）")
    _call(cfg, [{"role": "user", "content": "你好，请只回复OK"}], max_tokens=20, temperature=0.1, timeout=timeout)
    return cfg["model"]


def chat(messages: list[dict], max_tokens: int = 2000, temperature: float = 0.3, timeout: float = 90) -> str:
    """测验任务调 LLM：读独立配置直连，不影响对话模型。"""
    cfg = load_config()
    provider_cfg = llm_router.provider_config(cfg["provider"])
    if provider_cfg is None:
        raise RuntimeError(f"quiz LLM provider {cfg['provider']} 未配置 API Key（检查 .env）")
    return _call(provider_cfg, messages, max_tokens, temperature, timeout)
