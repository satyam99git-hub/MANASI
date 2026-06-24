import logging
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger("app.services.cta_service")


def _parse_line(line: str) -> Optional[tuple[str, str]]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None  # blank line or a markdown header/comment -- not data
    if "=" not in stripped:
        logger.warning("cta_service: skipping malformed registry line: %r", stripped)
        return None
    key, _, value = stripped.partition("=")
    key, value = key.strip(), value.strip()
    if not key or not value:
        logger.warning("cta_service: skipping registry line with empty key/value: %r", stripped)
        return None
    if not (value.startswith("http://") or value.startswith("https://")):
        logger.warning("cta_service: skipping non-URL registry entry: %r=%r", key, value)
        return None
    return key, value


def load_cta_registry(path: Path) -> dict[str, str]:
    """Parses data/cta/cta_links.md into an in-memory {key: url} dict. Read-only --
    never writes back to `path`. Returns {} (never raises) if the file is missing
    or unreadable, so a registry problem can never block the pipeline (FR-8)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("cta_service: failed to load CTA registry from %s: %s", path, exc)
        return {}

    registry: dict[str, str] = {}
    for line in text.splitlines():
        parsed = _parse_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if key in registry:
            logger.warning(
                "cta_service: duplicate CTA key %r in registry -- keeping first definition", key
            )
            continue
        registry[key] = value
    return registry


CTA_REGISTRY: dict[str, str] = load_cta_registry(settings.cta_links_path)


def get_cta_url(cta_key: str) -> Optional[str]:
    """Exact dict lookup only -- no normalization, no fuzzy matching, no fallback."""
    return CTA_REGISTRY.get(cta_key)


def resolve_cta_key(retrieved_docs: list[dict]) -> tuple[Optional[str], Optional[str]]:
    """Returns (cta_key, source_chunk_id) from the single most relevant retrieved
    document (retrieved_docs[0] -- already similarity-ranked by knowledge_node).
    Returns (None, None) when there are no retrieved docs, or the top document
    carries no cta_key metadata. Never inspects lower-ranked documents (FR-4)."""
    if not retrieved_docs:
        return None, None
    top_doc = retrieved_docs[0]
    cta_key = top_doc.get("metadata", {}).get("cta_key")
    return (cta_key, top_doc.get("chunk_id")) if cta_key else (None, None)


def format_final_response(safe_response: str, cta: dict) -> str:
    """The single, code-only place a CTA URL is ever concatenated onto user-facing
    text (spec Section 5.4). Called once, by the eventual unified pipeline entry
    point, after the full graph has produced both `safety` and `cta`."""
    if not cta.get("matched"):
        return safe_response
    return f"{safe_response}\n\nLearn More:\n{cta['cta_url']}"
