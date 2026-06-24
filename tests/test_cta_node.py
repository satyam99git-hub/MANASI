import json
import logging
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import app.services.cta_service as cta_service  # noqa: E402
from app.nodes.content_optimization_node import content_optimization_node  # noqa: E402
from app.nodes.cta_node import CTAOutput, build_cta_graph, cta_node  # noqa: E402
from app.nodes.empathy_node import empathy_node  # noqa: E402
from app.nodes.response_node import response_node  # noqa: E402
from app.nodes.safety_node import safety_node  # noqa: E402
from app.services.cta_service import (  # noqa: E402
    _parse_line,
    format_final_response,
    get_cta_url,
    load_cta_registry,
    resolve_cta_key,
)
from pydantic import ValidationError  # noqa: E402


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    """Scripted fake LLM: returns each item in `responses` in sequence on successive .invoke() calls."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls: list[str] = []

    def invoke(self, prompt: str):
        self.calls.append(prompt)
        text = self.responses[len(self.calls) - 1]
        return _FakeResponse(text)


# ---------------------------------------------------------------------------
# load_cta_registry / _parse_line
# ---------------------------------------------------------------------------


def test_parses_well_formed_registry_file(tmp_path):
    path = tmp_path / "cta_links.md"
    path.write_text(
        "# cta_links.md\n\n## Therapy\n\nmnri=https://example.com/mnri\narrowsmith=https://example.com/arrowsmith\n",
        encoding="utf-8",
    )
    registry = load_cta_registry(path)
    assert registry == {
        "mnri": "https://example.com/mnri",
        "arrowsmith": "https://example.com/arrowsmith",
    }


def test_blank_lines_and_headers_are_not_loaded_as_keys(tmp_path):
    path = tmp_path / "cta_links.md"
    path.write_text("# Title\n\n## Category\n\n\nfaq=https://example.com/faq\n", encoding="utf-8")
    registry = load_cta_registry(path)
    assert registry == {"faq": "https://example.com/faq"}


def test_line_with_no_equals_sign_is_skipped(tmp_path, caplog):
    path = tmp_path / "cta_links.md"
    path.write_text("this is not a valid line\nfaq=https://example.com/faq\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="app.services.cta_service"):
        registry = load_cta_registry(path)
    assert registry == {"faq": "https://example.com/faq"}
    assert any("malformed registry line" in record.message for record in caplog.records)


def test_line_with_empty_key_or_value_is_skipped(tmp_path):
    path = tmp_path / "cta_links.md"
    path.write_text("=https://example.com/empty-key\nfaq=\nadhd=https://example.com/adhd\n", encoding="utf-8")
    registry = load_cta_registry(path)
    assert registry == {"adhd": "https://example.com/adhd"}


def test_non_url_value_is_skipped(tmp_path, caplog):
    path = tmp_path / "cta_links.md"
    path.write_text("draft=coming soon\nfaq=https://example.com/faq\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="app.services.cta_service"):
        registry = load_cta_registry(path)
    assert registry == {"faq": "https://example.com/faq"}
    assert any("non-URL registry entry" in record.message for record in caplog.records)


def test_duplicate_key_keeps_first_definition(tmp_path, caplog):
    path = tmp_path / "cta_links.md"
    path.write_text("mnri=https://example.com/first\nmnri=https://example.com/second\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="app.services.cta_service"):
        registry = load_cta_registry(path)
    assert registry == {"mnri": "https://example.com/first"}
    assert any("duplicate CTA key" in record.message for record in caplog.records)


def test_missing_file_returns_empty_registry_without_raising(tmp_path, caplog):
    path = tmp_path / "does_not_exist.md"
    with caplog.at_level(logging.ERROR, logger="app.services.cta_service"):
        registry = load_cta_registry(path)
    assert registry == {}
    assert any("failed to load CTA registry" in record.message for record in caplog.records)


def test_load_cta_registry_never_writes_to_the_file(tmp_path):
    path = tmp_path / "cta_links.md"
    path.write_text("mnri=https://example.com/mnri\n", encoding="utf-8")
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    load_cta_registry(path)
    load_cta_registry(path)

    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime


def test_parse_line_skips_comment_at_any_heading_level():
    assert _parse_line("# Title") is None
    assert _parse_line("## Category") is None
    assert _parse_line("   ") is None
    assert _parse_line("") is None


# ---------------------------------------------------------------------------
# resolve_cta_key
# ---------------------------------------------------------------------------


def test_resolve_cta_key_empty_retrieved_docs():
    assert resolve_cta_key([]) == (None, None)


def test_resolve_cta_key_top_doc_missing_metadata_key():
    docs = [{"chunk_id": "c1", "content": "..."}]
    assert resolve_cta_key(docs) == (None, None)


def test_resolve_cta_key_top_doc_empty_metadata():
    docs = [{"chunk_id": "c1", "metadata": {}}]
    assert resolve_cta_key(docs) == (None, None)


def test_resolve_cta_key_top_doc_with_cta_key():
    docs = [{"chunk_id": "c1", "metadata": {"cta_key": "mnri"}}]
    assert resolve_cta_key(docs) == ("mnri", "c1")


def test_resolve_cta_key_only_inspects_top_document():
    docs = [
        {"chunk_id": "c1", "metadata": {}},
        {"chunk_id": "c2", "metadata": {"cta_key": "arrowsmith"}},
    ]
    assert resolve_cta_key(docs) == (None, None)


# ---------------------------------------------------------------------------
# get_cta_url / format_final_response
# ---------------------------------------------------------------------------


def test_get_cta_url_present_key(monkeypatch):
    monkeypatch.setattr(cta_service, "CTA_REGISTRY", {"mnri": "https://example.com/mnri"})
    assert get_cta_url("mnri") == "https://example.com/mnri"


def test_get_cta_url_absent_key_returns_none(monkeypatch):
    monkeypatch.setattr(cta_service, "CTA_REGISTRY", {"mnri": "https://example.com/mnri"})
    assert get_cta_url("adhd") is None


def test_format_final_response_not_matched_returns_unchanged_text():
    original = "Here is your answer."
    cta = {"matched": False, "cta_key": None, "cta_url": None}
    assert format_final_response(original, cta) is original


def test_format_final_response_matched_appends_learn_more_block():
    cta = {"matched": True, "cta_key": "mnri", "cta_url": "https://example.com/mnri"}
    result = format_final_response("Here is your answer.", cta)
    assert result == "Here is your answer.\n\nLearn More:\nhttps://example.com/mnri"


# ---------------------------------------------------------------------------
# cta_node
# ---------------------------------------------------------------------------


def make_retrieved_doc(chunk_id="c1", cta_key=None, content="MNRI is a therapy."):
    return {
        "chunk_id": chunk_id,
        "content": content,
        "content_type": "therapy_info",
        "source_title": "ManaScience Therapies",
        "source_url": None,
        "similarity_score": 0.9,
        "metadata": {"cta_key": cta_key} if cta_key is not None else {},
    }


def make_knowledge(retrieved_docs=None, source="rag"):
    return {
        "source": source,
        "retrieved_docs": retrieved_docs if retrieved_docs is not None else [],
        "confidence": 0.9 if source == "rag" else 0.0,
        "query_used": "What is MNRI?",
        "intent": "therapy_information",
        "retrieval_skipped": False,
        "content_types_searched": ["therapy_info"],
        "retrieval_time_ms": 10.0,
        "error": None,
    }


def make_state(knowledge=None):
    return {
        "user_message": "What is MNRI?",
        "chat_history": [],
        "understanding": None,
        "knowledge": knowledge,
        "cta": None,
    }


def test_cta_node_matched_when_top_doc_key_is_in_registry(monkeypatch):
    monkeypatch.setattr(cta_service, "CTA_REGISTRY", {"mnri": "https://example.com/mnri"})
    knowledge = make_knowledge(retrieved_docs=[make_retrieved_doc(cta_key="mnri")])
    result = cta_node(make_state(knowledge=knowledge))["cta"]
    assert result == {
        "matched": True,
        "cta_key": "mnri",
        "cta_url": "https://example.com/mnri",
        "source_chunk_id": "c1",
        "lookup_time_ms": result["lookup_time_ms"],
        "error": None,
    }
    assert result["lookup_time_ms"] >= 0.0


def test_cta_node_no_retrieval_resolves_no_match():
    knowledge = make_knowledge(retrieved_docs=[], source="llm")
    result = cta_node(make_state(knowledge=knowledge))["cta"]
    assert result["matched"] is False
    assert result["cta_key"] is None
    assert result["cta_url"] is None
    assert result["error"] is None


def test_cta_node_key_not_in_registry_logs_warning(monkeypatch, caplog):
    monkeypatch.setattr(cta_service, "CTA_REGISTRY", {})
    knowledge = make_knowledge(retrieved_docs=[make_retrieved_doc(cta_key="adhd")])
    with caplog.at_level(logging.WARNING, logger="app.nodes.cta_node"):
        result = cta_node(make_state(knowledge=knowledge))["cta"]
    assert result["matched"] is False
    assert result["cta_key"] == "adhd"
    assert result["cta_url"] is None
    assert result["error"] is None
    assert any("not found in registry" in record.message for record in caplog.records)


def test_cta_node_knowledge_is_none_resolves_no_match():
    result = cta_node(make_state(knowledge=None))["cta"]
    assert result["matched"] is False
    assert result["error"] is None


def test_cta_output_rejects_matched_true_with_null_url():
    try:
        CTAOutput(
            matched=True,
            cta_key="mnri",
            cta_url=None,
            source_chunk_id="c1",
            lookup_time_ms=0.1,
            error=None,
        )
        assert False, "expected ValidationError"
    except ValidationError:
        pass


def test_cta_output_rejects_matched_false_with_non_null_url():
    try:
        CTAOutput(
            matched=False,
            cta_key=None,
            cta_url="https://example.com/mnri",
            source_chunk_id=None,
            lookup_time_ms=0.1,
            error=None,
        )
        assert False, "expected ValidationError"
    except ValidationError:
        pass


def test_build_cta_graph_compiles():
    graph = build_cta_graph()
    assert graph is not None


# ---------------------------------------------------------------------------
# Integration: cta survives untouched through the real downstream nodes
# ---------------------------------------------------------------------------

MNRI_RETRIEVED_DOC = make_retrieved_doc(
    chunk_id="c1", cta_key="mnri", content="MNRI is a therapy used to support reflex integration for children."
)
MNRI_ANSWER = "I'm curious about MNRI and how it helps with reflexes today."
MNRI_FINAL_ANSWER = (
    "That's a great question! I'm glad you're curious about MNRI and how it supports "
    "reflex integration for many families over time."
)


def make_full_state():
    return {
        "user_message": "What is MNRI?",
        "chat_history": [],
        "understanding": {
            "intent": "therapy_information",
            "topic": "MNRI",
            "search_query": "What is MNRI",
            "emotional_state": "curious",
        },
        "knowledge": make_knowledge(retrieved_docs=[MNRI_RETRIEVED_DOC]),
        "cta": None,
        "response": None,
        "content_optimization": None,
        "empathy": None,
        "safety": None,
    }


def run_full_pipeline(monkeypatch, registry):
    monkeypatch.setattr(cta_service, "CTA_REGISTRY", registry)
    state = make_full_state()

    state.update(cta_node(state))

    response_llm = FakeLLM([json.dumps({"answer": MNRI_ANSWER})])
    state.update(response_node(state, llm=response_llm))

    state.update(content_optimization_node(state))  # word count below the skip threshold -- no LLM call

    empathy_llm = FakeLLM([json.dumps({"final_answer": MNRI_FINAL_ANSWER})])
    state.update(empathy_node(state, llm=empathy_llm))

    safety_llm = FakeLLM([json.dumps({"is_clean": True, "safe_response": MNRI_FINAL_ANSWER})])
    state.update(safety_node(state, llm=safety_llm))

    return state


def test_cta_survives_untouched_through_the_full_pipeline(monkeypatch):
    registry = {"mnri": "https://manascience.webflow.io/post/mnri"}
    baseline_cta = cta_node(make_full_state())["cta"]

    state = run_full_pipeline(monkeypatch, registry)

    # lookup_time_ms is wall-clock and legitimately differs between the two
    # separate cta_node calls -- every other field must be byte-identical.
    assert {k: v for k, v in state["cta"].items() if k != "lookup_time_ms"} == {
        k: v for k, v in baseline_cta.items() if k != "lookup_time_ms"
    }
    assert state["cta"]["matched"] is True
    assert state["content_optimization"]["error"] is None  # confirms the skip path, not a fallback
    assert state["response"]["error"] is None
    assert state["empathy"]["error"] is None
    assert state["safety"]["error"] is None


def test_cta_url_never_leaks_into_safe_response_before_format_final_response(monkeypatch):
    registry = {"mnri": "https://manascience.webflow.io/post/mnri"}
    state = run_full_pipeline(monkeypatch, registry)
    assert "https://manascience.webflow.io/post/mnri" not in state["safety"]["safe_response"]


# ---------------------------------------------------------------------------
# Acceptance-level tests, mirroring the brief's three literal examples
# ---------------------------------------------------------------------------


def test_mnri_example_final_text_ends_with_learn_more_block(monkeypatch):
    registry = {"mnri": "https://manascience.webflow.io/post/mnri"}
    state = run_full_pipeline(monkeypatch, registry)
    final_text = format_final_response(state["safety"]["safe_response"], state["cta"])
    assert final_text.endswith("Learn More:\nhttps://manascience.webflow.io/post/mnri")


def test_hello_example_general_chat_has_no_cta(monkeypatch):
    monkeypatch.setattr(cta_service, "CTA_REGISTRY", {"mnri": "https://example.com/mnri"})
    knowledge = make_knowledge(retrieved_docs=[], source="llm")
    cta = cta_node(make_state(knowledge=knowledge))["cta"]
    safe_response = "Hi there! How can I help you today?"
    final_text = format_final_response(safe_response, cta)
    assert final_text == safe_response
    assert "Learn More:" not in final_text


def test_primitive_reflexes_example_untagged_document_has_no_cta(monkeypatch):
    monkeypatch.setattr(cta_service, "CTA_REGISTRY", {"mnri": "https://example.com/mnri"})
    untagged_doc = make_retrieved_doc(chunk_id="c2", cta_key=None, content="Primitive reflexes are present from birth.")
    knowledge = make_knowledge(retrieved_docs=[untagged_doc])
    cta = cta_node(make_state(knowledge=knowledge))["cta"]
    safe_response = "Primitive reflexes are automatic movement patterns present from birth."
    final_text = format_final_response(safe_response, cta)
    assert final_text == safe_response
    assert "Learn More:" not in final_text
