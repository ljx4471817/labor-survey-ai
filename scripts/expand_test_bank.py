"""把题库选择题 + 正确答案 调 LLM 扩写成 faq.json 兼容的 prose 答案。

输入：reports/extracted-qa-*.json（regex 模式产物，含 question/options/answer_letter）
输出：reports/expanded-qa-<stem>.json（[{question, answer, category, source, keywords}]）

合并策略：每 BATCH_SIZE 题一次 API 调用，prompt 一次性喂多题。
LLM 输出每题一个 JSON 对象，整体用 ```json\n[...]\n``` 包裹。
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
BATCH_SIZE = 5

EXPAND_SYSTEM_PROMPT = """你是国家统计局劳动力调查制度专家。给定一组选择题（题目 + 4 个选项 + 正确答案），
为每题写一段「调查员实操指南」prose 答案，供 faq 知识库使用。

要求：
- answer 100-300 字书面语，给出明确判断标准或操作步骤
- 引用相关制度条款（如果适用，如「F26.2」「H2」等编号）
- 可包含 1-2 个具体场景示例
- category 从以下选 1 个：调查对象 / 填报规范 / 就业状态判断 / 失业原因 / 错误示例 / 复杂场景案例 / 工作情况 / 个人信息 / 住户信息
- keywords 3-5 个检索关键词
- 严格按指定 JSON 数组输出，不要 markdown 代码块，不要任何额外文字
"""


def _stdout_utf8() -> None:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", line_buffering=True
        )


def call_deepseek(messages: list[dict], timeout: int = 120) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("未找到 DEEPSEEK_API_KEY")
    resp = requests.post(
        DEEPSEEK_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": DEEPSEEK_MODEL, "messages": messages, "temperature": 0.2},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def parse_llm_array(text: str) -> list[dict]:
    """容错解析：处理 ```json 包裹、混合文本等情况。"""
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
        raise ValueError(f"LLM 输出无法解析为 JSON：{e}\n原文前 300 字：{text[:300]}") from e
    if not isinstance(data, list):
        raise ValueError("LLM 输出根节点不是数组")
    return data


def render_batch_questions(questions: list[dict]) -> str:
    parts: list[str] = []
    for i, q in enumerate(questions, 1):
        opts = q.get("options", [])
        opts_text = "\n".join(f"{o[0]}. {o[1]}" for o in opts)
        letter = q.get("answer_letter", "未知")
        parts.append(
            f"【第{i}题】\n"
            f"题目：{q.get('question', '').strip()}\n"
            f"选项：\n{opts_text}\n"
            f"正确答案：{letter}"
        )
    return "\n\n".join(parts)


def expand_one_batch(batch: list[dict], source_label: str) -> list[dict]:
    user_msg = (
        f"[来源：{source_label}]\n\n"
        f"{render_batch_questions(batch)}\n\n"
        f"请为每道题输出一个 JSON 对象，整体按数组形式返回。"
    )
    for attempt in range(3):
        try:
            text = call_deepseek(
                [
                    {"role": "system", "content": EXPAND_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ]
            )
            return parse_llm_array(text)
        except Exception as e:
            if attempt == 2:
                print(f"  批次失败：{e}")
                raise
            print(f"  重试 {attempt + 1}...")
            time.sleep(2 ** attempt)


def main() -> int:
    _stdout_utf8()
    p = argparse.ArgumentParser(description="题库选择题调 LLM 扩写为 prose FAQ")
    p.add_argument("input", type=Path, help="extracted-qa-*.json 或 gap-report-*.json 路径")
    p.add_argument(
        "--from-gap-report", action="store_true",
        help="input 解读为 gap-report JSON，只处理 add_candidates 段",
    )
    p.add_argument(
        "--source", default=None,
        help="标注到每条 source 字段（默认 = 文件名去前缀）",
    )
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    p.add_argument("--limit", type=int, default=0, help=">0 时只处理前 N 题（调试用）")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    if not args.input.exists():
        print(f"找不到输入: {args.input}")
        return 1

    data = json.loads(args.input.read_text(encoding="utf-8"))
    if args.from_gap_report:
        items = [rec["candidate"] for rec in data.get("add_candidates", [])]
        print(f"从 gap-report 读 add_candidates {len(items)} 条")
    else:
        items = data.get("items", [])
        print(f"从 extracted 读 items {len(items)} 条")
    # 仅保留有完整选项 + 答案的题
    usable = [
        it for it in items
        if it.get("options") and it.get("answer_letter")
    ]
    print(f"剔除缺选项/答案后 {len(usable)} 条可用")
    if args.limit > 0:
        usable = usable[: args.limit]
        print(f"--limit 截断到 {len(usable)} 条")

    source_label = args.source or args.input.stem.replace(
        "extracted-qa-", ""
    ).replace("gap-report-", "")

    out_path = args.out or (
        REPORTS_DIR / f"expanded-qa-{args.input.stem.replace('extracted-qa-', '')}.json"
    )

    out: list[dict] = []
    for batch_start in range(0, len(usable), args.batch_size):
        batch = usable[batch_start : batch_start + args.batch_size]
        print(
            f"[{batch_start + 1:3d}-{batch_start + len(batch):3d}/{len(usable)}] "
            f"LLM 调（{len(batch)} 题）...",
            end=" ", flush=True,
        )
        try:
            results = expand_one_batch(batch, source_label)
        except Exception as e:
            print(f"失败：{e}")
            continue
        # 按 q_no 对齐回原题
        for orig, expanded in zip(batch, results):
            if not isinstance(expanded, dict):
                continue
            expanded.setdefault("question", orig.get("question", ""))
            expanded["source"] = f"{source_label} 题号 {orig.get('q_no', '?')}"
            expanded.setdefault("category", "填报规范")
            expanded.setdefault("keywords", [])
            out.append(expanded)
        print(f"+{len(results)} 条")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "source": source_label,
                "count": len(out),
                "items": out,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n✓ 扩写 {len(out)} 条 → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
