"""Call LLM (OpenAI-compatible Chat Completions), routed by llm_router (MiniMax primary / DeepSeek fallback)."""
from __future__ import annotations

import requests
from loguru import logger

from app.services.llm_router import resolve_llm_config, strip_thinking


def chat(
    messages: list[dict],
    temperature: float = 0.3,
    max_tokens: int = 2000,
    timeout: float = 60,
) -> str:
    """Call LLM and return assistant text (strip MiniMax M2.x <think> blocks).

    max_tokens defaults to 2000: deepseek-v4-flash / MiniMax M2.7 think first, keep room.
    timeout defaults to 60s; heavy tasks (quiz extract/generate) may pass 90s.
    """
    cfg = resolve_llm_config()
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    logger.debug("LLM call provider={} model={}", cfg["provider"], cfg["model"])
    resp = requests.post(cfg["url"], headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"].get("content") or ""
    return strip_thinking(content)
