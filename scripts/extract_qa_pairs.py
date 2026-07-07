"""从 markdown 抽取 Q&A 候选。

两种模式：
- regex：题库结构（题号 + A./B./C./D. + 答案），产出结构化但不含完整 prose answer
- llm  ：制度/讲解文档，按章节切分后用 DeepSeek 提炼 faq 兼容条目

输出：reports/extracted-qa-<stem>.json

环境变量：DEEPSEEK_API_KEY
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = PROJECT_ROOT / "reports"

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

MAX_CHARS_PER_LLM_CALL = 6000  # 留余量，避免超 16K context

LLM_SYSTEM_PROMPT = """你是国家统计局劳动力调查制度专家，熟悉《劳动力调查制度》《劳动力调查指标讲解》全部内容。
你的任务是从给定章节中提炼 N 个常见调查员疑问 + 标准答案，用于补充知识库 FAQ。

要求：
1. question 必须是用户可能问的口语化问句（15-40 字），用问号结尾
2. answer 用书面语 80-300 字，给出明确判断标准或操作步骤，可含示例
3. category 从以下选一个：调查对象 / 填报规范 / 就业状态判断 / 失业原因 / 错误示例 / 复杂场景案例 / 工作情况 / 个人信息 / 住户信息
4. keywords 数组 3-5 个，便于检索
5. 只输出 JSON 数组，不要其他内容、不要 markdown 代码块包裹

示例输出：
[{"question": "F26.2从业人数包括哪些人？", "answer": "F26.2从业人数 = ...", "category": "填报规范", "keywords": ["F26.2", "从业人数"]}]
"""


def _stdout_utf8() -> None:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", line_buffering=True
        )


# ── regex 模式 ──────────────────────────────


_ANSWER_LINE_RE = re.compile(r"^\s*答案\s*[:：]?\s*(.+?)\s*$")
_QUESTION_NUM_RE = re.compile(r"^(\d+)\s*[、.]\s*(.+)$")
_OPTION_RE = re.compile(r"^([A-Z])[、.]\s*(.+)$")
_INLINE_ANSWER_RE = re.compile(r"[（(]([A-Da-d])[)）]")


def parse_regex(md_text: str) -> list[dict]:
    """题库正则抽取：题号 + 选项 + 答案。

    答案位置两种格式都支持：
    - 独立行：答案：D
    - 题干末尾：一般情况下...每月（B）日...
    """
    lines = md_text.splitlines()
    items: list[dict] = []
    current: dict | None = None
    pending_options: list[tuple[str, str]] = []

    def flush() -> None:
        nonlocal current, pending_options
        if current is not None:
            current["options"] = pending_options
            items.append(current)
        current = None
        pending_options = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        m = _ANSWER_LINE_RE.match(stripped)
        if m and current is not None:
            current["answer_letter"] = m.group(1).strip()
            flush()
            continue

        m = _OPTION_RE.match(stripped)
        if m and current is not None:
            pending_options.append((m.group(1), m.group(2)))
            continue

        m = _QUESTION_NUM_RE.match(stripped)
        if m:
            if current is not None:
                flush()
            question_text = m.group(2).strip()
            # 题干末尾的 (X) / （X） 当作答案（仅当还没从独立行拿到时）
            inline = _INLINE_ANSWER_RE.search(question_text)
            current = {
                "q_no": m.group(1),
                "question": _INLINE_ANSWER_RE.sub("", question_text).strip() if inline else question_text,
                "answer_letter": inline.group(1).upper() if inline else "",
            }
            continue

        if current is not None and not pending_options and not current.get("answer_letter"):
            new_q = (current["question"] + " " + stripped).strip()
            inline = _INLINE_ANSWER_RE.search(new_q)
            if inline:
                current["question"] = _INLINE_ANSWER_RE.sub("", new_q).strip()
                current["answer_letter"] = inline.group(1).upper()
            else:
                current["question"] = new_q

    flush()
    return items


# ── llm 模式 ──────────────────────────────


def call_deepseek(messages: list[dict], timeout: int = 90) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("未找到 DEEPSEEK_API_KEY")
    resp = requests.post(
        DEEPSEEK_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": DEEPSEEK_MODEL, "messages": messages, "temperature": 0.3},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def parse_llm_json(text: str) -> list[dict]:
    """LLM 输出可能含 ```json 包裹或前后杂文。"""
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", s, re.DOTALL)
    if fence:
        s = fence.group(1)
    else:
        start = s.find("[")
        end = s.rfind("]")
        if start != -1 and end != -1 and end > start:
            s = s[start : end + 1]
    try:
        data = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 输出无法解析为 JSON：{e}\n原文：{text[:200]}") from e
    if not isinstance(data, list):
        raise ValueError("LLM 输出根节点不是数组")
    return data


def split_into_sections(md_text: str) -> list[str]:
    """按顶级章节切分（一、xxx / 二、xxx / Slide N 等）。"""
    section_re = re.compile(
        r"^(?:" + r"|".join(
            [
                r"[一二三四五六七八九十]+、",  # 一、xxx
                r"第[一二三四五六七八九十百]+[章节部分]",  # 第三章
                r"--- Slide \d+ ---",  # PPTX slide
                r"--- Page \d+ ---",  # PDF page
            ]
        ) + r")",
        re.MULTILINE,
    )
    indices = [m.start() for m in section_re.finditer(md_text)]
    if not indices:
        return [md_text]
    indices.append(len(md_text))
    return [md_text[indices[i] : indices[i + 1]].strip() for i in range(len(indices) - 1)]


def chunk_section(section: str, max_chars: int) -> list[str]:
    if len(section) <= max_chars:
        return [section]
    parts: list[str] = []
    start = 0
    while start < len(section):
        end = min(start + max_chars, len(section))
        if end < len(section):
            nl = section.rfind("\n\n", start, end)
            if nl > start + max_chars // 2:
                end = nl
        parts.append(section[start:end].strip())
        start = end
    return [p for p in parts if p]


def extract_llm(md_text: str, source_label: str) -> list[dict]:
    sections = split_into_sections(md_text)
    out: list[dict] = []
    for sec in sections:
        chunks = chunk_section(sec, MAX_CHARS_PER_LLM_CALL)
        for chunk in chunks:
            user_msg = f"[来源：{source_label}]\n\n{chunk}"
            print(f"  LLM 调（{len(chunk)} 字）...", end=" ", flush=True)
            for attempt in range(3):
                try:
                    text = call_deepseek(
                        [
                            {"role": "system", "content": LLM_SYSTEM_PROMPT},
                            {"role": "user", "content": user_msg},
                        ]
                    )
                    items = parse_llm_json(text)
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        it.setdefault("source", source_label)
                        out.append(it)
                    print(f"+{len(items)} 条")
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"失败：{e}")
                        raise
                    print(f"重试 {attempt + 1}...")
                    time.sleep(2 ** attempt)
    return out


# ── main ──────────────────────────────


def main() -> int:
    _stdout_utf8()
    p = argparse.ArgumentParser(description="从 markdown 抽 Q&A 候选")
    p.add_argument("input", type=Path, help="markdown 路径")
    p.add_argument(
        "--mode",
        choices=["regex", "llm"],
        required=True,
        help="regex: 题库结构；llm: 制度/讲解走 DeepSeek 提炼",
    )
    p.add_argument("--source", default=None, help="标注到每条 source 字段（默认 = 文件名）")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    if not args.input.exists():
        print(f"找不到输入: {args.input}")
        return 1

    md_text = args.input.read_text(encoding="utf-8")
    source_label = args.source or args.input.stem
    out_path = args.out or (REPORTS_DIR / f"extracted-qa-{args.input.stem}.json")

    if args.mode == "regex":
        items = parse_regex(md_text)
    else:
        print(f"LLM 模式提取（{len(md_text)} 字）...")
        items = extract_llm(md_text, source_label)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {"source": source_label, "mode": args.mode, "count": len(items), "items": items},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n✓ 抽到 {len(items)} 条 → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
