#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch-fix whitelist region data to align with standard survey points.

Steps:
1. Re-tag 新蒲新区 / 贵安新区 in region_points.json.
2. Back up whitelist.db.
3. Fix whitelist entries:
   - Hard-delete known test junk.
   - Rename incomplete county names (道真县 → full name).
   - Fill missing township via exact county+community match.
   - Fix community suffix differences via normalized match.
4. Report entries that still need manual attention.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "backend" / "data" / "whitelist.db"
POINTS_PATH = PROJECT_ROOT / "backend" / "data" / "region_points.json"
BACKUP_DIR = PROJECT_ROOT / "backend" / "data" / "backups"

COUNTY_FIXES = {
    "道真县": "道真仡佬族苗族自治县",
}

DELETE_PHONES = {"13900009999"}

NORMALIZE_SUFFIXES = [
    "调查点",
    "社区居民委员会",
    "村民委员会",
    "居民委员会",
    "村委会",
    "居委会",
    "委员会",
    "社区",
    "村",
]

COUNTY_OVERRIDES = {
    ("遵义市", "三渡镇"): "新蒲新区",
    ("遵义市", "喇叭镇"): "新蒲新区",
    ("遵义市", "新中街道"): "新蒲新区",
    ("遵义市", "新舟镇"): "新蒲新区",
    ("遵义市", "新蒲街道"): "新蒲新区",
    ("遵义市", "礼仪街道"): "新蒲新区",
    ("贵阳市", "党武街道"): "贵安新区",
}


def normalize_name(name: str) -> str:
    """Strip administrative suffixes to produce a comparable base name."""
    name = name.strip()
    changed = True
    while changed:
        changed = False
        for suffix in NORMALIZE_SUFFIXES:
            if name.endswith(suffix) and len(name) > len(suffix):
                name = name[: -len(suffix)]
                changed = True
                break
    return name


def apply_county_overrides_to_json() -> int:
    points = json.loads(POINTS_PATH.read_text(encoding="utf-8"))
    changed = 0
    for point in points:
        key = (point["city"], point["township"])
        target = COUNTY_OVERRIDES.get(key)
        if target and point["county"] != target:
            point["county"] = target
            changed += 1
    if changed:
        POINTS_PATH.write_text(
            json.dumps(points, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return changed


def backup_db() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"whitelist_backup_{stamp}.db"
    shutil.copy2(DB_PATH, backup)
    return backup


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    json_changes = apply_county_overrides_to_json()
    print(f"[JSON] re-tagged {json_changes} points for 新蒲新区/贵安新区")

    backup = backup_db()
    print(f"[DB] backup: {backup}")

    points = json.loads(POINTS_PATH.read_text(encoding="utf-8"))
    exact_four = {
        (p["city"], p["county"], p["township"], p["community"]): p
        for p in points
    }
    by_cc: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    by_norm: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for point in points:
        by_cc[(point["city"], point["county"], point["community"])].append(point)
        by_norm[
            (point["city"], point["county"], normalize_name(point["community"]))
        ].append(point)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT phone, name, province, city, county, township, community,"
        " admin_level, sys_role, active FROM whitelist ORDER BY phone"
    ).fetchall()

    deleted = 0
    county_fixed = 0
    township_filled = 0
    community_fixed = 0
    manual: list[dict] = []

    for row in rows:
        phone = row["phone"]

        if phone in DELETE_PHONES:
            conn.execute("DELETE FROM whitelist WHERE phone = ?", (phone,))
            deleted += 1
            continue

        county = row["county"]
        if county in COUNTY_FIXES:
            new_county = COUNTY_FIXES[county]
            conn.execute(
                "UPDATE whitelist SET county = ? WHERE phone = ?",
                (new_county, phone),
            )
            county = new_county
            county_fixed += 1

        if row["admin_level"] != "调查员":
            continue

        city = row["city"]
        community = row["community"]
        township = row["township"] or ""

        if township and (city, county, township, community) in exact_four:
            continue

        cc_matches = by_cc.get((city, county, community), [])
        if len(cc_matches) == 1:
            std = cc_matches[0]
            conn.execute(
                "UPDATE whitelist SET township = ? WHERE phone = ?",
                (std["township"], phone),
            )
            township_filled += 1
            continue

        norm_key = (city, county, normalize_name(community))
        norm_matches = by_norm.get(norm_key, [])
        if len(norm_matches) == 1:
            std = norm_matches[0]
            conn.execute(
                "UPDATE whitelist SET community = ?, township = ? WHERE phone = ?",
                (std["community"], std["township"], phone),
            )
            community_fixed += 1
            township_filled += 1
            continue

        manual.append({
            "phone": phone,
            "name": row["name"],
            "city": city,
            "county": county,
            "township": township,
            "community": community,
            "active": row["active"],
        })

    conn.commit()
    conn.close()

    print(f"[DB] deleted: {deleted}")
    print(f"[DB] county renamed: {county_fixed}")
    print(f"[DB] community fixed: {community_fixed}")
    print(f"[DB] township filled: {township_filled}")
    print(f"[DB] manual attention: {len(manual)}")
    for item in manual:
        tag = "(停用)" if not item["active"] else ""
        print(
            f"  {item['phone']}{tag} {item['name']}"
            f" | {item['city']} / {item['county']} / {item['community']}"
        )


if __name__ == "__main__":
    main()
