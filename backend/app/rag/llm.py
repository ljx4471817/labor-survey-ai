"""调 LLM（OpenAI 兼容 Chat Completions）。"""
from __future__ import annotations

import requests
from loguru import logger

from app.core.config import settings


def chat(
    messages: list[dict],
    temperature: float = 0.3,
    max_tokens: int = 2000,
    timeout: float = 60,
) -> str:
    """调 LLM 返回 assistant 文本。

    max_tokens 默认 2000：v4-flash 会先 reasoning 再回答，预留空间。
    timeout 默认 60s；出题/提取等重任务可调大到 90s。
    """
    if not settings.llm_api_key:
        raise RuntimeError(
            f"未配置 {settings.llm_provider.upper()} API Key，无法调用 LLM"
        )
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    resp = requests.post(
        settings.llm_url, headers=headers, json=payload, timeout=timeout
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"].get("content") or ""
    return content.strip()
