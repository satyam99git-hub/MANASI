import logging
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.nodes.cta_node import CTAOutput, cta_node  # noqa: E402
from app.services.cta_loader import CTARecord, reload_cta_data  # noqa: E402
from app.services.cta_service import (  # noqa: E402
    CTAMatch,
    INTENT_CATEGORY_AFFINITY,
    build_cta_response,
    find_cta,
    process,
)


def make_cta_record(
    cta_id="therapies/mnri",
    title="MNRI Therapy CTA",
    category="Therapy",
    cta_type="Specific Therapy",
    status="Active",
    aliases=None,
    trigger_examples=None,
    related_topics=None,
    exclusion_conditions=None,
    do_not_trigger_examples=None,
    output_label="Learn More About MNRI",
    cta_url="https://manascience.webflow.io/post/mnri",
    priority="Specific Therapy - MNRI",
    description="Synthetic test CTA.",
    match_rule_raw="Category = Therapy",
    do_not_trigger_raw="N/A",
    fallback_rule="N/A",
):
    return CTARecord(
        cta_id=cta_id,
        title=title,
        source_path=f"{cta_id}.md",
        category_dir=cta_id.split("/")[0],
        status=status,
        category=category,
        cta_type=cta_type,
        priority=priority,
        match_rule_raw=match_rule_raw,
        match_conditions=[],
        exclusion_conditions=exclusion_conditions if exclusion_conditions is not None else [],
        description=description,
        trigger_examples=trigger_examples if trigger_examples is not None else ["placeholder trigger"],
        trigger_groups={},
        aliases=aliases if aliases is not None else [],
        related_topics=related_topics if related_topics is not None else [],
        do_not_trigger_raw=do_not_trigger_raw,
        do_not_trigger_examples=do_not_trigger_examples if do_not_trigger_examples is not None else [],
        fallback_rule=fallback_rule,
        output_label=output_label,
        cta_url=cta_url,
        extra_fields={},
        raw_text="",
    )


def make_understanding(intent="therapy_information", topic="MNRI", search_query="", emotional_state="curious"):
    return {
        "intent": intent,
        "topic": topic,
        "search_query": search_query,
        "emotional_state": emotional_state,
    }


def make_safety(safe_response="Here is some information."):
    return {
        "safe_response": safe_response,
        "safety_status": "approved",
        "violations_detected": [],
        "escalation_level": "none",
        "disclaimer_added": False,
        "original_final_answer": safe_response,
        "emotional_state": "curious",
        "source": "rag",
        "answer_type": "therapy_information",
        "topic": "MNRI",
        "intent": "therapy_information",
        "confidence": 0.9,
        "grounded_chunk_ids": [],
        "validation_time_ms": 5.0,
        "error": None,
    }


def make_state(user_message="What is MNRI?", understanding=None, safe_response="Here is some information."):
    return {
        "user_message": user_message,
        "chat_history": [],
        "understanding": understanding if understanding is not None else make_understanding(),
        "knowledge": None,
        "response": None,
        "empathy": None,
        "safety": make_safety(safe_response=safe_response),
        "cta": None,
    }


def test_specific_therapy_match_mnri(monkeypatch):
    mnri = make_cta_record(cta_id="therapies/mnri", category="Therapy", cta_type="Specific Therapy", aliases=["MNRI"])
    general = make_cta_record(
        cta_id="therapies/general",
        category="Therapy",
        cta_type="Library",
        trigger_examples=["What therapies are available?"],
        output_label="Explore the Therapy Library",
        cta_url="https://manascience.webflow.io/therapy-library",
    )
    monkeypatch.setattr("app.services.cta_service.get_ctas_by_status", lambda status: [mnri, general])
    state = make_state(
        user_message="What is MNRI?",
        understanding=make_understanding(intent="therapy_information", topic="MNRI"),
    )
    result = cta_node(state)["cta"]
    assert result["cta_found"] is True
    assert result["cta_id"] == "therapies/mnri"
    assert result["match_reason"] == "specific_match"


def test_specific_condition_match_adhd(monkeypatch):
    adhd = make_cta_record(
        cta_id="conditions/adhd",
        category="Condition",
        cta_type="Individual Condition",
        aliases=["ADHD"],
        output_label="Learn More About ADHD",
        cta_url="https://manascience.webflow.io/post/adhd",
        priority="Specific Condition",
    )
    general = make_cta_record(
        cta_id="conditions/general",
        category="Condition",
        cta_type="Library",
        trigger_examples=["What conditions does ManaScience cover?"],
        output_label="Explore Conditions",
        cta_url="https://manascience.webflow.io/conditions",
        priority="General Condition",
    )
    monkeypatch.setattr("app.services.cta_service.get_ctas_by_status", lambda status: [adhd, general])
    state = make_state(
        user_message="What is ADHD?",
        understanding=make_understanding(intent="personal_concern", topic="ADHD"),
    )
    result = cta_node(state)["cta"]
    assert result["cta_found"] is True
    assert result["cta_id"] == "conditions/adhd"


def test_general_therapy_library_direct_match():
    mnri = make_cta_record(cta_id="therapies/mnri", aliases=["MNRI"])
    general = make_cta_record(
        cta_id="therapies/general", cta_type="Library", trigger_examples=["What therapies are available?"]
    )
    match = find_cta(
        "What therapies are available?",
        make_understanding(intent="therapy_information", topic=""),
        [mnri, general],
    )
    assert match.cta.cta_id == "therapies/general"
    assert match.match_reason == "specific_match"


def test_intent_driven_category_fallback_when_nothing_matches():
    mnri = make_cta_record(cta_id="therapies/mnri", aliases=["MNRI"])
    general = make_cta_record(
        cta_id="therapies/general", cta_type="Library", trigger_examples=["What therapies are available?"]
    )
    understanding = make_understanding(intent="therapy_information", topic="")
    match = find_cta("hmm, not sure what to ask", understanding, [mnri, general])
    assert match is not None
    assert match.cta.cta_id == "therapies/general"
    assert match.match_reason == "category_fallback"
    assert match.matched_phrase is None


def test_no_match_when_nothing_scores_and_no_intent_affinity():
    mnri = make_cta_record(cta_id="therapies/mnri", aliases=["MNRI"])
    general = make_cta_record(
        cta_id="therapies/general", cta_type="Library", trigger_examples=["What therapies are available?"]
    )
    understanding = make_understanding(intent="general_chat", topic="")
    match = find_cta("hmm, not sure what to ask", understanding, [mnri, general])
    assert match is None


def test_no_match_returns_none_not_empty_list():
    match = find_cta(
        "hmm, not sure what to ask", make_understanding(intent="general_chat", topic=""), [make_cta_record()]
    )
    assert match is None
    assert not isinstance(match, list)


def test_exclusion_blocks_an_otherwise_matching_cta():
    record = make_cta_record(
        cta_id="therapies/mnri",
        trigger_examples=["What is MNRI therapy?"],
        do_not_trigger_examples=["What is MNRI therapy?"],
    )
    understanding = make_understanding(intent="general_chat", topic="")
    match = find_cta("What is MNRI therapy?", understanding, [record])
    assert match is None


def test_exclusion_also_blocks_the_category_fallback_path():
    general = make_cta_record(
        cta_id="therapies/general",
        category="Therapy",
        cta_type="Library",
        trigger_examples=["What therapies are available?"],
        do_not_trigger_examples=["something unrelated to therapy"],
    )
    understanding = make_understanding(intent="therapy_information", topic="")
    match = find_cta("something unrelated to therapy", understanding, [general])
    assert match is None


def test_specific_beats_general_in_same_category():
    mnri = make_cta_record(cta_id="therapies/mnri", category="Therapy", cta_type="Specific Therapy", aliases=["MNRI"])
    general = make_cta_record(
        cta_id="therapies/general",
        category="Therapy",
        cta_type="Library",
        trigger_examples=["tell me about mnri and other therapies"],
    )
    understanding = make_understanding(intent="therapy_information", topic="")
    match = find_cta("tell me about mnri and other therapies", understanding, [mnri, general])
    assert match.cta.cta_id == "therapies/mnri"


def test_alias_outranks_trigger_example_tier():
    a = make_cta_record(
        cta_id="therapies/a", category="Therapy", cta_type="Individual Therapy",
        related_topics=["reflex integration"],
    )
    b = make_cta_record(
        cta_id="therapies/b", category="Therapy", cta_type="Individual Therapy",
        aliases=["reflex integration"],
    )
    understanding = make_understanding(intent="therapy_information", topic="")
    match = find_cta("tell me about reflex integration", understanding, [a, b])
    assert match.cta.cta_id == "therapies/b"


def test_cross_category_tie_broken_by_intent_affinity():
    affinity_category = INTENT_CATEGORY_AFFINITY["therapy_information"]
    therapy_record = make_cta_record(
        cta_id="therapies/x", category=affinity_category, cta_type="Individual Therapy", aliases=["zzzmatch"]
    )
    condition_record = make_cta_record(
        cta_id="conditions/y", category="Condition", cta_type="Individual Condition", aliases=["zzzmatch"]
    )
    understanding = make_understanding(intent="therapy_information", topic="")
    match = find_cta("zzzmatch", understanding, [therapy_record, condition_record])
    assert match.cta.cta_id == "therapies/x"


def test_cross_category_genuine_tie_logged_and_deterministic(caplog):
    record_a = make_cta_record(cta_id="about/x", category="About", cta_type="Information Page", aliases=["zzzmatch"])
    record_b = make_cta_record(
        cta_id="community/y", category="Community", cta_type="Information Page", aliases=["zzzmatch"]
    )
    understanding = make_understanding(intent="general_chat", topic="")
    with caplog.at_level(logging.WARNING):
        match1 = find_cta("zzzmatch", understanding, [record_a, record_b])
        match2 = find_cta("zzzmatch", understanding, [record_a, record_b])
    assert match1.cta.cta_id == match2.cta.cta_id == "community/y"
    assert any("category tie broken" in r.message for r in caplog.records)


def test_duplicate_same_score_candidates_tie_broken_by_cta_id(caplog):
    record_a = make_cta_record(
        cta_id="therapies/zzz", category="Therapy", cta_type="Individual Therapy", aliases=["zzzmatch"]
    )
    record_b = make_cta_record(
        cta_id="therapies/aaa", category="Therapy", cta_type="Individual Therapy", aliases=["zzzmatch"]
    )
    understanding = make_understanding(intent="therapy_information", topic="")
    with caplog.at_level(logging.WARNING):
        match1 = find_cta("zzzmatch", understanding, [record_a, record_b])
        match2 = find_cta("zzzmatch", understanding, [record_a, record_b])
    assert match1.cta.cta_id == match2.cta.cta_id == "therapies/aaa"
    assert any("tie broken by cta_id" in r.message for r in caplog.records)


def test_empty_active_ctas_returns_no_match(monkeypatch, caplog):
    monkeypatch.setattr("app.services.cta_service.get_ctas_by_status", lambda status: [])
    with caplog.at_level(logging.WARNING):
        result = process("anything", make_understanding(), "some response")
    assert result["cta_found"] is False
    assert any("zero active CTAs" in r.message for r in caplog.records)


def test_unexpected_service_exception_falls_back_safely(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.nodes.cta_node.process", boom)
    state = make_state(safe_response="original response")
    result = cta_node(state)["cta"]
    assert result["cta_found"] is False
    assert result["error"] == "cta_lookup_failure"
    assert result["response"] == "original response"


def test_empty_safe_response_passed_through_unchanged(monkeypatch):
    mnri = make_cta_record(cta_id="therapies/mnri", aliases=["MNRI"])
    monkeypatch.setattr("app.services.cta_service.get_ctas_by_status", lambda status: [mnri])

    state_match = make_state(
        user_message="What is MNRI?",
        understanding=make_understanding(intent="therapy_information", topic="MNRI"),
        safe_response="",
    )
    assert cta_node(state_match)["cta"]["response"] == ""

    state_no_match = make_state(
        user_message="zzz unrelated", understanding=make_understanding(intent="general_chat", topic=""), safe_response=""
    )
    assert cta_node(state_no_match)["cta"]["response"] == ""

    monkeypatch.setattr("app.nodes.cta_node.process", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    state_exc = make_state(safe_response="")
    assert cta_node(state_exc)["cta"]["response"] == ""


def test_response_is_never_modified(monkeypatch):
    mnri = make_cta_record(cta_id="therapies/mnri", aliases=["MNRI"])
    monkeypatch.setattr("app.services.cta_service.get_ctas_by_status", lambda status: [mnri])
    safe_response = "Here is some detailed information about reflex integration."

    state_match = make_state(
        user_message="What is MNRI?",
        understanding=make_understanding(intent="therapy_information", topic="MNRI"),
        safe_response=safe_response,
    )
    assert cta_node(state_match)["cta"]["response"] == safe_response

    state_no_match = make_state(
        user_message="zzz unrelated",
        understanding=make_understanding(intent="general_chat", topic=""),
        safe_response=safe_response,
    )
    assert cta_node(state_no_match)["cta"]["response"] == safe_response


def test_topic_alone_can_trigger_a_match():
    mnri = make_cta_record(cta_id="therapies/mnri", aliases=["MNRI"])
    understanding = make_understanding(intent="therapy_information", topic="MNRI")
    match = find_cta("tell me more about it", understanding, [mnri])
    assert match is not None
    assert match.cta.cta_id == "therapies/mnri"


def test_missing_topic_key_does_not_raise():
    mnri = make_cta_record(cta_id="therapies/mnri", trigger_examples=["What is MNRI?"])
    understanding = {"intent": "therapy_information"}
    match = find_cta("What is MNRI?", understanding, [mnri])
    assert match is not None
    assert match.cta.cta_id == "therapies/mnri"


def test_build_cta_response_minimal_shape_on_match():
    record = make_cta_record(
        cta_id="therapies/mnri",
        output_label="Learn More About MNRI",
        cta_url="https://manascience.webflow.io/post/mnri",
        category="Therapy",
    )
    match = CTAMatch(cta=record, match_reason="specific_match", matched_phrase="mnri")
    result = build_cta_response("some response", match)
    assert set(result.keys()) == {"response", "cta_found", "cta"}
    assert result["cta_found"] is True
    assert set(result["cta"].keys()) == {"url", "trigger", "category"}
    assert result["cta"]["trigger"] == record.output_label
    assert result["cta"]["url"] == record.cta_url
    assert result["cta"]["category"] == record.category


def test_build_cta_response_minimal_shape_on_no_match():
    result = build_cta_response("some response", None)
    assert result == {"response": "some response", "cta_found": False, "cta": None}


def test_performance_under_corpus_growth():
    categories = ["Therapy", "Condition", "About", "Community", "Courses", "FAQ"]
    records = [
        make_cta_record(
            cta_id=f"synthetic/{i}",
            category=categories[i % len(categories)],
            cta_type="Library" if i % 5 == 0 else "Individual Therapy",
            trigger_examples=[f"synthetic trigger phrase number {i}"],
        )
        for i in range(200)
    ]
    understanding = make_understanding(intent="therapy_information", topic="")
    start = time.monotonic()
    find_cta("synthetic trigger phrase number 150", understanding, records)
    elapsed = time.monotonic() - start
    assert elapsed < 0.05


def test_real_corpus_smoke_no_crash_no_unexpected_match():
    # Forces a fresh scan of the real settings.cta_data_dir, undoing any global
    # cache pollution left by test_cta_loader.py's own reload_cta_data() calls
    # against a synthetic tmp_path corpus (monkeypatch reverts settings.cta_data_dir
    # itself, but not the loader's already-populated module-level cache).
    reload_cta_data()
    cases = [
        ("What is MNRI?", "therapy_information", "MNRI", "therapies/mnri"),
        ("What therapies are available?", "therapy_information", "", "therapies/general"),
        ("What is ADHD?", "personal_concern", "ADHD", "conditions/adhd"),
        ("asdkjasdkj random text", "general_chat", "", None),
    ]
    for message, intent, topic, expected_cta_id in cases:
        understanding = make_understanding(intent=intent, topic=topic)
        state = make_state(user_message=message, understanding=understanding)
        result = cta_node(state)["cta"]
        if expected_cta_id is None:
            assert result["cta_found"] is False
        else:
            assert result["cta_id"] == expected_cta_id


def test_cta_output_rejects_found_true_with_missing_cta_id():
    with pytest.raises(ValidationError):
        CTAOutput(
            cta_found=True,
            cta_id=None,
            cta_url="https://example.com",
            cta_trigger="Learn More",
            cta_category="Therapy",
            match_reason="specific_match",
            matched_phrase="x",
            response="r",
            lookup_time_ms=1.0,
            error=None,
        )


def test_cta_output_rejects_found_true_with_no_match_reason():
    with pytest.raises(ValidationError):
        CTAOutput(
            cta_found=True,
            cta_id="therapies/mnri",
            cta_url="https://example.com",
            cta_trigger="Learn More",
            cta_category="Therapy",
            match_reason="no_match",
            matched_phrase="x",
            response="r",
            lookup_time_ms=1.0,
            error=None,
        )


def test_cta_output_rejects_found_false_with_nonnull_cta_id():
    with pytest.raises(ValidationError):
        CTAOutput(
            cta_found=False,
            cta_id="therapies/mnri",
            cta_url=None,
            cta_trigger=None,
            cta_category=None,
            match_reason="no_match",
            matched_phrase=None,
            response="r",
            lookup_time_ms=1.0,
            error=None,
        )


def test_cta_output_rejects_found_false_with_wrong_match_reason():
    with pytest.raises(ValidationError):
        CTAOutput(
            cta_found=False,
            cta_id=None,
            cta_url=None,
            cta_trigger=None,
            cta_category=None,
            match_reason="specific_match",
            matched_phrase=None,
            response="r",
            lookup_time_ms=1.0,
            error=None,
        )
