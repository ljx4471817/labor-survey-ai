# -*- coding: utf-8 -*-
"""loguru sink 配置：异常堆栈不得回显局部变量（2026-09-20 密钥外泄回归）。"""
from __future__ import annotations

from loguru import logger

from app.infra.logging_setup import configure_logging

# 探针值故意取成不像凭据的字符串，避免触发 pre-commit 的凭据扫描（这里只是"某个局部变量的值"）
PROBE_VALUE = "leak-probe-42"


def _boom_with_secret() -> None:
    # 生产里这个局部变量是 llm._call_once 的 cfg（内含凭据字段）；
    # 这里只要验证「局部变量的值」不会进日志即可
    payload = PROBE_VALUE  # noqa: F841
    raise RuntimeError("provider down")


def _captured_exception_log() -> str:
    lines: list[str] = []
    configure_logging(sink=lines.append)
    try:
        _boom_with_secret()
    except RuntimeError:
        logger.exception("call failed")
    finally:
        configure_logging()  # 恢复默认 stderr sink，避免影响其它测试
    return "\n".join(lines)


def test_exception_log_does_not_leak_local_variables():
    out = _captured_exception_log()
    assert PROBE_VALUE not in out, "局部变量的值不应出现在日志里（生产里就是 cfg 里的凭据字段）"


def test_backtrace_is_still_available():
    """只关变量内省，完整调用栈要留着（否则没法排障）。"""
    out = _captured_exception_log()
    assert "call failed" in out
    assert "RuntimeError" in out
    assert "_boom_with_secret" in out


def test_configure_logging_is_idempotent():
    """重复调用只留一个 sink（避免日志重复输出）。"""
    lines: list[str] = []
    configure_logging(sink=lines.append)
    configure_logging(sink=lines.append)
    try:
        logger.warning("once")
    finally:
        configure_logging()
    assert sum(1 for line in lines if "once" in line) == 1
