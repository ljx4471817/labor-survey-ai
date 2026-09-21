# -*- coding: utf-8 -*-
"""LLM 连接级失败沿链回退（2026-09-21 /api/chat 500 根因回归）。"""
from __future__ import annotations

import pytest
import requests
from loguru import logger

from app.rag import llm


class _Resp:
    """最小 requests.Response 替身：只需要 status_code / json / raise_for_status。"""

    def __init__(self, status: int, payload: dict | None = None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err


def _ok(text: str = "正常答案") -> _Resp:
    return _Resp(200, {"choices": [{"message": {"content": text}}]})


def _http_error(status: int) -> requests.HTTPError:
    err = requests.HTTPError(f"HTTP {status}")
    err.response = _Resp(status)
    return err


@pytest.fixture(autouse=True)
def _clear_health_state():
    llm._UNHEALTHY.clear()
    yield
    llm._UNHEALTHY.clear()


def _patch_chain(monkeypatch, providers: list[str]):
    cfgs = [
        {
            "provider": p,
            "api_key": f"{p}-key",
            "model": f"{p}-model",
            "url": f"https://{p}.test/v1/chat",
        }
        for p in providers
    ]
    monkeypatch.setattr(llm, "resolve_llm_chain", lambda: cfgs)


def test_falls_back_on_connection_error(monkeypatch):
    """MiniMax 连接被中断（WinError 10053）时应回退到下一个 provider，而不是抛错。"""
    _patch_chain(monkeypatch, ["minimax", "dashscope"])
    calls: list[str] = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if "minimax" in url:
            raise requests.ConnectionError("WinError 10053 连接被中断")
        return _ok("回退答案")

    monkeypatch.setattr("requests.post", fake_post)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "回退答案"
    assert calls == ["https://minimax.test/v1/chat", "https://dashscope.test/v1/chat"]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_falls_back_on_provider_side_http_errors(monkeypatch, status):
    _patch_chain(monkeypatch, ["minimax", "deepseek"])
    calls: list[str] = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if "minimax" in url:
            return _Resp(status)
        return _ok()

    monkeypatch.setattr("requests.post", fake_post)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "正常答案"
    assert len(calls) == 2


def test_falls_back_on_unparseable_body(monkeypatch):
    """200 但响应体缺 choices：算 provider 侧异常，同样回退。"""
    _patch_chain(monkeypatch, ["minimax", "dashscope"])

    def fake_post(url, **kwargs):
        return _Resp(200, {}) if "minimax" in url else _ok()

    monkeypatch.setattr("requests.post", fake_post)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "正常答案"


def test_does_not_fall_back_on_client_side_4xx(monkeypatch):
    """400/401 是本项目自己的请求问题：照旧抛错，不要掩盖。"""
    _patch_chain(monkeypatch, ["minimax", "dashscope"])
    calls: list[str] = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _Resp(400)

    monkeypatch.setattr("requests.post", fake_post)
    with pytest.raises(requests.HTTPError):
        llm.chat([{"role": "user", "content": "hi"}])
    assert calls == ["https://minimax.test/v1/chat"]


def test_all_providers_failing_raises_runtime_error(monkeypatch):
    _patch_chain(monkeypatch, ["minimax", "dashscope", "deepseek"])
    calls: list[str] = []

    def fake_post(url, **kwargs):
        calls.append(url)
        raise requests.ConnectionError("down")

    monkeypatch.setattr("requests.post", fake_post)
    with pytest.raises(RuntimeError) as ei:
        llm.chat([{"role": "user", "content": "hi"}])
    assert "所有 LLM provider 均调用失败" in str(ei.value)
    assert len(calls) == 3


def test_unhealthy_provider_is_demoted_within_cooldown(monkeypatch):
    """失败过的 provider 冷却期内排到链尾，避免每次请求都白等一次。"""
    _patch_chain(monkeypatch, ["minimax", "dashscope"])
    calls: list[str] = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if "minimax" in url:
            raise requests.ConnectionError("down")
        return _ok()

    monkeypatch.setattr("requests.post", fake_post)
    llm.chat([{"role": "user", "content": "hi"}])
    calls.clear()
    llm.chat([{"role": "user", "content": "hi"}])
    assert calls == ["https://dashscope.test/v1/chat"]  # 不再先打 minimax


def test_recovery_clears_cooldown(monkeypatch):
    """冷却过期后（或成功后）恢复原优先级：下一次仍从 active 开始。"""
    _patch_chain(monkeypatch, ["minimax", "dashscope"])
    calls: list[str] = []
    state = {"fail": True}

    def fake_post(url, **kwargs):
        calls.append(url)
        if "minimax" in url and state["fail"]:
            raise requests.ConnectionError("down")
        return _ok()

    monkeypatch.setattr("requests.post", fake_post)
    llm.chat([{"role": "user", "content": "hi"}])  # minimax 失败 -> dashscope 成功
    state["fail"] = False
    llm._UNHEALTHY["minimax"] = 0.0  # 冷却到期
    calls.clear()
    llm.chat([{"role": "user", "content": "hi"}])
    assert calls == ["https://minimax.test/v1/chat"]
    assert "minimax" not in llm._UNHEALTHY  # 成功后清理冷却标记


def test_is_fallback_worthy_matrix():
    assert llm._is_fallback_worthy(requests.ConnectionError("x")) is True
    assert llm._is_fallback_worthy(requests.Timeout("x")) is True
    assert llm._is_fallback_worthy(_http_error(429)) is True
    assert llm._is_fallback_worthy(_http_error(503)) is True
    assert llm._is_fallback_worthy(KeyError("choices")) is True
    assert llm._is_fallback_worthy(_http_error(400)) is False
    assert llm._is_fallback_worthy(_http_error(401)) is False


def test_fallback_logging_does_not_leak_api_key(monkeypatch):
    """合规红线：日志不得回显密钥（本次回退日志只打 provider 名与异常文案）。"""
    _patch_chain(monkeypatch, ["minimax", "dashscope"])
    lines: list[str] = []

    def fake_post(url, **kwargs):
        if "minimax" in url:
            raise requests.ConnectionError("down")
        return _ok()

    monkeypatch.setattr("requests.post", fake_post)
    sink_id = logger.add(lambda msg: lines.append(str(msg)), level="DEBUG")
    try:
        llm.chat([{"role": "user", "content": "hi"}])
    finally:
        logger.remove(sink_id)

    joined = "\n".join(lines)
    assert "minimax-key" not in joined and "dashscope-key" not in joined
