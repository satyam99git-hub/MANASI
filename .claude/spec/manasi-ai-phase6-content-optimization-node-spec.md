# Manasi AI — Phase 6 Technical Specification
## Content Optimization Node

**Project:** Manasi AI
**Organization:** ManaScience
**Phase:** 6 of N — Content Optimization Node
**Status:** Draft for implementation
**Audience:** Python + FastAPI + LangGraph engineer
**Depends on:** Phase 1 — Understanding Node (`manasi-ai-phase1-understanding-node-spec.md`), Phase 2 — Knowledge Node (`manasi-ai-phase2-knowledge-node-spec.md`), Phase 3 — Response Generation Node (`manasi-ai-phase3-response-generation-node-spec.md`), Phase 4 — Empathy & Personality Node (`manasi-ai-phase4-empathy-personality-node-spec.md`)
**Runtime position note:** This document is numbered Phase 6 in spec sequence, but its compiled-graph position is *between* Phase 3 (Response Generation) and Phase 4 (Empathy & Personality) — not after Phase 5 (Safety). See Section 13.1 for the full rationale and the resulting graph order: `understanding → knowledge → response → content_optimization → empathy → safety`.

---

## 1. Executive Summary

Phase 3 (the Response Generation Node) turns Phase 1's structure and Phase 2's grounding decision into one accurate, freshly-written `answer`. That answer is correct, but it is also whatever shape the generation prompt happened to produce that turn: sometimes a tight two sentences, sometimes a four-paragraph concept essay, sometimes (when Phase 2 retrieved several long chunks) a denser block of prose carrying more raw material than a Phase 4 humanization pass can warmly restructure without first being told what the load-bearing pieces actually are. Phase 4 (the Empathy & Personality Node) is excellent at tone — Acknowledge, Explain, Support, Invite — but it is explicitly *not* a second generation layer (Phase 4 FR-8) and has no mandate to compress, title, or extract structure from what it's handed. Until now, nothing in the pipeline normalizes content shape *before* it reaches Empathy.

This document specifies **Phase 6 only: the Content Optimization Node**. Its job is to sit immediately after `response_node` and immediately before `empathy_node`, and transform whatever it receives — a short Phase 3 answer, a long one, or (outside the live per-turn graph) raw content pulled directly from a Markdown file, a Webflow CMS entry, a ChromaDB chunk aggregate, or a future API — into one small, standardized, structured payload: a `title` (or `null`), a length-bounded `summary`, a length-bounded `description`, a short list of `key_points`, and metadata describing what kind of content this was and how confident the node is in its own extraction. It does this without retrieving anything new, without deciding RAG-vs-LLM sourcing, without humanizing tone, and without ever inventing a fact that was not already present in the source text.

The Content Optimization Node's single most important behavioral guarantee mirrors Phase 4's: **the informational substance of the source content is never fabricated, never expanded with invented specifics, and never silently dropped beyond what compression necessarily discards.** Its second guarantee is that it is **source-agnostic by construction** — the same compression/title/description/key-point engine runs identically regardless of whether the raw text arrived from `response.answer`, a `.md` file, a Webflow CMS field, or a ChromaDB chunk, because every source is normalized into one common shape (`RawContentInput`, Section 6) before the engine ever sees it.

---

## 2. Business Objective

Phase 3 guarantees facts; Phase 4 guarantees warmth. Neither guarantees *shape* — and shape is what makes content reusable beyond a single chat turn. ManaScience's roadmap already names a Course Recommendation Engine, a Blog Recommendation Engine, and a Therapy Recommendation Engine (Section 17) — every one of which needs a title, a short description, and a handful of key points for a card or a search result, not a four-paragraph conversational answer. Today, nothing in the pipeline produces that shape; it would have to be reinvented, inconsistently, by whichever downstream consumer needed it first.

Specifically, this node exists to:

* **Decouple "how much was said" from "how it's said."** Phase 4 already decouples *accuracy* (Phase 3) from *tone* (Phase 4). Phase 6 adds the missing third axis — *shape and length* — as its own independently testable concern, so an engineer tuning compression never has to worry about breaking tone, and an engineer tuning tone never receives an answer so long or so structurally inconsistent that warmth can't fix it.
* **Make every piece of ManaScience content reusable outside the chat turn that produced it.** A `title` + `description` + `key_points` triple is the exact shape a recommendation card, a search-result snippet, or a CMS preview needs. By producing it once, mechanically, at the one point in the pipeline where content is already being looked at, Phase 6 avoids every future feature reinventing its own ad-hoc summarization.
* **Protect downstream latency and cost from upstream content variance.** Phase 2's retrieval can return up to `KNOWLEDGE_MAX_CONTEXT_CHARS` (6,000 characters) of chunk text, and Phase 3 has no upper bound on `answer` length (Phase 3's `response_min_answer_length` is a floor only). Without a normalization layer, an unusually long Phase 3 answer flows untouched into Phase 4's humanization prompt and the user's screen. Phase 6 puts a deterministic, configurable ceiling on what reaches Phase 4, while never padding content that was already short (Section 7).
* **Make the pipeline's content sources interchangeable.** "Where did this text come from" should never change *how* it gets summarized — only *which adapter* normalizes it first (Section 6). This is what lets the same engine serve the live per-turn graph today and a Markdown-ingestion job, a Webflow sync, or a future API tomorrow, without a rewrite.

---

## 3. Functional Requirements

### FR-1: Source-Agnostic Normalization
The node SHALL accept content from any supported source — Phase 3's `response.answer` (live graph), Markdown files, Webflow CMS entries, ChromaDB/RAG chunk aggregates, raw LLM-generated text, or future APIs — by first normalizing it into one common `RawContentInput` shape (Section 6.1). The optimization engine itself (Section 7) SHALL NOT contain source-specific branching; all source-specific logic SHALL live in adapter functions (Section 6.2).

### FR-2: Optimization Skip Logic
The node SHALL skip the optimization LLM call entirely — using only deterministic, Python-only normalization — when `response.error` is non-null (Phase 3 already fell back to a static infrastructure-failure message; Section 9 of Phase 3) or when the source text is trivially short (Section 7.1). This mirrors Phase 2's retrieval-skip logic (Phase 2 FR-2): a structural/length signal decides whether the expensive path runs, not a guess.

### FR-3: Content Summarization & Compression
When the optimization LLM call runs, the node SHALL produce a `summary` that preserves the source's critical information and factual accuracy, avoids hallucination (FR-10), and is length-adaptive: bounded above by `CONTENT_OPTIMIZATION_SUMMARY_MAX_WORDS` and, for source text already shorter than `CONTENT_OPTIMIZATION_SUMMARY_MIN_WORDS`, never artificially padded up to that floor (FR-4, Section 7.3).

### FR-4: Anti-Padding Guarantee
The node SHALL NOT inflate short, already-concise source content with invented elaboration merely to satisfy a minimum length target. The effective minimum length target for any given input is `min(original_word_count, CONTENT_OPTIMIZATION_SUMMARY_MIN_WORDS)` (Section 7.3) — identical in spirit to Phase 4's length-ratio floor existing to catch *trimming*, not to mandate padding.

### FR-5: Title Extraction
The node SHALL extract a `title` deterministically from source metadata whenever one exists — a RAG chunk's `source_title`, a Markdown file's frontmatter `title:`, or a Webflow CMS item's name field (Section 8.1) — without ever asking the LLM to invent a title when a real one is available. Only when no structural title exists and the optimization LLM call runs anyway MAY the node accept an LLM-proposed title candidate, subject to the validation in Section 8.2. When no title can be established by either path, the node SHALL return `null`, never a fabricated or placeholder title.

### FR-6: Description Generation
The node SHALL produce a `description` distinct in purpose from `summary` (Section 9.1): a short, user-friendly, standalone blurb suitable for a recommendation card or search snippet, bounded between `CONTENT_OPTIMIZATION_DESCRIPTION_MIN_WORDS` and `CONTENT_OPTIMIZATION_DESCRIPTION_MAX_WORDS` words, subject to the same anti-padding floor as FR-4.

### FR-7: Key Concept Extraction
The node SHALL extract between `CONTENT_OPTIMIZATION_KEY_POINTS_MIN` and `CONTENT_OPTIMIZATION_KEY_POINTS_MAX` short, deduplicated `key_points`, each traceable to content actually present in the source text. On the skip path (FR-2) or the never-block fallback path (FR-14), `key_points` SHALL be an empty list rather than a fabricated one.

### FR-8: Content and Source Type Classification
The node SHALL assign `content_type` and `source_type` deterministically from upstream metadata (Phase 2's `knowledge.retrieved_docs[*].content_type`, Phase 3's `response.source`, or the active adapter's declared source) — never by asking the LLM to self-classify (Section 6, Section 11.4).

### FR-9: Mechanical Confidence Scoring
The node SHALL compute `confidence_score` — a measure of the optimization step's own extraction confidence, distinct from the pipeline's pre-existing RAG-grounding `confidence` field (Section 10) — via a deterministic formula (Section 10.2) over signals already available to the node (source type, retries used, fact-retention ratio, whether hard truncation occurred). It SHALL NOT be self-reported by the LLM.

### FR-10: Factual Fidelity / Anti-Hallucination Guard
The node SHALL NOT introduce any name, number, claim, or specific detail into `summary`, `description`, or `key_points` that is not present, in substance, in the source text. This is enforced both by prompt instruction (Section 12) and by a mechanical fabrication guard (Section 7.5) that triggers the retry path in FR-13.

### FR-11: Deterministic Metadata Passthrough
`source`, `answer_type`, `topic`, `intent`, `confidence`, `grounded_chunk_ids`, and `original_answer` in the output SHALL be copied unchanged from `response.*` (live graph) by the node's own Python logic — never self-reported or altered by the optimization LLM. This preserves Phase 5's existing dependency on `empathy["source"]` (Section 13.6) without any change to Phase 5.

### FR-12: Structured Output Only
The node SHALL emit a single JSON-serializable object conforming to Section 11.2. `summary`, `description`, and `key_points` MAY contain light user-facing punctuation but the overall node output SHALL NOT be wrapped in commentary, code fences, or surrounding prose.

### FR-13: Retry on Quality-Guard Failure
If the generated output fails the fabrication guard, the fact-retention guard, the length-bound guard, or the banned-refusal-phrase guard (Section 7.4–7.6), the node SHALL retry generation exactly once with a corrective reprompt before falling back (Section 11.7).

### FR-14: Never-Block, Never-Regress Fallback
If both generation attempts fail a quality guard, or the optimization LLM call itself fails at the infrastructure level, the node SHALL fall back to a deterministic, Python-only normalization of the source text (Section 11.8) — never an empty `summary`, never a fabricated `title`, and never an unhandled exception escaping the node.

### FR-15: Statelessness
The node SHALL be a pure function of (`response`, `knowledge`) in the live graph, or of a single `RawContentInput` in offline/adapter use (Section 6). It SHALL NOT mutate any input state object and SHALL NOT persist content across invocations.

---

## 4. Non-Functional Requirements

### 4.1 General NFRs

| Category | Requirement |
|---|---|
| **Latency** | The node SHOULD complete in under 2,500ms p95 when the optimization LLM call runs (one call, plus a possible one corrective retry), and under 5ms when the skip path (FR-2) applies. |
| **Reliability** | The node MUST always return a valid, schema-conforming output (Section 11) whose `summary` is non-empty, even on LLM infrastructure failure or repeated quality-guard failure (FR-14). It MUST NOT raise an unhandled exception under any input. |
| **Statelessness** | Per FR-15. The node SHALL NOT call the retriever, the embeddings model, the response generator, or the humanization service directly — it only reads their already-computed output, or a normalized adapter payload. |
| **Testability** | The node MUST be unit-testable in isolation using a fake/mock LLM that returns scripted JSON, independent of Phases 1–5 and without live OpenAI calls, mirroring `tests/test_empathy_node.py`'s `FakeLLM` pattern. |
| **Observability** | Every invocation SHOULD be logged with `source_type`, `content_type`, whether the skip path or the LLM path ran, retry count, which guard (if any) triggered a retry or fallback, `confidence_score`, and latency. |
| **Cost** | The node SHOULD use the smallest chat model that meets quality targets (`gpt-4o-mini` by default, matching Phases 1, 3, 4, 5) and SHALL bound retries to at most one extra call per turn. The skip path (FR-2) SHALL incur zero LLM cost. |
| **Consistency** | Because the optimization call runs at low but non-zero temperature (Section 12), identical inputs are NOT expected to produce byte-identical output across calls — but length bounds, factual content, and classification fields SHOULD remain consistent, since the deterministic guards and passthrough logic are not subject to model variance. |
| **Safety boundary** | The node MUST NOT fabricate ManaScience-specific claims, soften or strengthen qualifiers present in the source text, or introduce diagnostic/prescriptive language. It performs no medical/domain/trust review itself — that remains Phase 5's job, which still runs after Phase 4 unchanged (Section 13.6) — but it MUST NOT make already-safe content less safe through careless compression (e.g., dropping a "may help some children" qualifier while keeping the claim). |

### 4.2 Performance Targets

| Metric | Target | Notes |
|---|---|---|
| Optimization call latency (first attempt) | p95 < 1,800ms | Single chat completion call producing `title`/`summary`/`description`/`key_points` together in one structured JSON response — mirrors Phase 5's single multi-field review call (Phase 5 Section 12). |
| End-to-end node latency (LLM path) | p95 < 2,500ms | First attempt + possible one corrective retry + guard checks (local string/token operations, negligible latency). |
| End-to-end node latency (skip path) | p95 < 5ms | No network call; pure Python normalization (Section 7.1). |
| Max generation attempts | 2 (`CONTENT_OPTIMIZATION_MAX_RETRIES = 1`) | One initial attempt, one corrective retry; never more (FR-13, FR-14). |
| Summary length bounds | 50–200 words (`CONTENT_OPTIMIZATION_SUMMARY_MIN_WORDS` / `_MAX_WORDS`), floor relaxed per FR-4 | Per the product brief's compression target. |
| Description length bounds | 50–200 words (`CONTENT_OPTIMIZATION_DESCRIPTION_MIN_WORDS` / `_MAX_WORDS`), floor relaxed per FR-4 | Ceiling aligned with `summary`'s 200-word cap for implementation consistency (Section 9.1) — the product brief's own description target was 50–150 words, but the two fields are differentiated by *purpose*, not by length tier. |
| Key points count | 3–7 (`CONTENT_OPTIMIZATION_KEY_POINTS_MIN` / `_MAX`) | Empty list permitted only on skip/fallback paths. |
| Fact-retention minimum ratio | 0.6 (`CONTENT_OPTIMIZATION_FACT_RETENTION_MIN_RATIO`) | Deliberately looser than Phase 4's 0.9 (Phase 4 Section 4.2) — compression is expected to shed minor detail; this guards against losing the *majority* of significant facts, not all of them (Section 7.4). |
| Fabrication ceiling ratio | 0.1 (`CONTENT_OPTIMIZATION_FABRICATION_MAX_RATIO`) | At most 10% of significant tokens in the output may be absent from the source text (Section 7.5) — the inverse-direction counterpart to fact-retention. |
| Token reduction target (compression-triggering inputs) | ≥ 50% word-count reduction | Measured in words, not exact tokens (no `tiktoken` dependency in this codebase today — Section 17); a ~1.3 tokens/word English approximation is used for cost estimation only, never for guard thresholds. |
| Max batch/offline input size | 5,000 words (`CONTENT_OPTIMIZATION_MAX_BATCH_INPUT_WORDS`) | Inputs from offline adapters (Section 6) exceeding this SHOULD be pre-chunked by the caller before reaching this node; the live graph never approaches this size (Phase 3 answers are not pre-chunked). |
| Cost per turn (live graph) | 0–2 chat completion calls | 0 on the skip path (FR-2); 1–2 on the LLM path, scaling with retry rate. |

---

## 5. Content Optimization Architecture

### 5.1 Position in the Pipeline

```
understanding_node
      │
      ▼
knowledge_node            (RAG retrieval decision; produces retrieved_docs, source, confidence)
      │
      ▼
response_node             (Phase 3; produces answer — no upper length bound)
      │
      ▼
content_optimization_node  ◄── THIS SPEC (Phase 6)
      │   produces: title, summary, description, key_points,
      │   content_type, source_type, confidence_score
      ▼
empathy_node               (Phase 4; now humanizes content_optimization.summary, not response.answer)
      │
      ▼
safety_node                (Phase 5; unchanged — still reads empathy.* exactly as today)
      │
      ▼
END
```

This insertion point was chosen over the alternative of running between `knowledge_node` and `response_node` because the product brief's supported-source list includes "LLM-generated content" and "Mixed RAG + LLM responses" — both of which only exist *after* `response_node` has run. `knowledge_node` alone only ever produces raw retrieved chunks; it is `response_node` that combines retrieval with LLM generation into the answer this node's brief describes optimizing.

### 5.2 Two Operating Modes

The same compression/title/description/key-point engine (Section 7) operates in two distinct modes, distinguished only by which adapter feeds it (Section 6) — never by different logic inside the engine itself:

| Mode | When | Typical input size | What usually happens |
|---|---|---|---|
| **Inline (live graph)** | Every turn, via `_from_pipeline_state` (Section 6.3) | `response.answer`: typically 20–400 words (no enforced ceiling — Phase 3 has a length floor only) | Usually a "metadata enrichment" pass — title/description/key_points get produced fresh every turn; `summary` is lightly normalized when already under the max, or genuinely compressed on the (uncommon but real) occasions a Phase 3 answer runs long. |
| **Batch / offline** | Ingestion-time (`scripts/build_knowledge_index.py`, future CMS sync jobs) or ad-hoc calls against Markdown/Webflow/ChromaDB sources, via the other adapters (Section 6.4–6.7) | 500–5,000 words | The brief's heavy-compression case: long documents get reduced to a 50–200 word `summary` before ever being chunked/embedded, or before being surfaced as a recommendation card. |

This distinction matters for one reason: it resolves an apparent tension in the product brief between "transform 500–5000 word documents" and the reality that the live per-turn pipeline mostly hands this node short, already-simplified text. Both are true — they are just two different callers of the same engine.

### 5.3 Supported Sources

| Source | Mode | `source_type` value | Adapter (Section 6) |
|---|---|---|---|
| Phase 3 `response.answer` (RAG-grounded) | Inline | `"rag"` | `_from_pipeline_state` |
| Phase 3 `response.answer` (LLM-only) | Inline | `"llm"` | `_from_pipeline_state` |
| Markdown (`.md`) files | Batch | `"markdown"` | `_from_markdown_file` |
| Webflow CMS entries | Batch | `"webflow_cms"` | `_from_webflow_cms_item` |
| ChromaDB / vector DB chunk aggregates | Batch | `"chromadb"` | `_from_chroma_chunks` |
| Raw RAG retrieval results (outside a chat turn, e.g. a standalone retrieval job) | Batch | `"chromadb"` | `_from_chroma_chunks` |
| Raw LLM-generated content (no retrieval, no chat turn) | Batch | `"llm"` | `_from_raw_text` |
| Mixed RAG + LLM (an explicit retrieved-chunk-plus-elaboration aggregate, assembled outside `response_node`) | Batch | `"mixed_rag_llm"` | `_from_raw_text` (caller-declared) |
| Future APIs / unspecified sources | Batch | `"api"` | `_from_raw_text` (caller-declared `source_type="api"`) |

`source_type` values `"markdown"`, `"webflow_cms"`, `"chromadb"`, `"mixed_rag_llm"`, and `"api"` never occur in the live graph — `_from_pipeline_state` only ever yields `"rag"` or `"llm"`, verbatim from `response.source` (FR-11). They exist so the same `ContentOptimizationOutput` schema (Section 11.2) is the right shape for every future caller in Section 17, not just the chat pipeline.

---

## 6. Source Adapter System

### 6.1 The Common Shape: `RawContentInput`

Every adapter normalizes its source into this one shape before the optimization engine (Section 7) ever runs:

```python
class RawContentInput(TypedDict):
    text: str                      # the raw body to optimize
    title: Optional[str]           # a structurally-known title, if any (never None when one truly exists)
    source_type: Literal[
        "rag", "llm", "mixed_rag_llm", "markdown", "webflow_cms", "chromadb", "api",
    ]
    content_type: Optional[str]    # e.g. "course", "blog", "neuroplasticity_content" -- null if unknown
    metadata: dict                 # adapter-specific passthrough (e.g. source_url, chunk_id)
```

The optimization engine (`optimize_content(raw: RawContentInput, llm=None) -> dict`, Section 11.6) contains **no source-specific branching whatsoever** — it only ever reads `text`, `title`, `source_type`, and `content_type` from this normalized shape (FR-1). All source-specific knowledge lives in the adapters below.

### 6.2 Adapter Responsibilities

| Adapter | Responsibility |
|---|---|
| `_from_pipeline_state(response, knowledge) -> RawContentInput` | **The only adapter wired into the live LangGraph.** Builds `text=response["answer"]`, `source_type=response["source"]` verbatim, `title=knowledge["retrieved_docs"][0]["source_title"]` when `response["source"]=="rag"` and `retrieved_docs` is non-empty, else `None`; `content_type=knowledge["retrieved_docs"][0]["content_type"]` under the same condition, else `None`. See Section 6.3. |
| `_from_markdown_file(path: Path) -> RawContentInput` | Parses YAML frontmatter for a `title:` key if present; `text` is the Markdown body with frontmatter stripped; `source_type="markdown"`; `content_type` read from a frontmatter `content_type:` key when present, else `None`. |
| `_from_webflow_cms_item(item: dict) -> RawContentInput` | Maps Webflow's `fieldData.name` (or `fieldData.title`) to `title`; `text` from the item's rich-text/body field; `source_type="webflow_cms"`; `content_type` from the CMS collection slug when it maps cleanly onto the existing content-type enum (Section 8.3), else `None`. |
| `_from_chroma_chunks(docs: list[RetrievedDocument]) -> RawContentInput` | Joins one or more `RetrievedDocument.content` values (Phase 2 shape) into `text`; `title=docs[0]["source_title"]`; `source_type="chromadb"`; `content_type=docs[0]["content_type"]`. Used for standalone retrieval-aggregate optimization outside a chat turn (e.g. pre-summarizing a long document's chunks before they are ever returned to a user). |
| `_from_raw_text(text, *, source_type, title=None, content_type=None) -> RawContentInput` | The generic catch-all for raw LLM completions, mixed RAG+LLM aggregates assembled by a future job, and unspecified future-API payloads. The caller declares `source_type` and, optionally, `title`/`content_type` directly — there is no structure to parse. |

### 6.3 `_from_pipeline_state` in Detail

```python
def _from_pipeline_state(response: dict, knowledge: dict) -> dict:
    docs = knowledge.get("retrieved_docs") or []
    top_doc = docs[0] if response["source"] == "rag" and docs else None
    return {
        "text": response["answer"],
        "title": top_doc["source_title"] if top_doc else None,
        "source_type": response["source"],
        "content_type": top_doc["content_type"] if top_doc else None,
        "metadata": {"grounded_chunk_ids": response.get("grounded_chunk_ids", [])},
    }
```

This is the only adapter the live `content_optimization_node` (Section 13) calls. It is intentionally a few lines of pure dict logic — exactly the kind of "correct by construction" passthrough already established for `_to_retrieved_document` (`app/nodes/knowledge_node.py:115-126`) and the `_safe_fallback_result` helpers across Phases 3–5.

---

## 7. Summarization & Compression Engine

### 7.1 Skip Logic (FR-2)

The optimization LLM call is skipped — and the node falls through to deterministic-only normalization — when either:

1. `response["error"] is not None` (live graph only: Phase 3 already returned its static `INFRA_FAILURE_FALLBACK_ANSWER`; optimizing a known-fallback string and labeling it "optimized" would misrepresent it), or
2. `len(text.split()) < CONTENT_OPTIMIZATION_SKIP_MIN_WORDS` (default 12 — content too trivially short to summarize, e.g. `"Hi! What would you like to know about ManaScience?"`).

On the skip path:

```python
def _skip_result(raw: dict) -> dict:
    text = raw["text"].strip()
    return {
        "title": raw["title"],                 # deterministic only -- never LLM-guessed here
        "summary": text,                        # verbatim, whitespace-normalized
        "description": text,                    # same text; too short to need a distinct blurb
        "key_points": [],                       # cannot be reliably extracted without an LLM call
        "content_type": raw["content_type"] or "llm_generated",
        "source_type": raw["source_type"],
        "confidence_score": 0.95,                # no lossy transformation occurred
    }
```

This mirrors Phase 2's `_skipped_result` (`app/nodes/knowledge_node.py:74-84`): a short-circuit that is correct by construction and incurs zero LLM cost.

### 7.2 When the LLM Call Runs

For everything else, the node makes exactly one structured-JSON chat completion call producing `title` (candidate, only used per Section 8.2), `summary`, `description`, and `key_points` together — one call per attempt, mirroring the single-call-per-attempt pattern already used by Phases 3, 4, and 5 rather than issuing separate calls per field.

### 7.3 Length-Adaptive Targets (FR-3, FR-4)

```python
def _target_bounds(original_word_count: int, floor: int, ceiling: int) -> tuple[int, int]:
    """Never asks the model to pad content that started shorter than the floor."""
    effective_floor = min(original_word_count, floor)
    return effective_floor, ceiling
```

Applied independently to `summary` (`floor=CONTENT_OPTIMIZATION_SUMMARY_MIN_WORDS`, `ceiling=..._MAX_WORDS`) and `description` (`floor=CONTENT_OPTIMIZATION_DESCRIPTION_MIN_WORDS`, `ceiling=..._MAX_WORDS`). The prompt (Section 12) is given the *computed* effective floor and ceiling for this specific input, not the raw config constants, so a 30-word Phase 3 answer is asked for a 30-ish-word summary, never stretched to 50.

### 7.4 Fact-Retention Guard

Reuses the token-overlap technique already proven in `empathy_service._fact_retention_ratio` (`app/services/empathy_service.py:105-119`) — numbers/percentages and capitalized multi-letter words, lowercased, as a coarse proxy for "load-bearing facts":

```python
def _fact_retention_ratio(source_text: str, summary: str) -> float:
    source_tokens = _significant_tokens(source_text)
    if not source_tokens:
        return 1.0
    retained = source_tokens & _significant_tokens(summary)
    return len(retained) / len(source_tokens)
```

Fails when the ratio drops below `CONTENT_OPTIMIZATION_FACT_RETENTION_MIN_RATIO` (0.6 — deliberately looser than Phase 4's 0.9, since compression is *expected* to shed minor detail; Section 4.2).

### 7.5 Fabrication Guard (FR-10)

The inverse-direction counterpart to 7.4 — catches the engine inventing specifics that were never in the source:

```python
def _fabrication_ratio(source_text: str, summary: str) -> float:
    summary_tokens = _significant_tokens(summary)
    if not summary_tokens:
        return 0.0
    fabricated = summary_tokens - _significant_tokens(source_text)
    return len(fabricated) / len(summary_tokens)
```

Fails when the ratio exceeds `CONTENT_OPTIMIZATION_FABRICATION_MAX_RATIO` (0.1) — at most 10% of the significant tokens in the output may be absent from the source.

### 7.6 Banned Refusal-Phrase Guard

Reuses the same category of check as Phase 3's `BANNED_PHRASES` (`app/services/response_generator.py:64-71`), adapted to this node's failure mode — a model declining to summarize rather than declining to answer:

```python
CONTENT_OPTIMIZATION_BANNED_PHRASES = [
    "i cannot summarize", "i can't summarize", "no content was provided",
    "there is nothing to summarize", "unable to generate a summary",
    "i don't have enough content", "no information to extract",
]
```

### 7.7 Corrective Reprompt Suffixes

One suffix per guard, concatenated when multiple guards fail simultaneously — identical structure to Phase 3 (`_corrective_suffix_for`, `app/services/response_generator.py:190-198`) and Phase 4 (`app/services/empathy_service.py:155-166`):

```python
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
```

---

## 8. Title Extraction

### 8.1 Deterministic-First Algorithm (FR-5)

```python
def _resolve_title(raw: dict, llm_candidate: Optional[str], body_text: str) -> Optional[str]:
    if raw["title"]:
        return raw["title"].strip()           # structural title always wins -- never overridden by the LLM
    if llm_candidate and _is_valid_title_candidate(llm_candidate, body_text):
        return llm_candidate.strip()
    return None
```

Priority order:

1. **Structural title from the adapter** (`raw["title"]`) — a RAG chunk's `source_title`, a Markdown file's frontmatter `title:`, a Webflow CMS item's name field. Assigned by Python before the LLM call even runs, identical in spirit to every other deterministic-passthrough field in this codebase (Phase 4 FR-9, Phase 5 FR-12).
2. **LLM-proposed candidate**, only when (1) is absent *and* the optimization LLM call ran anyway (Section 7.2) *and* the candidate passes the validation in Section 8.2. This covers a genuinely titleless source — e.g. a raw multi-paragraph LLM essay with no frontmatter and no RAG grounding.
3. **`null`**, when neither applies — per the product brief's explicit instruction ("If no title exists: Return null"). This is the expected, normal outcome for most `source_type == "llm"` live-graph turns, since Phase 3 answers carry no structural title of their own.

### 8.2 Validating an LLM-Proposed Title Candidate

```python
def _is_valid_title_candidate(candidate: str, body_text: str) -> bool:
    words = candidate.strip().split()
    if not (1 <= len(words) <= 8):
        return False
    candidate_tokens = _significant_tokens(candidate)
    body_tokens = _significant_tokens(body_text)
    return not candidate_tokens or bool(candidate_tokens & body_tokens)
```

A candidate must be a short noun phrase (≤ 8 words) and, if it carries any significant token (a number or capitalized term) at all, that token must actually appear in the body — a cheap, mechanical guard against the model titling the content with something it didn't say. A candidate that fails either check is discarded; the node falls through to `null`, never to a half-trusted guess.

### 8.3 `content_type` Enum

```python
CONTENT_TYPES = Literal[
    "course", "blog", "research_article", "faq", "practitioner_info",
    "therapy_info", "website_content", "neuroplasticity_content",
    "pdf_document",       # the 9 values validated today by knowledge_node.RetrievedDocumentModel
    "llm_generated",      # NEW -- no RAG grounding at all (response.source == "llm")
    "mixed",              # NEW -- reserved for batch/offline aggregates explicitly combining RAG + LLM content
]
```

*Implementation note:* `app/nodes/knowledge_node.py`'s `CONTENT_TYPES` (the literal actually enforced by `RetrievedDocumentModel`) and `app/graph/state.py`'s `RetrievedDocument.content_type` literal currently disagree on three spellings (`practitioner_info` vs `practit_info`, `therapy_info` vs `therapye_content`, `website_content` vs `websitioner_info`). This is a pre-existing inconsistency outside this spec's scope; this document follows `knowledge_node.py`'s list, since that is the one Pydantic actually validates against at runtime.

---

## 9. Description & Key-Point Generation

### 9.1 Why `description` Is Not Just a Shorter `summary`

The product brief asks for both a `summary` (the compression target) and a `description` ("short description, user-friendly explanation, easy-to-read summary," 50–150 words) — two fields with overlapping definitions. This spec resolves the overlap with a clear product distinction:

* **`summary`** is the primary downstream payload. It is what `empathy_node` reads and humanizes (Section 13.5) — the load-bearing, conversational substance of the turn.
* **`description`** is a secondary, standalone teaser independent of the conversation — the exact shape a recommendation card, search-result snippet, or CMS preview needs (Section 2). It is written to make sense *without* the surrounding chat context, where `summary` may lean on phrasing carried over from the user's question.

The two fields are distinguished by this purpose, not by a separate length tier: `description`'s implemented ceiling (`CONTENT_OPTIMIZATION_DESCRIPTION_MAX_WORDS`) is 200 words, matching `summary`'s, rather than the product brief's literal 150 — one fewer independent constant to tune, since `summary` vs. `description` already differ in framing instruction (Section 12) and in the floor (`CONTENT_OPTIMIZATION_DESCRIPTION_MIN_WORDS` remains independently configurable at 50).

In the live graph, both are generated from the same source text in the same LLM call; they differ in framing instruction (Section 12), not in source material.

### 9.2 Key Point Extraction Rules (FR-7)

* Between `CONTENT_OPTIMIZATION_KEY_POINTS_MIN` (3) and `CONTENT_OPTIMIZATION_KEY_POINTS_MAX` (7) items.
* Each item is a short phrase or single sentence (enforced as non-empty after `.strip()`; no hard word cap, but the prompt — Section 12 — instructs brevity).
* Deduplicated case-insensitively after stripping.
* Never produced on the skip path (Section 7.1) or the never-block fallback path (Section 11.8) — both return `[]` rather than a best-effort guess, since reliable concept extraction without an LLM call is not attempted (FR-7).

---

## 10. Confidence Scoring

### 10.1 Why a New Field, Not a Reused One

The pipeline already has a `confidence` field, established in Phase 2 and passed through unchanged by every node since (`knowledge.confidence` → `response.confidence` → ... ). It has one fixed meaning: *RAG-grounding confidence* — `0.0` whenever `source == "llm"`, and the top similarity score otherwise (`knowledge_node._decide_source`, `app/nodes/knowledge_node.py:106-112`). Phase 5's trust calibration (Phase 5 Section 6.2) already depends on this exact field keeping this exact meaning.

The product brief's `confidence_score` asks for something different: how confident the *optimization step itself* is in its own title/summary/extraction. Reusing `confidence` for that would silently break Phase 5. This spec therefore keeps both fields, with distinct names and distinct semantics:

* `confidence` (passthrough, FR-11) — unchanged RAG-grounding confidence, exactly as today.
* `confidence_score` (new, this node's own) — optimization-extraction confidence, computed below.

### 10.2 Mechanical Formula (FR-9)

```python
def _confidence_score(
    *, source_type: str, used_llm_call: bool, retries_used: int,
    fact_retention_ratio: float, hit_length_cap: bool,
) -> float:
    score = 1.0
    if source_type == "llm":
        score -= 0.3                                            # no original document to compress against
    if used_llm_call:
        score -= 0.1 * retries_used                              # a clean attempt was already burned
        score -= max(0.0, 0.9 - fact_retention_ratio) * 0.5      # softer signal than the hard 0.6 guard floor
    if hit_length_cap:
        score -= 0.1                                              # had to hard-truncate, not cleanly summarize
    return round(max(0.1, min(1.0, score)), 2)
```

Like every other score in this codebase (Phase 2's similarity-threshold confidence, Phase 4's length/fact-retention ratios), this is a deliberately coarse, auditable, mechanical computation over signals the node already has in hand — never an LLM self-report (consistent with Phase 4 Section 15's stated preference for "auditable mechanical checks over LLM-judge classifiers").

---

## 11. JSON Schema

### 11.1 Input Contract (recap from Phases 2 and 3)

The product brief's minimal input shape is a single raw content string. In production, the live graph instead supplies two already-validated state objects, which `_from_pipeline_state` (Section 6.3) normalizes:

```json
{
  "response": {
    "answer": "Neuroplasticity is the brain's ability to reorganize itself by forming new neural connections throughout life. This continues well beyond childhood...",
    "source": "rag",
    "answer_type": "concept_explanation",
    "topic": "neuroplasticity",
    "intent": "concept_explanation",
    "confidence": 0.89,
    "grounded_chunk_ids": ["a1b2c3d4e5f6"],
    "generation_time_ms": 1842.6,
    "error": null
  },
  "knowledge": {
    "source": "rag",
    "retrieved_docs": [
      {
        "chunk_id": "a1b2c3d4e5f6",
        "content": "...",
        "content_type": "neuroplasticity_content",
        "source_title": "Understanding Neuroplasticity",
        "source_url": "https://manascience.com/blog/neuroplasticity",
        "similarity_score": 0.91,
        "metadata": {}
      }
    ],
    "confidence": 0.89,
    "query_used": "what is neuroplasticity",
    "intent": "concept_explanation",
    "retrieval_skipped": false,
    "content_types_searched": ["neuroplasticity_content", "blog"],
    "retrieval_time_ms": 312.4,
    "error": null
  }
}
```

Offline/adapter callers instead construct a `RawContentInput` directly (Section 6.1) and call `optimize_content(raw, llm=...)` without any `response`/`knowledge` object at all.

### 11.2 Output Schema Definition

```json
{
  "title": "Understanding Neuroplasticity",
  "summary": "Neuroplasticity is the brain's ability to reorganize itself by forming new neural connections throughout life, not just in childhood. This means the brain retains the capacity to adapt and build new pathways well into adulthood.",
  "description": "An introduction to neuroplasticity -- the brain's lifelong ability to form new neural connections and adapt -- and why this capacity isn't limited to early childhood development.",
  "key_points": [
    "Neuroplasticity is the brain's ability to reorganize itself",
    "New neural connections can form throughout life",
    "This capacity is not limited to childhood"
  ],
  "content_type": "neuroplasticity_content",
  "source_type": "rag",
  "confidence_score": 0.95,
  "source": "rag",
  "answer_type": "concept_explanation",
  "topic": "neuroplasticity",
  "intent": "concept_explanation",
  "confidence": 0.89,
  "grounded_chunk_ids": ["a1b2c3d4e5f6"],
  "original_answer": "Neuroplasticity is the brain's ability to reorganize itself by forming new neural connections throughout life. This continues well beyond childhood...",
  "optimization_time_ms": 743.2,
  "error": null
}
```

The product brief's 7-field minimal shape (`content_type`, `title`, `summary`, `description`, `key_points`, `source_type`, `confidence_score`) is the **required core subset** of this schema; the remaining fields are required production fields, following the same pattern Phase 4 and Phase 5 established for their own schemas (Phase 4 Section 9.2, Phase 5 Section 11.2) — full passthrough of every field `empathy_node` and `safety_node` need, so neither phase ever has to reach back into `state["response"]` or `state["knowledge"]` directly.

### 11.3 Top-Level Field Definitions

| Field | Type | Required | Allowed Values | Notes |
|---|---|---|---|---|
| `title` | string \| null | Yes | — | Per Section 8.1. `null` whenever no structural or validated LLM-proposed title exists — this is the expected, normal value for most `source_type == "llm"` turns, not an error. |
| `summary` | string | Yes | — | Length-adaptive per Section 7.3. Never empty, even on the fallback path (Section 11.8). |
| `description` | string | Yes | — | Per Section 9.1; standalone, conversation-independent blurb. |
| `key_points` | array of string | Yes | — | 3–7 items when the LLM path ran cleanly; `[]` on the skip path (Section 7.1) or fallback path (Section 11.8). |
| `content_type` | string (enum) | Yes | Section 8.3's 11 values | Deterministic, from `knowledge.retrieved_docs[0].content_type` when RAG-sourced, else `"llm_generated"`. |
| `source_type` | string (enum) | Yes | `"rag"`, `"llm"`, `"mixed_rag_llm"`, `"markdown"`, `"webflow_cms"`, `"chromadb"`, `"api"` | Live graph yields only `"rag"`/`"llm"` (Section 5.3); the rest are offline-adapter-only. |
| `confidence_score` | float | Yes | `0.1`–`1.0` | Per Section 10.2. Distinct from `confidence` below — never confuse the two. |
| `source` | string (enum) | Yes | `"rag"`, `"llm"` | Passed through unchanged from `response.source` (FR-11). Preserved so Phase 5 needs no changes (Section 13.6). |
| `answer_type` | string (enum) | Yes | Same 8 values as Phase 3's `answer_type` | Passed through unchanged from `response.answer_type`. |
| `topic` | string | Yes | — | Passed through unchanged from `response.topic`. |
| `intent` | string | Yes | Same enum as Phase 1's `intent` | Passed through unchanged from `response.intent`. |
| `confidence` | float | Yes | `0.0`–`1.0` | Passed through unchanged from `response.confidence` — the pre-existing RAG-grounding score (Section 10.1). |
| `grounded_chunk_ids` | array of string | Yes | — | Passed through unchanged from `response.grounded_chunk_ids`. |
| `original_answer` | string | Yes | — | The verbatim, pre-optimization `response.answer`, preserved for observability and human review — mirrors Phase 5's `original_final_answer` field (`app/graph/state.py:110`). |
| `optimization_time_ms` | float | Yes | — | Wall-clock time for this node's work, for latency monitoring (Section 4.2). |
| `error` | string \| null | Yes | — | `null` on a clean optimization or a clean skip. A short machine-readable code (Section 11.5) when the never-block fallback path (Section 11.8) was used. |

### 11.4 Validation Rules

Enforced by a Pydantic model, `ContentOptimizationOutput`, mirroring the `model_validator`-based pattern already used by `UnderstandingOutput`, `KnowledgeOutput`, `ResponseOutput`, `EmpathyOutput`, and `SafetyOutput`:

```python
class ContentOptimizationOutput(BaseModel):
    title: Optional[str]
    summary: str
    description: str
    key_points: list[str]
    content_type: Literal[
        "course", "blog", "research_article", "faq", "practitioner_info",
        "therapy_info", "website_content", "neuroplasticity_content",
        "pdf_document", "llm_generated", "mixed",
    ]
    source_type: Literal[
        "rag", "llm", "mixed_rag_llm", "markdown", "webflow_cms", "chromadb", "api",
    ]
    confidence_score: float
    source: Literal["rag", "llm"]
    answer_type: Literal[
        "concept_explanation", "therapy_information", "course_information",
        "research_summary", "website_information", "personal_guidance",
        "supportive_information", "general_knowledge",
    ]
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
        if len(self.key_points) > settings.content_optimization_key_points_max:
            raise ValueError("key_points exceeds content_optimization_key_points_max")
        if any(not point.strip() for point in self.key_points):
            raise ValueError("key_points must not contain empty strings")
        return self

    @model_validator(mode="after")
    def _validate_confidence_score_bounds(self) -> "ContentOptimizationOutput":
        if not (0.0 <= self.confidence_score <= 1.0):
            raise ValueError("confidence_score out of bounds")
        return self
```

As with Phases 3–5, the fact-retention guard (Section 7.4), the fabrication guard (Section 7.5), and the length-bound guard (Section 7.3) run against the source text, which the Pydantic model itself has no access to — those failures are checked separately, before this model is constructed, and treated identically to a `ValidationError` for retry purposes (Section 11.6). `key_points`' *minimum* count (3) is intentionally not enforced here, since the legitimate skip and fallback paths (Sections 7.1, 11.8) return `[]` by design; the minimum is enforced only as a soft target on the LLM-generated path via the retry guard, not as a hard schema constraint that would make those paths unrepresentable.

`source`, `answer_type`, `topic`, `intent`, `confidence`, `grounded_chunk_ids`, and `original_answer` are never independently re-validated against any LLM output, because the LLM never produces them — they are assigned by the node itself and are correct by construction (FR-11).

### 11.5 Error Handling

Recognized `error` codes, all of which route through the never-block fallback (Section 11.8) rather than raising:

| Code | Meaning |
|---|---|
| `null` | Clean optimization, or a clean skip-path result (Section 7.1); no fallback used. |
| `"llm_call_failure"` | The chat completion API call raised (timeout, connection error, API error) on both the initial attempt and the corrective retry. |
| `"quality_guard_exhausted"` | Both the initial attempt and the corrective retry produced output that failed the fabrication guard, the fact-retention guard, the length-bound guard, or the banned-refusal-phrase guard. |

A non-null `error` is an internal observability signal, logged as a `content_optimization_node_failure` event, and is never itself shown to the user — `summary` always contains real, displayable text regardless of `error`'s value.

### 11.6 Retry Flow Summary

```
1. Build RawContentInput via adapter (Section 6)
        │
        ▼
2. Skip check (Section 7.1): error present, or text too short?
        │
   yes  │  no
        │   └──────────► 3. Build prompt, invoke optimization LLM
        ▼                       (temperature = CONTENT_OPTIMIZATION_TEMPERATURE)
4. Deterministic skip result            │
   (Section 7.1), error = null          ▼
        │                       5. Guard checks: fabrication (7.5), fact-retention (7.4),
        │                          length-bounds (7.3), banned-phrase (7.6)
        │                                │
        │                          fail  │  pass
        │                                │    └────► ContentOptimizationOutput validated, error = null
        │                                ▼
        │                       6. Corrective reprompt retry (max 1 -- FR-13),
        │                          suffix per failed guard (7.7)
        │                                │
        │                                ▼ (retry also fails, or LLM call itself errors on either attempt)
        │                       7. Never-block fallback (Section 11.8)
        ▼                                │
        └────────────────────────────────┴──────────► {"content_optimization": {...}}
```

### 11.7 Confidence-Score Computation Order

`confidence_score` (Section 10.2) is computed *after* the guard checks and retry resolution in step 5/6 above, using whichever attempt was ultimately selected — never computed mid-retry, so it always reflects the final, returned output.

### 11.8 Never-Block Fallback Algorithm (FR-14)

```python
def _select_fallback(raw: dict, llm_call_failed: bool) -> dict:
    """Hand-built result that is correct by construction -- bypasses
    ContentOptimizationOutput validation entirely so this path cannot itself fail."""
    text = raw["text"].strip()
    error_code = "llm_call_failure" if llm_call_failed else "quality_guard_exhausted"
    return {
        "title": raw["title"],                      # deterministic only, never an LLM guess on this path
        "summary": text,                              # verbatim source text -- always real, never empty
        "description": text,
        "key_points": [],
        "content_type": raw["content_type"] or "llm_generated",
        "source_type": raw["source_type"],
        "confidence_score": 0.3,                       # fixed, conservative -- no successful extraction occurred
        "error": error_code,
    }
```

Like Phase 4's fallback (verbatim `response.answer`) and Phase 3's fallback (a static, always-safe template), this path is guaranteed not to itself raise: every value either comes straight from the already-validated `RawContentInput` or is a fixed constant. Unlike Phase 4 (whose fallback is *always* exactly the unmodified input, since there is nothing better to offer once humanization fails), this node's fallback still tries to honor an already-cheap, already-correct deterministic title — it just gives up on compression, description framing, and key-point extraction, none of which can be done safely without a working LLM call.

---

## 12. Prompt Design

### 12.1 Requirements

The prompt in `content_optimization_prompt.txt` MUST:

* Establish the node's narrow role before any task instruction: it optimizes *shape*, not *facts* or *tone* — it does not retrieve, does not decide sourcing, does not add warmth (those belong to Phases 2, 2, and 4 respectively).
* State the no-fabrication rule (Section 7.5, FR-10) as a CRITICAL RULE, mirroring how Phases 3–5 elevate their own non-negotiable rules to CRITICAL RULE status.
* Provide the computed effective length bounds for `summary` and `description` (Section 7.3) substituted per-request, never the raw config constants.
* Provide the key-point count bounds (3–7).
* Instruct that a title MAY be proposed only when none is supplied, with the constraints from Section 8.2 (short, grounded in the body).
* Provide the source text verbatim, clearly delimited.
* Instruct JSON-only output with exactly four fields (`title`, `summary`, `description`, `key_points`), no surrounding commentary.

### 12.2 Guardrails (recap, enforced both in-prompt and mechanically per Section 7)

* Never invent a name, number, claim, or specific detail absent from the source (FR-10 / Section 7.5).
* Never strengthen or soften a qualifier present in the source (e.g. "may help some children" must not become "helps children").
* Never add a medical/clinical claim that was not already present — this node performs no safety review itself (Phase 5 still owns that, unchanged, after Phase 4).
* Never pad short content to hit a minimum length (Section 7.3 / FR-4).
* Never produce a title when no structural title exists and the proposed candidate fails validation (Section 8.2) — return `null` instead of guessing.

### 12.3 Production-Ready Prompt Example (`prompts/content_optimization_prompt.txt`)

```text
You are the Content Optimization Node for Manasi AI, ManaScience's guide.

Your job is narrow: take the CONTENT below and produce a shorter, structured
version of it -- a summary, a standalone description, and a short list of
key points, plus a title only if one isn't already known. You are NOT
responsible for deciding whether the content is accurate, where it came
from, or how warm it sounds -- other systems already handled that, or will
handle it after you. Focus only on faithful compression and structure.

---
CRITICAL RULE -- NEVER ADD INFORMATION THAT ISN'T THERE:

Do not introduce any name, number, claim, or specific detail that is not
already present, in substance, in the CONTENT below. If the CONTENT says
"some children" or "a 2021 study," your output must still say "some
children" or "a 2021 study" -- never "all children" or "research shows."
You are compressing what it says, never adding to it.

---
LENGTH REQUIREMENTS FOR THIS CONTENT:

- summary: {{summary_min_words}}-{{summary_max_words}} words
- description: {{description_min_words}}-{{description_max_words}} words
- key_points: 3-7 short items

Do not pad the summary or description with filler to reach the minimum --
if the content is naturally shorter than the minimum, a shorter result is
correct and expected.

---
TITLE INSTRUCTIONS:

{{title_instructions}}

---
CONTENT TO OPTIMIZE:

{{content_text}}

---
OUTPUT FORMAT:

Return ONLY a valid JSON object with exactly these fields. No markdown code
fences, no commentary, no text before or after the JSON.

{
  "title": "<a short title for this content, or null if none fits>",
  "summary": "<a faithful, compressed rewrite of the content above>",
  "description": "<a short, standalone, user-friendly blurb that makes
  sense without any surrounding context>",
  "key_points": ["<short key point 1>", "<short key point 2>", "..."]
}

Return the JSON object now.
```

`{{title_instructions}}` is substituted from one of two fixed blocks, never freeform:

```python
TITLE_INSTRUCTIONS_KNOWN = (
    "A title is already known for this content: \"{title}\". Use it exactly "
    "as given -- do not propose a different one."
)
TITLE_INSTRUCTIONS_UNKNOWN = (
    "No title is known for this content. If a short (1-8 word) title "
    "naturally fits the content, propose one in the \"title\" field. If "
    "nothing fits naturally, return null for \"title\" -- do not force one."
)
```

### 12.4 Notes on Prompt Maintenance

* `{{content_text}}` is the verbatim source text (`response.answer` in the live graph) — no preprocessing beyond whitespace trimming, since the model needs the exact facts and qualifiers it must preserve and never exceed.
* `{{summary_min_words}}`/`{{summary_max_words}}`/`{{description_min_words}}`/`{{description_max_words}}` are substituted from `_target_bounds` (Section 7.3) on every call, not hardcoded — this is what makes the anti-padding guarantee (FR-4) hold even though the prompt text itself is static.
* When a structural title is already known (`TITLE_INSTRUCTIONS_KNOWN`), the prompt still asks the model to echo it rather than omitting the title instruction entirely — this keeps the JSON schema's shape identical across both branches, simplifying parsing, even though the echoed value is discarded in favor of `raw["title"]` regardless (Section 8.1, step 1 always wins).
* This file is versioned alongside code, kept as plain text rather than embedded in Python, identical in rationale to every other phase's prompt file (Phase 1 Section 12.3).

---

## 13. LangGraph Integration

### 13.1 Node Purpose and Runtime Position

`content_optimization_node` runs immediately after `response_node` and immediately before `empathy_node`. This is a deliberate departure from the product brief's literal diagram (`Knowledge → Content Optimization → Empathy → Safety → Response`), which does not match the pipeline actually implemented in `app/` (`understanding → knowledge → response → empathy → safety`, wired in `build_safety_graph()`, `app/nodes/safety_node.py:136-158`). The brief's own supported-source list — "LLM-generated content," "Mixed RAG + LLM responses" — only makes sense for content that already passed through `response_node`, which settles the placement (Section 5.1).

The resulting full graph order becomes:

```
understanding_node → knowledge_node → response_node → content_optimization_node → empathy_node → safety_node → END
```

### 13.2 Input State

| State Field | Type | Source |
|---|---|---|
| `response` | `Response` (Phase 3 `GraphState` field) | Written by `response_node` earlier in the same graph invocation. |
| `knowledge` | `Knowledge` (Phase 2 `GraphState` field) | Written by `knowledge_node`; the node reads only `knowledge["retrieved_docs"]`, for title/content_type derivation (Section 6.3). |

The node does not read `user_message`, `chat_history`, or `understanding` — none of those are needed once `response` and `knowledge` already exist (FR-1, mirroring Phase 4's equivalent narrowing, Phase 4 Section 11.2).

### 13.3 Output State

| State Field | Type | Description |
|---|---|---|
| `content_optimization` | `dict` matching Section 11.2 schema | The optimized, structured payload for the current turn. |

The node MUST NOT mutate `response` or `knowledge`. It only adds `content_optimization` to state.

### 13.4 State Definition (`graph/state.py` additions)

```python
class ContentOptimization(TypedDict):
    title: Optional[str]
    summary: str
    description: str
    key_points: list[str]
    content_type: Literal[
        "course", "blog", "research_article", "faq", "practitioner_info",
        "therapy_info", "website_content", "neuroplasticity_content",
        "pdf_document", "llm_generated", "mixed",
    ]
    source_type: Literal[
        "rag", "llm", "mixed_rag_llm", "markdown", "webflow_cms", "chromadb", "api",
    ]
    confidence_score: float
    source: Literal["rag", "llm"]
    answer_type: Literal[
        "concept_explanation", "therapy_information", "course_information",
        "research_summary", "website_information", "personal_guidance",
        "supportive_information", "general_knowledge",
    ]
    topic: str
    intent: str
    confidence: float
    grounded_chunk_ids: list[str]
    original_answer: str
    optimization_time_ms: float
    error: Optional[str]


class GraphState(TypedDict):
    user_message: str
    chat_history: list[ChatTurn]
    understanding: Optional[Understanding]
    knowledge: Optional[Knowledge]
    response: Optional[Response]
    content_optimization: Optional[ContentOptimization]
    empathy: Optional[Empathy]
    safety: Optional[Safety]
```

This is a strict additive edit to the existing `GraphState` in `app/graph/state.py` — no existing field changes shape. `content_optimization` is inserted between `response` and `empathy` to mirror the graph's actual data-flow order, though `TypedDict` field order has no runtime effect on LangGraph's state merging.

### 13.5 Required Edit to `nodes/empathy_node.py`

This is the one existing file this phase must change, and the change is intentionally minimal — a one-line input-source swap, nothing else:

```python
# Before (Phase 4 as shipped):
def empathy_node(state: GraphState, llm: Optional[Any] = None) -> dict:
    response = state["response"]
    emotional_state = state["understanding"]["emotional_state"]
    result = humanize_response(response, emotional_state, llm=llm)
    ...

# After (Phase 6 integration):
def empathy_node(state: GraphState, llm: Optional[Any] = None) -> dict:
    content_optimization = state["content_optimization"]
    emotional_state = state["understanding"]["emotional_state"]
    result = humanize_response(content_optimization, emotional_state, llm=llm)
    ...
```

`humanize_response` (`app/services/empathy_service.py:218`) already reads its input via `response["answer"]`, `response["source"]`, `response["answer_type"]`, etc. — every one of those keys exists with an identical name and meaning on `content_optimization`, **except** `answer`, which becomes `summary` (Section 9.1). The only required change inside `empathy_service.py` itself is:

```python
# humanize_response(response: dict, ...) reads response["answer"] today;
# change that one read to response["summary"] (the dict passed in is now
# content_optimization, but the parameter name and every other key are unchanged).
```

No change to `EMOTIONAL_TONE_INSTRUCTIONS`, the guard functions, the retry loop, or `EmpathyOutput`'s schema is needed — Phase 4's contract was already "an answer-shaped dict plus an emotional state," and `content_optimization` satisfies that shape with `summary` standing in for `answer`.

### 13.6 No Change Required to `nodes/safety_node.py`

`safety_node` reads `empathy["source"]` to decide whether to pull `knowledge["retrieved_docs"]` for hallucination grounding (`app/nodes/safety_node.py:82-84`). Because `source` is passed through unchanged at every step (`response.source` → `content_optimization.source` → `empathy.source`, FR-11), this read continues to resolve correctly with **zero changes to Phase 5**. This is the direct payoff of FR-11's full-passthrough design.

### 13.7 State Updates

```python
{"content_optimization": {
    "title": "Understanding Neuroplasticity",
    "summary": "...",
    "description": "...",
    "key_points": ["...", "...", "..."],
    "content_type": "neuroplasticity_content",
    "source_type": "rag",
    "confidence_score": 0.95,
    "source": "rag",
    "answer_type": "concept_explanation",
    "topic": "neuroplasticity",
    "intent": "concept_explanation",
    "confidence": 0.89,
    "grounded_chunk_ids": ["a1b2c3d4e5f6"],
    "original_answer": "...",
    "optimization_time_ms": 743.2,
    "error": None,
}}
```

LangGraph merges this partial update into the running `GraphState`, identical in mechanism to every other node's `{"<field>": {...}}` return.

### 13.8 Recommended Graph Wiring

Mirroring `build_empathy_graph()` (`app/nodes/empathy_node.py:96-115`), a five-node chain for isolated Phase 6 testing/deployment:

```python
def build_content_optimization_graph():
    """Compile a five-node StateGraph (understanding -> knowledge -> response ->
    content_optimization -> empathy) for Phase 6 isolated testing/deployment."""
    from langgraph.graph import END, START, StateGraph

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
```

`build_safety_graph()` (`app/nodes/safety_node.py:136-158`) gets the same two-line insertion (`add_node("content_optimization_node", ...)`, and routing `response_node → content_optimization_node → empathy_node` instead of `response_node → empathy_node`) to become the full six-node production pipeline.

### 13.9 Routing Logic and Failure Recovery

There is no conditional branching in the graph itself — the skip-vs-LLM-call decision (Section 7.1) happens *inside* `content_optimization_node`, not as a LangGraph conditional edge, exactly matching how Phase 2's retrieval-skip decision (`intent == "general_chat"`) lives inside `knowledge_node` rather than as graph-level branching. This keeps the graph topology simple and keeps all failure recovery (retry, fallback) co-located with the logic it protects (Section 11.6, 11.8) — consistent with every prior phase's node ever raising past its own boundary.

### 13.10 API Layer

A `/optimize-content` endpoint, mirroring the existing `/understand`, `/knowledge`, `/respond`, `/humanize`, and `/safety` endpoints in `app/main.py`, returning `state["content_optimization"]` directly as the response body for isolated Phase 6 testing:

```python
@app.post("/optimize-content", response_model=ContentOptimizationResponse)
def optimize_content_endpoint(request: ChatRequest):
    if content_optimization_graph is None:
        raise HTTPException(status_code=503, detail="Content optimization node is still starting up")
    history = session_histories.get(request.session_id, [])
    result = content_optimization_graph.invoke({
        "user_message": request.message,
        "chat_history": _history_to_chat_turns(history),
        "understanding": None,
        "knowledge": None,
        "response": None,
        "content_optimization": None,
    })
    return ContentOptimizationResponse(**result["content_optimization"])
```

### 13.11 Integration with Future Nodes

* **Empathy Node (Phase 4, edited per Section 13.5):** reads `content_optimization["summary"]` in place of `response["answer"]`; every other behavior is unchanged.
* **Safety Node (Phase 5, unchanged):** continues to read `empathy["source"]`/`empathy["confidence"]` exactly as today (Section 13.6).
* **Recommendation engines (future, Section 17):** read `content_optimization["title"]`, `content_optimization["description"]`, and `content_optimization["key_points"]` directly — these three fields exist specifically so a future recommendation feature never has to re-derive them from `response.answer` or re-run its own summarization.
* **Eventual single production graph:** Phases 1–6 (and Phase 5) are each independently buildable and testable via their own `build_*_graph()` function today; assembling all phases into the one production `StateGraph` used by the live `/chat` endpoint remains the deferred integration milestone already noted in Phase 4 and Phase 5 (Phase 5 Section 13.8).

---

## 14. File Structure

```
graph/
    state.py                          (edited)

nodes/
    content_optimization_node.py      (new)
    empathy_node.py                   (edited -- Section 13.5)
    safety_node.py                    (edited -- Section 13.8 graph wiring only)

prompts/
    content_optimization_prompt.txt   (new)

services/
    content_optimization_service.py   (new)
    empathy_service.py                (edited -- Section 13.5)

config.py                             (edited)
models.py                             (edited)
main.py                               (edited)
```

### 14.1 `graph/state.py` (edited)
**Responsibility:** Add the `ContentOptimization` TypedDict and the `content_optimization: Optional[ContentOptimization]` field to the existing `GraphState`, per Section 13.4.

### 14.2 `nodes/content_optimization_node.py` (new)
**Responsibility:** A thin LangGraph wrapper, mirroring `nodes/empathy_node.py`'s split between orchestration and business logic. Specifically:
* Defines the `ContentOptimizationOutput` Pydantic model (Section 11.4) and its validators.
* Implements `content_optimization_node(state: GraphState, llm: Optional[Any] = None) -> dict`: builds a `RawContentInput` via `_from_pipeline_state(state["response"], state["knowledge"])` (Section 6.3), calls `services/content_optimization_service.py`'s `optimize_content(...)`, times the call, validates the result, and returns `{"content_optimization": {...}}`.
* Implements a `_safe_fallback_result(...)` helper, mirroring `empathy_node.py:79-93`, that hand-builds a guaranteed-valid result for the case where even `optimize_content` somehow raises unexpectedly — bypassing `ContentOptimizationOutput` validation entirely (FR-14's "never raise" guarantee, belt-and-suspenders).
* Implements `build_content_optimization_graph()` (Section 13.8) for isolated testing/deployment.

### 14.3 `services/content_optimization_service.py` (new)
**Responsibility:** All optimization business logic, mirroring `services/empathy_service.py`'s role for Phase 4. Specifically:
* Loads and formats `prompts/content_optimization_prompt.txt` with `{{summary_min_words}}`, `{{summary_max_words}}`, `{{description_min_words}}`, `{{description_max_words}}`, `{{title_instructions}}`, and `{{content_text}}` (Section 12.3).
* Holds `CONTENT_OPTIMIZATION_BANNED_PHRASES` (Section 7.6), `TITLE_INSTRUCTIONS_KNOWN`/`_UNKNOWN` (Section 12.3), and the corrective reprompt suffix constants (Section 7.7).
* Implements the four adapter functions (Section 6.2): `_from_pipeline_state`, `_from_markdown_file`, `_from_webflow_cms_item`, `_from_chroma_chunks`, `_from_raw_text`.
* Implements the guard functions: `_fact_retention_ratio`/`_fails_fact_retention` (Section 7.4), `_fabrication_ratio`/`_fails_fabrication` (Section 7.5), `_target_bounds` (Section 7.3), `_is_valid_title_candidate` (Section 8.2), `_confidence_score` (Section 10.2).
* Implements `_build_llm()` (`ChatOpenAI(model=settings.content_optimization_model, temperature=settings.content_optimization_temperature)`) and `_invoke(llm, prompt)`, mirroring every prior service module's identically-named helpers.
* Implements `optimize_content(raw: RawContentInput, llm: Optional[Any] = None) -> dict`: the skip check (Section 7.1), the retry loop (Section 11.6), guard checks, and the never-block fallback (Section 11.8) — the source-agnostic engine at the center of this whole phase (FR-1).
* Never raises; always returns a complete dict matching the `ContentOptimization` schema minus `optimization_time_ms`, which the calling node times itself (identical contract to every prior phase's generator function).

### 14.4 `prompts/content_optimization_prompt.txt` (new)
**Responsibility:** Holds the full instruction prompt (Section 12.3) as a standalone text file, identical rationale to every other phase's prompt file.

### 14.5 `config.py` (edited)

```python
content_optimization_model: str = os.getenv("CONTENT_OPTIMIZATION_MODEL", "gpt-4o-mini")
content_optimization_temperature: float = float(os.getenv("CONTENT_OPTIMIZATION_TEMPERATURE", "0.2"))
content_optimization_max_retries: int = int(os.getenv("CONTENT_OPTIMIZATION_MAX_RETRIES", "1"))
content_optimization_skip_min_words: int = int(os.getenv("CONTENT_OPTIMIZATION_SKIP_MIN_WORDS", "12"))
content_optimization_summary_min_words: int = int(os.getenv("CONTENT_OPTIMIZATION_SUMMARY_MIN_WORDS", "50"))
content_optimization_summary_max_words: int = int(os.getenv("CONTENT_OPTIMIZATION_SUMMARY_MAX_WORDS", "200"))
content_optimization_description_min_words: int = int(os.getenv("CONTENT_OPTIMIZATION_DESCRIPTION_MIN_WORDS", "50"))
content_optimization_description_max_words: int = int(os.getenv("CONTENT_OPTIMIZATION_DESCRIPTION_MAX_WORDS", "200"))
content_optimization_key_points_min: int = int(os.getenv("CONTENT_OPTIMIZATION_KEY_POINTS_MIN", "3"))
content_optimization_key_points_max: int = int(os.getenv("CONTENT_OPTIMIZATION_KEY_POINTS_MAX", "7"))
content_optimization_fact_retention_min_ratio: float = float(os.getenv("CONTENT_OPTIMIZATION_FACT_RETENTION_MIN_RATIO", "0.6"))
content_optimization_fabrication_max_ratio: float = float(os.getenv("CONTENT_OPTIMIZATION_FABRICATION_MAX_RATIO", "0.1"))
content_optimization_max_batch_input_words: int = int(os.getenv("CONTENT_OPTIMIZATION_MAX_BATCH_INPUT_WORDS", "5000"))
```

### 14.6 `models.py` (edited)

```python
class ContentOptimizationResponse(BaseModel):
    title: str | None
    summary: str
    description: str
    key_points: list[str]
    content_type: str
    source_type: str
    confidence_score: float
    source: str
    answer_type: str
    topic: str
    intent: str
    confidence: float
    grounded_chunk_ids: list[str]
    original_answer: str
    optimization_time_ms: float
    error: str | None
```

### 14.7 `main.py` (edited)
**Responsibility:** Add `content_optimization_graph` global + lifespan build call, the `/optimize-content` endpoint (Section 13.10), and update `build_safety_graph()`'s wiring call site if the production graph builder is invoked directly rather than reconstructed.

---

## 15. Examples

Each entry shows **Source context** (narrative grounding only), **Input** (what the node actually receives), and **Output** (the resulting `content_optimization` fields most relevant to the case).

### 15.1 RAG-grounded concept explanation, title from retrieved chunk

Source: live graph, user asked "What is neuroplasticity?"
Input: `response.source="rag"`, `response.answer` (180 words), `knowledge.retrieved_docs[0].source_title="Understanding Neuroplasticity"`, `content_type="neuroplasticity_content"`.
Output: `title="Understanding Neuroplasticity"` (deterministic, Section 8.1 step 1), `summary` ≈150 words (lightly normalized, under the 200-word ceiling so no real compression needed), `key_points` has 4 items, `confidence_score=0.97`, `source_type="rag"`.

### 15.2 LLM-only answer, no title

Source: live graph, user asked an off-domain-adjacent general-knowledge question with no ManaScience grounding.
Input: `response.source="llm"`, `knowledge.retrieved_docs=[]`.
Output: `title=null` (no structural title, and no LLM candidate proposed because none fit naturally — Section 8.1 step 3), `confidence_score=0.65` (the 0.3 `source_type=="llm"` penalty from Section 10.2 applied), `source_type="llm"`, `content_type="llm_generated"`.

### 15.3 Long Phase 3 answer triggers real compression

Source: live graph, a multi-paragraph concept-explanation answer (≈340 words) assembled from several long retrieved chunks (Phase 2's `KNOWLEDGE_MAX_CONTEXT_CHARS` allows up to 6,000 characters of source material).
Input: `response.answer` at 340 words, `response.source="rag"`.
Output: `summary` compressed to ≈190 words (within the 200-word ceiling — genuine compression, not just normalization, occurring in the live path per Section 5.2), `description` ≈90 words, `key_points` has 6 items, all guarded by the fact-retention check (Section 7.4) passing at ratio ≥0.6.

### 15.4 Trivial reply skips the LLM call entirely

Source: live graph, `understanding.intent="general_chat"`, response is a short pleasantry.
Input: `response.answer="Hi! What would you like to know about ManaScience today?"` (10 words).
Output: skip path (Section 7.1) triggers since 10 < `CONTENT_OPTIMIZATION_SKIP_MIN_WORDS` (12); `summary`/`description` equal the input verbatim, `key_points=[]`, `confidence_score=0.95`, `optimization_time_ms` < 5ms, zero LLM cost.

### 15.5 Upstream infrastructure failure skips the LLM call

Source: live graph, `response.error="llm_call_failure"` (Phase 3's own infra-failure fallback fired).
Input: `response.answer=INFRA_FAILURE_FALLBACK_ANSWER` text.
Output: skip path triggers on the `response.error is not None` condition (Section 7.1), regardless of word count — optimizing a known-fallback string would misrepresent it as genuinely-extracted content.

### 15.6 Offline: Markdown file with frontmatter title

Source: batch ingestion job calling `_from_markdown_file(Path("data/blog/occupational-therapy.md"))` directly, outside the live graph.
Input: frontmatter `title: Occupational Therapy`, 1,200-word body.
Output: `title="Occupational Therapy"` (deterministic, from frontmatter), `summary` compressed to ≈180 words, `source_type="markdown"`, `content_type` taken from a frontmatter `content_type: therapy_info` key.

### 15.7 Offline: ChromaDB chunk aggregate, multi-chunk source

Source: a standalone retrieval job calling `_from_chroma_chunks(docs)` against 5 chunks of a single long research article, outside any chat turn.
Input: 5 chunks totaling ≈2,400 words, `docs[0].source_title="Primitive Reflexes and Early Development"`.
Output: `title="Primitive Reflexes and Early Development"`, `summary` ≈195 words, `key_points` has 7 items (the max), `source_type="chromadb"`, `content_type="research_article"`.

### 15.8 Guard-triggered retry: fabrication caught and corrected

Source: live graph; first optimization attempt's `summary` mentions a specific statistic not present in `response.answer`.
Input/flow: fabrication guard (Section 7.5) computes a ratio above 0.1 on attempt 1; `CORRECTIVE_REPROMPT_SUFFIX_FABRICATION` (Section 7.7) is appended; attempt 2 passes with ratio 0.0.
Output: the attempt-2 result is what gets validated and returned; `error=null` (a successful retry is not itself an error, mirroring Phase 4/5's identical convention).

### 15.9 Never-block fallback: both attempts fail the fact-retention guard

Source: live graph; both optimization attempts produce an over-compressed summary that drops most significant tokens from a dense, jargon-heavy answer.
Output: `summary`/`description` fall back to the verbatim source text (Section 11.8), `key_points=[]`, `confidence_score=0.3`, `error="quality_guard_exhausted"`, `title` still resolved deterministically if one was available — the fallback never sacrifices the one field it can still get right for free.

### 15.10 Offline: raw LLM essay with an LLM-proposed title

Source: a future content-enrichment job calling `_from_raw_text(long_essay, source_type="llm")` against a 600-word freeform LLM-generated explainer with no structural title.
Input: no `title`, no `content_type` declared.
Output: the optimization LLM call proposes `"ADHD and Executive Function"` as a title candidate; it passes validation (4 words, shares the token "ADHD" with the body — Section 8.2); `title="ADHD and Executive Function"`, `content_type="llm_generated"`, `source_type="llm"`.

---

## 16. Testing Requirements & Acceptance Criteria

### 16.1 Unit Tests

Mirroring `tests/test_empathy_node.py`'s `FakeLLM` pattern, scripted-JSON, no live OpenAI calls:

* `test_happy_path_produces_all_seven_core_fields` — schema-complete output for a representative RAG-grounded input.
* `test_title_taken_from_retrieved_doc_never_overridden_by_llm` — even when the LLM call's JSON proposes a different title, `raw["title"]` wins (Section 8.1 step 1).
* `test_no_title_and_no_valid_candidate_returns_null` — Section 8.1 step 3.
* `test_llm_proposed_title_over_eight_words_is_rejected` — Section 8.2.
* `test_llm_proposed_title_sharing_no_token_with_body_is_rejected` — Section 8.2.
* `test_short_input_does_not_get_padded_to_minimum_length` — FR-4 / Section 7.3's `_target_bounds`.
* `test_fabrication_guard_triggers_retry_then_succeeds` — Section 7.5.
* `test_fact_retention_guard_triggers_retry_then_succeeds` — Section 7.4.
* `test_length_bound_guard_triggers_retry_then_succeeds` — Section 7.3.
* `test_banned_refusal_phrase_triggers_retry_then_succeeds` — Section 7.6.
* `test_confidence_score_penalized_for_llm_source_type` — Section 10.2.
* `test_confidence_score_penalized_per_retry_used` — Section 10.2.
* `test_does_not_mutate_input_state` — mirroring `test_empathy_node.py:259`.
* `test_node_does_not_read_user_message_or_chat_history_or_understanding` — Section 13.2.

### 16.2 Integration Tests

* `test_content_optimization_node_after_response_node_in_build_content_optimization_graph` — full five-node graph (Section 13.8) invocation end-to-end with a fake LLM at every generation step.
* `test_empathy_node_reads_summary_from_content_optimization_not_response` — verifies the Section 13.5 edit: feeding a `content_optimization` dict whose `summary` differs from `response.answer` produces a humanized output traceable to `summary`, not `answer`.
* `test_safety_node_source_passthrough_unaffected_by_insertion` — verifies Section 13.6's claim: a six-node graph with `content_optimization_node` inserted still lets `safety_node` correctly decide whether to pull `retrieved_docs` based on `empathy["source"]`.
* `test_optimize_content_endpoint_returns_schema_valid_response` — `/optimize-content` (Section 13.10) integration test against the FastAPI test client.

### 16.3 Edge Cases

* Empty `knowledge.retrieved_docs` with `response.source="rag"` (should not occur given existing invariants, but the adapter must not raise if it does — `top_doc` resolves to `None`, `title=None`).
* `response.answer` containing only punctuation/whitespace after a hypothetical upstream bug — the skip path's word-count check must not divide by zero or raise.
* A source text exactly at `CONTENT_OPTIMIZATION_SKIP_MIN_WORDS` (boundary: 12 words exactly should NOT skip, since the check is `<`, not `<=`).
* A title candidate that is exactly 8 words (boundary: should pass `_is_valid_title_candidate`; 9 words should fail).
* `key_points` from the LLM containing duplicate entries differing only in case/whitespace — must be deduplicated (Section 9.2).

### 16.4 Failure Scenarios

* `test_both_llm_calls_raise_falls_back_with_llm_call_failure` — mirrors `test_empathy_node.py:230`.
* `test_mixed_failure_first_raises_second_fails_guard_is_quality_guard_exhausted` — mirrors `test_empathy_node.py:240`.
* `test_malformed_json_first_attempt_triggers_retry_then_succeeds` — mirrors `test_empathy_node.py:250`.
* `test_node_level_unexpected_exception_falls_through_to_safe_fallback_result` — the node-level `_safe_fallback_result` belt-and-suspenders path (Section 14.2), exercised by making `optimize_content` itself raise (simulating a bug in the service layer it's not designed to have, per its own "never raises" contract).

### 16.5 Large Document Tests

* `test_2000_word_chroma_chunk_aggregate_compresses_to_summary_ceiling` — `_from_chroma_chunks` (Section 6.2) against several joined chunks, verifying `summary` lands at or under `CONTENT_OPTIMIZATION_SUMMARY_MAX_WORDS`.
* `test_5000_word_markdown_file_at_max_batch_input_words_boundary` — `_from_markdown_file` at exactly `CONTENT_OPTIMIZATION_MAX_BATCH_INPUT_WORDS`, verifying no error is raised (the cap is advisory for callers, per Section 4.2's note that the live graph never approaches it — this node itself does not hard-reject oversized input).
* `test_token_reduction_ratio_meets_target_on_large_input` — verifies the ≥50% word-count reduction target (Section 4.2) on a representative 1,000+ word input.

### 16.6 Mixed RAG + LLM Tests

* `test_mixed_rag_llm_source_type_via_from_raw_text` — `_from_raw_text(text, source_type="mixed_rag_llm")` (Section 6.2) produces a schema-valid output with `source_type="mixed_rag_llm"`, `content_type="mixed"` when undeclared.
* `test_live_graph_rag_sourced_answer_with_background_elaboration_still_yields_source_type_rag` — confirms Section 5.3's decision: even though Phase 3's RAG path may blend in general background knowledge (`response_generator.py`'s `RAG_INSTRUCTIONS`), the live graph's `source_type` stays exactly `response.source` (`"rag"`), never auto-promoted to `"mixed_rag_llm"` — that value is reserved for explicitly-declared offline aggregates only.

### 16.7 Acceptance Criteria

| Test Category | Description | Pass Criteria |
|---|---|---|
| Schema validity | Run node against all 10 examples in Section 15 | 100% of outputs are valid JSON matching the schema in Section 11.2, with no extra/missing fields. |
| Fact preservation (mechanical) | Run the fact-retention guard (Section 7.4) against all LLM-path examples | 100% of clean (non-fallback) outputs retain ≥`CONTENT_OPTIMIZATION_FACT_RETENTION_MIN_RATIO` of significant tokens from the source text. |
| Fabrication prevention (mechanical) | Run the fabrication guard (Section 7.5) against all LLM-path examples | 100% of clean outputs have a fabrication ratio ≤`CONTENT_OPTIMIZATION_FABRICATION_MAX_RATIO`. |
| Fact preservation (human review) | Independent reviewer compares source text and `summary` side by side for a ≥20-example sample | ≥95% judged to have identical factual substance, allowing for the expected loss of minor detail under compression. |
| Title correctness | Run against all examples with a structural title available | 100% return that exact title, never an LLM-altered variant. |
| Anti-padding | Run against ≥10 inputs shorter than `CONTENT_OPTIMIZATION_SUMMARY_MIN_WORDS` | 0% produce a `summary` that is materially longer than the source (no invented elaboration to hit a word count). |
| Guard-triggered retry | Simulate a first-attempt draft that violates each guard in turn (fabrication, fact-retention, length, refusal) | Node retries exactly once with the correct corrective suffix (Section 7.7) per violation type. |
| Never-block fallback | Simulate (a) the LLM call raising on both attempts, and (b) both attempts failing a quality guard | In both cases, `summary` exactly equals the source text, `error` is set to the correct code (Section 11.5), and no exception escapes the node. |
| Source-agnosticism | Run the same underlying text through `_from_pipeline_state`, `_from_markdown_file` (with matching frontmatter), and `_from_raw_text` | All three produce equivalent `summary`/`description`/`key_points` content (allowing for model non-determinism), differing only in `source_type` and `title` resolution — proving the engine itself (Section 7) has no source-specific branching (FR-1). |
| Latency | Run node against 20 representative examples from Section 15 | p95 end-to-end latency under 2,500ms (LLM path) / 5ms (skip path) per Section 4.2. |

### 16.8 Definition of Done

Phase 6 (Content Optimization Node) is considered complete only when **all** of the following hold:

1. JSON output is 100% schema-valid across the full test set, with the corrective-retry path and the never-block fallback path each exercised and verified at least once.
2. Zero fabricated facts across both the example set and a ≥20-example human-reviewed sample (Section 16.7).
3. Title extraction is 100% correct (verbatim) whenever a structural title exists, and returns `null` — never a placeholder — whenever none does and no valid LLM candidate was proposed.
4. The anti-padding guarantee (FR-4) holds across every example shorter than the configured minimums.
5. The node is integrated into a LangGraph `StateGraph` per Section 13 and runnable end-to-end (API → graph → JSON) chained after Phases 1–3, with `empathy_node` successfully reading `content_optimization["summary"]` (Section 13.5) and `safety_node` requiring no changes (Section 13.6).
6. The same optimization engine (`optimize_content`) is demonstrated working correctly against at least one non-live-graph adapter (Markdown or ChromaDB chunk aggregate), proving source-agnosticism (FR-1) before this phase is called done.
7. A reviewer reading `summary`/`description`/`key_points` outputs cold (without the source text alongside) reports that they read as faithful, concise, and free of invented specifics — the qualitative bar underlying the mechanical guards in (2).

### 16.9 Production Readiness Checklist

- [ ] `ContentOptimizationOutput` Pydantic model and all validators implemented and unit-tested (Section 11.4).
- [ ] All five source adapters (Section 6.2) implemented; `_from_pipeline_state` wired into the live graph, the remaining four available for offline/batch callers.
- [ ] Skip logic (Section 7.1) verified to incur zero LLM calls on both trigger conditions.
- [ ] Fact-retention, fabrication, length-bound, and banned-phrase guards (Section 7.4–7.6) implemented with corrective reprompt suffixes wired to the retry loop.
- [ ] Never-block fallback (Section 11.8) verified to never raise under any input, including malformed adapter input.
- [ ] `nodes/empathy_node.py` and `services/empathy_service.py` edited per Section 13.5; Phase 4's existing unit test suite re-run and passing against the new input shape.
- [ ] `nodes/safety_node.py` graph-wiring updated per Section 13.8; Phase 5's existing unit test suite re-run and passing unchanged (Section 13.6).
- [ ] `config.py`, `models.py`, `main.py` edited per Section 14.5–14.7.
- [ ] `/optimize-content` endpoint live and integration-tested (Section 13.10).
- [ ] Observability logging (`source_type`, skip-vs-LLM-path, retry count, guard failures, `confidence_score`, latency) emitted on every invocation per Section 4.1.
- [ ] Token-reduction and latency targets (Section 4.2) measured against a representative production-like sample, not just the example set.

---

## 17. Future Compatibility & Future Considerations

The following are explicitly **out of scope for Phase 6** but noted here so the engineer understands what this node's output contract must remain stable for:

* **Webflow CMS live sync:** `_from_webflow_cms_item` (Section 6.2) is specified at the adapter-signature level only; the actual Webflow API client, polling/webhook trigger, and field-mapping configuration are a separate integration project. The adapter's job ends at producing a `RawContentInput` — everything upstream of that is future work.
* **ChromaDB / Supabase Vector as a batch optimization target:** `_from_chroma_chunks` (Section 6.2) is designed to also work unchanged against a future Supabase Vector-backed retriever, since it only depends on the `RetrievedDocument` shape (Phase 2), not on ChromaDB specifically — a future migration to Supabase Vector would only need a new retriever, not a new adapter here.
* **Multi-provider LLMs:** `_build_llm()` (Section 14.3) follows the same `ChatOpenAI(...)`-construction pattern as every other service module today; swapping providers is a pre-existing cross-cutting concern for the whole codebase, not something this phase introduces or solves.
* **Memory systems:** This node is stateless per FR-15 and does not currently consider prior turns' `content_optimization` output (e.g. avoiding re-extracting the same `key_points` across a session). A future memory layer could pass recent `title`/`key_points` history through `chat_history` so the node can deduplicate across turns — not a Phase 6 requirement today.
* **Course / Blog / Therapy Recommendation Engines:** these are the primary intended consumers of `title`, `description`, and `key_points` outside the chat turn that produced them (Section 2, Section 13.11). None of them are built yet; this phase's job is only to make their eventual implementation not require re-inventing summarization.
* **Exact token counting:** Section 4.2's token-reduction target uses a word-count proxy because this codebase has no `tiktoken` dependency today. A future revision could add exact token counting for cost-tracking precision without changing any guard threshold, since all existing guards (Section 7.3–7.5) are deliberately defined in words/tokens-as-significant-terms, not raw token counts.
* **Semantic (LLM-judge) fidelity scoring:** Section 7.4/7.5's guards are coarse, deterministic token-overlap proxies, consistent with this codebase's preference for auditable mechanical checks over LLM-judge classifiers (Phase 2 Section 7.4, Phase 4 Section 15). A future revision could add an offline, non-blocking LLM-judge evaluation pass over production samples to catch subtler meaning drift the mechanical guards miss.
* **Single production graph assembly:** as in every prior phase's spec, wiring Phases 1–6 into the one `StateGraph` actually used by the live `/chat` endpoint remains a deferred integration milestone, not part of this document.
* **Confidence-score calibration via experimentation:** Section 10.2's formula is a reasonable starting point, not an empirically tuned final answer — a future phase could calibrate its coefficients against human-judged extraction-quality labels once the node is in production.

---

*End of Phase 6 Specification — Content Optimization Node.*
