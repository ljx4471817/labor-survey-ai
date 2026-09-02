"""把现有 whitelist.json → whitelist.db 迁移。

旧格式: {"phones": ["13800000000", ...], "_说明": "..."}
新格式: whitelist.db (phone, name, province, city, county, township, community)

旧数据没有 region 信息，默认填"贵州省/贵阳市/需手动更新"——迁移后到白名单管理 UI 补齐。
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WHITELIST_JSON = PROJECT_ROOT / "backend" / "data" / "whitelist.json"

sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.whitelist_db import DB_PATH, upsert, list_all  # noqa: E402


def migrate() -> None:
    if not WHITELIST_JSON.exists():
        print(f"[跳过] {WHITELIST_JSON} 不存在，无需迁移")
        return

    try:
        data = json.loads(WHITELIST_JSON.read_text("utf-8"))
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"[错误] 无法读取 {WHITELIST_JSON}: {e}")
        sys.exit(1)

    phones = data.get("phones", [])
    if not phones:
        print("[跳过] whitelist.json 中 phone 列表为空")
        return

    existing = {u["phone"] for u in list_all(active_only=False)}
    migrated = skipped = 0

    for phone in phones:
        if phone in existing:
            skipped += 1
            continue
        upsert({
            "phone": phone,
            "name": "未设置",
            "province": "贵州省",
            "city": "贵阳市",
            "county": "需手动更新",
            "township": "",
            "community": "需手动更新",
        })
        migrated += 1

    total = len(list_all(active_only=True))
    print(f"[完成] 新增 {migrated}，跳过 {skipped}（已存在）。白名单共 {total} 条 active。")
    print(f"DB 文件: {DB_PATH}")
    print()
    print("注意：迁移的条目 region 字段为默认值（需手动更新）。请到 /whitelist-admin 页补全。")


if __name__ == "__main__":
    migrate()
