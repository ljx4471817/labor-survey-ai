"""Background LLM routing job: poll MiniMax 5h quota and switch primary/fallback.

Fail-safe: if quota checks fail repeatedly (e.g. endpoint/network issue), switch to the
DeepSeek fallback so MiniMax quota is never burned blind; recover via normal hysteresis.
"""
from __future__ import annotations

import threading
import time

from loguru import logger

from app.services import llm_router
from app.services.minimax_quota import check_quota

CHECK_INTERVAL_S = 600  # every 10 minutes
FAILSAFE_SWITCH_AFTER = 3  # consecutive failures before forcing DeepSeek


def check_and_switch() -> None:
    """Query quota once, switch provider per hysteresis, and apply fail-safe on errors."""
    state = llm_router.load_state()
    old_provider = state["active_provider"]
    try:
        quota = check_quota()
        if quota.used_5h_pct is None and quota.used_7d_pct is None:
            raise RuntimeError("MiniMax quota response has no usable usage data")
        state["consecutive_failures"] = 0
        new_provider = llm_router.decide_active_provider(
            old_provider,
            quota.used_5h_pct,
            used_7d_pct=quota.used_7d_pct,
            last_switch_at=state["last_switch_at"],
        )
        state.update(
            {
                "active_provider": new_provider,
                "last_check_at": time.time(),
                "used_5h_pct": quota.used_5h_pct,
                "used_7d_pct": quota.used_7d_pct,
                "interval_status": quota.interval_status,
                "last_error": None,
            }
        )
        if new_provider != old_provider:
            state["last_switch_at"] = time.time()
            logger.warning(
                "LLM route switch {} -> {} (5h used {}%)",
                old_provider, new_provider, quota.used_5h_pct,
            )
        else:
            logger.info(
                "LLM route stays {} (5h used {}%, 7d {}%)",
                new_provider, quota.used_5h_pct, quota.used_7d_pct,
            )
    except Exception as e:
        state["last_error"] = f"{type(e).__name__}: {e}"
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        if (
            state["consecutive_failures"] >= FAILSAFE_SWITCH_AFTER
            and old_provider != llm_router.FALLBACK
        ):
            state["active_provider"] = llm_router.FALLBACK
            state["last_switch_at"] = time.time()
            logger.warning(
                "MiniMax quota check failing ({}x); fail-safe switch to deepseek: {}",
                state["consecutive_failures"], e,
            )
        else:
            logger.warning(
                "MiniMax quota check failed, keep {}: {}", old_provider, e
            )
    finally:
        llm_router.save_state(state)


class LlmSwitchScheduler:
    """Daemon thread that runs check_and_switch every interval_s."""

    def __init__(self, interval_s: int = CHECK_INTERVAL_S):
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="llm-switch", daemon=True
        )
        self._thread.start()
        logger.info("LLM route scheduler started (check every {}s)", self._interval_s)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        # Run once immediately so state is fresh on startup.
        try:
            check_and_switch()
        except Exception:
            logger.exception("LLM route first check failed")
        while not self._stop.wait(self._interval_s):
            try:
                check_and_switch()
            except Exception:
                logger.exception("LLM route check failed")


scheduler = LlmSwitchScheduler()
