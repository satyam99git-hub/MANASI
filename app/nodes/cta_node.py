import logging
import time
from typing import Optional

from pydantic import BaseModel, model_validator

from app.graph.state import GraphState
from app.services.cta_service import get_cta_url, resolve_cta_key

logger = logging.getLogger("app.nodes.cta_node")


class CTAOutput(BaseModel):
    matched: bool
    cta_key: Optional[str]
    cta_url: Optional[str]
    source_chunk_id: Optional[str]
    lookup_time_ms: float
    error: Optional[str]

    @model_validator(mode="after")
    def _validate_match_consistency(self) -> "CTAOutput":
        if self.matched and (not self.cta_key or not self.cta_url):
            raise ValueError("matched=True requires non-null cta_key and cta_url")
        if not self.matched and self.cta_url is not None:
            raise ValueError("matched=False requires cta_url to be null")
        return self


def cta_node(state: GraphState) -> dict:
    """LangGraph node: resolve a deterministic CTA link for the current turn from
    state["knowledge"]["retrieved_docs"], via the manually maintained CTA registry
    (data/cta/cta_links.md). Makes no LLM call and never modifies the registry file.

    Pure function of state["knowledge"] -> partial state update; does not mutate
    any input. Returns {"cta": {...}}.
    """
    knowledge = state.get("knowledge")
    retrieved_docs = knowledge["retrieved_docs"] if knowledge else []

    start = time.monotonic()
    try:
        cta_key, source_chunk_id = resolve_cta_key(retrieved_docs)
        cta_url = get_cta_url(cta_key) if cta_key else None
        if cta_key and cta_url is None:
            logger.warning("cta_node: cta_key=%r not found in registry", cta_key)
        result = {
            "matched": cta_url is not None,
            "cta_key": cta_key,
            "cta_url": cta_url,
            "source_chunk_id": source_chunk_id,
            "error": None,
            "lookup_time_ms": (time.monotonic() - start) * 1000,
        }
        validated = CTAOutput.model_validate(result).model_dump()
    except Exception as exc:
        logger.error("cta_node_failure: error=%s", exc)
        validated = {
            "matched": False,
            "cta_key": None,
            "cta_url": None,
            "source_chunk_id": None,
            "lookup_time_ms": (time.monotonic() - start) * 1000,
            "error": "cta_lookup_failure",
        }

    logger.info(
        "cta_node ok: matched=%s cta_key=%s elapsed_ms=%.2f",
        validated["matched"],
        validated["cta_key"],
        validated["lookup_time_ms"],
    )
    return {"cta": validated}


def build_cta_graph():
    """Compile a three-node StateGraph (understanding -> knowledge -> cta) for
    Phase 7 isolated testing/deployment."""
    from langgraph.graph import END, START, StateGraph

    from app.nodes.knowledge_node import knowledge_node
    from app.nodes.understanding_node import understanding_node

    graph = StateGraph(GraphState)
    graph.add_node("understanding_node", understanding_node)
    graph.add_node("knowledge_node", knowledge_node)
    graph.add_node("cta_node", cta_node)
    graph.add_edge(START, "understanding_node")
    graph.add_edge("understanding_node", "knowledge_node")
    graph.add_edge("knowledge_node", "cta_node")
    graph.add_edge("cta_node", END)
    return graph.compile()
