#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识包漂移检测：比对 knowledge-base/packs 源内容 vs 注册库已发布版本。

输出稳定（无时间戳）：无变化时输出恒定，供 Hermes cron monitor 做哈希比对；
有漂移时输出改变 -> 触发 agent 执行发布链路（skill: labor-survey-kb-release）。

状态文件 ~/.hermes/kb_pack_state.json 记录「上次走完发布链路时」每个包的
源内容哈希与对应 release。链路收尾用 --record 写入。

用法:
    kb_pack_drift.py           检测并打印状态（stdout，exit 0）
    kb_pack_drift.py --record <pack_id> <release_id>   链路完成后记录基线
    kb_pack_drift.py --init    以当前状态建立基线（仅首次）
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

# 仓库根：生产机默认 /opt/labor-survey-ai，其它机器用 LSX_REPO_ROOT 覆盖
REPO = Path(os.environ.get("LSX_REPO_ROOT", "/opt/labor-survey-ai"))
PACKS = REPO / "knowledge-base" / "packs"
REGISTRY = REPO / "backend" / "data" / "knowledge_admin.db"
STATE = Path.home() / ".hermes" / "kb_pack_state.json"


def pack_source_hash(pack_dir: Path) -> str:
    """包目录内容哈希：排序遍历所有文件，逐文件 sha256 再汇总。"""
    h = hashlib.sha256()
    files = sorted(p for p in pack_dir.rglob("*") if p.is_file())
    for p in files:
        h.update(str(p.relative_to(pack_dir)).encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def load_state() -> dict:
    if not STATE.exists():
        return {}
    return json.loads(STATE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_registry() -> tuple[dict, dict, dict]:
    """返回 (packages, releases, bindings)；注册库不存在时返回空结构。"""
    if not REGISTRY.exists():
        return {}, {}, {}
    conn = sqlite3.connect(f"file:{REGISTRY}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        packages = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, kind, status, current_release_id FROM kb_packages")}
        releases = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, package_id, build_status, eval_status, published_at "
            "FROM kb_releases")}
        bindings = {r["province_code"]: r["package_id"] for r in
                    conn.execute("SELECT province_code, package_id "
                                 "FROM province_kb_bindings")}
    finally:
        conn.close()
    return packages, releases, bindings


def detect() -> list[str]:
    state = load_state()
    packages, releases, _bindings = load_registry()
    lines: list[str] = []

    pack_dirs = sorted(d for d in PACKS.iterdir() if d.is_dir()) \
        if PACKS.exists() else []
    pack_ids = {d.name for d in pack_dirs}

    for d in pack_dirs:
        cur_hash = pack_source_hash(d)
        st = state.get(d.name)
        if st is None:
            lines.append(
                f"NEW {d.name}: 内容源未纳入基线（首次接入需建包+发布+绑省）")
            continue
        if st.get("source_hash") == cur_hash:
            rel = st.get("release")
            pkg = packages.get(d.name)
            rel_row = releases.get(rel or "")
            if (pkg and rel_row
                    and pkg.get("current_release_id") == rel
                    and rel_row.get("published_at")):
                lines.append(f"OK {d.name} {rel}")
            else:
                lines.append(
                    f"CHECK {d.name}: 基线 release={rel} 与注册库状态不一致"
                    f"（package={pkg and pkg.get('current_release_id')}"
                    f" published={bool(rel_row and rel_row.get('published_at'))}）")
        else:
            lines.append(
                f"DRIFT {d.name}: 内容已变更，尚未构建/发布"
                f"（上次发布 {st.get('release')}）")

    # 注册库里存在但磁盘没有内容源的包 / 构建了没发布的版本（链路半途失败）
    for pid in sorted(packages):
        if pid not in pack_ids:
            lines.append(f"ORPHAN {pid}: 注册库有包但 packs 目录缺失")
    for rid in sorted(releases):
        r = releases[rid]
        if not r.get("published_at"):
            st_rel = state.get(r.get("package_id"), {}).get("release")
            if rid != st_rel:
                lines.append(
                    f"PENDING {r.get('package_id')} {rid}: 已构建未发布"
                    f"（build={r.get('build_status')} eval={r.get('eval_status')}）")
    return sorted(lines)


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--record":
        if len(args) != 3:
            print("用法: --record <pack_id> <release_id>", file=sys.stderr)
            return 2
        pack_id, release_id = args[1], args[2]
        pack_dir = PACKS / pack_id
        if not pack_dir.is_dir():
            print(f"包目录不存在: {pack_dir}", file=sys.stderr)
            return 2
        state = load_state()
        state[pack_id] = {
            "source_hash": pack_source_hash(pack_dir),
            "release": release_id,
        }
        save_state(state)
        print(f"recorded {pack_id} -> {release_id}")
        return 0

    if args and args[0] == "--init":
        state = load_state()
        pack_dirs = sorted(d for d in PACKS.iterdir() if d.is_dir())
        for d in pack_dirs:
            if d.name not in state:
                state[d.name] = {"source_hash": pack_source_hash(d),
                                 "release": None}
        save_state(state)
        print("baseline initialized")
        return 0

    for line in detect():
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
