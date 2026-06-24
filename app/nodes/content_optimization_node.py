import logging
import time
from typing import Any, Literal, Optional

from pydantic import BaseModel, model_validator

from app.graph.state import GraphState
from app.services.content_optimization_service import (
    CONTENT_OPTIMIZATION_BANNED_PHRASES,
    from_pipeline_state,
    optimize_content,
)

logger = logging.getLogger("app.nodes.content_optimization_node")

CONTENT_TYPES = Literal[
    "course",
    "blog",
    "research_article",
    "faq",
    "practitioner_info",
    "therapy_info",
    "website_content",
    "neuroplasticity_content",
    "pdf_document",
    "llm_generated",
    "mixed",
]

SOURCE_TYPES = Literal["rag", "llm", "mixed_rag_llm", "markdown", "webflow_cms", "chromadb", "api"]

ANSWER_TYPES = Literal[
    "concept_explanation",
    "therapy_information",
    "course_information",
    "research_summary",
    "website_information",
    "personal_guidance",
    "supportive_information",
    "general_knowledge",
]


class ContentOptimizationOutput(BaseModel):
    title: Optional[str]
    summary: str
    description: str
    key_points: list[str]
    content_type: CONTENT_TYPES
    source_type: SOURCE_TYPES
    confidence_score: float
    source: Literal["rag", "llm"]
    answer_type: ANSWER_TYPES
    topic: str
    intent: str
    confidence: float
    grounded_chunk_ids: list[str]
    original_answer: str
    optimization_time_ms: float
    error: Optional[str]

    @model_validator(mode="after")
    def _validate_summary_quality(self) -> "ContentOptimizationOutput":
        stripped = self.summary.strip()
        if not stripped:
            raise ValueError("summary must not be empty")
        lowered = stripped.lower()
        if any(phrase in lowered for phrase in CONTENT_OPTIMIZATION_BANNED_PHRASES):
            raise ValueError("summary contains a refusal-style phrase")
        return self

    @model_validator(mode="after")
    def _validate_key_points_shape(self) -> "ContentOptimizationOutput":
        if len(self.key_points) > 7:
            raise ValueError("key_points exceeds content_optimization_key_points_max")
        if any(not point.strip() for point in self.key_points):
            raise ValueError("key_points must not contain empty strings")
        return self

    @model_validator(mode="after")
    def _validate_confidence_score_bounds(self) -> "ContentOptimizationOutput":
        if not (0.0 <= self.confidence_score <= 1.0):
            raise ValueError("confidence_score out of bounds")
        return self


def content_optimization_node(state: GraphState, llm: Optional[Any] = None) -> dict:
    """LangGraph node: normalize and compress state["response"]["answer"] (plus
    state["knowledge"]["retrieved_docs"] for title/content_type derivation) into a
    structured title/summary/description/key_points payload for state["empathy"].

    Pure function of (state["response"], state["knowledge"]) -> partial state
    update; does not mutate any input. Returns {"content_optimization": {...}}.
    Does not read user_message, chat_history, or understanding.
    """
    response = state["response"]
    knowledge = state["knowledge"]
    raw = from_pipeline_state(response, knowledge)

    start = time.monotonic()
    try:
        result = optimize_content(raw, llm=llm, force_skip=response.get("error") is not None)
        full_result = {
            **result,
            "source": response["source"],
            "answer_type": response["answer_type"],
            "topic": response["topic"],
            "intent": response["intent"],
            "confidence": response["confidence"],
            "grounded_chunk_ids": response["grounded_chunk_ids"],
            "original_answer": response["answer"],
        }
        full_result["optimization_time_ms"] = (time.monotonic() - start) * 1000
        validated = ContentOptimizationOutput.model_validate(full_result).model_dump()
    except Exception as exc:
        logger.error("content_optimization_node_failure: topic=%r error=%s", response.get("topic"), exc)
        validated = _safe_fallback_result(response, knowledge, start)

    logger.info(
        "content_optimization_node ok: source_type=%s content_type=%s confidence_score=%.2f error=%s elapsed_ms=%.1f",
        validated["source_type"],
        validated["content_type"],
        validated["confidence_score"],
        validated["error"],
        validated["optimization_time_ms"],
    )
    return {"content_optimization": validated}


def _safe_fallback_result(response: dict, knowledge: dict, start: float) -> dict:
    """Hand-built result that is correct by construction -- bypasses
    ContentOptimizationOutput validation entirely so this path cannot itself raise."""
    docs = (knowledge or {}).get("retrieved_docs") or []
    top_doc = docs[0] if response.get("source") == "rag" and docs else None
    text = response.get("answer", "").strip()
    return {
        "title": top_doc["source_title"] if top_doc else None,
        "summary": text,
        "description": text,
        "key_points": [],
        "content_type": (top_doc["content_type"] if top_doc else None) or "llm_generated",
        "source_type": response.get("source", "llm"),
        "confidence_score": 0.3,
        "source": response.get("source", "llm"),
        "answer_type": response.get("answer_type", "general_knowledge"),
        "topic": response.get("topic", ""),
        "intent": response.get("intent", "general_chat"),
        "confidence": response.get("confidence", 0.0),
        "grounded_chunk_ids": response.get("grounded_chunk_ids", []),
        "original_answer": response.get("answer", ""),
        "optimization_time_ms": (time.monotonic() - start) * 1000,
        "error": "llm_call_failure",
    }


def build_content_optimization_graph():
    """Compile a five-node StateGraph (understanding -> knowledge -> response ->
    content_optimization -> empathy) for Phase 6 isolated testing/deployment."""
    from langgraph.graph import END, START, StateGraph

    from app.nodes.cta_node import cta_node
    from app.nodes.empathy_node import empathy_node
    from app.nodes.knowledge_node import knowledge_node
    from app.nodes.response_node import response_node
    from app.nodes.understanding_node import understanding_node

    graph = StateGraph(GraphState)
    graph.add_node("understanding_node", understanding_node)
    graph.add_node("knowledge_node", knowledge_node)
    graph.add_node("cta_node", cta_node)
    graph.add_node("response_node", response_node)
    graph.add_node("content_optimization_node", content_optimization_node)
    graph.add_node("empathy_node", empathy_node)
    graph.add_edge(START, "understanding_node")
    graph.add_edge("understanding_node", "knowledge_node")
    graph.add_edge("knowledge_node", "cta_node")
    graph.add_edge("cta_node", "response_node")
    graph.add_edge("response_node", "content_optimization_node")
    graph.add_edge("content_optimization_node", "empathy_node")
    graph.add_edge("empathy_node", END)
    return graph.compile()
