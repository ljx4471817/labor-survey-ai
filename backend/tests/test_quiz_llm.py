"""Unit tests for quiz_llm service (独立测验模型配置) and /api/admin/quiz/llm-config endpoints."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.services import quiz_llm


# ---------- service: 配置读写 ----------

def test_default_config(tmp_path, monkeypatch):
    monkeypatch.setattr(quiz_llm, "CONFIG_FILE", tmp_path / "nope.json")
    cfg = quiz_llm.load_config()
    assert cfg["provider"] == "dashscope"
    assert cfg["model"] == "qwen-flash"
    assert cfg["updated_at"] is None


def test_load_config_roundtrip(tmp_path, monkeypatch):
    f = tmp_path / "quiz_llm_config.json"
    monkeypatch.setattr(quiz_llm, "CONFIG_FILE", f)
    cfg = quiz_llm.save_config("minimax", "15500000000", "管理员")
    assert cfg["provider"] == "minimax"
    assert cfg["updated_by"] == "15500000000"
    assert cfg["updated_by_name"] == "管理员"
    assert cfg["updated_at"] is not None
    loaded = quiz_llm.load_config()
    assert loaded["provider"] == "minimax"
    assert loaded["updated_by"] == "15500000000"


def test_load_config_invalid_provider_falls_back(tmp_path, monkeypatch):
    f = tmp_path / "quiz_llm_config.json"
    monkeypatch.setattr(quiz_llm, "CONFIG_FILE", f)
    f.write_text(json.dumps({"provider": "gpt5"}), encoding="utf-8")
    assert quiz_llm.load_config()["provider"] == "dashscope"


def test_load_config_corrupt_falls_back(tmp_path, monkeypatch):
    f = tmp_path / "quiz_llm_config.json"
    monkeypatch.setattr(quiz_llm, "CONFIG_FILE", f)
    f.write_text("{bad json", encoding="utf-8")
    assert quiz_llm.load_config()["provider"] == "dashscope"


def test_save_config_invalid_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(quiz_llm, "CONFIG_FILE", tmp_path / "x.json")
    with pytest.raises(ValueError):
        quiz_llm.save_config("gpt5", "15500000000")


# ---------- service: 探测 / chat ----------

def test_probe_ok(monkeypatch):
    monkeypatch.setattr(
        quiz_llm.llm_router, "provider_config",
        lambda p: {"provider": p, "api_key": "k", "model": "qwen-flash", "url": "http://x"},
    )
    monkeypatch.setattr(quiz_llm, "_call", lambda *a, **k: "OK")
    assert quiz_llm.probe("dashscope") == "qwen-flash"


def test_probe_missing_key(monkeypatch):
    monkeypatch.setattr(quiz_llm.llm_router, "provider_config", lambda p: None)
    with pytest.raises(RuntimeError):
        quiz_llm.probe("dashscope")


def test_chat_uses_independent_config(monkeypatch):
    calls = {}

    def fake_load():
        return {"provider": "deepseek", "model": "deepseek-v4-flash", "updated_at": None,
                "updated_by": None, "updated_by_name": None}

    monkeypatch.setattr(quiz_llm, "load_config", fake_load)
    monkeypatch.setattr(
        quiz_llm.llm_router, "provider_config",
        lambda p: {"provider": p, "api_key": "kd", "model": "deepseek-v4-flash", "url": "http://ds"},
    )

    def fake_call(cfg, messages, max_tokens, temperature, timeout):
        calls["cfg"] = cfg
        return "答案"

    monkeypatch.setattr(quiz_llm, "_call", fake_call)
    out = quiz_llm.chat([{"role": "user", "content": "hi"}])
    assert out == "答案"
    assert calls["cfg"]["provider"] == "deepseek"  # 用测验独立配置，不经过 llm_route


# ---------- admin endpoints ----------

def test_llm_config_get(monkeypatch):
    monkeypatch.setattr(
        quiz_llm, "load_config",
        lambda: {"provider": "dashscope", "model": "qwen-flash", "updated_at": "2026-08-14T10:00:00+08:00",
                 "updated_by": "15500000000", "updated_by_name": "管理员"},
    )
    from app.api import quiz_admin
    d = quiz_admin.quiz_llm_config_get(phone="15500000000")
    assert d["provider"] == "dashscope"
    assert d["display_name"] == "qwen-flash"
    assert d["updated_by_name"] == "管理员"


def test_llm_config_set_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(quiz_llm, "CONFIG_FILE", tmp_path / "quiz_llm_config.json")
    monkeypatch.setattr(quiz_llm, "probe", lambda p: "MiniMax-M2.7-highspeed")
    from app.api import quiz_admin
    monkeypatch.setattr(
        quiz_admin, "get_current_user",
        lambda phone: {"name": "管理员", "sys_role": "系统管理员"},
    )
    d = quiz_admin.quiz_llm_config_set(
        quiz_admin.QuizLlmConfigRequest(provider="minimax"), phone="15500000000"
    )
    assert d["provider"] == "minimax"
    assert d["probe_model"] == "MiniMax-M2.7-highspeed"
    # 文件已落盘
    saved = json.loads((tmp_path / "quiz_llm_config.json").read_text(encoding="utf-8"))
    assert saved["provider"] == "minimax"
    assert saved["updated_by"] == "15500000000"


def test_llm_config_set_probe_failure(monkeypatch):
    def _boom(p):
        raise RuntimeError("401 invalid key")
    monkeypatch.setattr(quiz_llm, "probe", _boom)
    from app.api import quiz_admin
    with pytest.raises(HTTPException) as exc:
        quiz_admin.quiz_llm_config_set(
            quiz_admin.QuizLlmConfigRequest(provider="deepseek"), phone="15500000000"
        )
    assert exc.value.status_code == 400
    assert "探测失败" in exc.value.detail
