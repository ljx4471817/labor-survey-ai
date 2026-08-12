"""Runtime LLM router: MiniMax M2.7-highspeed primary, DeepSeek flash fallback.

- State file: backend/data/llm_route.json (runtime data, not committed).
- Hysteresis: switch minimax -> deepseek when 5h used >= 85% or 7d used >= 90%;
  switch back when 5h used < 70% and 7d used < 85% and cooldown elapsed.
- The 5h/7d windows are rolling (no fixed reset); switch-back is usage-driven.
"""
from __future__ import annotations

import json
import os
import re
import time

from loguru import logger

from app.core.config import PROJECT_ROOT

SWITCH_TO_FALLBACK_PCT = 85
SWITCH_BACK_PCT = 70
SWITCH_TO_FALLBACK_WEEKLY_PCT = 90
SWITCH_BACK_WEEKLY_PCT = 85
MIN_SWITCH_INTERVAL_S = 1800

PRIMARY = "minimax"
FALLBACK = "deepseek"

STATE_FILE = PROJECT_ROOT / "backend" / "data" / "llm_route.json"

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

_KNOWN_PROVIDERS = (PRIMARY, FALLBACK, "dashscope")


def strip_thinking(text: str) -> str:
    """Strip MiniMax M2.x <think>...</think> blocks from assistant content."""
    return _THINK_RE.sub("", text).strip()


def provider_config(provider: str) -> dict | None:
    """Return {provider, api_key, model, url} for a provider; None if not configured."""
    if provider == "minimax":
        api_key = os.environ.get("MINIMAX_API_KEY", "")
        model = os.environ.get("MINIMAX_MODEL", "MiniMax-M2.7-highspeed")
        url = "https://api.minimaxi.com/v1/chat/completions"
    elif provider == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
        url = "https://api.deepseek.com/v1/chat/completions"
    elif provider == "dashscope":
        api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        model = os.environ.get("DASHSCOPE_LLM_MODEL", "qwen-plus")
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    else:
        return None
    if not api_key:
        return None
    return {"provider": provider, "api_key": api_key, "model": model, "url": url}


def _default_state() -> dict:
    primary = os.environ.get("LLM_PROVIDER", PRIMARY).lower()
    if primary not in _KNOWN_PROVIDERS:
        primary = PRIMARY
    return {
        "active_provider": primary,
        "last_check_at": None,
        "used_5h_pct": None,
        "used_7d_pct": None,
        "interval_status": None,
        "last_switch_at": None,
        "consecutive_failures": 0,
        "last_error": None,
        "manual_override": None,
    }


def load_state() -> dict:
    """Load routing state; tolerate missing/corrupt file by returning defaults."""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        state = _default_state()
        state.update({k: v for k, v in data.items() if k in state})
        if state["active_provider"] not in _KNOWN_PROVIDERS:
            state["active_provider"] = _default_state()["active_provider"]
        return state
    except FileNotFoundError:
        return _default_state()
    except Exception:
        logger.exception("llm_route state file unreadable; using defaults")
        return _default_state()


def save_state(state: dict) -> None:
    """Atomically persist routing state (write tmp then replace)."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def set_manual_override(provider: str) -> dict:
    """手动锁定模型（写入状态；自动 job 不再改 provider）。返回更新后的 state。"""
    if provider not in (PRIMARY, FALLBACK):
        raise ValueError(f"不支持的模型：{provider}（仅 minimax / deepseek）")
    state = load_state()
    state["manual_override"] = {"provider": provider, "set_at": time.time()}
    state["active_provider"] = provider
    save_state(state)
    return state


def release_manual_override() -> dict:
    """清除手动锁定（恢复自动决策）。返回更新后的 state。"""
    state = load_state()
    state["manual_override"] = None
    save_state(state)
    return state


def decide_active_provider(
    current: str,
    used_5h_pct: int | None,
    *,
    used_7d_pct: int | None = None,
    now: float | None = None,
    last_switch_at: float | None = None,
    min_switch_interval_s: float = MIN_SWITCH_INTERVAL_S,
    switch_to_fallback_pct: int = SWITCH_TO_FALLBACK_PCT,
    switch_back_pct: int = SWITCH_BACK_PCT,
    switch_to_fallback_weekly_pct: int = SWITCH_TO_FALLBACK_WEEKLY_PCT,
    switch_back_weekly_pct: int = SWITCH_BACK_WEEKLY_PCT,
) -> str:
    """Hysteresis switch decision (pure; 5h + 7d weekly windows).

    - Unknown usage / unsupported current provider: keep current.
    - minimax and (5h used >= 85 OR 7d used >= 90) -> deepseek.
    - deepseek and (5h used < 70 AND 7d used < 85) and cooldown elapsed -> minimax.
    """
    if (used_5h_pct is None and used_7d_pct is None) or current not in (PRIMARY, FALLBACK):
        return current
    now = time.time() if now is None else now
    if current == PRIMARY:
        if used_5h_pct is not None and used_5h_pct >= switch_to_fallback_pct:
            return FALLBACK
        if used_7d_pct is not None and used_7d_pct >= switch_to_fallback_weekly_pct:
            return FALLBACK
        return PRIMARY
    # current == FALLBACK
    five_ok = used_5h_pct is None or used_5h_pct < switch_back_pct
    seven_ok = used_7d_pct is None or used_7d_pct < switch_back_weekly_pct
    if five_ok and seven_ok and (
        last_switch_at is None or (now - last_switch_at) >= min_switch_interval_s
    ):
        return PRIMARY
    return FALLBACK


def resolve_llm_config() -> dict:
    """Resolve active provider config; fall back to the other provider if unconfigured."""
    state = load_state()
    active = state["active_provider"]
    cfg = provider_config(active)
    if cfg is not None:
        return cfg
    alt = FALLBACK if active == PRIMARY else PRIMARY
    cfg = provider_config(alt)
    if cfg is not None:
        logger.warning(
            "LLM provider '{}' not configured; falling back to '{}'", active, alt
        )
        return cfg
    raise RuntimeError("No LLM API key configured (MINIMAX_API_KEY / DEEPSEEK_API_KEY)")
