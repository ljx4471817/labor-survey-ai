"""Unit tests for llm_router hysteresis switching, state, provider config, think stripping."""
from __future__ import annotations

import time

import pytest

from app.services import llm_router
from app.services.minimax_quota import _parse_pct, fetch_quota


def test_unknown_usage_keeps_current():
    assert llm_router.decide_active_provider("minimax", None) == "minimax"
    assert llm_router.decide_active_provider("deepseek", None) == "deepseek"


def test_minimax_below_threshold_stays():
    assert llm_router.decide_active_provider("minimax", 84) == "minimax"


def test_minimax_at_threshold_switches_to_deepseek():
    assert llm_router.decide_active_provider("minimax", 85) == "deepseek"
    assert llm_router.decide_active_provider("minimax", 100) == "deepseek"


def test_deepseek_above_back_threshold_stays():
    assert llm_router.decide_active_provider("deepseek", 70) == "deepseek"
    assert llm_router.decide_active_provider("deepseek", 85) == "deepseek"


def test_deepseek_below_back_threshold_switches_back():
    assert llm_router.decide_active_provider("deepseek", 69) == "minimax"
    assert llm_router.decide_active_provider("deepseek", 0) == "minimax"


def test_switch_back_respects_cooldown():
    now = time.time()
    assert (
        llm_router.decide_active_provider("deepseek", 50, now=now, last_switch_at=now - 100)
        == "deepseek"
    )
    assert (
        llm_router.decide_active_provider("deepseek", 50, now=now, last_switch_at=now - 1900)
        == "minimax"
    )


def test_switch_back_without_history():
    assert llm_router.decide_active_provider("deepseek", 50, last_switch_at=None) == "minimax"


def test_unknown_provider_untouched():
    assert llm_router.decide_active_provider("dashscope", 95) == "dashscope"


def test_strip_single_think():
    assert llm_router.strip_thinking("<think>thinking</think>answer") == "answer"


def test_strip_multiple_think():
    assert llm_router.strip_thinking("<think>a</think>mid<think>b</think>dle") == "middle"


def test_strip_no_think():
    assert llm_router.strip_thinking("plain answer") == "plain answer"


def test_strip_think_multiline():
    assert llm_router.strip_thinking("<think>\nline1\nline2\n</think>result") == "result"


def test_provider_config_minimax(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    cfg = llm_router.provider_config("minimax")
    assert cfg is not None
    assert cfg["model"] == "MiniMax-M2.7-highspeed"
    assert cfg["url"] == "https://api.minimaxi.com/v1/chat/completions"
    assert cfg["api_key"] == "k"


def test_provider_config_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k2")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    cfg = llm_router.provider_config("deepseek")
    assert cfg is not None
    assert cfg["model"] == "deepseek-v4-flash"
    assert cfg["url"] == "https://api.deepseek.com/v1/chat/completions"


def test_provider_config_missing_key(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    assert llm_router.provider_config("minimax") is None


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_router, "STATE_FILE", tmp_path / "llm_route.json")
    state = llm_router.load_state()
    assert state["active_provider"] == "minimax"
    state["used_5h_pct"] = 90
    llm_router.save_state(state)
    loaded = llm_router.load_state()
    assert loaded["used_5h_pct"] == 90
    assert loaded["active_provider"] == "minimax"


def test_load_state_corrupt(tmp_path, monkeypatch):
    f = tmp_path / "llm_route.json"
    f.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(llm_router, "STATE_FILE", f)
    state = llm_router.load_state()
    assert state["active_provider"] == "minimax"
    assert state["used_5h_pct"] is None


def test_resolve_minimax(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    monkeypatch.setattr(llm_router, "STATE_FILE", tmp_path / "llm_route.json")
    cfg = llm_router.resolve_llm_config()
    assert cfg["provider"] == "minimax"


def test_resolve_fallback_when_minimax_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "kd")
    monkeypatch.setattr(llm_router, "STATE_FILE", tmp_path / "llm_route.json")
    cfg = llm_router.resolve_llm_config()
    assert cfg["provider"] == "deepseek"


def test_resolve_raises_when_none(monkeypatch, tmp_path):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(llm_router, "STATE_FILE", tmp_path / "llm_route.json")
    with pytest.raises(RuntimeError):
        llm_router.resolve_llm_config()


def test_parse_pct():
    assert _parse_pct("58%") == 58
    assert _parse_pct("0%") == 0
    assert _parse_pct("100%") == 100
    assert _parse_pct(None) is None
    assert _parse_pct("abc") is None


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_fetch_quota_general(monkeypatch):
    payload = {
        "model_remains": [
            {
                "model_name": "general",
                "current_interval_used_percent": "58%",
                "current_interval_status": 1,
                "current_weekly_used_percent": "13%",
            },
            {"model_name": "video", "current_interval_used_percent": "0%"},
        ],
        "base_resp": {"status_code": 0},
    }
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp(200, payload))
    snap = fetch_quota("token")
    assert snap.used_5h_pct == 58
    assert snap.used_7d_pct == 13
    assert snap.interval_status == 1


def test_fetch_quota_no_general(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp(200, {"model_remains": []}))
    snap = fetch_quota("token")
    assert snap.used_5h_pct is None
    assert snap.used_7d_pct is None


# ---------- check_and_switch integration ----------

from app.services import llm_switch_job
from app.services.minimax_quota import QuotaSnapshot


def _seed_state(monkeypatch, tmp_path, active, last_switch_at=None):
    monkeypatch.setattr(llm_router, "STATE_FILE", tmp_path / "llm_route.json")
    state = llm_router._default_state()
    state["active_provider"] = active
    state["last_switch_at"] = last_switch_at
    llm_router.save_state(state)
    return state


def test_check_and_switch_high_usage(monkeypatch, tmp_path):
    _seed_state(monkeypatch, tmp_path, "minimax")
    monkeypatch.setattr(
        llm_switch_job, "check_quota",
        lambda: QuotaSnapshot(90, 50, 1, {}),
    )
    llm_switch_job.check_and_switch()
    state = llm_router.load_state()
    assert state["active_provider"] == "deepseek"
    assert state["used_5h_pct"] == 90
    assert state["last_switch_at"] is not None


def test_check_and_switch_back_after_cooldown(monkeypatch, tmp_path):
    _seed_state(monkeypatch, tmp_path, "deepseek", last_switch_at=0)
    monkeypatch.setattr(
        llm_switch_job, "check_quota",
        lambda: QuotaSnapshot(50, 20, 1, {}),
    )
    llm_switch_job.check_and_switch()
    state = llm_router.load_state()
    assert state["active_provider"] == "minimax"


def test_check_and_switch_failure_keeps_provider(monkeypatch, tmp_path):
    _seed_state(monkeypatch, tmp_path, "minimax")
    def _boom():
        raise RuntimeError("quota boom")
    monkeypatch.setattr(llm_switch_job, "check_quota", _boom)
    llm_switch_job.check_and_switch()
    state = llm_router.load_state()
    assert state["active_provider"] == "minimax"
    assert "boom" in state["last_error"]


# ---------- quota via Bearer API key ----------

def _capture_get(captured):
    def _get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers or {}
        return _FakeResp(200, {
            "model_remains": [{
                "model_name": "general",
                "current_interval_used_percent": "12%",
                "current_interval_status": 1,
                "current_weekly_used_percent": "5%",
            }]
        })
    return _get


def test_fetch_quota_uses_bearer_key(monkeypatch):
    captured = {}
    monkeypatch.setattr("requests.get", _capture_get(captured))
    snap = fetch_quota("sekrit")
    assert snap.used_5h_pct == 12
    assert captured["headers"]["Authorization"] == "Bearer sekrit"


def test_check_quota_missing_key(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "")
    from app.services.minimax_quota import check_quota
    with pytest.raises(RuntimeError):
        check_quota()


# ---------- fail-safe after consecutive failures ----------

def test_check_and_switch_failsafe_after_three(monkeypatch, tmp_path):
    _seed_state(monkeypatch, tmp_path, "minimax")
    def _boom():
        raise RuntimeError("quota endpoint down")
    monkeypatch.setattr(llm_switch_job, "check_quota", _boom)
    llm_switch_job.check_and_switch()
    assert llm_router.load_state()["active_provider"] == "minimax"
    llm_switch_job.check_and_switch()
    assert llm_router.load_state()["active_provider"] == "minimax"
    llm_switch_job.check_and_switch()
    state = llm_router.load_state()
    assert state["active_provider"] == "deepseek"
    assert state["consecutive_failures"] == 3


def test_check_and_switch_recovers_after_failsafe(monkeypatch, tmp_path):
    _seed_state(monkeypatch, tmp_path, "minimax")
    calls = {"n": 0}
    def _flaky():
        calls["n"] += 1
        if calls["n"] <= 3:
            raise RuntimeError("down")
        return QuotaSnapshot(50, 20, 1, {})
    monkeypatch.setattr(llm_switch_job, "check_quota", _flaky)
    for _ in range(3):
        llm_switch_job.check_and_switch()
    assert llm_router.load_state()["active_provider"] == "deepseek"
    # recovery: success resets counter, but cooldown still applies
    llm_switch_job.check_and_switch()
    state = llm_router.load_state()
    assert state["active_provider"] == "deepseek"
    assert state["consecutive_failures"] == 0


def test_check_and_switch_back_after_failsafe_cooldown(monkeypatch, tmp_path):
    _seed_state(monkeypatch, tmp_path, "minimax")
    calls = {"n": 0}
    def _flaky():
        calls["n"] += 1
        if calls["n"] <= 3:
            raise RuntimeError("down")
        return QuotaSnapshot(50, 20, 1, {})
    monkeypatch.setattr(llm_switch_job, "check_quota", _flaky)
    for _ in range(3):
        llm_switch_job.check_and_switch()
    state = llm_router.load_state()
    state["last_switch_at"] = 0
    llm_router.save_state(state)
    llm_switch_job.check_and_switch()
    state = llm_router.load_state()
    assert state["active_provider"] == "minimax"
    assert state["consecutive_failures"] == 0


# ---------- weekly (7d) guard ----------

def test_minimax_weekly_guard_switches():
    assert llm_router.decide_active_provider("minimax", 10, used_7d_pct=90) == "deepseek"
    assert llm_router.decide_active_provider("minimax", 10, used_7d_pct=89) == "minimax"


def test_minimax_5h_guard_precedes_weekly():
    assert llm_router.decide_active_provider("minimax", 85, used_7d_pct=0) == "deepseek"


def test_deepseek_weekly_guard_holds():
    assert llm_router.decide_active_provider("deepseek", 50, used_7d_pct=86) == "deepseek"
    assert llm_router.decide_active_provider("deepseek", 50, used_7d_pct=84, last_switch_at=None) == "minimax"


def test_deepseek_needs_both_windows_low():
    assert llm_router.decide_active_provider("deepseek", 69, used_7d_pct=80, last_switch_at=None) == "minimax"
    assert llm_router.decide_active_provider("deepseek", 69, used_7d_pct=85, last_switch_at=None) == "deepseek"


# ---------- admin endpoint ----------

def test_llm_route_endpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_router, "STATE_FILE", tmp_path / "llm_route.json")
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    from app.api.llm_admin import get_llm_route
    d = get_llm_route(phone="13900000001")
    assert d["active_provider"] == "minimax"
    assert d["active_model"] == "MiniMax-M2.7-highspeed"
    assert d["consecutive_failures"] == 0
    assert d["last_error"] is None


def test_check_and_switch_no_usable_data_counts_as_failure(monkeypatch, tmp_path):
    _seed_state(monkeypatch, tmp_path, "minimax")
    monkeypatch.setattr(
        llm_switch_job, "check_quota",
        lambda: QuotaSnapshot(None, None, None, {"model_remains": []}),
    )
    llm_switch_job.check_and_switch()
    assert llm_router.load_state()["active_provider"] == "minimax"
    assert llm_router.load_state()["consecutive_failures"] == 1
    llm_switch_job.check_and_switch()
    llm_switch_job.check_and_switch()
    assert llm_router.load_state()["active_provider"] == "deepseek"
    assert "no usable usage data" in llm_router.load_state()["last_error"]


# ---------- manual override (主动切换模型) ----------

def test_default_state_manual_override_none(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_router, "STATE_FILE", tmp_path / "llm_route.json")
    assert llm_router.load_state()["manual_override"] is None


def test_set_manual_override_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_router, "STATE_FILE", tmp_path / "llm_route.json")
    llm_router.set_manual_override("deepseek")
    state = llm_router.load_state()
    assert state["active_provider"] == "deepseek"
    assert state["manual_override"]["provider"] == "deepseek"
    assert "set_at" in state["manual_override"]


def test_set_manual_override_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_router, "STATE_FILE", tmp_path / "llm_route.json")
    with pytest.raises(ValueError):
        llm_router.set_manual_override("dashscope")


def test_release_manual_override(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_router, "STATE_FILE", tmp_path / "llm_route.json")
    llm_router.set_manual_override("deepseek")
    llm_router.release_manual_override()
    state = llm_router.load_state()
    assert state["manual_override"] is None
    assert state["active_provider"] == "deepseek"  # 只清标记，不动 provider


def test_check_and_switch_manual_keeps_provider(monkeypatch, tmp_path):
    _seed_state(monkeypatch, tmp_path, "minimax")
    llm_router.set_manual_override("minimax")
    monkeypatch.setattr(
        llm_switch_job, "check_quota",
        lambda: QuotaSnapshot(95, 95, 1, {}),  # 已超阈值，但手动锁定不改
    )
    llm_switch_job.check_and_switch()
    state = llm_router.load_state()
    assert state["active_provider"] == "minimax"
    assert state["used_5h_pct"] == 95  # 用量照常更新
    assert state["manual_override"]["provider"] == "minimax"


def test_check_and_switch_manual_blocks_failsafe(monkeypatch, tmp_path):
    _seed_state(monkeypatch, tmp_path, "minimax")
    llm_router.set_manual_override("minimax")

    def _boom():
        raise RuntimeError("quota endpoint down")

    monkeypatch.setattr(llm_switch_job, "check_quota", _boom)
    for _ in range(4):
        llm_switch_job.check_and_switch()
    state = llm_router.load_state()
    assert state["active_provider"] == "minimax"  # 手动锁定连 fail-safe 也不切
    assert state["consecutive_failures"] == 4


def test_llm_route_post_manual_and_auto(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_router, "STATE_FILE", tmp_path / "llm_route.json")
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "kd")
    from app.api import llm_admin
    from app.services import llm_switch_job
    # 手动切 deepseek
    d = llm_admin.set_llm_route(llm_admin.LlmRouteRequest(provider="deepseek"), phone="13900000001")
    assert d["active_provider"] == "deepseek"
    assert d["manual_override"]["provider"] == "deepseek"
    # 手动切回 minimax
    d2 = llm_admin.set_llm_route(llm_admin.LlmRouteRequest(provider="minimax"), phone="13900000001")
    assert d2["active_provider"] == "minimax"
    # auto 恢复自动：立即触发一次自动决策（mock quota）
    calls = {"n": 0}

    def _ok():
        calls["n"] += 1
        return QuotaSnapshot(30, 20, 1, {})

    monkeypatch.setattr(llm_switch_job, "check_quota", _ok)
    d3 = llm_admin.set_llm_route(llm_admin.LlmRouteRequest(provider="auto"), phone="13900000001")
    assert d3["manual_override"] is None
    assert calls["n"] == 1  # 恢复自动触发一次自动决策
    assert d3["active_provider"] == "minimax"
