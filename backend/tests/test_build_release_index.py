# -*- coding: utf-8 -*-
"""release 索引构建编排（TDD）。"""
import json
from pathlib import Path

import pytest
import sys

import importlib.util


def load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "build_release_index.py"
    spec = importlib.util.spec_from_file_location("build_release_index", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_release_uses_isolated_artifact_paths(tmp_path):
    module = load_module()
    pack = tmp_path / "pack"
    (pack / "qa").mkdir(parents=True)
    (pack / "qa" / "faq.json").write_text("[]", encoding="utf-8")
    (pack / "raw" / "markdown").mkdir(parents=True)
    (pack / "raw" / "markdown" / "制度.md").write_text("# 制度", encoding="utf-8")

    plan = module.plan_release("nat-2026-01", pack, tmp_path / "releases")
    assert plan["faq_path"] == pack / "qa" / "faq.json"
    assert plan["release_dir"] == tmp_path / "releases" / "nat-2026-01"
    assert plan["chroma_dir"] == plan["release_dir" ] / "chroma"
    assert plan["bm25_path"] == plan["release_dir"] / "bm25_index.json"


def test_plan_release_rejects_missing_pack(tmp_path):
    module = load_module()
    with pytest.raises(FileNotFoundError):
        module.plan_release("nat-2026-01", tmp_path / "missing")

def test_release_id_is_validated_before_any_build(tmp_path):
    module = load_module()
    pack = tmp_path / "pack"
    (pack / "qa").mkdir(parents=True)
    (pack / "qa" / "faq.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        module.plan_release("../escape", pack)

def test_importing_build_script_does_not_load_dotenv(monkeypatch):
    def fail_load_dotenv(*args, **kwargs):
        raise AssertionError("dotenv must be loaded in main(), not at import time")

    monkeypatch.setattr("dotenv.load_dotenv", fail_load_dotenv)
    load_module()


def test_release_build_rejects_existing_nonempty_release(tmp_path):
    module = load_module()
    pack = tmp_path / "pack"
    (pack / "qa").mkdir(parents=True)
    (pack / "qa" / "faq.json").write_text("[]", encoding="utf-8")
    release_dir = tmp_path / "releases" / "nat-2026-01"
    release_dir.mkdir(parents=True)
    (release_dir / "bm25_index.json").write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError, match="已存在"):
        module.build_release(
            "nat-2026-01", pack,
            releases_root=tmp_path / "releases",
            with_chroma=False, with_bm25=False,
        )


def test_release_build_fails_when_chroma_has_failures(tmp_path, monkeypatch):
    module = load_module()
    pack = tmp_path / "pack"
    (pack / "qa").mkdir(parents=True)
    (pack / "qa" / "faq.json").write_text("[]", encoding="utf-8")

    class FakeBuildKb:
        @staticmethod
        def build(*args, **kwargs):
            return {"total": 1, "added": 0, "skipped": 0, "failed": 1}

    class FakeBuildChunks:
        @staticmethod
        def make_chunks(path):
            return []

        @staticmethod
        def save_chunks_jsonl(chunks, path, full):
            return 0

        @staticmethod
        def upsert_chunks(*args, **kwargs):
            raise AssertionError("empty chunks must not be upserted")

    monkeypatch.setitem(sys.modules, "build_kb", FakeBuildKb)
    monkeypatch.setitem(sys.modules, "build_chunks", FakeBuildChunks)
    monkeypatch.setitem(sys.modules, "build_bm25", type("FakeBuildBm25", (), {}))

    with pytest.raises(RuntimeError, match="Chroma 构建失败"):
        module.build_release(
            "nat-2026-01", pack,
            releases_root=tmp_path / "releases",
            with_chroma=True, with_bm25=False,
        )


def test_release_build_reuses_reviewed_pack_chunks(tmp_path):
    module = load_module()

    class FakeBuildChunks:
        @staticmethod
        def make_chunks(path):
            raise AssertionError("must reuse reviewed pack chunks")

    pack = tmp_path / "pack"
    (pack / "qa").mkdir(parents=True)
    (pack / "qa" / "faq.json").write_text("[]", encoding="utf-8")
    (pack / "chunks.jsonl").write_text(
        '{"chunk_id":"001","section":"制度","text":"内容","doc_type":"chunk","doc_id":"d","source":"s","indicators":[]}',
        encoding="utf-8",
    )
    plan = module.plan_release("gz-2026-01", pack, tmp_path / "releases")
    assert module._load_pack_chunks(plan, FakeBuildChunks)[0]["chunk_id"] == "001"


def test_partial_build_skips_manifest_instead_of_crashing(tmp_path, monkeypatch):
    module = load_module()

    class FakeBuildKb:
        @staticmethod
        def build(*args, **kwargs):
            return {"total": 1, "added": 1, "skipped": 0, "failed": 0}

    class FakeBuildChunks:
        @staticmethod
        def make_chunks(path):
            return []

        @staticmethod
        def save_chunks_jsonl(chunks, path, full):
            return 0

        @staticmethod
        def upsert_chunks(*args, **kwargs):
            return {"count": 0}

    class FakeBuildBm25:
        @staticmethod
        def build(*args, **kwargs):
            return {"total": 1}

    monkeypatch.setitem(sys.modules, "build_kb", FakeBuildKb)
    monkeypatch.setitem(sys.modules, "build_chunks", FakeBuildChunks)
    monkeypatch.setitem(sys.modules, "build_bm25", FakeBuildBm25)

    pack = tmp_path / "pack"
    (pack / "qa").mkdir(parents=True)
    (pack / "qa" / "faq.json").write_text("[]", encoding="utf-8")

    result = module.build_release(
        "nat-2026-01", pack,
        releases_root=tmp_path / "releases",
        with_chroma=True, with_bm25=False,
    )
    assert result["results"]["artifact_manifest"]["status"] == "skipped"
    release_dir = tmp_path / "releases" / "nat-2026-01"
    assert not (release_dir / "artifact_manifest.json").exists()


def test_register_release_writes_registry_and_is_idempotent(tmp_path, monkeypatch):
    module = load_module()
    from app.persistence import knowledge_registry as registry

    pack = tmp_path / "pack"
    (pack / "qa").mkdir(parents=True)
    (pack / "qa" / "faq.json").write_text("[]", encoding="utf-8")
    (pack / "manifest.json").write_text(
        json.dumps({"kb_id": "national-base", "kind": "national"}),
        encoding="utf-8",
    )
    plan = module.plan_release("nat-2026-09", pack, tmp_path / "releases")
    plan["release_dir"].mkdir(parents=True)
    for relpath in ("bm25_index.json", "chunks.jsonl", "chroma/chroma.sqlite3"):
        target = plan["release_dir"] / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(relpath.encode("utf-8"))
    from app.services.release_artifacts import write_artifact_manifest, manifest_content_hash

    write_artifact_manifest(plan["release_dir"], "nat-2026-09")
    expected_hash = manifest_content_hash(
        json.loads((plan["release_dir"] / "artifact_manifest.json").read_text(encoding="utf-8"))
    )

    calls = []
    package_calls = []
    monkeypatch.setattr(registry, "list_packages", lambda: [])
    monkeypatch.setattr(
        registry, "create_package",
        lambda **kw: package_calls.append(kw) or {"id": kw["package_id"]},
    )
    monkeypatch.setattr(registry, "create_release", lambda **kw: calls.append(kw) or {"id": kw["release_id"]})
    result = module.register_release(plan, actor="13985000001")
    assert result["registered"] is True
    assert calls[0]["content_hash"] == expected_hash
    assert calls[0]["build_status"] == "success"
    assert calls[0]["eval_status"] == "pending"
    # 包不存在 → 自动创建 draft 包（ADR 0028：后台不再提供建包入口）
    assert package_calls[0]["package_id"] == "national-base"
    assert package_calls[0]["kind"] == "national"
    assert package_calls[0].get("status", "draft") == "draft"

    # 包已存在 → 不重复创建
    monkeypatch.setattr(registry, "list_packages", lambda: [{"id": "national-base"}])
    package_calls.clear()
    module.register_release(plan, actor="13985000001")
    assert package_calls == []

    # 幂等重跑：同 hash 已登记 → 跳过；hash 不同 → 报错
    from app.persistence import db

    def _duplicate(**kw):
        raise db.IntegrityError("duplicate")

    monkeypatch.setattr(registry, "create_release", _duplicate)
    monkeypatch.setattr(
        registry, "get_release",
        lambda release_id: {"content_hash": expected_hash},
    )
    result2 = module.register_release(plan, actor="13985000001")
    assert result2 == {
        "registered": False,
        "already_registered": True,
        "package_id": "national-base",
        "content_hash": expected_hash,
    }

    monkeypatch.setattr(
        registry, "get_release",
        lambda release_id: {"content_hash": "different"},
    )
    with pytest.raises(db.IntegrityError):
        module.register_release(plan, actor="13985000001")


def test_register_release_requires_based_on_for_provincial(tmp_path):
    module = load_module()
    pack = tmp_path / "pack"
    (pack / "qa").mkdir(parents=True)
    (pack / "qa" / "faq.json").write_text("[]", encoding="utf-8")
    (pack / "manifest.json").write_text(
        json.dumps({"kb_id": "guizhou", "kind": "provincial"}),
        encoding="utf-8",
    )
    plan = module.plan_release("gz-2026-09", pack, tmp_path / "releases")
    plan["release_dir"].mkdir(parents=True)
    (plan["release_dir"] / "artifact_manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="based-on"):
        module.register_release(plan, actor="13985000001")
