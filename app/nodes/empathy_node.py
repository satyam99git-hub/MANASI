import logging
import time
from typing import Any, Literal, Optional

from pydantic import BaseModel, model_validator

from app.graph.state import GraphState
from app.services.empathy_service import EMPATHY_BANNED_PHRASES, humanize_response

logger = logging.getLogger("app.nodes.empathy_node")

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


class EmpathyOutput(BaseModel):
    final_answer: str
    emotional_state: Literal[
        "neutral", "curious", "confused", "worried", "overwhelmed", "frustrated"
    ]
    source: Literal["rag", "llm"]
    answer_type: ANSWER_TYPES
    topic: str
    intent: str
    confidence: float
    grounded_chunk_ids: list[str]
    humanization_time_ms: float
    error: Optional[str]

    @model_validator(mode="after")
    def _validate_final_answer_quality(self) -> "EmpathyOutput":
        stripped = self.final_answer.strip()
        if not stripped:
            raise ValueError("final_answer must not be empty")
        lowered = stripped.lower()
        if any(phrase in lowered for phrase in EMPATHY_BANNED_PHRASES):
            raise ValueError("final_answer contains an identity-violation phrase")
        return self


def empathy_node(state: GraphState, llm: Optional[Any] = None) -> dict:
    """LangGraph node: humanize state["content_optimization"]["summary"] per
    state["understanding"]["emotional_state"].

    Pure function of (state["content_optimization"], state["understanding"]["emotional_state"])
    -> partial state update; does not mutate any input. Returns {"empathy": {...}}.
    Does not read user_message, chat_history, or knowledge.
    """
    content_optimization = state["content_optimization"]
    emotional_state = state["understanding"]["emotional_state"]

    start = time.monotonic()
    try:
        result = humanize_response(content_optimization, emotional_state, llm=llm)
        result["humanization_time_ms"] = (time.monotonic() - start) * 1000
        validated = EmpathyOutput.model_validate(result).model_dump()
    except Exception as exc:
        logger.error("empathy_node_failure: topic=%r error=%s", content_optimization.get("topic"), exc)
        validated = _safe_fallback_result(content_optimization, emotional_state, start)

    logger.info(
        "empathy_node ok: emotional_state=%s answer_type=%s error=%s elapsed_ms=%.1f",
        validated["emotional_state"],
        validated["answer_type"],
        validated["error"],
        validated["humanization_time_ms"],
    )
    return {"empathy": validated}


def _safe_fallback_result(content_optimization: dict, emotional_state: str, start: float) -> dict:
    """Hand-built result that is correct by construction -- bypasses EmpathyOutput
    validation entirely so this path cannot itself raise."""
    return {
        "final_answer": content_optimization.get("summary", ""),
        "emotional_state": emotional_state,
        "source": content_optimization.get("source", "llm"),
        "answer_type": content_optimization.get("answer_type", "general_knowledge"),
        "topic": content_optimization.get("topic", ""),
        "intent": content_optimization.get("intent", "general_chat"),
        "confidence": content_optimization.get("confidence", 0.0),
        "grounded_chunk_ids": content_optimization.get("grounded_chunk_ids", []),
        "humanization_time_ms": (time.monotonic() - start) * 1000,
        "error": "llm_call_failure",
    }


def build_empathy_graph():
    """Compile a five-node StateGraph (understanding -> knowledge -> response ->
    content_optimization -> empathy) for Phase 4/6 isolated testing/deployment."""
    from langgraph.graph import END, START, StateGraph

    from app.nodes.content_optimization_node import content_optimization_node
    from app.nodes.knowledge_node import knowledge_node
    from app.nodes.response_node import response_node
    from app.nodes.understanding_node import understanding_node

    graph = StateGraph(GraphState)
    graph.add_node("understanding_node", understanding_node)
    graph.add_node("knowledge_node", knowledge_node)
    graph.add_node("response_node", response_node)
    graph.add_node("content_optimization_node", content_optimization_node)
    graph.add_node("empathy_node", empathy_node)
    graph.add_edge(START, "understanding_node")
    graph.add_edge("understanding_node", "knowledge_node")
    graph.add_edge("knowledge_node", "response_node")
    graph.add_edge("response_node", "content_optimization_node")
    graph.add_edge("content_optimization_node", "empathy_node")
    graph.add_edge("empathy_node", END)
    return graph.compile()
