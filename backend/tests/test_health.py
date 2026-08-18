# -*- coding: utf-8 -*-
"""health 端点测试 —— 锁定计数行为，防止回归到硬编码 0。

背景：/health 曾把 qa_count / chunk_count / chroma_count 硬编码为 0，
导致首页一直显示「在线 · QA 0 · 制度原文 0」。本测试确保端点始终
返回 Chroma collection 的真实计数。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from httpx import ASGITransport

from app.main import app


async def _get_health():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/health")


def test_health_returns_real_counts():
    """health 端点应返回非零计数（KB 已构建的前提下）。"""
    r = asyncio.run(_get_health())
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["qa_count"] > 0, f"qa_count 应 > 0，实际 {data['qa_count']}"
    assert data["chunk_count"] > 0, f"chunk_count 应 > 0，实际 {data['chunk_count']}"
    assert data["chroma_count"] == data["qa_count"] + data["chunk_count"]


def test_health_has_required_fields():
    """health 响应必须包含前端依赖的全部字段。"""
    r = asyncio.run(_get_health())
    data = r.json()
    for field in ("status", "chroma_count", "qa_count", "chunk_count", "llm_configured"):
        assert field in data, f"缺少字段 {field}"