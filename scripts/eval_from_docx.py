"""从 docx 题库自测：抽取 Q&A → 调 RAG → 对比参考解析。

docx 结构（已观察）：
  画像1\n<题1>   (header 和第 1 题合并在同一 cell)
  <选项>
  答案解析:\n<解析>
  <空行>
  画像2           (header 单独成行)
  <题>
  ...

抽取规则：以"答案解析"或"答案："为锚，每道题的回看 1-2 行得到 question，
回看 1-2 行得到 options，回看 0 行得到 explanation。

输出 reports/eval-docx.json（含每题 RAG 答案）和 终端报告（含覆盖度排序）。
"""
from __future__ import annotations

import json
import re
import sys
import time
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import jieba
import requests
from docx import Document

DOCX_PATH = Path(r"C:\Users\Administrator\Desktop\课件\劳动力调查指标讲解（已更新）.docx")
BACKEND_URL = "http://127.0.0.1:8765"
REPORT_PATH = Path(__file__).resolve().parent.parent / "reports" / "eval-docx.json"


def parse_docx(path: Path) -> list[dict]:
    """解析 docx 为 [{section, q_no, question, options, correct, ref_answer}, ...]"""
    doc = Document(str(path))
    rows = [r.cells[0].text.strip() for r in doc.tables[0].rows]

    # 找到所有 答案解析 锚点
    expl_indices = []
    for i, r in enumerate(rows):
        if re.match(r"^\s*答案(解析|：)\s*[:：]?", r):
            expl_indices.append(i)
        elif "答案解析" in r and r.startswith("答案解析"):
            expl_indices.append(i)

    items: list[dict] = []
    current_section = ""
    prev_anchor = -1

    for idx in expl_indices:
        # 扫 [prev_anchor, idx] 之间的所有行，更新 current_section
        for r in rows[max(prev_anchor, 0) : idx]:
            m = re.search(r"画像\s*(\d+)", r)
            if m:
                current_section = f"画像{m.group(1)}"
        prev_anchor = idx

        ref_answer = rows[idx]
        # 去掉前缀"答案解析:" / "答案："
        ref_answer = re.sub(r"^答案\s*(解析|：)\s*[:：]?\s*", "", ref_answer).strip()

        # 找 question（向上找到第一个非空、非 options 的行）
        q_row = None
        for j in range(idx - 1, max(idx - 6, -1), -1):
            text = rows[j]
            if not text:
                continue
            if re.match(r"^\s*[A-Z][、.]", text) or re.match(r"^\s*[①②③④⑤⑥⑦⑧⑨]", text):
                # 是 options 行（"A、xxx" / "①xxx"），跳过
                continue
            q_row = text
            break

        # 找 options（question 之后到 expl 之前）
        options = ""
        correct = ""
        if q_row:
            try:
                q_idx = rows.index(q_row, max(idx - 6, 0), idx)
                if q_idx + 1 < idx:
                    options = rows[q_idx + 1]
                    m = re.search(r"[(（](正确答案)[)）]", options)
                    if m:
                        correct = m.group(1)
            except ValueError:
                pass

        # 提取 section（画像X）— 已在前面 prev_anchor -> idx 扫描中更新
        section = current_section

        # 提取题号和问题正文
        question = q_row or ""
        question = re.sub(r"^画像\s*\d+\s*\n?", "", question).strip()
        q_no = ""
        m = re.match(r"^(\d+)[、.\s]", question)
        if m:
            q_no = m.group(1)
        # 去掉 [单选题] 等题型标记
        question = re.sub(r"\s*\[.*?\]\s*\*?\s*$", "", question).strip()
        # 去掉题号
        question = re.sub(r"^\d+[、.\s]+\s*", "", question).strip()

        items.append({
            "section": section,
            "q_no": q_no,
            "question": question,
            "options": options,
            "correct": correct,
            "ref_answer": ref_answer,
        })

    return items


def ask_rag(question: str, timeout: int = 60) -> dict:
    """调 /api/chat，返回 {answer, mode, sources, latency_ms}。"""
    t0 = time.time()
    r = requests.post(
        f"{BACKEND_URL}/api/chat",
        json={"message": question},
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    return {
        **data,
        "latency_ms": int((time.time() - t0) * 1000),
    }


def score_coverage(ref: str, rag: str) -> dict:
    """用 jieba 分词的 token 重叠率评估 RAG 答案对参考解析的覆盖度。"""
    if not ref or not rag:
        return {"precision": 0, "recall": 0, "f1": 0, "ref_tokens": 0, "rag_tokens": 0}

    # 参考解析里的"关键词"：去掉常见停用词
    stop = {"的", "了", "是", "在", "和", "与", "或", "等", "为", "有", "对", "这", "那",
            "应", "应选", "应该", "可以", "指", "属于", "按", "把", "被", "用", "而", "及",
            "不", "没", "也", "都", "就", "要", "会", "能", "可", "但", "若", "则", "所",
            "一个", "一种", "一样", "一直", "一定", "一样", "一同"}

    ref_tokens = [t for t in jieba.cut(ref) if t.strip() and t not in stop and len(t) > 1]
    rag_tokens = [t for t in jieba.cut(rag) if t.strip()]

    if not ref_tokens:
        return {"precision": 0, "recall": 0, "f1": 0, "ref_tokens": 0, "rag_tokens": len(rag_tokens)}

    ref_set = set(ref_tokens)
    rag_set = set(rag_tokens)
    hit = len(ref_set & rag_set)
    precision = hit / max(len(rag_set), 1)
    recall = hit / max(len(ref_set), 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)
    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "ref_tokens": len(ref_set),
        "rag_tokens": len(rag_set),
        "hit_tokens": hit,
    }


def main() -> int:
    if not DOCX_PATH.exists():
        print(f"docx not found: {DOCX_PATH}")
        return 1

    print(f"解析 {DOCX_PATH.name} ...")
    items = parse_docx(DOCX_PATH)
    print(f"抽到 {len(items)} 道题\n")

    results: list[dict] = []
    section_stats: dict[str, dict[str, int]] = {}

    for i, item in enumerate(items, 1):
        q = item["question"]
        print(f"[{i:2d}/{len(items)}] {item['section']} q{item['q_no']}: {q[:35]}...", end=" ", flush=True)
        try:
            resp = ask_rag(q)
        except Exception as e:
            print(f"ERR {e}")
            results.append({**item, "error": str(e), "rag_answer": "", "rag_mode": "error"})
            continue

        cov = score_coverage(item["ref_answer"], resp.get("answer", ""))
        results.append({
            **item,
            "rag_answer": resp.get("answer", ""),
            "rag_mode": resp.get("mode", ""),
            "rag_sources": len(resp.get("sources", [])),
            "retrieval_score": resp.get("retrieval_score"),
            "coverage": cov,
            "latency_ms": resp["latency_ms"],
        })
        sec = section_stats.setdefault(item["section"], {"total": 0, "pass": 0, "fail": 0})
        sec["total"] += 1
        if cov["f1"] >= 0.4 and resp.get("mode") == "rag":
            sec["pass"] += 1
        else:
            sec["fail"] += 1
        print(
            f"mode={resp.get('mode'):11s} f1={cov['f1']:.2f}  "
            f"{resp['latency_ms']}ms"
        )

    # 汇总
    total = len(results)
    passed = sum(1 for r in results if r.get("coverage", {}).get("f1", 0) >= 0.4 and r.get("rag_mode") == "rag")
    by_section = {
        sec: {
            "total": stats["total"],
            "pass": stats["pass"],
            "fail": stats["fail"],
            "rate": round(stats["pass"] / max(stats["total"], 1), 2),
        }
        for sec, stats in section_stats.items()
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {"summary": {"total": total, "pass": passed, "by_section": by_section},
             "results": results},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    # 终端汇总
    print(f"\n=== 汇总 ===")
    print(f"总通过: {passed}/{total} = {passed/total:.1%}\n")
    for sec, s in sorted(
        by_section.items(),
        key=lambda x: int(re.search(r"\d+", x[0]).group()) if re.search(r"\d+", x[0]) else 999,
    ):
        print(f"  {sec:6s}: {s['pass']:2d}/{s['total']:2d}  pass_rate={s['rate']:.0%}")

    # 列出 f1 最低 10 题（最需要关注的）
    print(f"\n=== F1 最低 10 题（潜在缺口）===")
    bad = sorted([r for r in results if r.get("rag_mode") == "rag"],
                 key=lambda x: x.get("coverage", {}).get("f1", 0))[:10]
    for r in bad:
        print(f"  [{r['section']} q{r['q_no']}] f1={r['coverage']['f1']:.2f}")
        print(f"    Q: {r['question'][:60]}")
        print(f"    参考: {r['ref_answer'][:60]}")
        print(f"    RAG: {r['rag_answer'][:60]}")
        print()

    print(f"\n详细报告: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
