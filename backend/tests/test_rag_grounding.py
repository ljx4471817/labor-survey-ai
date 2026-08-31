from app.rag.grounding import ensure_kb_anchors


def test_ensure_kb_anchors_appends_source_derived_terms():
    source = {
        "metadata": {"doc_type": "qa"},
        "document": "装修工月收入浮动较大，F27先问发放周期。",
    }

    result = ensure_kb_anchors("先问发放周期。", source)

    assert result.endswith("适用场景：装修工；适用指标：F27。")


def test_ensure_kb_anchors_appends_missing_metadata_keywords():
    source = {
        "metadata": {
            "doc_type": "qa",
            "keywords": "多人同住,询问顺序,优先子女,逐人登记",
        },
        "document": "优先子女，然后逐人登记。",
    }

    result = ensure_kb_anchors("优先子女。", source)

    assert "适用要点：多人同住、询问顺序、逐人登记。" in result


def test_ensure_kb_anchors_keeps_answer_with_existing_terms():
    source = {
        "metadata": {"doc_type": "qa"},
        "document": "装修工月收入浮动较大，F27先问发放周期。",
    }

    result = ensure_kb_anchors("装修工的F27先问发放周期。", source)

    assert result == "装修工的F27先问发放周期。"
