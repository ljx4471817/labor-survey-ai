#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build one immutable knowledge-release index directory."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RELEASES_ROOT = PROJECT_ROOT / "backend" / "data" / "kb_releases"
DEFAULT_COLLECTION = "labor_survey_qa"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.rag import index_paths  # noqa: E402
from app.services.release_artifacts import (  # noqa: E402
    build_artifact_manifest,
    manifest_content_hash,
    validate_release_dir,
    write_artifact_manifest,
)


def plan_release(
    release_id: str,
    pack_root: Path,
    releases_root: Path | None = None,
) -> dict:
    """Validate inputs and return the release artifact plan without building."""
    release_id = index_paths.validate_release_id(release_id)
    pack_root = Path(pack_root)
    if not pack_root.exists():
        raise FileNotFoundError(f"知识包目录不存在：{pack_root}")
    faq_path = pack_root / "qa" / "faq.json"
    if not faq_path.exists():
        raise FileNotFoundError(f"知识包 FAQ 不存在：{faq_path}")

    markdown_root = pack_root / "raw" / "markdown"
    markdown_files = sorted(markdown_root.glob("*.md")) if markdown_root.exists() else []
    chunks_path = pack_root / "chunks.jsonl"
    root = Path(releases_root) if releases_root else index_paths.RELEASES_ROOT
    release_dir = root / release_id
    return {
        "release_id": release_id,
        "pack_root": pack_root,
        "faq_path": faq_path,
        "markdown_files": markdown_files,
        "existing_chunks_path": chunks_path,
        "release_dir": release_dir,
        "chroma_dir": release_dir / "chroma",
        "bm25_path": release_dir / "bm25_index.json",
        "chunks_path": release_dir / "chunks.jsonl",
    }


def _load_pack_chunks(plan: dict, build_chunks) -> list[dict]:
    """Reuse the reviewed pack chunks when available; otherwise regenerate."""
    source = plan["existing_chunks_path"]
    if source.exists():
        rows = []
        for line in source.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        if rows:
            return rows

    chunks: list[dict] = []
    for markdown_file in plan["markdown_files"]:
        chunks.extend(build_chunks.make_chunks(markdown_file))
    return chunks


def build_release(
    release_id: str,
    pack_root: Path,
    *,
    releases_root: Path | None = None,
    collection: str = DEFAULT_COLLECTION,
    provider: str = "dashscope",
    with_chroma: bool = True,
    with_bm25: bool = True,
) -> dict:
    """Build isolated QA/chunk/BM25 artifacts for one release."""
    import build_bm25
    import build_chunks
    import build_kb

    plan = plan_release(release_id, pack_root, releases_root)
    if plan["release_dir"].exists() and any(plan["release_dir"].iterdir()):
        raise FileExistsError(
            f"release 目录已存在，禁止重建：{plan['release_dir']}"
        )
    plan["release_dir"].mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}

    chunks = _load_pack_chunks(plan, build_chunks)
    if chunks:
        build_chunks.save_chunks_jsonl(chunks, plan["chunks_path"], full=True)
    else:
        plan["chunks_path"].write_text("", encoding="utf-8")
    results["chunks"] = {"count": len(chunks)}

    if with_chroma:
        results["qa_chroma"] = build_kb.build(
            plan["faq_path"], plan["chroma_dir"], collection, provider,
            full=True, release_id=release_id,
        )
        results["chunk_chroma"] = build_chunks.upsert_chunks(
            chunks, plan["chroma_dir"], collection, provider,
            full=True, release_id=release_id,
        ) if chunks else {"count": 0}
        chroma_failures = sum(
            int(results[name].get("failed") or 0)
            for name in ("qa_chroma", "chunk_chroma")
        )
        if chroma_failures:
            raise RuntimeError(f"Chroma 构建失败：{chroma_failures} 条")
    if with_bm25:
        results["bm25"] = build_bm25.build(
            plan["faq_path"], plan["chunks_path"], plan["bm25_path"], force=True
        )
    if with_chroma and with_bm25:
        manifest_path = write_artifact_manifest(
            plan["release_dir"], release_id=release_id
        )
        validate_release_dir(plan["release_dir"], release_id=release_id)
        results["artifact_manifest"] = {
            "path": str(manifest_path),
            "files": len(build_artifact_manifest(plan["release_dir"], release_id)["files"]),
        }
    elif with_chroma or with_bm25:
        # 部分构建永远缺 REQUIRED_FILES，提前写 manifest 只会在校验时白崩一场。
        results["artifact_manifest"] = {
            "status": "skipped",
            "reason": "部分构建（缺 chroma 或 bm25），不可发布；发布需完整构建",
        }
    return {"plan": plan, "results": results}


def register_release(
    plan: dict,
    *,
    actor: str,
    version: str | None = None,
    based_on: str | None = None,
) -> dict:
    """Register the freshly built release in the knowledge registry.

    content_hash 取 artifact manifest 的 canonical hash，发布时会被强绑定校验；
    eval_status 固定 pending，Eval 通过必须由人工/eval 流程更新，防止假 passed。
    """
    from app.persistence import db
    from app.persistence import knowledge_registry as registry

    pack_manifest = json.loads(
        (plan["pack_root"] / "manifest.json").read_text(encoding="utf-8")
    )
    package_id = str(pack_manifest["kb_id"])
    if pack_manifest.get("kind") == "provincial" and not based_on:
        raise ValueError("省级包版本必须用 --based-on 指定基于的全国版本")

    # 新增省份的包记录也在开发机链路创建（ADR 0028 职责分离：后台不再提供建包入口）
    existing_packages = {str(item["id"]) for item in registry.list_packages()}
    if package_id not in existing_packages:
        try:
            registry.create_package(
                package_id=package_id,
                name=str(pack_manifest.get("name") or package_id),
                kind=str(pack_manifest.get("kind") or "provincial"),
                created_by=actor,
            )
        except db.IntegrityError:
            pass  # 并发下包刚被创建，直接复用

    manifest_payload = json.loads(
        (plan["release_dir"] / "artifact_manifest.json").read_text(encoding="utf-8")
    )
    content_hash = manifest_content_hash(manifest_payload)
    artifact_path = f"data/kb_releases/{plan['release_id']}"
    try:
        release = registry.create_release(
            release_id=plan["release_id"],
            package_id=package_id,
            version=version or plan["release_id"],
            artifact_path=artifact_path,
            content_hash=content_hash,
            based_on_national_release_id=based_on,
            build_status="success",
            eval_status="pending",
            created_by=actor,
        )
        return {"registered": True, "package_id": package_id, "content_hash": content_hash, "release": release}
    except db.IntegrityError:
        try:
            existing = registry.get_release(plan["release_id"])
        except ValueError as exc:
            raise db.IntegrityError(
                f"同包版本号重复：{version or plan['release_id']}（换 version 或新 release-id）"
            ) from exc
        if existing.get("content_hash") == content_hash:
            return {
                "registered": False,
                "already_registered": True,
                "package_id": package_id,
                "content_hash": content_hash,
            }
        raise


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description="构建一个不可变知识包版本索引")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--pack-root", type=Path, required=True)
    parser.add_argument("--releases-root", type=Path, default=DEFAULT_RELEASES_ROOT)
    parser.add_argument(
        "--collection",
        default=os.environ.get("CHROMA_COLLECTION", DEFAULT_COLLECTION),
    )
    parser.add_argument(
        "--provider",
        default=os.environ.get("EMBEDDING_PROVIDER", "dashscope"),
    )
    parser.add_argument("--skip-chroma", action="store_true")
    parser.add_argument("--skip-bm25", action="store_true")
    parser.add_argument(
        "--register", action="store_true",
        help="构建成功后自动写入知识注册库（build=success, eval=pending）",
    )
    parser.add_argument("--actor", help="注册操作人手机号（--register 必填）")
    parser.add_argument("--version", help="注册版本号（缺省用 release-id）")
    parser.add_argument("--based-on", help="省级包基于的全国版本 ID（省级 --register 必填）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        plan = plan_release(args.release_id, args.pack_root, args.releases_root)
        for key, value in plan.items():
            print(f"{key}: {value}")
        return 0

    result = build_release(
        args.release_id,
        args.pack_root,
        releases_root=args.releases_root,
        collection=args.collection,
        provider=args.provider,
        with_chroma=not args.skip_chroma,
        with_bm25=not args.skip_bm25,
    )
    print(f"release={result['plan']['release_id']} results={result['results']}")

    if args.register:
        if args.skip_chroma or args.skip_bm25:
            print("部分构建不写注册库（产物不完整，不可发布）")
            return 1
        if not args.actor:
            print("--register 需要同时传 --actor")
            return 1
        registration = register_release(
            result["plan"],
            actor=args.actor,
            version=args.version,
            based_on=args.based_on,
        )
        print(f"register={registration}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
