"""把审核通过的 Q&A 追加到 faq.json，自动续号，跑 validate_faq.py。

输入：JSON 数组文件，每项至少含 question/answer/category/source/keywords：
    python scripts/add_faq_entries.py reports/approved-xxx.json
    python scripts/add_faq_entries.py reports/approved-xxx.json --dry-run

字段处理：
- 自动分配 id：当前最大 id + 1（3 位零填充）
- 缺 category 报错（不允许）
- 缺 keywords 报错（validate_faq 要求 ≥3）
- 缺 source 报错（validate_faq 要求非空）
- answer 长度 50-400 字（硬约束，超出报错）
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FAQ_PATH = PROJECT_ROOT / "knowledge-base" / "qa" / "faq.json"
MIN_ANSWER_LEN = 50
MAX_ANSWER_LEN = 400
MIN_KEYWORDS = 3
ID_RE = re.compile(r"^\d{3}$")


def _stdout_utf8() -> None:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", line_buffering=True
        )


def next_id(existing: list[dict]) -> str:
    used = {int(qa["id"]) for qa in existing if ID_RE.fullmatch(str(qa.get("id", "")))}
    nxt = max(used, default=0) + 1
    return f"{nxt:03d}"


def validate_entry(entry: dict) -> list[str]:
    """返回错误列表，空表示通过。"""
    errs: list[str] = []
    for f in ("question", "answer", "category", "source", "keywords"):
        if not entry.get(f):
            errs.append(f"缺字段 {f}")
    if entry.get("answer"):
        n = len(entry["answer"])
        if n < MIN_ANSWER_LEN:
            errs.append(f"answer 长度 {n} < {MIN_ANSWER_LEN}")
        elif n > MAX_ANSWER_LEN:
            errs.append(f"answer 长度 {n} > {MAX_ANSWER_LEN}")
    kws = entry.get("keywords") or []
    if not isinstance(kws, list) or len(kws) < MIN_KEYWORDS:
        errs.append(f"keywords 数 {len(kws) if isinstance(kws, list) else 0} < {MIN_KEYWORDS}")
    return errs


def main() -> int:
    _stdout_utf8()
    p = argparse.ArgumentParser(description="追加 Q&A 到 faq.json")
    p.add_argument("input", type=Path, help="JSON 数组文件，每项是一条例入条目")
    p.add_argument("--faq", type=Path, default=FAQ_PATH)
    p.add_argument("--dry-run", action="store_true", help="只检查不入库")
    args = p.parse_args()

    if not args.input.exists():
        print(f"找不到输入: {args.input}")
        return 1
    if not args.faq.exists():
        print(f"找不到 faq: {args.faq}")
        return 1

    try:
        items = json.loads(args.input.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"输入不是合法 JSON: {e}")
        return 1
    if not isinstance(items, list) or not items:
        print("输入必须是非空数组")
        return 1

    faq = json.loads(args.faq.read_text(encoding="utf-8"))
    print(f"faq 现有 {len(faq)} 条，本次拟加 {len(items)} 条")

    # 单测每条 + 收集错误
    valid: list[dict] = []
    for i, item in enumerate(items, 1):
        errs = validate_entry(item)
        if errs:
            print(f"  [{i}] 跳过：{'; '.join(errs)}")
            print(f"      Q: {(item.get('question') or '')[:50]}")
            continue
        valid.append(item)

    if not valid:
        print("\n没有可加的有效条目")
        return 1

    # 续号
    base_id = int(next_id(faq))
    for i, item in enumerate(valid):
        item["id"] = f"{base_id + i:03d}"

    # 检查 id 不冲突（防御性）
    existing_ids = {qa["id"] for qa in faq}
    for item in valid:
        if item["id"] in existing_ids:
            print(f"id {item['id']} 已存在，冲突")
            return 2

    print("\n拟新增条目：")
    for item in valid:
        print(f"  +{item['id']} [{item['category']}] {item['question'][:45]}...")

    if args.dry_run:
        print("\n[dry-run] 不写入")
        return 0

    faq.extend(valid)
    args.faq.write_text(
        json.dumps(faq, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n✓ 写入 {args.faq}（共 {len(faq)} 条）")

    print("\n跑 validate_faq.py ...")
    r = subprocess.run(
        ["python", "scripts/validate_faq.py"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(r.stdout)
    if r.returncode != 0:
        print("VALIDATE 失败！")
        if r.stderr:
            print(r.stderr[-500:])
        return 1

    print("完成。下一步：python scripts/build_kb.py --full && python scripts/build_bm25.py --full")
    return 0


if __name__ == "__main__":
    sys.exit(main())
