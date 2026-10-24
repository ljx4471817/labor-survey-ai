# -*- coding: utf-8 -*-
"""多知识包 Phase 0 内容拆分规则（TDD 红灯）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "split_knowledge_packs.py"
    spec = importlib.util.spec_from_file_location("split_knowledge_packs", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_classifies_qa_by_source():
    module = load_module()
    assert module.classify_qa({"source": "劳动力调查制度（2026版）问题10"}) == "national-base"
    assert module.classify_qa({"source": "贵阳调查队填报规范指引·包工头就业判定"}) == "guizhou"
    assert module.classify_qa({"source": "统计云系统培训课件 第14页"}) == "guizhou"
    assert module.classify_qa({"source": "四川劳动力调查技能测试题题库2026 题号 14"}) == "guizhou"


def test_split_keeps_every_entry_exactly_once():
    module = load_module()
    rows = [
        {"id": "001", "source": "劳动力调查制度"},
        {"id": "002", "source": "贵阳调查队填报规范指引"},
        {"id": "003", "source": "统计云系统培训课件 第5页"},
        {"id": "004", "source": "四川劳动力调查技能测试题题库2026 题号 14"},
    ]
    split = module.split_entries(rows)
    assert sorted(split) == ["guizhou", "national-base"]
    assert [qa["source"] for qa in split["guizhou"]] == [
        "贵阳调查队填报规范指引",
        "统计云系统培训课件 第5页",
        "四川劳动力调查技能测试题题库2026 题号 14",
    ]
    ids = [qa["id"] for items in split.values() for qa in items]
    assert sorted(ids) == ["001", "002", "003", "004"]


def test_eval_split_sends_local_cases_to_guizhou():
    module = load_module()
    assert module.classify_eval({"id": "eval-103", "question": "装修工工资浮动，月收入怎么填"}) == "guizhou"
    assert module.classify_eval({"id": "eval-108", "question": "包工头F27上月工作报酬怎么填？"}) == "national-base"
    assert module.classify_eval({"id": "eval-071", "question": "港澳台居民在贵阳居住怎么登记？", "type": "out_of_kb"}) == "national-base"


def test_guizhou_rule_keys_are_stable_and_well_formed():
    module = load_module()
    expected = {"355", "209", "357", "429", "430", "431", "433", "434"}
    assert set(module.GUIZHOU_RULE_KEYS) == expected
    for key in module.GUIZHOU_RULE_KEYS.values():
        assert key == key.strip()
        assert "." in key
        assert " " not in key

def test_adopted_sichuan_content_is_scoped_to_guizhou_without_rewriting_source():
    module = load_module()
    source = "四川劳动力调查技能测试题题库2026 题号 101"
    qa = {"id": "333", "source": source}
    split = module.split_entries([qa])
    assert split["guizhou"][0]["source"] == source

    chunk = {"source": source, "text": "题干"}
    assert module.classify_chunk(chunk) == "guizhou"
    manifest = module.build_manifest("guizhou", [], [], [], [])
    assert manifest["content_scope"] == "guizhou"
    assert manifest["adopted_sources"] == ["四川劳动力调查技能测试题题库2026"]
    assert manifest["superseded_draft_packs"] == ["sichuan"]

def test_manifest_does_not_claim_unverified_eval_as_passed():
    module = load_module()
    manifest = module.build_manifest("guizhou", [], [], [], [])
    assert manifest["eval_status"] == "pending"
    assert manifest["binding_status"] == "planned"
def test_guizhou_markdown_links_are_rewritten_for_pack_depth():
    module = load_module()
    source = "![图](../../../backend/static/kb-images/page_01/image12.jpeg)"
    adjusted = module.rewrite_guizhou_source_links(source)
    assert adjusted == "![图](../../../../../backend/static/kb-images/page_01/image12.jpeg)"
def test_copy_guizhou_source_writes_inside_pack_root(tmp_path, monkeypatch):
    module = load_module()
    source_root = tmp_path / "knowledge-base" / "raw" / "pptx_extract"
    source_root.mkdir(parents=True)
    source = source_root / "pages.md"
    source.write_text("![图](../../../backend/static/kb-images/page_01/image12.jpeg)", encoding="utf-8")

    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "PACKS_ROOT", tmp_path / "knowledge-base" / "packs")

    copied = module._copy_sources("guizhou")

    output = tmp_path / "knowledge-base" / "packs" / "guizhou" / "raw" / "markdown" / "统计云系统培训课件.md"
    assert output.exists()
    assert output.read_text(encoding="utf-8").startswith("![图](../../../../../backend/static/")
    assert copied == ["raw/markdown/统计云系统培训课件.md"]