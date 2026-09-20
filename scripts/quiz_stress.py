# -*- coding: utf-8 -*-
"""月度测验 100 并发压测（PRD v3 12.6：无 500、提交 P95 < 500ms）。

进程内 ASGI（httpx ASGITransport）跑真实端点 + 临时 DB，不触碰真实数据。
- 构造 100 套测验：每套 1 题、1 个目标用户（phone=13900000000+i）
- 鉴权覆盖：Authorization header 直接当 phone
- 100 并发 POST /api/quiz/submit，统计 P95 / 失败数

用法：python scripts/quiz_stress.py
"""
from __future__ import annotations

import asyncio
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.infra.auth import require_user  # noqa: E402
from app.main import app  # noqa: E402
from app.persistence import quiz_db  # noqa: E402

UTC8 = timezone(timedelta(hours=8))
N = 100
P95_LIMIT_MS = 500


def _seed():
    tmp = Path(tempfile.mkdtemp(prefix="quiz_stress_")) / "stress.db"
    quiz_db.DB_PATH = tmp
    quiz_db.reset_conn()
    now = datetime.now(UTC8).isoformat(timespec="seconds")
    valid_until = (datetime.now(UTC8) + timedelta(days=7)).isoformat(timespec="seconds")
    items = []
    for i in range(N):
        qid = quiz_db.create_quiz(f"压测{i}", scene="月度通知", created_by="admin", month="2026-08")
        quiz_db.replace_questions(qid, [{
            "question": f"问题{i}？",
            "options": '{"A": "1", "B": "2", "C": "3", "D": "4"}',
            "answer": "A",
            "explanation": "解析",
            "created_by": "admin",
        }])
        for q in quiz_db.list_questions(qid):
            # 审核通过即拟下发：submit 会校验 selected=1（该题在本次测验中）
            quiz_db.update_question(q["id"], status="approved", selected=1)
        phone = f"13900000000{i:02d}"
        quiz_db.set_targets(qid, [phone])
        quiz_db.update_quiz(qid, status="published", valid_from=now, valid_until=valid_until)
        items.append((qid, phone))
    return items


async def _run():
    items = _seed()

    from fastapi import Header

    def fake_user(authorization: str | None = Header(None)) -> str:
        # 直接用 Authorization: Bearer <phone> 当作登录手机号（压测专用覆盖）
        return (authorization or "").replace("Bearer ", "").strip()

    app.dependency_overrides[require_user] = fake_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as c:
        # 先热一次（触发 Chroma/线程池等懒加载与连接池）
        await c.get("/health")

        qid0, phone0 = items[0]
        q0 = quiz_db.list_questions(qid0)[0]

        async def submit(quiz_id: str, phone: str, q_id: str):
            t0 = time.perf_counter()
            r = await c.post(
                "/api/quiz/submit",
                headers={"Authorization": f"Bearer {phone}"},
                json={"quiz_id": quiz_id, "q_id": q_id, "selected": "A"},
            )
            return r.status_code, (time.perf_counter() - t0) * 1000

        latencies: list[float] = []
        failures = 0
        tasks = []
        for i, (qid, phone) in enumerate(items):
            q = quiz_db.list_questions(qid)[0]
            tasks.append(asyncio.create_task(submit(qid, phone, q["id"])))
        results = await asyncio.gather(*tasks)
        for code, ms in results:
            if code != 200:
                failures += 1
            latencies.append(ms)

    latencies.sort()
    p50 = statistics.median(latencies)
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    p99 = latencies[int(len(latencies) * 0.99) - 1]
    print(f"并发提交: {len(latencies)} 个")
    print(f"成功: {len(latencies) - failures} / 失败(非200): {failures}")
    print(f"P50={p50:.1f}ms  P95={p95:.1f}ms  P99={p99:.1f}ms  max={latencies[-1]:.1f}ms")
    ok = failures == 0 and p95 < P95_LIMIT_MS
    print(f"验收（PRD 12.6）: 无500={'PASS' if failures == 0 else 'FAIL'}  P95<{P95_LIMIT_MS}ms={'PASS' if p95 < P95_LIMIT_MS else 'FAIL'}")
    return ok


if __name__ == "__main__":
    ok = asyncio.run(_run())
    sys.exit(0 if ok else 1)
