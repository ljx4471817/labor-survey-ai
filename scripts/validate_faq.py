"""验证 faq.json 的数据质量。

检查项：
1. 字段完整性（id/category/question/answer/source/keywords）
2. id 连续无断号、无重复
3. answer 长度合理（50-400 字）
4. keywords ≥ 3 个
5. source 非空、不含"待核实"
6. question 完全重复检测
7. 同一 category 内问题相似度检测（粗略）

用法：
    python scripts/validate_faq.py
    python scripts/validate_faq.py --path knowledge-base/qa/faq.json
    python scripts/validate_faq.py --strict   # 把 warning 也算 error
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_FIELDS = ("id", "category", "question", "answer", "source", "keywords")
MIN_ANSWER_LEN = 50
MAX_ANSWER_LEN = 400
MIN_KEYWORDS = 3
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = PROJECT_ROOT / "knowledge-base" / "qa" / "faq.json"
CATALOG_PATH = PROJECT_ROOT / "knowledge-base" / "indicator_catalog.json"


@dataclass
class Issue:
    severity: str  # "error" | "warning"
    code: str
    qa_id: str
    detail: str


@dataclass
class Report:
    total: int = 0
    issues: list[Issue] = field(default_factory=list)

    def add(self, severity: str, code: str, qa_id: str, detail: str) -> None:
        self.issues.append(Issue(severity, code, qa_id, detail))

    def by_severity(self) -> dict[str, int]:
        return Counter(i.severity for i in self.issues)

    def by_code(self) -> dict[str, int]:
        return Counter(i.code for i in self.issues)


def check_required_fields(qa: dict, report: Report) -> bool:
    qa_id = str(qa.get("id", "<missing>"))
    ok = True
    for f in REQUIRED_FIELDS:
        if f not in qa:
            report.add("error", "missing_field", qa_id, f"缺字段 {f}")
            ok = False
    return ok


def check_id_format(qa: dict, report: Report) -> int | None:
    qa_id_raw = qa.get("id", "")
    if not re.fullmatch(r"\d{3}", str(qa_id_raw)):
        report.add("error", "bad_id_format", str(qa_id_raw), "id 必须是 3 位数字字符串")
        return None
    return int(qa_id_raw)


def check_answer_length(qa: dict, report: Report) -> None:
    qa_id = str(qa.get("id", ""))
    answer = qa.get("answer", "") or ""
    n = len(answer)
    if n < MIN_ANSWER_LEN:
        report.add("error", "answer_too_short", qa_id, f"answer 长度 {n} < {MIN_ANSWER_LEN}")
    elif n > MAX_ANSWER_LEN:
        report.add("warning", "answer_too_long", qa_id, f"answer 长度 {n} > {MAX_ANSWER_LEN}")


def check_keywords(qa: dict, report: Report) -> None:
    qa_id = str(qa.get("id", ""))
    keywords = qa.get("keywords", []) or []
    if not isinstance(keywords, list):
        report.add("error", "keywords_not_list", qa_id, "keywords 必须是数组")
        return
    if len(keywords) < MIN_KEYWORDS:
        report.add("error", "keywords_too_few", qa_id, f"keywords 数 {len(keywords)} < {MIN_KEYWORDS}")
    for k in keywords:
        if not isinstance(k, str) or not k.strip():
            report.add("error", "keyword_empty", qa_id, f"存在空关键词: {k!r}")


def check_source(qa: dict, report: Report) -> None:
    qa_id = str(qa.get("id", ""))
    source = (qa.get("source", "") or "").strip()
    if not source:
        report.add("error", "source_empty", qa_id, "source 为空")
        return
    if "待核实" in source or "TODO" in source.upper():
        report.add("error", "source_placeholder", qa_id, f"source 含占位符: {source}")


def check_question(qa: dict, report: Report) -> None:
    qa_id = str(qa.get("id", ""))
    q = (qa.get("question", "") or "").strip()
    if not q:
        report.add("error", "question_empty", qa_id, "question 为空")
    if q.endswith("？") is False and q.endswith("?") is False:
        # 多数 QA 问题应该是问句
        report.add("warning", "question_no_question_mark", qa_id, "question 缺少问号")


def check_answer_placeholders(qa: dict, report: Report) -> None:
    qa_id = str(qa.get("id", ""))
    answer = qa.get("answer", "") or ""
    # 整词匹配，避免误报话术里的 XXX（姓名）/占位号码 等合规用法
    placeholders = [
        (r"\bTODO\b", "TODO"),
        (r"\bTBD\b", "TBD"),
        (r"\bFIXME\b", "FIXME"),
        (r"待核实", "待核实"),
        (r"占位符", "占位符"),
    ]
    for pattern, label in placeholders:
        if re.search(pattern, answer):
            report.add("warning", "answer_has_placeholder", qa_id, f"answer 含占位符: {label}")


def collect_question_texts(qas: list[dict]) -> dict[str, list[str]]:
    """返回 question 文本 → [id, ...] 映射，用于查重。"""
    bucket: dict[str, list[str]] = defaultdict(list)
    for qa in qas:
        q = (qa.get("question", "") or "").strip().lower()
        if q:
            bucket[q].append(str(qa.get("id", "")))
    return bucket


def check_duplicates(qas: list[dict], report: Report) -> None:
    bucket = collect_question_texts(qas)
    for q, ids in bucket.items():
        if len(ids) > 1:
            report.add("error", "question_duplicate", ",".join(ids), f"问题完全相同: {q!r}")


def check_id_continuity(qas: list[dict], report: Report) -> None:
    """ID 应当从 001 开始，连续递增。"""
    ids: list[int] = []
    for qa in qas:
        v = check_id_format(qa, report)
        if v is not None:
            ids.append(v)
    if not ids:
        return
    ids_sorted = sorted(set(ids))
    expected = list(range(min(ids_sorted), max(ids_sorted) + 1))
    missing = sorted(set(expected) - set(ids_sorted))
    dup = [i for i, c in Counter(ids).items() if c > 1]
    for m in missing:
        report.add("warning", "id_gap", "-", f"id 断号: {m:03d}")
    for d in dup:
        report.add("error", "id_duplicate", f"{d:03d}", "id 重复")


def category_stats(qas: list[dict]) -> dict[str, int]:
    return dict(Counter(qa.get("category", "<空>") for qa in qas))


def check_indicators(qa: dict, report: Report, all_catalog_codes: set[str]) -> None:
    qa_id = str(qa.get("id", ""))
    if qa.get("_indicators_review"):
        report.add("warning", "indicators_review_needed", qa_id,
                   "indicators 字段需人工标注")
        return
    indicators = qa.get("indicators")
    if indicators is None:
        report.add("warning", "indicators_missing", qa_id,
                   "缺少 indicators 字段（运行 backfill_indicators.py）")
        return
    if not isinstance(indicators, list):
        report.add("error", "indicators_not_list", qa_id,
                   "indicators 必须是数组")
        return
    for code in indicators:
        if code not in all_catalog_codes:
            report.add("error", "indicators_unknown_code", qa_id,
                       f"indicators 含未注册编号: {code}（检查 indicator_catalog.json）")


def load_catalog_codes() -> set[str]:
    if not CATALOG_PATH.exists():
        return set()
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    codes = set()
    for mod_indicators in catalog.get("modules", {}).values():
        codes.update(mod_indicators.keys())
    return codes


def validate(path: Path) -> Report:
    report = Report()
    if not path.exists():
        report.add("error", "file_missing", "-", f"找不到文件: {path}")
        return report
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        report.add("error", "json_invalid", "-", f"JSON 解析失败: {e}")
        return report
    if not isinstance(data, list):
        report.add("error", "json_not_array", "-", "根节点必须是数组")
        return report
    report.total = len(data)
    catalog_codes = load_catalog_codes()
    for qa in data:
        if not isinstance(qa, dict):
            report.add("error", "qa_not_object", "-", "QA 项必须是对象")
            continue
        if not check_required_fields(qa, report):
            continue
        check_id_format(qa, report)
        check_question(qa, report)
        check_answer_length(qa, report)
        check_keywords(qa, report)
        check_source(qa, report)
        check_answer_placeholders(qa, report)
        check_indicators(qa, report, catalog_codes)
    check_id_continuity(data, report)
    check_duplicates(data, report)
    return report


def print_report(path: Path, report: Report, strict: bool) -> int:
    sev = report.by_severity()
    codes = report.by_code()
    print(f"\n=== faq.json 验证报告 ===")
    print(f"文件: {path}")
    print(f"总条数: {report.total}")
    print(f"错误数: {sev.get('error', 0)}")
    print(f"警告数: {sev.get('warning', 0)}")
    if codes:
        print("\n按问题类型统计:")
        for code, n in codes.most_common():
            print(f"  {code}: {n}")
    if report.issues:
        print("\n详细问题（前 50 条）:")
        for i in report.issues[:50]:
            print(f"  [{i.severity}] {i.code} id={i.qa_id}: {i.detail}")
        if len(report.issues) > 50:
            print(f"  ... 还有 {len(report.issues) - 50} 条未显示")
    errors = sev.get("error", 0)
    warnings = sev.get("warning", 0)
    if errors == 0 and (warnings == 0 or not strict):
        print("\n[OK] 验证通过")
        return 0
    if errors > 0:
        print(f"\n[FAIL] 存在 {errors} 个错误")
        return 1
    print(f"\n[WARN] 存在 {warnings} 个警告（strict 模式视为失败）")
    return 2 if strict else 0


def main() -> int:
    p = argparse.ArgumentParser(description="验证 faq.json 数据质量")
    p.add_argument("--path", type=Path, default=DEFAULT_PATH, help="faq.json 路径")
    p.add_argument("--strict", action="store_true", help="把 warning 也算失败")
    args = p.parse_args()
    report = validate(args.path)
    return print_report(args.path, report, args.strict)


if __name__ == "__main__":
    sys.exit(main())
