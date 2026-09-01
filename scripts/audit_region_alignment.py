# -*- coding: utf-8 -*-
"""Audit whitelist region data against standard survey points.

Reads whitelist.db and region_points.json, classifies each entry by match
quality, and writes a markdown report with alignment recommendations.
"""
from __future__ import annotations

import difflib
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "backend" / "data" / "whitelist.db"
POINTS_PATH = PROJECT_ROOT / "backend" / "data" / "region_points.json"
REPORT_PATH = PROJECT_ROOT / "reports" / "region-alignment-audit.md"


def load_points() -> list[dict]:
    return json.loads(POINTS_PATH.read_text(encoding="utf-8"))


def load_whitelist() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT phone, name, province, city, county, township, community,"
        " admin_level, sys_role, active FROM whitelist ORDER BY phone"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def classify(entry: dict, points: list[dict]) -> dict:
    """Classify one whitelist entry against the standard catalog."""
    admin_level = entry["admin_level"]
    province, city = entry["province"], entry["city"]
    county, township, community = entry["county"], entry["township"], entry["community"]

    if admin_level == "省级":
        return {"match": "ok", "detail": "省级固定贵州省"}

    if admin_level == "市级":
        std_cities = {p["city"] for p in points if p["province"] == province}
        if city in std_cities:
            return {"match": "ok", "detail": "市级匹配标准市"}
        return {"match": "city_mismatch", "detail": f"市 '{city}' 不在标准数据中"}

    if admin_level == "区县":
        std_counties = {
            p["county"] for p in points
            if p["province"] == province and p["city"] == city
        }
        if county in std_counties:
            return {"match": "ok", "detail": "区县匹配标准县"}
        return {"match": "county_mismatch", "detail": f"县 '{county}' 不在标准数据中"}

    # admin_level == "调查员" — full 4-level match required
    exact = [
        p for p in points
        if p["city"] == city and p["county"] == county
        and p["township"] == township and p["community"] == community
    ]
    if exact:
        return {"match": "exact", "detail": "四级完全匹配"}

    # Try county+community match (township may differ or be empty)
    cc_match = [
        p for p in points
        if p["city"] == city and p["county"] == county and p["community"] == community
    ]
    if cc_match:
        std_townships = sorted({p["township"] for p in cc_match})
        return {
            "match": "township_mismatch",
            "detail": f"县+社区匹配，但乡镇不同/为空。标准乡镇: {'; '.join(std_townships)}",
            "candidates": cc_match,
        }

    # Try fuzzy community match within same county
    same_county = [
        p for p in points
        if p["city"] == city and p["county"] == county
    ]
    if same_county:
        std_communities = sorted({p["community"] for p in same_county})
        close = difflib.get_close_matches(community, std_communities, n=3, cutoff=0.4)
        if close:
            candidates = [
                p for p in same_county if p["community"] in close
            ]
            return {
                "match": "community_fuzzy",
                "detail": f"同县内模糊匹配到: {', '.join(close)}",
                "candidates": candidates,
            }

    # Try exact community match in same city (different county)
    same_city = [
        p for p in points
        if p["city"] == city and p["community"] == community
    ]
    if same_city:
        other_counties = sorted({p["county"] for p in same_city if p["county"] != county})
        return {
            "match": "community_other_county",
            "detail": f"社区名在同市其他县存在: {', '.join(other_counties)}",
            "candidates": same_city,
        }

    # Fuzzy across whole city
    all_city = [p for p in points if p["city"] == city]
    std_all = sorted({p["community"] for p in all_city})
    close = difflib.get_close_matches(community, std_all, n=3, cutoff=0.4)
    if close:
        candidates = [p for p in all_city if p["community"] in close]
        return {
            "match": "community_fuzzy_city",
            "detail": f"同市内模糊匹配到: {', '.join(close)}",
            "candidates": candidates,
        }

    return {"match": "no_match", "detail": "标准数据中找不到任何近似匹配"}


def format_candidates(candidates: list[dict]) -> str:
    if not candidates:
        return ""
    parts = []
    for c in candidates[:5]:
        parts.append(f"{c['city']}·{c['county']}·{c['township']}·{c['community']}")
    if len(candidates) > 5:
        parts.append(f"...共 {len(candidates)} 条")
    return " → ".join(parts)


def main() -> None:
    points = load_points()
    entries = load_whitelist()

    results = []
    for entry in entries:
        r = classify(entry, points)
        r["entry"] = entry
        results.append(r)

    by_match = defaultdict(list)
    for r in results:
        by_match[r["match"]].append(r)

    total = len(entries)
    active = sum(1 for e in entries if e["active"])
    inactive = total - active

    lines = []
    lines.append("# 白名单区域数据与标准调查点对齐审计报告\n")
    lines.append(f"- 总条目: **{total}**（启用 {active} / 停用 {inactive}）")
    lines.append(f"- 标准调查点: **{len(points)}** 条\n")

    order = ["exact", "ok", "township_mismatch", "community_fuzzy",
             "community_other_county", "community_fuzzy_city", "no_match",
             "city_mismatch", "county_mismatch"]
    labels = {
        "exact": "调查员·四级完全匹配",
        "ok": "管理员·区域匹配",
        "township_mismatch": "调查员·乡镇不匹配",
        "community_fuzzy": "调查员·同县模糊匹配",
        "community_other_county": "调查员·社区在同市其他县",
        "community_fuzzy_city": "调查员·同市模糊匹配",
        "no_match": "调查员·完全无匹配",
        "city_mismatch": "市级管理员·市不匹配",
        "county_mismatch": "区县管理员·县不匹配",
    }
    actions = {
        "exact": "无需处理",
        "ok": "无需处理",
        "township_mismatch": "确认正确乡镇后更新 township 字段",
        "community_fuzzy": "人工确认社区名后更新全部四级",
        "community_other_county": "确认正确县后更新 county+township+community",
        "community_fuzzy_city": "人工确认社区名后更新全部四级",
        "no_match": "需人工对照源 Excel 确认或补录标准数据",
        "city_mismatch": "需确认市名并更新",
        "county_mismatch": "需确认县名并更新",
    }

    lines.append("## 匹配情况汇总\n")
    lines.append("| 分类 | 数量 | 建议动作 |")
    lines.append("|------|------|----------|")
    for key in order:
        if key in by_match:
            lines.append(f"| {labels[key]} | {len(by_match[key])} | {actions[key]} |")
    lines.append("")

    problem_keys = [k for k in order if k not in ("exact", "ok") and k in by_match]
    for key in problem_keys:
        items = by_match[key]
        lines.append(f"## {labels[key]}（{len(items)} 条）\n")
        lines.append("| 手机号 | 姓名 | 市 | 县 | 乡镇 | 社区 | 诊断 | 建议匹配 |")
        lines.append("|--------|------|-----|-----|------|------|------|----------|")
        for r in items:
            e = r["entry"]
            status = "停用" if not e["active"] else ""
            cands = format_candidates(r.get("candidates", []))
            lines.append(
                f"| {e['phone']}{'(停)' if status else ''} | {e['name']}"
                f"| {e['city']} | {e['county']} | {e['township'] or '—'}"
                f"| {e['community'] or '—'} | {r['detail']} | {cands} |"
            )
        lines.append("")

    report = "\n".join(lines)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"report written to {REPORT_PATH}")
    print(f"total={total} exact={len(by_match['exact'])} admin_ok={len(by_match['ok'])}")
    for key in problem_keys:
        print(f"  {labels[key]}: {len(by_match[key])}")


if __name__ == "__main__":
    main()
