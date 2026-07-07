"""并发压测脚本：跑指定档位并发，输出 P50/P95/5xx 报告。

不引新依赖：用 ThreadPoolExecutor + urllib（已在 requirements）。
直接打本地后端（默认 8002），不走 Cloudflare Tunnel 排除变量。

用法：
    python scripts/load_test.py --all                       # 跑 baseline / 20 / 50 / 100 四档
    python scripts/load_test.py --concurrency 20 --duration 60
    python scripts/load_test.py --concurrency 100 --duration 300 --phone 13985000001
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def gen_questions(n: int = 50) -> list[str]:
    """从 eval_set.json 抽 in_kb 题。"""
    p = ROOT / "knowledge-base" / "qa" / "eval_set.json"
    with open(p, encoding="utf-8") as f:
        es = json.load(f)
    qs = []
    for item in es:
        if item.get("type") == "in_kb":
            qs.append(item["question"])
        if len(qs) >= n:
            break
    return qs


def login(base: str, phone: str) -> str:
    data = json.dumps({"phone": phone}).encode()
    req = urllib.request.Request(
        f"{base}/api/auth/login", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["token"]


def chat_once(base: str, token: str, msg: str) -> dict:
    """一次 chat 调用，返回 {status, ms, request_id, mode, error?}。"""
    t0 = time.perf_counter()
    data = json.dumps({"message": msg, "history": []}).encode()
    req = urllib.request.Request(
        f"{base}/api/chat", data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read())
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            return {
                "status": r.status, "ms": elapsed_ms,
                "request_id": body.get("request_id"), "mode": body.get("mode"),
            }
    except urllib.error.HTTPError as e:
        return {"status": e.code, "ms": int((time.perf_counter() - t0) * 1000),
                "error": e.read().decode()[:120]}
    except Exception as e:
        return {"status": 0, "ms": int((time.perf_counter() - t0) * 1000),
                "error": str(e)[:120]}


def percentile(sorted_vals: list[int], p: int) -> int:
    if not sorted_vals:
        return 0
    k = int(len(sorted_vals) * p / 100)
    return sorted_vals[min(k, len(sorted_vals) - 1)]


def run_one_stage(base: str, token: str, questions: list[str],
                  concurrency: int, duration: int, label: str) -> list[dict]:
    """单档跑 duration 秒；用 ThreadPoolExecutor 控制并发。"""
    print(f"\n=== 阶段 {label}: 并发={concurrency}, 时长={duration}s ===", flush=True)
    stop_at = time.time() + duration
    results: list[dict] = []
    inflight: set = set()
    q_idx = [0]
    last_print = time.time()
    ex = ThreadPoolExecutor(max_workers=concurrency)

    try:
        def submit_one() -> object:
            q = questions[q_idx[0] % len(questions)]
            q_idx[0] += 1
            return ex.submit(chat_once, base, token, q)

        while time.time() < stop_at:
            while len(inflight) < concurrency and time.time() < stop_at:
                inflight.add(submit_one())
            done = {f for f in inflight if f.done()}
            for f in done:
                results.append(f.result())
                inflight.remove(f)
            now = time.time()
            if now - last_print >= 1.0:
                recent_2xx = [r for r in results if r.get("status") == 200]
                if recent_2xx:
                    lats = sorted(r["ms"] for r in recent_2xx)
                    err = sum(1 for r in results if r.get("status") != 200)
                    err_rate = err / max(len(results), 1)
                    print(
                        f"  [{int(now - stop_at + duration):>3}s] "
                        f"done={len(results):>4} inflight={len(inflight):>3} "
                        f"p50={percentile(lats, 50):>5}ms "
                        f"p95={percentile(lats, 95):>5}ms "
                        f"err={err_rate * 100:.1f}%",
                        flush=True,
                    )
                last_print = now
            time.sleep(0.02)

        # 收尾
        for f in as_completed(inflight, timeout=180):
            results.append(f.result())
    finally:
        ex.shutdown(wait=True)

    return results


def summarize(label: str, concurrency: int, results: list[dict], duration: int) -> dict:
    latencies = sorted(
        r["ms"] for r in results
        if r.get("status") == 200 and r.get("ms", 0) > 0
    )
    errors = [r for r in results if r.get("status") != 200]
    total = len(results)
    qps = total / duration if duration > 0 else 0
    return {
        "label": label, "concurrency": concurrency, "duration_s": duration,
        "total": total, "qps": round(qps, 2),
        "p50_ms": percentile(latencies, 50),
        "p90_ms": percentile(latencies, 90),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "err_count": len(errors),
        "err_rate": round(len(errors) / max(total, 1), 4),
        "errors_sample": errors[:5],
    }


def write_report(stages: list[dict], base: str, n_questions: int, phone: str) -> Path:
    today = datetime.now().strftime("%Y%m%d-%H%M")
    report = ROOT / "reports" / f"load-test-{today}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    with open(report, "w", encoding="utf-8") as f:
        f.write(f"# 压测报告 {today}\n\n")
        f.write(f"- 后端: `{base}`\n")
        f.write(f"- 测试账号: `{phone[:3]}****`\n")
        f.write(f"- 题库: {n_questions} 条 from `eval_set.json` (in_kb)\n")
        f.write(f"- 路径: 直接打本地后端，不走 Cloudflare Tunnel\n\n")
        f.write("| 阶段 | 并发 | 时长(s) | 总请求 | QPS | P50(ms) | P95(ms) | P99(ms) | 5xx数 | 5xx率 |\n")
        f.write("|------|------|---------|--------|-----|---------|---------|---------|-------|-------|\n")
        for s in stages:
            f.write(
                f"| {s['label']} | {s['concurrency']} | {s['duration_s']} | {s['total']} | "
                f"{s['qps']} | {s['p50_ms']} | {s['p95_ms']} | {s['p99_ms']} | "
                f"{s['err_count']} | {s['err_rate'] * 100:.1f}% |\n"
            )
        # 错误样例
        any_err = any(s["errors_sample"] for s in stages)
        if any_err:
            f.write("\n## 错误样例\n\n")
            for s in stages:
                if not s["errors_sample"]:
                    continue
                f.write(f"### {s['label']}\n")
                for e in s["errors_sample"]:
                    f.write(
                        f"- status={e.get('status')} ms={e.get('ms')} "
                        f"err=`{(e.get('error') or '')[:100]}`\n"
                    )
        # 结论占位
        f.write("\n## 结论 / 瓶颈定位\n\n_待补充_\n")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8002")
    ap.add_argument("--phone", default="13985000001")
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--duration", type=int, default=60)
    ap.add_argument("--questions", type=int, default=50)
    ap.add_argument("--all", action="store_true",
                    help="跑 baseline 1×30s / 20×60s / 50×90s / 100×120s 四档")
    args = ap.parse_args()

    token = login(args.base, args.phone)
    qs = gen_questions(args.questions)
    if not qs:
        print("题库为空，退出", file=sys.stderr)
        return 1
    print(f"loaded {len(qs)} questions, token={token[:20]}...", flush=True)

    if args.all:
        plan = [
            (1, 30, "baseline"),
            (20, 60, "20"),
            (50, 90, "50"),
            (100, 120, "100"),
        ]
    else:
        plan = [(args.concurrency, args.duration, f"single-{args.concurrency}")]

    stages: list[dict] = []
    for c, d, label in plan:
        results = run_one_stage(args.base, token, qs, c, d, label)
        s = summarize(label, c, results, d)
        stages.append(s)
        print(
            f"  → total={s['total']} qps={s['qps']} "
            f"p50={s['p50_ms']}ms p95={s['p95_ms']}ms err={s['err_rate'] * 100:.1f}%",
            flush=True,
        )

    report = write_report(stages, args.base, len(qs), args.phone)
    print(f"\n报告写入 {report}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())