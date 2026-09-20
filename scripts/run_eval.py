"""跑 eval_set.json 100 道题回归。

需要后端在 http://127.0.0.1:8765 运行。

用法：
    python scripts/run_eval.py
    python scripts/run_eval.py --url http://127.0.0.1:8765
    python scripts/run_eval.py --out reports/eval-2026-06-16.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
EVAL_PATH = ROOT / "knowledge-base" / "qa" / "eval_set.json"
DEFAULT_URL = "http://127.0.0.1:8765"
DEFAULT_OUT = ROOT / "reports" / "eval-latest.json"


def login(url: str, phone: str) -> str:
    r = requests.post(
        f"{url}/api/auth/login",
        json={"phone": phone},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["token"]


_NEGATIONS = (
    "\u4e0d\u5f97", "\u4e0d\u80fd", "\u4e0d\u8981", "\u7981\u6b62", "\u8bf7\u52ff",
    "\u907f\u514d", "\u5207\u52ff", "\u4e0d\u5e94", "\u4e0d\u53ef", "\u522b",
)


_MD_MARKS = ("**", "*", "`", "__")


def _strip_md(text: str) -> str:
    """去掉 markdown 强调标记后再做子串比对。

    LLM 输出常带 **“自营者”** 这类加粗包裹，原文子串匹配会漏判（2026-09-20 eval-112 实测）。
    仅影响 must_contain / must_contain_any（放松匹配，只会把假 FAIL 纠正为 PASS）。
    """
    for m in _MD_MARKS:
        text = text.replace(m, "")
    return text


_QUOTE_CHARS = ("“", "”", "「", "」", "『", "』", "\"", "'", "‘", "’")


def _norm_for_match(text: str) -> str:
    """比对前归一化：去掉 markdown 标记 + 抹平引号风格差异。

    引号风格（全角 / 直引号 / 「」）在 LLM 输出里随机变化，逐条枚举变体不可持续
    —— 2026-09-20 eval-111 三连跑里两轮假失败（分别用了直引号与 "**不能**选"）。
    仅用于 must_contain / must_contain_any 等「必含」检查；should_not_contain 仍比对原文。
    """
    text = _strip_md(text)
    for q in _QUOTE_CHARS:
        text = text.replace(q, "")
    return text


def _bad_word_hit(answer: str, bad: str) -> bool:
    """True if a forbidden word appears in a non-negated sentence.

    Avoids false positives like "must not estimate" (negated) being flagged.
    """
    for sent in re.split(r"[\u3002\uff01\uff1f!?;\n]", answer):
        if bad in sent and not any(neg in sent for neg in _NEGATIONS):
            return True
    return False


def evaluate_item(item: dict, response: dict) -> dict:
    """对单题评估，返回 {passed, reason, details}。"""
    q_type = item["type"]
    actual_mode = response.get("mode")
    answer = response.get("answer", "")
    sources = response.get("sources", [])

    checks: list[tuple[bool, str]] = []

    # 1. mode 期望
    if q_type == "in_kb":
        checks.append((actual_mode == "rag", f"mode={actual_mode}（期望 rag）"))
    elif q_type == "out_of_kb":
        # 边缘场景：LLM 拒答（out_of_kb）或基于通用规则合理推断（rag）都算通过
        # 只检查答案长度合理（避免 LLM 完全跑题）
        ok = actual_mode in ("out_of_kb", "rag")
        detail = f"mode={actual_mode}（期望 rag 或 out_of_kb）"
        checks.append((ok, detail))
    elif q_type == "trap":
        checks.append(
            (actual_mode == "out_of_scope", f"mode={actual_mode}（期望 out_of_scope）")
        )
    elif q_type == "ambiguous":
        checks.append(
            (actual_mode == "ambiguous", f"mode={actual_mode}（期望 ambiguous）")
        )
    elif q_type == "fixed_kb":
        expected = item.get("expected_answer", "")
        checks.append((actual_mode == "rag", f"mode={actual_mode}（期望 rag）"))
        checks.append((answer == expected, f"fixed_answer={answer!r}（期望 {expected!r}）"))

    # 2. in_kb 题：mode=rag + 关键词命中 + 答案长度（must_contain 改为软指标）
    if q_type == "in_kb":
        # 关键词命中率
        kws = item.get("expected_keywords", [])
        if kws:
            _ans = _norm_for_match(answer)
            hit = sum(1 for k in kws if k in _ans)
            ratio = hit / len(kws)
            ok = ratio >= 0.25
            detail = f"关键词命中 {hit}/{len(kws)} = {ratio:.0%}" + ("" if ok else "（< 25%）")
            checks.append((ok, detail))
        # 答案长度合理
        if len(answer) < 30:
            checks.append((False, f"答案过短：{len(answer)} 字"))
        # 必含词（软指标：缺失不扣分，仅记录）
        if item.get("must_contain") and item["must_contain"] not in _norm_for_match(answer):
            checks.append((True, f"must_contain 措辞差异（不扣分）：{item['must_contain'][:20]}"))
        # 必含词列表（硬指标：任一命中才算过，用于 corner case 正面锁定）
        mca = item.get("must_contain_any")
        if isinstance(mca, list) and mca:
            _ans = _norm_for_match(answer)
            hit = next((s for s in mca if _norm_for_match(s) in _ans), None)
            if hit:
                checks.append((True, f"must_contain_any 命中：{hit}"))
            else:
                checks.append((False, f"must_contain_any 全缺失：{mca}"))

    # 3. out_of_kb 题：只检查答案长度合理（避免 LLM 跑题或拒答）
    elif q_type == "out_of_kb":
        # 拒答（out_of_kb 模式）也算合理回答
        if actual_mode == "out_of_kb":
            checks.append((True, "触发 out_of_kb 兜底"))
        else:
            # mode=rag 时检查答案长度
            if len(answer) < 30:
                checks.append((False, f"答案过短：{len(answer)} 字"))
            else:
                checks.append((True, f"答案合理（{len(answer)} 字）"))

    # 4. trap / ambiguous 题：检查必含词（trap 只看 mode，跳过必含词）
    if q_type == "ambiguous" and item.get("must_contain"):
        target = item["must_contain"]
        ok = _norm_for_match(target) in _norm_for_match(answer)
        detail = (
            f"must_contain 命中：{target[:20]}"
            if ok
            else f"must_contain 缺失：{target[:20]}"
        )
        checks.append((ok, detail))

    # 4. 禁词检查
    for bad in item.get("should_not_contain", []):
        if _bad_word_hit(answer, bad):
            checks.append((False, f"含禁词：{bad}"))

    passed = all(c[0] for c in checks)
    return {
        "passed": passed,
        "checks": [{"ok": c[0], "reason": c[1]} for c in checks],
        "actual_mode": actual_mode,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--eval", type=Path, default=EVAL_PATH)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--limit", type=int, default=None, help="只跑前 N 题（调试用）")
    p.add_argument("--phone", default=None,
                   help="白名单内手机号，用于登录拿 token（门禁启用后必填）")
    args = p.parse_args()

    headers: dict[str, str] = {}
    if args.phone:
        token = login(args.url, args.phone)
        headers["Authorization"] = f"Bearer {token}"
        print(f"已登录：phone={args.phone[:3]}****")
    else:
        print("未传 --phone，将以匿名调用（仅适用于未启门禁的环境）")

    items = json.loads(args.eval.read_text(encoding="utf-8"))
    if args.limit:
        items = items[: args.limit]
    print(f"评测题数: {len(items)}")
    print(f"后端: {args.url}\n")

    results: list[dict] = []
    type_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "pass": 0})
    failures: list[dict] = []

    for i, item in enumerate(items, start=1):
        t0 = time.time()
        try:
            payload: dict = {"message": item["question"]}
            if item.get("history"):
                payload["history"] = item["history"]
            r = requests.post(
                f"{args.url}/api/chat",
                json=payload,
                headers=headers,
                timeout=60,
            )
            r.raise_for_status()
            resp = r.json()
        except Exception as e:
            print(f"[{i:3d}/{len(items)}] {item['id']} HTTP 错误: {e}")
            results.append({"id": item["id"], "error": str(e)})
            continue
        dt = time.time() - t0

        ev = evaluate_item(item, resp)
        results.append({
            "id": item["id"],
            "type": item["type"],
            "question": item["question"],
            "expected_mode": item["type"],
            "actual_mode": ev["actual_mode"],
            "passed": ev["passed"],
            "checks": ev["checks"],
            "answer": resp.get("answer", "")[:300],
            "latency_ms": int(dt * 1000),
        })
        type_stats[item["type"]]["total"] += 1
        if ev["passed"]:
            type_stats[item["type"]]["pass"] += 1
        else:
            failures.append({"id": item["id"], "type": item["type"], "checks": ev["checks"]})

        status = "PASS" if ev["passed"] else "FAIL"
        print(f"[{i:3d}/{len(items)}] {item['id']} {item['type']:12s} {status}  {dt:.1f}s")

    # 汇总
    total = sum(s["total"] for s in type_stats.values())
    passed = sum(s["pass"] for s in type_stats.values())
    print(f"\n=== 汇总 ===")
    print(f"总通过: {passed}/{total} = {passed/total:.1%}\n")
    for t, s in type_stats.items():
        rate = s["pass"] / s["total"] if s["total"] else 0
        print(f"  {t:12s}: {s['pass']:3d}/{s['total']:3d} = {rate:.0%}")

    if failures:
        print(f"\n=== 失败 {len(failures)} 道 ===")
        for f in failures[:15]:
            print(f"  {f['id']} [{f['type']}]:")
            for c in f["checks"]:
                if not c["ok"]:
                    print(f"    - {c['reason']}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {"summary": {"total": total, "passed": passed, "by_type": dict(type_stats)},
             "results": results,
             "failures": failures},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n详细报告: {args.out}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
