#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从权限表 xlsx 批量导入白名单（灾难恢复 / 初始导入）。

用法：
    backend/venv/bin/python scripts/import_whitelist_from_xlsx.py \
        --xlsx <权限表.xlsx> --csv-out /tmp/fixlist.csv --report-out /tmp/report.md
    # 默认 dry-run；确认无误后加 --apply 才写库

设计：
- 只走 whitelist_db.upsert()（自动派生 sys_role），不预填 sys_role
- 调查员行按 (县, 社区) 从 region_points.json 回填街道（exact/fuzzy/stem/difflib）
- 每行先 validate_account_scope + validate_region_selection，失败进报告不写库
- 写库前停服务 + 备份 whitelist.db（含 -wal/-shm）
- 每行写 whitelist_audit（create/update）
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import openpyxl  # noqa: E402

from app.persistence import whitelist_db  # noqa: E402
from app.services.region_points import (  # noqa: E402
    load_region_points,
    validate_account_scope,
    validate_region_selection,
)

DB = PROJECT_ROOT / "backend" / "data" / "whitelist.db"
BACKUP_DIR = PROJECT_ROOT / "backend" / "data" / "backups"
NOW = lambda: datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")

PROTECTED_PHONES = {"13985000001", "13985000002", "13985000003", "13985000004"}

# 地区名简称 -> 标准全称（excel 写简称，标准库写全称）
CITY_ALIASES = {
    "黔东南州": "黔东南苗族侗族自治州",
    "黔南州": "黔南布依族苗族自治州",
    "黔西南州": "黔西南布依族苗族自治州",
}
# 已知手机号笔误（用户 2026-09-03 确认），key=xlsx 原始值
PHONE_FIX = {
    "159851323383": "15985132383",   # 胡贤（毕节·威宁）多打一位
}

# 简称 -> 全称 的模糊规则（保留 社区/村 两类，避免跨类碰撞）
LONG_BY_KIND = {"社区": "社区居民委员会", "村": "村村民委员会"}
SHORT_SUFFIXES = (
    "社区居委会", "社区居委员", "社区委员会", "社区委",
    "村村委会", "村村委员", "村民委会", "村民委", "村委会", "村委",
)


def norm_phone(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
    except (ValueError, TypeError):
        pass
    return s


def norm_text(v) -> str:
    if v is None:
        return ""
    return " ".join(str(v).split())


def kind_of(community: str) -> str | None:
    if not community:
        return None
    # 先判村（含"村"即为村类），其余居委会/社区类归 社区
    if "村" in community:
        return "村"
    if "社区" in community or "居委会" in community or "居民委员会" in community \
            or community.endswith("委会"):
        return "社区"
    return None


def fuzzy_candidates(community: str) -> list[str]:
    """简称/漏字 -> 标准全称候选。"""
    out: list[str] = []
    k = kind_of(community)
    if k:
        # 去掉任意长度的尾部片段后补全称
        for sfx in SHORT_SUFFIXES:
            if community.endswith(sfx) and len(community) > len(sfx):
                out.append(community[: -len(sfx)] + LONG_BY_KIND[k])
                break
        # 兜底：把尾部的 居委会/村委会/委员会/委会/社区/村 换成全称后缀
        for tail in ("居委会", "村委会", "委员会", "委会", "社区", "村"):
            if community.endswith(tail) and len(community) > len(tail):
                out.append(community[: -len(tail)] + LONG_BY_KIND[k])
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


class Backfiller:
    def __init__(self, points):
        self.points = points
        self.exact = {(p["county"], p["community"]): p for p in points}
        self.by_county: dict[str, list[dict]] = defaultdict(list)
        self.by_cc: dict[tuple, list[dict]] = defaultdict(list)
        for p in points:
            self.by_county[p["county"]].append(p)
            self.by_cc[(p["county"], p["community"])].append(p)

    def resolve(self, county: str, community: str):
        """返回 (township, community_std, status)。"""
        if not community:
            return None, None, "empty"
        row = self.exact.get((county, community))
        if row:
            return row["township"], row["community"], "exact"
        # 模糊：简称 -> 全称
        for cand in fuzzy_candidates(community):
            row = self.exact.get((county, cand))
            if row:
                return row["township"], row["community"], "fuzzy"
        # 同县同类别里做闭包匹配
        k = kind_of(community)
        pool = [p for p in self.by_county.get(county, []) if kind_of(p["community"]) == k]
        names = [p["community"] for p in pool]
        for m in difflib.get_close_matches(community, names, n=3, cutoff=0.6):
            row = self.exact.get((county, m))
            if row:
                return row["township"], row["community"], "difflib"
        matches = self.by_cc.get((county, community), [])
        if len(matches) > 1:
            return None, None, "ambiguous"
        return None, None, "not_found"


def read_rows(xlsx: Path):
    """解析两个 sheet，返回 (rows, skipped_raw)。"""
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    rows: list[dict] = []
    skipped: list[str] = []

    # --- 调查员：省 市 县 调查小区 姓名 联系电话 管理员层级 备注 ---
    if "调查员" in wb.sheetnames:
        ws = wb["调查员"]
        for i, r in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
            if not r or all(c is None or str(c).strip() == "" for c in r):
                continue
            phone = norm_phone(r[5] if len(r) > 5 else None)
            name = norm_text(r[4] if len(r) > 4 else None)
            if not phone and not name:
                continue
            rows.append({
                "sheet": "调查员", "row": i,
                "province": norm_text(r[0]), "city": norm_text(r[1]), "county": norm_text(r[2]),
                "community_raw": norm_text(r[3]), "name": name, "phone": phone,
                "admin_level": norm_text(r[6] if len(r) > 6 else "") or "调查员",
                "remark": norm_text(r[7] if len(r) > 7 else ""),
            })

    # --- 管理人员：省 市 县 姓名 联系电话 管理员层级 备注 ---
    if "管理人员" in wb.sheetnames:
        ws = wb["管理人员"]
        for i, r in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
            if not r or all(c is None or str(c).strip() == "" for c in r):
                continue
            phone = norm_phone(r[4] if len(r) > 4 else None)
            name = norm_text(r[3] if len(r) > 3 else None)
            if not phone and not name:
                continue
            rows.append({
                "sheet": "管理人员", "row": i,
                "province": norm_text(r[0]), "city": norm_text(r[1]), "county": norm_text(r[2]),
                "community_raw": "", "name": name, "phone": phone,
                "admin_level": norm_text(r[5] if len(r) > 5 else ""),
                "remark": norm_text(r[6] if len(r) > 6 else ""),
            })
    wb.close()
    return rows, skipped


def mask_phone(p: str) -> str:
    return p[:3] + "****" + p[-4:] if len(p) >= 7 else p[:3] + "****"


def main() -> int:
    ap = argparse.ArgumentParser(description="权限表 xlsx 批量导入白名单")
    ap.add_argument("--xlsx", type=Path, required=True)
    ap.add_argument("--csv-out", type=Path, default=None)
    ap.add_argument("--report-out", type=Path, default=None)
    ap.add_argument("--actor", default=None, help="审计 actor 手机号（默认读 .env LSX_SYSTEM_ADMIN_PHONE）")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    ap.add_argument("--allow-skip", action="store_true", help="有无法匹配的行时也继续写库（跳过该行）")
    args = ap.parse_args()

    actor = args.actor
    if not actor:
        envp = PROJECT_ROOT / ".env"
        if envp.exists():
            for line in envp.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("LSX_SYSTEM_ADMIN_PHONE"):
                    actor = line.split("=", 1)[1].strip()
                    break
    actor = actor or "unknown"

    points = load_region_points()
    bf = Backfiller(points)
    rows, _ = read_rows(args.xlsx)
    print(f"解析 {args.xlsx.name}: {len(rows)} 行")

    catalog_cities = {p["city"] for p in points}
    catalog_counties: dict[tuple, set] = defaultdict(set)
    for p in points:
        catalog_counties[(p["province"], p["city"])].add(p["county"])

    def norm_city(city: str) -> str:
        if not city or city in catalog_cities:
            return city
        if city in CITY_ALIASES:
            return CITY_ALIASES[city]
        stem = city.rstrip("州市")
        for c in catalog_cities:
            if c.startswith(stem):
                return c
        m = difflib.get_close_matches(city, list(catalog_cities), n=1, cutoff=0.5)
        return m[0] if m else city

    def norm_county(province: str, city: str, county: str) -> str:
        if not county:
            return county
        pool = catalog_counties.get((province, city), set())
        if county in pool:
            return county
        stem = county.rstrip("县市区")
        for c in pool:
            if c.startswith(stem):
                return c
        m = difflib.get_close_matches(county, list(pool), n=1, cutoff=0.5)
        return m[0] if m else county

    ok, failed, report = [], [], []
    stat = defaultdict(int)
    for r in rows:
        problems = []
        phone = PHONE_FIX.get(r["phone"], r["phone"])
        r["city"] = norm_city(r["city"])
        r["county"] = norm_county(r["province"], r["city"], r["county"])
        if not phone or not phone.isdigit() or len(phone) != 11:
            problems.append(f"手机号异常({r['phone']!r})")
        admin_level = r["admin_level"] or "调查员"

        if r["sheet"] == "调查员" or admin_level == "调查员":
            admin_level = "调查员"
            township, community_std, status = bf.resolve(r["county"], r["community_raw"])
            stat[status] += 1
            if status in ("not_found", "ambiguous", "empty"):
                problems.append(f"社区无法匹配({r['community_raw']!r} → {status})")
            r["township"], r["community"] = (township or ""), (community_std or r["community_raw"])
        else:
            r["township"], r["community"] = "", ""

        r["admin_level"] = admin_level

        if not problems:
            try:
                validate_region_selection(
                    points, admin_level=admin_level,
                    province=r["province"], city=r["city"], county=r["county"],
                    township=r["township"], community=r["community"],
                )
            except ValueError as e:
                problems.append(f"区域校验: {e}")
        if not problems and phone in PROTECTED_PHONES:
            problems.append("保护测试号")

        rec = {
            "phone": phone, "name": r["name"], "province": r["province"], "city": r["city"],
            "county": r["county"], "township": r["township"], "community": r["community"],
            "admin_level": admin_level, "remark": r["remark"] or f"灾后重建导入({args.xlsx.stem})",
        }
        if problems:
            failed.append((r, problems))
            report.append(f"[SKIP] {r['sheet']} r{r['row']} {phone} {r['name']}: {'; '.join(problems)}")
        else:
            ok.append(rec)

    print(f"  可导入 {len(ok)} 行，跳过 {len(failed)} 行")
    print(f"  社区匹配分布: {dict(stat)}")
    if failed:
        print("\n--- 跳过明细 ---")
        for line in report:
            print("  " + line)

    # 写 CSV 修正清单
    if args.csv_out and failed:
        with open(args.csv_out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["sheet", "row", "省", "市", "县", "调查小区", "姓名", "电话", "层级", "问题"])
            for r, probs in failed:
                w.writerow([r["sheet"], r["row"], r["province"], r["city"], r["county"],
                            r["community_raw"], r["name"], r["phone"], r["admin_level"], "; ".join(probs)])
        print(f"\n修正清单: {args.csv_out}")

    if args.report_out:
        args.report_out.write_text("\n".join(report) or "(无跳过)", encoding="utf-8")
        print(f"报告: {args.report_out}")

    if not args.apply:
        print("\n=== DRY-RUN（未写库）。确认无误加 --apply ===")
        return 0 if not failed else 1

    # ---- 写库 ----
    if failed and not args.allow_skip:
        print("\n有跳过行，拒绝 --apply。请先修 xlsx，或加 --allow-skip 跳过这些行。")
        return 1
    if failed:
        print(f"\n⚠️ --allow-skip：将跳过 {len(failed)} 行，只导入 {len(ok)} 行")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bkp = BACKUP_DIR / f"whitelist-before-import-{ts}.db"
    shutil.copy2(DB, bkp)
    for s in ("-wal", "-shm"):
        if (DB.parent / (DB.name + s)).exists():
            shutil.copy2(str(DB.parent / (DB.name + s)), str(bkp) + s)
    print(f"备份: {bkp}")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    created = updated = 0
    for rec in ok:
        before = whitelist_db.get_user_any(rec["phone"])
        action = whitelist_db.upsert(rec)
        after = whitelist_db.get_user_any(rec["phone"])
        whitelist_db.log_audit(
            actor_phone=actor, actor_name="系统管理员",
            action=("update" if before else "create"),
            target_phone=rec["phone"], before=before, after=after,
        )
        if before:
            updated += 1
        else:
            created += 1
    conn.close()
    print(f"完成: 新增 {created}，更新 {updated}，共 {len(ok)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
