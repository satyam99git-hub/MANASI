import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import openai  # noqa: E402

from app.nodes.content_optimization_node import content_optimization_node  # noqa: E402
from app.services.content_optimization_service import (  # noqa: E402
    CORRECTIVE_REPROMPT_SUFFIX_FABRICATION,
    CORRECTIVE_REPROMPT_SUFFIX_FACT_DROP,
    CORRECTIVE_REPROMPT_SUFFIX_LENGTH,
    CORRECTIVE_REPROMPT_SUFFIX_MALFORMED,
    CORRECTIVE_REPROMPT_SUFFIX_REFUSAL,
    from_chroma_chunks,
    from_markdown_file,
    from_pipeline_state,
    from_raw_text,
    from_webflow_cms_item,
    optimize_content,
)


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    """Scripted fake LLM: returns or raises each item in `responses` in sequence on
    successive .invoke() calls."""

    def __init__(self, responses: list):
        self.responses = responses
        self.calls: list[str] = []

    def invoke(self, prompt: str):
        self.calls.append(prompt)
        item = self.responses[len(self.calls) - 1]
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


def optimization_json(summary: str, description: str, key_points=None, title=None) -> str:
    return json.dumps(
        {
            "title": title,
            "summary": summary,
            "description": description,
            "key_points": key_points if key_points is not None else ["point one", "point two", "point three"],
        }
    )


SOURCE_TEXT = (
    "Neuroplasticity is the brain's remarkable ability to reorganize itself by forming new "
    "neural connections throughout a person's entire life, not only during early childhood "
    "development as scientists once believed. Researchers at Stanford University have tracked "
    "this phenomenon in more than 200 participants in an ongoing study that began back in 1960, "
    "observing measurable changes in neural pathways well into adulthood and even into old age."
)

CLEAN_SUMMARY = (
    "Neuroplasticity is the brain's ability to reorganize itself by forming brand new neural "
    "connections throughout an entire life, not only just in early childhood as once believed. "
    "Stanford University researchers have tracked this in more than 200 participants over an "
    "ongoing study since 1960, finding measurable changes well into adulthood and even old age."
)

CLEAN_DESCRIPTION = (
    "An overview of neuroplasticity, the brain's lifelong ability to form brand new neural "
    "connections and adapt over time. Stanford University research spanning more than 200 "
    "participants since 1960 shows this capacity clearly continues well into adulthood and old "
    "age, not only during early childhood development as scientists once long believed."
)

FABRICATED_SUMMARY = (
    "Neuroplasticity is the brain's ability to reorganize itself by forming brand new neural "
    "connections throughout an entire life. Stanford University researchers, working alongside "
    "Harvard colleagues, tracked this in more than 200 participants since 1960, with an "
    "additional 500 participants joining a related follow-up study well into adulthood and old age."
)

FACT_DROP_SUMMARY = (
    "Neuroplasticity is the brain's wonderful ability to reorganize itself by forming brand "
    "new connections throughout a person's whole entire life, and many different people have "
    "studied this fascinating phenomenon for a very long time now, finding that it occurs well "
    "into adulthood and even old age for a great many people across many different walks of life."
)

TOO_SHORT_SUMMARY = "Stanford University tracked 200 participants since 1960."

REFUSAL_SUMMARY = (
    "I cannot summarize this content properly, but Stanford University tracked 200 "
    "participants since 1960, and neuroplasticity lets the brain reorganize itself by forming "
    "brand new neural connections throughout an entire life, including well into adulthood and "
    "old age for a great many people across many different walks of life nowadays."
)

MALFORMED_OUTPUT = "Sure, here's my summary: the brain can change!"

TRIVIAL_SHORT_ANSWER = "Hi! What would you like to know about ManaScience today?"

MEDIUM_TEXT = (
    "Primitive reflexes are automatic movements that infants are born with, and most of them "
    "are expected to fade away naturally within the first year of life as the brain develops."
)

MEDIUM_SUMMARY = (
    "Primitive reflexes are automatic movements that infants are born with. Most of these "
    "reflexes fade away naturally within the first year of life as a child's brain continues "
    "to develop and mature."
)

VALID_TITLE_CANDIDATE = "Understanding Stanford Research"
INVALID_TITLE_CANDIDATE_NO_OVERLAP = "Understanding Brain Plasticity Today"
INVALID_TITLE_CANDIDATE_TOO_LONG = "A Very Long And Detailed Title About Stanford Research"


def make_response(
    answer=SOURCE_TEXT,
    source="rag",
    answer_type="concept_explanation",
    topic="neuroplasticity",
    intent="concept_explanation",
    confidence=0.89,
    grounded_chunk_ids=None,
    error=None,
):
    return {
        "answer": answer,
        "source": source,
        "answer_type": answer_type,
        "topic": topic,
        "intent": intent,
        "confidence": confidence,
        "grounded_chunk_ids": grounded_chunk_ids if grounded_chunk_ids is not None else ["c1"],
        "generation_time_ms": 100.0,
        "error": error,
    }


def make_retrieved_doc(
    source_title="Understanding Neuroplasticity",
    content_type="neuroplasticity_content",
    chunk_id="c1",
    content=SOURCE_TEXT,
):
    return {
        "chunk_id": chunk_id,
        "content": content,
        "content_type": content_type,
        "source_title": source_title,
        "source_url": "https://example.com/neuroplasticity",
        "similarity_score": 0.9,
        "metadata": {},
    }


def make_knowledge(retrieved_docs=None, source="rag"):
    docs = retrieved_docs if retrieved_docs is not None else ([make_retrieved_doc()] if source == "rag" else [])
    return {
        "source": source,
        "retrieved_docs": docs,
        "confidence": 0.89 if source == "rag" else 0.0,
        "query_used": "what is neuroplasticity",
        "intent": "concept_explanation",
        "retrieval_skipped": False,
        "content_types_searched": ["neuroplasticity_content"],
        "retrieval_time_ms": 100.0,
        "error": None,
    }


def make_understanding(emotional_state="curious"):
    return {
        "intent": "concept_explanation",
        "topic": "neuroplasticity",
        "search_query": "what is neuroplasticity",
        "emotional_state": emotional_state,
    }


def make_state(response=None, knowledge=None, understanding=None, user_message="What is neuroplasticity?"):
    return {
        "user_message": user_message,
        "chat_history": [],
        "understanding": understanding if understanding is not None else make_understanding(),
        "knowledge": knowledge if knowledge is not None else make_knowledge(),
        "response": response if response is not None else make_response(),
        "content_optimization": None,
    }


# ---------------------------------------------------------------------------
# Happy path / title resolution
# ---------------------------------------------------------------------------


def test_happy_path_produces_all_core_fields():
    llm = FakeLLM([optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION)])
    result = content_optimization_node(make_state(), llm=llm)["content_optimization"]
    assert result["summary"] == CLEAN_SUMMARY
    assert result["description"] == CLEAN_DESCRIPTION
    assert result["key_points"] == ["point one", "point two", "point three"]
    assert result["title"] == "Understanding Neuroplasticity"
    assert result["content_type"] == "neuroplasticity_content"
    assert result["source_type"] == "rag"
    assert result["source"] == "rag"
    assert result["answer_type"] == "concept_explanation"
    assert result["topic"] == "neuroplasticity"
    assert result["intent"] == "concept_explanation"
    assert result["confidence"] == 0.89
    assert result["grounded_chunk_ids"] == ["c1"]
    assert result["original_answer"] == SOURCE_TEXT
    assert result["error"] is None
    assert len(llm.calls) == 1


def test_title_taken_from_retrieved_doc_never_overridden_by_llm():
    llm = FakeLLM(
        [optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION, title="A Totally Different Title")]
    )
    result = content_optimization_node(make_state(), llm=llm)["content_optimization"]
    assert result["title"] == "Understanding Neuroplasticity"


def test_no_title_and_no_valid_candidate_returns_null():
    response = make_response(source="llm")
    knowledge = make_knowledge(source="llm")
    llm = FakeLLM(
        [optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION, title=INVALID_TITLE_CANDIDATE_NO_OVERLAP)]
    )
    result = content_optimization_node(make_state(response=response, knowledge=knowledge), llm=llm)[
        "content_optimization"
    ]
    assert result["title"] is None
    assert result["source_type"] == "llm"
    assert result["content_type"] == "llm_generated"


def test_llm_proposed_title_over_eight_words_is_rejected():
    response = make_response(source="llm")
    knowledge = make_knowledge(source="llm")
    llm = FakeLLM(
        [optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION, title=INVALID_TITLE_CANDIDATE_TOO_LONG)]
    )
    result = content_optimization_node(make_state(response=response, knowledge=knowledge), llm=llm)[
        "content_optimization"
    ]
    assert result["title"] is None


def test_valid_llm_proposed_title_accepted_when_none_known():
    response = make_response(source="llm")
    knowledge = make_knowledge(source="llm")
    llm = FakeLLM([optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION, title=VALID_TITLE_CANDIDATE)])
    result = content_optimization_node(make_state(response=response, knowledge=knowledge), llm=llm)[
        "content_optimization"
    ]
    assert result["title"] == VALID_TITLE_CANDIDATE


# ---------------------------------------------------------------------------
# Anti-padding (FR-4)
# ---------------------------------------------------------------------------


def test_short_input_does_not_get_padded_to_configured_minimum():
    response = make_response(answer=MEDIUM_TEXT)
    knowledge = make_knowledge(source="rag")
    llm = FakeLLM([optimization_json(MEDIUM_SUMMARY, MEDIUM_SUMMARY)])
    result = content_optimization_node(make_state(response=response, knowledge=knowledge), llm=llm)[
        "content_optimization"
    ]
    assert result["error"] is None
    assert len(result["summary"].split()) < 50
    assert len(llm.calls) == 1


# ---------------------------------------------------------------------------
# Guard-triggered retries (each isolated to exactly one guard)
# ---------------------------------------------------------------------------


def test_fabrication_guard_triggers_retry_then_succeeds():
    llm = FakeLLM(
        [
            optimization_json(FABRICATED_SUMMARY, CLEAN_DESCRIPTION),
            optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION),
        ]
    )
    result = content_optimization_node(make_state(), llm=llm)["content_optimization"]
    assert result["summary"] == CLEAN_SUMMARY
    assert result["error"] is None
    assert len(llm.calls) == 2
    assert CORRECTIVE_REPROMPT_SUFFIX_FABRICATION in llm.calls[1]


def test_fact_retention_guard_triggers_retry_then_succeeds():
    llm = FakeLLM(
        [
            optimization_json(FACT_DROP_SUMMARY, CLEAN_DESCRIPTION),
            optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION),
        ]
    )
    result = content_optimization_node(make_state(), llm=llm)["content_optimization"]
    assert result["summary"] == CLEAN_SUMMARY
    assert result["error"] is None
    assert len(llm.calls) == 2
    assert CORRECTIVE_REPROMPT_SUFFIX_FACT_DROP in llm.calls[1]


def test_length_bound_guard_triggers_retry_then_succeeds():
    llm = FakeLLM(
        [
            optimization_json(TOO_SHORT_SUMMARY, CLEAN_DESCRIPTION),
            optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION),
        ]
    )
    result = content_optimization_node(make_state(), llm=llm)["content_optimization"]
    assert result["summary"] == CLEAN_SUMMARY
    assert result["error"] is None
    assert len(llm.calls) == 2
    assert CORRECTIVE_REPROMPT_SUFFIX_LENGTH in llm.calls[1]


def test_banned_refusal_phrase_guard_triggers_retry_then_succeeds():
    llm = FakeLLM(
        [
            optimization_json(REFUSAL_SUMMARY, CLEAN_DESCRIPTION),
            optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION),
        ]
    )
    result = content_optimization_node(make_state(), llm=llm)["content_optimization"]
    assert result["summary"] == CLEAN_SUMMARY
    assert result["error"] is None
    assert len(llm.calls) == 2
    assert CORRECTIVE_REPROMPT_SUFFIX_REFUSAL in llm.calls[1]


def test_malformed_json_first_attempt_triggers_retry_then_succeeds():
    llm = FakeLLM([MALFORMED_OUTPUT, optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION)])
    result = content_optimization_node(make_state(), llm=llm)["content_optimization"]
    assert result["summary"] == CLEAN_SUMMARY
    assert result["error"] is None
    assert len(llm.calls) == 2
    assert CORRECTIVE_REPROMPT_SUFFIX_MALFORMED in llm.calls[1]


# ---------------------------------------------------------------------------
# Never-block fallback
# ---------------------------------------------------------------------------


def test_both_attempts_fail_guard_falls_back_to_verbatim_source():
    llm = FakeLLM(
        [
            optimization_json(FABRICATED_SUMMARY, CLEAN_DESCRIPTION),
            optimization_json(FABRICATED_SUMMARY, CLEAN_DESCRIPTION),
        ]
    )
    response = make_response()
    result = content_optimization_node(make_state(response=response), llm=llm)["content_optimization"]
    assert result["summary"] == response["answer"]
    assert result["key_points"] == []
    assert result["confidence_score"] == 0.3
    assert result["error"] == "quality_guard_exhausted"
    assert len(llm.calls) == 2


def test_both_llm_calls_raise_falls_back_with_llm_call_failure():
    exc = openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    llm = FakeLLM([exc, exc])
    response = make_response()
    result = content_optimization_node(make_state(response=response), llm=llm)["content_optimization"]
    assert result["summary"] == response["answer"]
    assert result["error"] == "llm_call_failure"
    assert len(llm.calls) == 2


def test_mixed_failure_first_raises_second_fails_guard_is_quality_guard_exhausted():
    exc = openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    llm = FakeLLM([exc, optimization_json(FABRICATED_SUMMARY, CLEAN_DESCRIPTION)])
    response = make_response()
    result = content_optimization_node(make_state(response=response), llm=llm)["content_optimization"]
    assert result["summary"] == response["answer"]
    assert result["error"] == "quality_guard_exhausted"
    assert len(llm.calls) == 2


# ---------------------------------------------------------------------------
# Skip logic (FR-2)
# ---------------------------------------------------------------------------


def test_trivially_short_input_skips_llm_call_entirely():
    llm = FakeLLM([])
    response = make_response(answer=TRIVIAL_SHORT_ANSWER, source="llm")
    knowledge = make_knowledge(source="llm")
    result = content_optimization_node(make_state(response=response, knowledge=knowledge), llm=llm)[
        "content_optimization"
    ]
    assert result["summary"] == TRIVIAL_SHORT_ANSWER
    assert result["key_points"] == []
    assert result["error"] is None
    assert len(llm.calls) == 0


def test_upstream_response_error_skips_llm_call_regardless_of_length():
    llm = FakeLLM([])
    response = make_response(answer=SOURCE_TEXT, error="llm_call_failure")
    result = content_optimization_node(make_state(response=response), llm=llm)["content_optimization"]
    assert result["summary"] == SOURCE_TEXT
    assert result["error"] is None
    assert len(llm.calls) == 0


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


def test_confidence_score_lower_for_llm_source_than_rag_source():
    rag_llm = FakeLLM([optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION)])
    rag_result = content_optimization_node(make_state(), llm=rag_llm)["content_optimization"]

    llm_response = make_response(source="llm")
    llm_knowledge = make_knowledge(source="llm")
    llm_llm = FakeLLM([optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION)])
    llm_result = content_optimization_node(
        make_state(response=llm_response, knowledge=llm_knowledge), llm=llm_llm
    )["content_optimization"]

    assert llm_result["confidence_score"] < rag_result["confidence_score"]


def test_confidence_score_lower_when_a_retry_was_consumed():
    clean_llm = FakeLLM([optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION)])
    clean_result = content_optimization_node(make_state(), llm=clean_llm)["content_optimization"]

    retried_llm = FakeLLM(
        [
            optimization_json(TOO_SHORT_SUMMARY, CLEAN_DESCRIPTION),
            optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION),
        ]
    )
    retried_result = content_optimization_node(make_state(), llm=retried_llm)["content_optimization"]

    assert retried_result["confidence_score"] < clean_result["confidence_score"]


# ---------------------------------------------------------------------------
# Statelessness / isolation
# ---------------------------------------------------------------------------


def test_does_not_mutate_input_state():
    response = make_response()
    knowledge = make_knowledge()
    state = make_state(response=response, knowledge=knowledge)
    original_response = dict(response)
    original_knowledge = dict(knowledge)
    llm = FakeLLM([optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION)])
    content_optimization_node(state, llm=llm)
    assert state["response"] == original_response
    assert state["knowledge"] == original_knowledge


def test_node_does_not_read_user_message_or_chat_history_or_understanding():
    distinctive_message = "ZZZ_DISTINCTIVE_USER_MESSAGE_ZZZ"
    llm = FakeLLM([optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION)])
    state = make_state(user_message=distinctive_message)
    result = content_optimization_node(state, llm=llm)["content_optimization"]
    assert result["error"] is None
    assert distinctive_message not in llm.calls[0]


# ---------------------------------------------------------------------------
# Key point deduplication
# ---------------------------------------------------------------------------


def test_key_points_deduplicated_case_insensitively():
    llm = FakeLLM(
        [
            optimization_json(
                CLEAN_SUMMARY,
                CLEAN_DESCRIPTION,
                key_points=[
                    "Stanford tracked 200 participants",
                    "stanford tracked 200 participants ",
                    "A new finding about adulthood",
                ],
            )
        ]
    )
    result = content_optimization_node(make_state(), llm=llm)["content_optimization"]
    assert result["key_points"] == ["Stanford tracked 200 participants", "A new finding about adulthood"]


# ---------------------------------------------------------------------------
# Source adapters (spec Section 6) -- direct unit tests, source-agnosticism
# ---------------------------------------------------------------------------


def test_from_pipeline_state_resolves_title_and_content_type_from_top_doc():
    response = make_response()
    knowledge = make_knowledge()
    raw = from_pipeline_state(response, knowledge)
    assert raw["text"] == SOURCE_TEXT
    assert raw["title"] == "Understanding Neuroplasticity"
    assert raw["source_type"] == "rag"
    assert raw["content_type"] == "neuroplasticity_content"


def test_from_pipeline_state_llm_source_has_no_title():
    response = make_response(source="llm")
    knowledge = make_knowledge(source="llm")
    raw = from_pipeline_state(response, knowledge)
    assert raw["title"] is None
    assert raw["source_type"] == "llm"
    assert raw["content_type"] is None


def test_from_markdown_file_parses_frontmatter_title_and_content_type(tmp_path):
    md_file = tmp_path / "occupational-therapy.md"
    md_file.write_text(
        "---\ntitle: Occupational Therapy\ncontent_type: therapy_info\n---\nBody text about OT.\n",
        encoding="utf-8",
    )
    raw = from_markdown_file(md_file)
    assert raw["title"] == "Occupational Therapy"
    assert raw["content_type"] == "therapy_info"
    assert raw["source_type"] == "markdown"
    assert "Body text about OT." in raw["text"]


def test_from_webflow_cms_item_maps_name_and_body():
    item = {"id": "123", "fieldData": {"name": "ADHD Overview", "body": "Some CMS body text."}, "content_type": "blog"}
    raw = from_webflow_cms_item(item)
    assert raw["title"] == "ADHD Overview"
    assert raw["text"] == "Some CMS body text."
    assert raw["source_type"] == "webflow_cms"
    assert raw["content_type"] == "blog"


def test_from_chroma_chunks_joins_content_and_uses_top_doc_metadata():
    docs = [
        make_retrieved_doc(content="First chunk.", chunk_id="c1"),
        make_retrieved_doc(content="Second chunk.", chunk_id="c2"),
    ]
    raw = from_chroma_chunks(docs)
    assert "First chunk." in raw["text"] and "Second chunk." in raw["text"]
    assert raw["title"] == "Understanding Neuroplasticity"
    assert raw["source_type"] == "chromadb"
    assert raw["content_type"] == "neuroplasticity_content"


def test_from_raw_text_uses_caller_declared_fields():
    raw = from_raw_text("hello world", source_type="api", title="T", content_type="blog")
    assert raw == {
        "text": "hello world",
        "title": "T",
        "source_type": "api",
        "content_type": "blog",
        "metadata": {},
    }


def test_source_agnosticism_pipeline_state_and_raw_text_produce_equivalent_shape():
    response = make_response()
    knowledge = make_knowledge()
    raw_from_pipeline = from_pipeline_state(response, knowledge)
    raw_from_text = from_raw_text(SOURCE_TEXT, source_type="mixed_rag_llm")

    llm_a = FakeLLM([optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION)])
    llm_b = FakeLLM([optimization_json(CLEAN_SUMMARY, CLEAN_DESCRIPTION)])
    result_a = optimize_content(raw_from_pipeline, llm=llm_a)
    result_b = optimize_content(raw_from_text, llm=llm_b)

    assert result_a["summary"] == result_b["summary"] == CLEAN_SUMMARY
    assert result_a["source_type"] == "rag"
    assert result_b["source_type"] == "mixed_rag_llm"
