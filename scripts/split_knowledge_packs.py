#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Split the flat knowledge base into national and Guizhou packs.

Pack membership represents content applicability, while each QA/source keeps
its original provenance. The original flat files are kept as compatibility
sources; this script never deletes source content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_FAQ = PROJECT_ROOT / "knowledge-base" / "qa" / "faq.json"
SOURCE_EVAL = PROJECT_ROOT / "knowledge-base" / "qa" / "eval_set.json"
SOURCE_CHUNKS = PROJECT_ROOT / "knowledge-base" / "chunks.jsonl"
PACKS_ROOT = PROJECT_ROOT / "knowledge-base" / "packs"
SHARED_ROOT = PROJECT_ROOT / "knowledge-base" / "shared"

NATIONAL = "national-base"
GUIZHOU = "guizhou"
PACK_ORDER = (NATIONAL, GUIZHOU)

ADOPTED_SICHUAN_SOURCE_MARKER = "四川劳动力调查技能测试题题库2026"
GUIZHOU_SOURCE_MARKERS = (
    "贵阳调查队填报规范指引",
    "统计云系统培训课件",
    ADOPTED_SICHUAN_SOURCE_MARKER,
)

# Local rule keys are assigned only to Guizhou content at Phase 0. Matching
# national entries are deliberately not changed until business review; this
# avoids silently suppressing broader national rules.
GUIZHOU_RULE_KEYS = {
    "355": "H3.student_vacation_registration",
    "209": "F20_21.flexible_worker_employment_identity",
    "357": "F27.non_monthly_income",
    "429": "F10.contractor.no_active_project",
    "430": "F10.contractor.no_activity",
    "431": "F16.contractor.weekly_hours",
    "433": "F10.contractor.stopped_or_retired",
    "434": "F10.contractor.affiliated_waiting_project",
}

# These eval cases depend on Guizhou-specific answers. eval-108 stays in the
# national pack because its expected answer comes from the national制度.
GUIZHOU_EVAL_IDS = {
    "eval-103",
    "eval-105",
    "eval-106",
    "eval-107",
    "eval-109",
    "eval-110",
    "eval-111",
    "eval-112",
}

NATIONAL_SOURCES = (
    "劳动力调查制度（2026年定期报表）-定稿.md",
    "劳动力调查及指标讲解.md",
    "劳动力调查专业题库（24年8月）.md",
)
GUIZHOU_SOURCES = (
    "统计云系统培训课件.md",
    "四川劳动力调查技能测试题题库2026.md",
)


def classify_qa(qa: dict) -> str:
    """Return the target pack by content applicability, not source origin."""
    source = str(qa.get("source") or "")
    if any(marker in source for marker in GUIZHOU_SOURCE_MARKERS):
        return GUIZHOU
    return NATIONAL


def classify_eval(item: dict) -> str:
    """Return the target eval pack for one evaluation case."""
    if item.get("id") in GUIZHOU_EVAL_IDS:
        return GUIZHOU
    source = str(item.get("expected_source_section") or "")
    if "贵阳" in source:
        return GUIZHOU
    question = str(item.get("question") or "")
    if "装修" in question and "工资浮动" in question:
        return GUIZHOU
    return NATIONAL


def classify_chunk(chunk: dict) -> str:
    """Return the target pack for one markdown chunk by applicability."""
    source = str(chunk.get("source") or "")
    if any(marker in source for marker in GUIZHOU_SOURCE_MARKERS):
        return GUIZHOU
    return NATIONAL


def _with_rule_key(qa: dict) -> dict:
    qa_id = str(qa.get("id") or "")
    if qa_id not in GUIZHOU_RULE_KEYS:
        return dict(qa)
    updated = dict(qa)
    updated["rule_key"] = GUIZHOU_RULE_KEYS[qa_id]
    return updated


def split_entries(rows: Iterable[dict]) -> dict[str, list[dict]]:
    """Split rows into pack buckets while preserving source order."""
    result: dict[str, list[dict]] = {pack: [] for pack in PACK_ORDER}
    seen_ids: set[str] = set()
    for row in rows:
        qa_id = str(row.get("id") or "")
        if qa_id in seen_ids:
            raise ValueError(f"重复 QA id: {qa_id}")
        seen_ids.add(qa_id)
        pack = classify_qa(row)
        result[pack].append(_with_rule_key(row))
    return result


def split_evals(rows: Iterable[dict]) -> dict[str, list[dict]]:
    """Split eval cases into pack buckets."""
    result: dict[str, list[dict]] = {pack: [] for pack in PACK_ORDER}
    for row in rows:
        result[classify_eval(row)].append(dict(row))
    return result


def split_chunks(rows: Iterable[dict]) -> dict[str, list[dict]]:
    """Split markdown chunks into pack buckets."""
    result: dict[str, list[dict]] = {pack: [] for pack in PACK_ORDER}
    for row in rows:
        result[classify_chunk(row)].append(dict(row))
    return result


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )
    path.write_text(text, encoding="utf-8")


def _content_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def rewrite_guizhou_source_links(text: str) -> str:
    """Adapt pptx_extract links to the deeper knowledge-base/packs path."""
    return text.replace("](../../../backend/static/", "](../../../../../backend/static/")


def _copy_sources(pack: str) -> list[str]:
    raw_out = PACKS_ROOT / pack / "raw" / "markdown"
    raw_out.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    if pack == NATIONAL:
        names = NATIONAL_SOURCES
        source_root = PROJECT_ROOT / "knowledge-base" / "raw" / "markdown"
        for name in names:
            src = source_root / name
            if src.exists():
                shutil.copy2(src, raw_out / name)
                copied.append(f"raw/markdown/{name}")
    elif pack == GUIZHOU:
        src = PROJECT_ROOT / "knowledge-base" / "raw" / "pptx_extract" / "pages.md"
        if src.exists():
            (raw_out / "统计云系统培训课件.md").write_text(
                rewrite_guizhou_source_links(src.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            copied.append("raw/markdown/统计云系统培训课件.md")
        json_src = PROJECT_ROOT / "knowledge-base" / "raw" / "pptx_extract" / "pages.json"
        if json_src.exists():
            json_out = PACKS_ROOT / pack / "raw" / "统计云系统培训课件.json"
            shutil.copy2(json_src, json_out)
            copied.append("raw/统计云系统培训课件.json")
        adopted = (
            PROJECT_ROOT / "knowledge-base" / "raw" / "markdown"
            / "四川劳动力调查技能测试题题库2026.md"
        )
        if adopted.exists():
            shutil.copy2(adopted, raw_out / adopted.name)
            copied.append(f"raw/markdown/{adopted.name}")
    return copied


def build_manifest(
    pack: str,
    qas: list[dict],
    evals: list[dict],
    chunks: list[dict],
    sources: list[str],
) -> dict:
    kind = "national" if pack == NATIONAL else "provincial"
    province_code = {"guizhou": "520000"}.get(pack)
    qa_hash = _content_hash(qas)
    eval_hash = _content_hash(evals)
    manifest = {
        "schema_version": 1,
        "kb_id": pack,
        "name": {
            NATIONAL: "全国基础知识包",
            GUIZHOU: "贵州知识包",
        }[pack],
        "kind": kind,
        "status": "draft",
        "province_codes": [province_code] if province_code else [],
        "entry_count": len(qas),
        "eval_count": len(evals),
        "chunk_count": len(chunks),
        "sources": sources,
        "qa_sha256": qa_hash,
        "eval_sha256": eval_hash,
        "rule_key_count": sum(1 for qa in qas if qa.get("rule_key")),
        "binding_status": "always_active" if pack == NATIONAL else "planned",
        "eval_status": "pending",
    }
    if pack == GUIZHOU:
        manifest["content_scope"] = "guizhou"
        manifest["adopted_sources"] = [ADOPTED_SICHUAN_SOURCE_MARKER]
        manifest["superseded_draft_packs"] = ["sichuan"]
        manifest["rule_key_review"] = {
            "status": "pending_national_match_review",
            "note": "地方规则键已分配；全国同键条目需业务复核后再启用确定性覆盖。",
        }
    return manifest


def write_report() -> Path:
    report_path = PROJECT_ROOT / "reports" / "kb-pack-split-20261015.md"
    lines = [
        "# 知识包 Phase 0 拆分报告",
        "",
        "原文件保留，本报告只记录新生成的知识包副本。",
        "",
        "| 知识包 | QA | Eval | Chunks | 绑定 | Eval 状态 |",
        "|---|---:|---:|---:|---|---|",
    ]
    for pack in PACK_ORDER:
        manifest = json.loads(
            (PACKS_ROOT / pack / "manifest.json").read_text(encoding="utf-8")
        )
        lines.append(
            f"| {manifest['name']} | {manifest['entry_count']} | "
            f"{manifest['eval_count']} | {manifest['chunk_count']} | "
            f"{manifest['binding_status']} | {manifest['eval_status']} |"
        )
    lines.extend(
        [
            "",
            "## 分类依据",
            "",
            "- `四川劳动力调查技能测试题题库2026` → 贵州知识包；这是贵州第一版 KB 已采纳的参考资料，保留原始来源，不建立四川适用包。",
            "- `贵阳调查队填报规范指引`、`统计云系统培训课件` → 贵州知识包。",
            "- 其余国家制度、指标讲解和专业题库 → 全国基础知识包。",
            "- `eval-108` 仍归全国包，其期望来源是国家制度原文。",
            "- 贵州地方条目已分配稳定 `rule_key`；全国同键条目尚未启用，待业务复核后才开始确定性覆盖。",
            "",
            "## 待复核",
            "",
            "- 是否需要为 `rule_key` 指定全国同键条目，尤其是 F27 非按月收入、包工头挂靠等待项目。",
            "- 统计云系统培训课件是否应保留整包贵州归属，还是拆出全国通用系统操作页面。",
            "- 早期生成的 `knowledge-base/packs/sichuan/` 来源桶草稿已确认删除。",
            "",
        ]
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def build() -> dict[str, dict]:
    """Create pack directories and return the generated manifests."""
    qas = _load_json(SOURCE_FAQ)
    evals = _load_json(SOURCE_EVAL)
    chunks = _load_jsonl(SOURCE_CHUNKS)
    split_qas = split_entries(qas)
    evals_by_pack = split_evals(evals)
    chunks_by_pack = split_chunks(chunks)

    if sum(len(rows) for rows in split_qas.values()) != len(qas):
        raise ValueError("QA 拆分条数不守恒")
    if sum(len(rows) for rows in evals_by_pack.values()) != len(evals):
        raise ValueError("Eval 拆分条数不守恒")
    if sum(len(rows) for rows in chunks_by_pack.values()) != len(chunks):
        raise ValueError("Chunk 拆分条数不守恒")

    SHARED_ROOT.mkdir(parents=True, exist_ok=True)
    shared_catalog = PROJECT_ROOT / "knowledge-base" / "indicator_catalog.json"
    if shared_catalog.exists():
        shutil.copy2(shared_catalog, SHARED_ROOT / "indicator_catalog.json")

    manifests: dict[str, dict] = {}
    for pack in PACK_ORDER:
        pack_root = PACKS_ROOT / pack
        pack_root.mkdir(parents=True, exist_ok=True)
        _write_json(pack_root / "qa" / "faq.json", split_qas[pack])
        _write_json(pack_root / "eval" / "eval_set.json", evals_by_pack[pack])
        _write_jsonl(pack_root / "chunks.jsonl", chunks_by_pack[pack])
        sources = _copy_sources(pack)
        manifest = build_manifest(
            pack, split_qas[pack], evals_by_pack[pack], chunks_by_pack[pack], sources
        )
        _write_json(pack_root / "manifest.json", manifest)
        manifests[pack] = manifest

    write_report()
    return manifests


def main() -> None:
    parser = argparse.ArgumentParser(description="拆分全国 / 省级知识包")
    parser.add_argument("--dry-run", action="store_true", help="只输出统计，不写文件")
    args = parser.parse_args()

    if args.dry_run:
        qas = _load_json(SOURCE_FAQ)
        evals = _load_json(SOURCE_EVAL)
        chunks = _load_jsonl(SOURCE_CHUNKS)
        for pack, rows in split_entries(qas).items():
            print(f"QA {pack}: {len(rows)}")
        for pack, rows in split_evals(evals).items():
            print(f"Eval {pack}: {len(rows)}")
        for pack, rows in split_chunks(chunks).items():
            print(f"Chunk {pack}: {len(rows)}")
        return

    manifests = build()
    for pack in PACK_ORDER:
        item = manifests[pack]
        print(
            f"{item['name']}: QA={item['entry_count']} "
            f"Eval={item['eval_count']} Chunk={item['chunk_count']} "
            f"status={item['status']}"
        )


if __name__ == "__main__":
    main()