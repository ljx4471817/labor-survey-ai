"""KB grounding anchors for LLM answers."""
from __future__ import annotations

import re

_INDICATOR_PATTERN = re.compile(r"F\d{2}", re.IGNORECASE)
_SCENARIO_TERMS = ("装修工", "建筑工", "零工")


def ensure_kb_anchors(answer: str, top_source: dict | None) -> str:
    """Append source-derived indicator and scenario anchors omitted by the LLM."""
    if not answer or not top_source:
        return answer

    metadata = top_source.get("metadata") or {}
    if metadata.get("doc_type") != "qa":
        return answer

    document = top_source.get("document") or ""
    indicators: list[str] = []
    for indicator in _INDICATOR_PATTERN.findall(document):
        normalized = indicator.upper()
        if normalized not in answer.upper():
            indicators.append(normalized)

    scenarios: list[str] = []
    for scenario in _SCENARIO_TERMS:
        if scenario in document and scenario not in answer:
            scenarios.append(scenario)

    keywords = [
        keyword
        for keyword in str(metadata.get("keywords", "")).split(",")
        if keyword and keyword not in answer
    ]

    if not indicators and not scenarios and not keywords:
        return answer

    anchors: list[str] = []
    if keywords:
        anchors.append("适用要点：" + "、".join(keywords))
    if scenarios:
        anchors.append("适用场景：" + "、".join(scenarios))
    if indicators:
        anchors.append("适用指标：" + "、".join(dict.fromkeys(indicators)))
    return f"{answer}\n\n{'；'.join(anchors)}。"
