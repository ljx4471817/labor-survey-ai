"""Call LLM (OpenAI-compatible Chat Completions), routed by llm_router.

回退链：minimax（主）-> dashscope/qwen-flash -> deepseek。
连接级失败（连不上/超时/5xx/429）在**同一次请求内**沿链重试，
避免单个 provider 抖动直接变成整站 500（2026-09-21 实测 MiniMax 连接中断冒泡成 500）。
"""
from __future__ import annotations

import time

import requests
from loguru import logger

from app.services.llm_router import resolve_llm_chain, strip_thinking

# 某个 provider 失败后冷却多久（进程内、不落盘）：期间它排到链尾，避免每次都白等一次
UNHEALTHY_COOLDOWN_S = 60.0
_UNHEALTHY: dict[str, float] = {}


def _is_fallback_worthy(exc: Exception) -> bool:
    """只对「provider 侧不可用」回退；4xx 是本项目自己的请求问题，直接抛。"""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(code, int):
        return code == 429 or code >= 500
    # 200 但响应体不可解析（choices/message 缺失）也算 provider 侧异常
    return isinstance(exc, (KeyError, IndexError, ValueError))


def _order_by_health(cfgs: list[dict]) -> list[dict]:
    """把冷却中的 provider 排到最后；全部冷却时保持原序，仍逐个尝试。"""
    now = time.time()
    healthy = [c for c in cfgs if _UNHEALTHY.get(c["provider"], 0.0) <= now]
    cooling = [c for c in cfgs if _UNHEALTHY.get(c["provider"], 0.0) > now]
    return healthy + cooling


def _call_once(
    cfg: dict,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> str:
    """向单个 provider 发一次请求并取回正文（不含回退逻辑）。"""
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


def chat(
    messages: list[dict],
    temperature: float = 0.3,
    max_tokens: int = 2000,
    timeout: float = 60,
) -> str:
    """Call LLM and return assistant text (strip MiniMax M2.x <think> blocks).

    max_tokens defaults to 2000: deepseek-v4-flash / MiniMax M2.7 think first, keep room.
    timeout defaults to 60s; heavy tasks (quiz extract/generate) may pass 90s.
    连接级失败时按回退链尝试下一个 provider，全部失败才抛错（原样冒泡）。
    """
    cfgs = _order_by_health(resolve_llm_chain())
    last_exc: Exception | None = None
    for idx, cfg in enumerate(cfgs):
        try:
            text = _call_once(cfg, messages, temperature, max_tokens, timeout)
        except Exception as exc:  # noqa: BLE001 —— 分类后决定回退还是原样抛
            if not _is_fallback_worthy(exc):
                raise
            last_exc = exc
            _UNHEALTHY[cfg["provider"]] = time.time() + UNHEALTHY_COOLDOWN_S
            logger.warning(
                "LLM provider {} 不可用（{}: {}）；尝试回退链中的下一个",
                cfg["provider"],
                type(exc).__name__,
                exc,
            )
            continue
        _UNHEALTHY.pop(cfg["provider"], None)
        if idx > 0:
            logger.warning("LLM 回退成功：provider={} model={}", cfg["provider"], cfg["model"])
        return text
    raise RuntimeError(f"所有 LLM provider 均调用失败：{last_exc}") from last_exc
