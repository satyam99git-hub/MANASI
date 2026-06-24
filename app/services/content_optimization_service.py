import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional, TypedDict

from app.config import settings

logger = logging.getLogger("app.services.content_optimization_service")

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "content_optimization_prompt.txt"
_PROMPT_TEMPLATE = PROMPT_PATH.read_text(encoding="utf-8")

SOURCE_TYPES = Literal[
    "rag", "llm", "mixed_rag_llm", "markdown", "webflow_cms", "chromadb", "api",
]


class RawContentInput(TypedDict):
    text: str
    title: Optional[str]
    source_type: SOURCE_TYPES
    content_type: Optional[str]
    metadata: dict


CONTENT_OPTIMIZATION_BANNED_PHRASES = [
    "i cannot summarize", "i can't summarize", "no content was provided",
    "there is nothing to summarize", "unable to generate a summary",
    "i don't have enough content", "no information to extract",
]

TITLE_INSTRUCTIONS_KNOWN = (
    'A title is already known for this content: "{title}". Use it exactly '
    "as given -- do not propose a different one."
)
TITLE_INSTRUCTIONS_UNKNOWN = (
    "No title is known for this content. If a short (1-8 word) title "
    "naturally fits the content, propose one in the \"title\" field. If "
    "nothing fits naturally, return null for \"title\" -- do not force one."
)

CORRECTIVE_REPROMPT_SUFFIX_FABRICATION = (
    "\n\nYour previous summary included a name, number, or detail that was "
    "not in the source content. Rewrite it using only information actually "
    "present in the source. Try again."
)
CORRECTIVE_REPROMPT_SUFFIX_FACT_DROP = (
    "\n\nYour previous summary lost most of the source's specific facts. "
    "Rewrite it to retain the key names, numbers, and details. Try again."
)
CORRECTIVE_REPROMPT_SUFFIX_LENGTH = (
    "\n\nYour previous summary or description was outside the required "
    "length range. Rewrite it to fit within the specified word counts. "
    "Try again."
)
CORRECTIVE_REPROMPT_SUFFIX_REFUSAL = (
    "\n\nYour previous output declined to summarize instead of producing "
    "one. You must produce a real summary from the content given -- it is "
    "never empty of source content. Try again."
)
CORRECTIVE_REPROMPT_SUFFIX_MALFORMED = (
    "\n\nYour previous output was not valid JSON matching the required "
    'schema. Return ONLY a valid JSON object with "title", "summary", '
    '"description", and "key_points" fields.'
)


@dataclass
class _Attempt:
    attempt_index: int
    title: Optional[str]
    summary: str
    description: str
    key_points: list[str]
    violation_count: int
    fact_retention_ratio: float
    hit_length_cap: bool


# ---------------------------------------------------------------------------
# Source adapters (spec Section 6) -- normalize any source into RawContentInput.
# Only `from_pipeline_state` is wired into the live graph (content_optimization_node.py);
# the rest exist for offline/batch callers (ingestion jobs, future CMS/API syncs).
# The optimization engine below never branches on source -- it only ever reads
# text/title/source_type/content_type off the normalized RawContentInput.
# ---------------------------------------------------------------------------


def from_pipeline_state(response: dict, knowledge: dict) -> RawContentInput:
    docs = knowledge.get("retrieved_docs") or [] if knowledge else []
    top_doc = docs[0] if response["source"] == "rag" and docs else None
    return {
        "text": response["answer"],
        "title": top_doc["source_title"] if top_doc else None,
        "source_type": response["source"],
        "content_type": top_doc["content_type"] if top_doc else None,
        "metadata": {"grounded_chunk_ids": response.get("grounded_chunk_ids", [])},
    }


def from_markdown_file(path: Path) -> RawContentInput:
    raw_text = path.read_text(encoding="utf-8")
    title: Optional[str] = None
    content_type: Optional[str] = None
    body = raw_text
    if raw_text.startswith("---"):
        end = raw_text.find("---", 3)
        if end != -1:
            frontmatter = raw_text[3:end]
            body = raw_text[end + 3 :].strip()
            for line in frontmatter.splitlines():
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                key = key.strip().lower()
                value = value.strip().strip("\"'")
                if key == "title":
                    title = value or None
                elif key == "content_type":
                    content_type = value or None
    return {
        "text": body,
        "title": title,
        "source_type": "markdown",
        "content_type": content_type,
        "metadata": {"path": str(path)},
    }


def from_webflow_cms_item(item: dict) -> RawContentInput:
    field_data = item.get("fieldData", {})
    title = field_data.get("name") or field_data.get("title")
    return {
        "text": field_data.get("body") or field_data.get("content") or "",
        "title": title,
        "source_type": "webflow_cms",
        "content_type": item.get("content_type"),
        "metadata": {"item_id": item.get("id")},
    }


def from_chroma_chunks(docs: list[dict]) -> RawContentInput:
    text = "\n\n".join(doc["content"] for doc in docs)
    top_doc = docs[0] if docs else None
    return {
        "text": text,
        "title": top_doc["source_title"] if top_doc else None,
        "source_type": "chromadb",
        "content_type": top_doc["content_type"] if top_doc else None,
        "metadata": {"chunk_ids": [doc.get("chunk_id") for doc in docs]},
    }


def from_raw_text(
    text: str,
    *,
    source_type: SOURCE_TYPES,
    title: Optional[str] = None,
    content_type: Optional[str] = None,
) -> RawContentInput:
    return {
        "text": text,
        "title": title,
        "source_type": source_type,
        "content_type": content_type,
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# Guards (spec Section 7.4-7.6) -- mechanical, auditable, no LLM self-report.
# ---------------------------------------------------------------------------


def _significant_tokens(text: str) -> set[str]:
    """Numbers/percentages and capitalized multi-letter words, lowercased -- a
    deliberately coarse proxy for 'load-bearing facts' (same technique as
    empathy_service._significant_tokens). Whitespace is collapsed first so a
    paragraph break (e.g. "...life. \nThis...") is treated the same as a normal
    sentence boundary ("...life. This...") -- otherwise every word starting a
    new paragraph would be spuriously flagged as a 'fact' the next rewrite
    'dropped', since multi-paragraph content (this node's specialty, per
    Section 6) is exactly what `from_chroma_chunks` and live Phase 3 answers
    routinely contain."""
    normalized = re.sub(r"\s+", " ", text)
    numbers = re.findall(r"\d+(?:\.\d+)?%?", normalized)
    proper_nouns = re.findall(r"(?<!^)(?<!\. )\b[A-Z][a-zA-Z]{3,}\b", normalized)
    return {tok.lower() for tok in numbers + proper_nouns}


def _fact_retention_ratio(source_text: str, summary: str) -> float:
    source_tokens = _significant_tokens(source_text)
    if not source_tokens:
        return 1.0
    retained = source_tokens & _significant_tokens(summary)
    return len(retained) / len(source_tokens)


def _fails_fact_retention(source_text: str, summary: str) -> bool:
    return _fact_retention_ratio(source_text, summary) < settings.content_optimization_fact_retention_min_ratio


def _fabrication_ratio(source_text: str, summary: str) -> float:
    summary_tokens = _significant_tokens(summary)
    if not summary_tokens:
        return 0.0
    fabricated = summary_tokens - _significant_tokens(source_text)
    return len(fabricated) / len(summary_tokens)


def _fails_fabrication(source_text: str, summary: str) -> bool:
    return _fabrication_ratio(source_text, summary) > settings.content_optimization_fabrication_max_ratio


def _word_count(text: str) -> int:
    return len(text.split())


def _target_bounds(original_word_count: int, floor: int, ceiling: int) -> tuple[int, int]:
    """Never asks the model to pad content that started shorter than the floor."""
    effective_floor = min(original_word_count, floor)
    return effective_floor, ceiling


def _fails_length_bounds(summary: str, description: str, original_word_count: int) -> bool:
    summary_floor, summary_ceiling = _target_bounds(
        original_word_count,
        settings.content_optimization_summary_min_words,
        settings.content_optimization_summary_max_words,
    )
    description_floor, description_ceiling = _target_bounds(
        original_word_count,
        settings.content_optimization_description_min_words,
        settings.content_optimization_description_max_words,
    )
    return not (
        summary_floor <= _word_count(summary) <= summary_ceiling
        and description_floor <= _word_count(description) <= description_ceiling
    )


def _hit_length_cap(summary: str, original_word_count: int) -> bool:
    """True when the source was long enough that the ceiling was the binding
    constraint -- a signal the model hard-truncated rather than cleanly
    summarized (used only by confidence scoring, Section 10.2)."""
    _, ceiling = _target_bounds(
        original_word_count,
        settings.content_optimization_summary_min_words,
        settings.content_optimization_summary_max_words,
    )
    return original_word_count > ceiling and _word_count(summary) >= ceiling


def _contains_banned_phrase(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in CONTENT_OPTIMIZATION_BANNED_PHRASES)


def _is_valid_title_candidate(candidate: str, body_text: str) -> bool:
    words = candidate.strip().split()
    if not (1 <= len(words) <= 8):
        return False
    candidate_tokens = _significant_tokens(candidate)
    body_tokens = _significant_tokens(body_text)
    return not candidate_tokens or bool(candidate_tokens & body_tokens)


def _resolve_title(raw: RawContentInput, llm_candidate: Optional[str], body_text: str) -> Optional[str]:
    if raw["title"]:
        return raw["title"].strip()
    if llm_candidate and _is_valid_title_candidate(llm_candidate, body_text):
        return llm_candidate.strip()
    return None


def _dedupe_key_points(key_points: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for point in key_points:
        stripped = point.strip()
        if not stripped:
            continue
        key = stripped.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(stripped)
    return deduped


def _count_violations(source_text: str, summary: str, description: str, original_word_count: int) -> int:
    return (
        int(_fails_fabrication(source_text, summary))
        + int(_fails_fact_retention(source_text, summary))
        + int(_fails_length_bounds(summary, description, original_word_count))
        + int(_contains_banned_phrase(summary))
    )


def _corrective_suffix_for(source_text: str, summary: str, description: str, original_word_count: int) -> str:
    suffixes = []
    if _fails_fabrication(source_text, summary):
        suffixes.append(CORRECTIVE_REPROMPT_SUFFIX_FABRICATION)
    if _fails_fact_retention(source_text, summary):
        suffixes.append(CORRECTIVE_REPROMPT_SUFFIX_FACT_DROP)
    if _fails_length_bounds(summary, description, original_word_count):
        suffixes.append(CORRECTIVE_REPROMPT_SUFFIX_LENGTH)
    if _contains_banned_phrase(summary):
        suffixes.append(CORRECTIVE_REPROMPT_SUFFIX_REFUSAL)
    return "".join(suffixes)


# ---------------------------------------------------------------------------
# Confidence scoring (spec Section 10.2) -- deterministic, never LLM self-reported.
# Distinct from the pipeline's pre-existing `confidence` (RAG-grounding) field.
# ---------------------------------------------------------------------------


def _confidence_score(
    *,
    source_type: str,
    used_llm_call: bool,
    retries_used: int,
    fact_retention_ratio: float,
    hit_length_cap: bool,
) -> float:
    score = 1.0
    if source_type == "llm":
        score -= 0.3
    if used_llm_call:
        score -= 0.1 * retries_used
        score -= max(0.0, 0.9 - fact_retention_ratio) * 0.5
    if hit_length_cap:
        score -= 0.1
    return round(max(0.1, min(1.0, score)), 2)


# ---------------------------------------------------------------------------
# Skip path and never-block fallback (spec Sections 7.1, 11.8).
# ---------------------------------------------------------------------------


def _skip_result(raw: RawContentInput) -> dict:
    text = raw["text"].strip()
    return {
        "title": raw["title"],
        "summary": text,
        "description": text,
        "key_points": [],
        "content_type": raw["content_type"] or "llm_generated",
        "source_type": raw["source_type"],
        "confidence_score": 0.95,
        "error": None,
    }


def _select_fallback(raw: RawContentInput, llm_call_failed: bool) -> dict:
    """Hand-built result that is correct by construction -- bypasses
    ContentOptimizationOutput validation entirely so this path cannot itself fail."""
    text = raw["text"].strip()
    error_code = "llm_call_failure" if llm_call_failed else "quality_guard_exhausted"
    return {
        "title": raw["title"],
        "summary": text,
        "description": text,
        "key_points": [],
        "content_type": raw["content_type"] or "llm_generated",
        "source_type": raw["source_type"],
        "confidence_score": 0.3,
        "error": error_code,
    }


# ---------------------------------------------------------------------------
# Prompt + LLM plumbing.
# ---------------------------------------------------------------------------


def _build_prompt(raw: RawContentInput, extra_suffix: str = "") -> str:
    original_word_count = _word_count(raw["text"])
    summary_min, summary_max = _target_bounds(
        original_word_count,
        settings.content_optimization_summary_min_words,
        settings.content_optimization_summary_max_words,
    )
    description_min, description_max = _target_bounds(
        original_word_count,
        settings.content_optimization_description_min_words,
        settings.content_optimization_description_max_words,
    )
    title_instructions = (
        TITLE_INSTRUCTIONS_KNOWN.format(title=raw["title"]) if raw["title"] else TITLE_INSTRUCTIONS_UNKNOWN
    )
    prompt = (
        _PROMPT_TEMPLATE.replace("{{summary_min_words}}", str(summary_min))
        .replace("{{summary_max_words}}", str(summary_max))
        .replace("{{description_min_words}}", str(description_min))
        .replace("{{description_max_words}}", str(description_max))
        .replace("{{title_instructions}}", title_instructions)
        .replace("{{content_text}}", raw["text"].strip())
    )
    return prompt + extra_suffix


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _parse_optimization_result(raw_text: str) -> tuple[Optional[str], str, str, list[str]]:
    cleaned = _strip_code_fences(raw_text)
    parsed = json.loads(cleaned)
    summary = parsed["summary"]
    description = parsed["description"]
    key_points = parsed["key_points"]
    title = parsed.get("title")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary field missing, empty, or not a string")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description field missing, empty, or not a string")
    if not isinstance(key_points, list) or not all(isinstance(p, str) for p in key_points):
        raise ValueError("key_points field missing or not a list of strings")
    if title is not None and not isinstance(title, str):
        raise ValueError("title field must be a string or null")
    return title, summary, description, key_points


def _build_llm():
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.content_optimization_model,
        temperature=settings.content_optimization_temperature,
    )


def _invoke(llm: Any, prompt: str) -> str:
    response = llm.invoke(prompt)
    return response.content if hasattr(response, "content") else str(response)


def optimize_content(raw: RawContentInput, llm: Optional[Any] = None, force_skip: bool = False) -> dict:
    """Optimize a normalized RawContentInput into title/summary/description/key_points.

    `force_skip` lets the caller (content_optimization_node, when the upstream
    response already carries an error) bypass the LLM call regardless of length --
    optimizing a known-fallback string would misrepresent it as genuinely extracted.

    Never raises -- always returns a complete dict matching the non-passthrough
    subset of the ContentOptimization schema (Section 11.2): title, summary,
    description, key_points, content_type, source_type, confidence_score, error.
    """
    original_word_count = _word_count(raw["text"])
    if force_skip or original_word_count < settings.content_optimization_skip_min_words:
        logger.info(
            "content_optimization_service skipped: force_skip=%s word_count=%d",
            force_skip,
            original_word_count,
        )
        return _skip_result(raw)

    llm = llm or _build_llm()
    attempts: list[_Attempt] = []
    extra_suffix = ""
    max_attempts = 1 + settings.content_optimization_max_retries

    for attempt_index in range(max_attempts):
        prompt = _build_prompt(raw, extra_suffix=extra_suffix)
        try:
            llm_title, summary, description, key_points = _parse_optimization_result(_invoke(llm, prompt))
        except Exception as exc:
            logger.warning(
                "content_optimization_service attempt %d produced no usable result: %s",
                attempt_index,
                exc,
            )
            extra_suffix = CORRECTIVE_REPROMPT_SUFFIX_MALFORMED
            continue

        violations = _count_violations(raw["text"], summary, description, original_word_count)
        attempts.append(
            _Attempt(
                attempt_index=attempt_index,
                title=llm_title,
                summary=summary,
                description=description,
                key_points=_dedupe_key_points(key_points),
                violation_count=violations,
                fact_retention_ratio=_fact_retention_ratio(raw["text"], summary),
                hit_length_cap=_hit_length_cap(summary, original_word_count),
            )
        )
        if violations == 0:
            break
        extra_suffix = _corrective_suffix_for(raw["text"], summary, description, original_word_count)

    clean_attempt = next((a for a in attempts if a.violation_count == 0), None)
    if clean_attempt is not None:
        confidence_score = _confidence_score(
            source_type=raw["source_type"],
            used_llm_call=True,
            retries_used=clean_attempt.attempt_index,
            fact_retention_ratio=clean_attempt.fact_retention_ratio,
            hit_length_cap=clean_attempt.hit_length_cap,
        )
        return {
            "title": _resolve_title(raw, clean_attempt.title, raw["text"]),
            "summary": clean_attempt.summary,
            "description": clean_attempt.description,
            "key_points": clean_attempt.key_points,
            "content_type": raw["content_type"] or "llm_generated",
            "source_type": raw["source_type"],
            "confidence_score": confidence_score,
            "error": None,
        }

    return _select_fallback(raw, llm_call_failed=not attempts)
