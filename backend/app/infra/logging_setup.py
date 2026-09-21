# -*- coding: utf-8 -*-
"""loguru 基础配置：关掉异常堆栈的变量内省（diagnose）。

为什么需要：loguru 默认 LOGURU_DIAGNOSE=True，异常堆栈会把每一帧的局部变量值也打印出来
—— 实测 LLM 调用失败时把配置字典里的 api_key 明文写进了日志（2026-09-20 排查时发现）。
保留 backtrace（完整调用栈）以便排障，只去掉变量值。
"""
from __future__ import annotations

import sys
from typing import Callable

from loguru import logger


def configure_logging(sink: Callable | None = None) -> None:
    """用「无变量内省」的 sink 替换默认 handler（幂等：重复调用只留一个 sink）。

    sink 默认写 stderr；测试可传入可调用对象来捕获输出。
    """
    logger.remove()  # 默认 handler 开着 diagnose，必须换掉
    logger.add(
        sys.stderr if sink is None else sink,
        level="DEBUG",
        backtrace=True,
        diagnose=False,
    )
