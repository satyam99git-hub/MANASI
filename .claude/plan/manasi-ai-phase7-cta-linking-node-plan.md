# Implementation Plan — Manasi AI Phase 7: CTA Linking Node

**Source spec:** `.claude/spec/manasi-ai-phase7-cta-linking-node-spec.md` (729 lines, authored this session, read in full)

## Context

Phases 1–6 are implemented and working: `understanding_node -> knowledge_node -> response_node -> content_optimization_node -> empathy_node -> safety_node` (`build_safety_graph()` in `app/nodes/safety_node.py:136-162`). Baseline today: `pytest tests/ -q` → **106 passed**. `data/cta/cta_links.md` already exists in the working tree (currently untracked in git, 16 keys across 9 markdown-header-delimited categories) and `git status` shows nothing else pending besides the Phase 7 spec file itself.

The Phase 7 spec adds a new node, `cta_node`, inserted **between `knowledge_node` and `response_node`**. Three design facts are already settled in the spec and must not be re-derived or second-guessed during implementation:

1. **The lookup key is never LLM output.** It is `state["knowledge"]["retrieved_docs"][0]["metadata"]["cta_key"]` — a brand-new, optional metadata field set by hand in `scripts/build_knowledge_index.py`'s `load_therapy_chunks`, via a new `THERAPY_HEADING_TO_CTA_KEY` dict keyed on the heading text already mechanically extracted there (spec Section 7.1). It is **not** derived from `understanding.topic`, `response.answer`, or any other free-text/model-generated field (spec Section 7.3 explains why that was rejected).
2. **Matching is plain `dict.get()` — no `.lower()`, no `.strip()`, no transform of either side, ever** (spec Section 7.2, FR-2).
3. **The literal `"Learn More:\n{url}"` text concatenation does not happen inside `cta_node`, and does not happen inside any LLM-calling node.** It is a separate pure function, `format_final_response(safe_response, cta)`, in `app/services/cta_service.py`, intended to be called once — by whatever future code assembles the final user-facing turn — after `safety_node` has already produced `safety.safe_response` (spec Section 5.4). No single endpoint chains all the nodes end-to-end today (the live `/chat` endpoint still runs the separate, older `chat_chain`, `app/main.py:42,60-77`) — this is a pre-existing, already-documented gap (Phase 5 Section 13.8, Phase 6 Section 13.11), not something this phase needs to close. `format_final_response` is implemented and unit-tested now so the correct call site already exists when that integration eventually happens.

This is the **first node in the pipeline with zero LLM involvement** — no prompt file, no `FakeLLM`/`_FakeResponse` scaffolding needed anywhere in its own tests. Conventions confirmed from the live codebase: `app/validators/*.py` (e.g. `boundary_validator.py`) are the closest existing analog for "pure, deterministic, hand-testable Python with no model in the loop"; `app/nodes/empathy_node.py`/`app/nodes/safety_node.py` are the structural analog for the thin-node + `model_validator`-based Pydantic output + `build_*_graph()`-with-local-imports pattern this node still follows even without an LLM.

---

## Implementation Plan

Files in dependency order: config → ingestion script → state → service → node → models → wiring edits (`safety_node.py` required; `response_node.py`/`content_optimization_node.py`/`empathy_node.py` mechanical-but-recommended) → `main.py`.

### 1. `app/config.py` (edit)
Add one line to the `Settings` class, after the `content_optimization_*` block and before `def validate`:
```python
cta_links_path: Path = BASE_DIR / os.getenv("CTA_LINKS_PATH", "data/cta/cta_links.md")
```
Append the matching commented line to `.env.example`'s "Optional overrides" block: `# CTA_LINKS_PATH=data/cta/cta_links.md`.

### 2. `scripts/build_knowledge_index.py` (edit)
Per spec **Section 7.1**. Add a new module-level constant near the top of the file (after the existing imports, before `load_faq_chunks`):
```python
THERAPY_HEADING_TO_CTA_KEY = {
    "MNRI® (Masgutova Neurosensorimotor Reflex Integration)": "mnri",
    "Arrowsmith Program®": "arrowsmith",
    "Neurofeedback": "neurofeedback",
}
```
In `load_therapy_chunks` (`scripts/build_knowledge_index.py:53-83`), add one key to the existing `type_metadata` dict, immediately after `"conditions_addressed": ""`:
```python
"cta_key": THERAPY_HEADING_TO_CTA_KEY.get(therapy_name),
```
No other line in this function changes. Headings not present in the table (Feldenkrais, Jill Stowell, Access Consciousness, Vision Therapy, Lynn Valley, the Brain & Gut Health and Listening entries, and the non-named intro/outro chunks) get `cta_key: None`, which is correct — no CTA for those (spec Section 7.4). This script must be re-run after this edit lands (Verification Step 3) so existing Chroma chunks pick up the new metadata key — `collection.upsert(...)` is keyed by `chunk_id` and is therefore safe to re-run idempotently.

### 3. `app/graph/state.py` (edit)
Insert, verbatim from spec **Section 11.1**, a `CTA` TypedDict between the existing `Knowledge` and `Response` classes:
```python
class CTA(TypedDict):
    matched: bool
    cta_key: Optional[str]
    cta_url: Optional[str]
    source_chunk_id: Optional[str]
    lookup_time_ms: float
    error: Optional[str]
```
Add `cta: Optional[CTA]` to `GraphState`, between `knowledge` and `response` (spec Section 11.2):
```python
class GraphState(TypedDict):
    user_message: str
    chat_history: list[ChatTurn]
    understanding: Optional[Understanding]
    knowledge: Optional[Knowledge]
    cta: Optional[CTA]
    response: Optional[Response]
    content_optimization: Optional[ContentOptimization]
    empathy: Optional[Empathy]
    safety: Optional[Safety]
```
Strictly additive — no existing field changes shape.

### 4. `app/services/cta_service.py` (new)
Lift verbatim from spec **Section 12.1–12.4**:
- `logger = logging.getLogger("app.services.cta_service")`
- `_parse_line(line: str) -> Optional[tuple[str, str]]` — skips blank/`#`-prefixed lines; logs `WARNING` and skips on no `=`, empty key/value, or a non-`http(s)://` value.
- `load_cta_registry(path: Path) -> dict[str, str]` — reads the file once; returns `{}` and logs `ERROR` (never raises) on `OSError`; on a duplicate key, keeps the first definition and logs `WARNING`.
- `CTA_REGISTRY: dict[str, str] = load_cta_registry(settings.cta_links_path)` — module-level, loaded once at import time, mirroring `understanding_node.py`'s `_PROMPT_TEMPLATE` load pattern (`app/nodes/understanding_node.py:15-16`).
- `get_cta_url(cta_key: str) -> Optional[str]` — `CTA_REGISTRY.get(cta_key)`. Nothing else.
- `resolve_cta_key(retrieved_docs: list[dict]) -> tuple[Optional[str], Optional[str]]` — `(None, None)` on empty `retrieved_docs`; otherwise reads only `retrieved_docs[0].get("metadata", {}).get("cta_key")` plus its `chunk_id`.
- `format_final_response(safe_response: str, cta: dict) -> str` — returns `safe_response` unchanged when `not cta.get("matched")`, else `f"{safe_response}\n\nLearn More:\n{cta['cta_url']}"`.

### 5. `app/nodes/cta_node.py` (new)
Lift verbatim from spec **Section 11.4 / 12.5**:
- `CTAOutput(BaseModel)` with `_validate_match_consistency` (`matched=True` requires non-null `cta_key`/`cta_url`; `matched=False` forbids a non-null `cta_url` — `cta_key` may still be non-null on this branch, the "resolved but not in registry" case).
- `cta_node(state: GraphState) -> dict` — reads only `state.get("knowledge")`; on any unexpected exception inside its own try block, falls back to a hand-built `matched: False, error: "cta_lookup_failure"` dict that bypasses `CTAOutput` validation (same defensive shape as every other node's `_safe_fallback_result`); logs `INFO` on every invocation, `WARNING` when a resolved key isn't in the registry.
- `build_cta_graph()` — three-node isolated graph (`understanding_node -> knowledge_node -> cta_node`), local imports inside the function, matching `build_knowledge_graph()`'s exact shape.

### 6. `app/models.py` (edit)
Add `CTAResponse(BaseModel)` per spec **Section 11.3**, placed after the existing `ContentOptimizationResponse` block (the current last class in the file):
```python
class CTAResponse(BaseModel):
    matched: bool
    cta_key: str | None
    cta_url: str | None
    source_chunk_id: str | None
    lookup_time_ms: float
    error: str | None
```

### 7. `app/nodes/safety_node.py` (edit — required)
Per spec **Section 12.6**. In `build_safety_graph()` only: add `from app.nodes.cta_node import cta_node`, `graph.add_node("cta_node", cta_node)`, and replace the single edge `graph.add_edge("knowledge_node", "response_node")` with:
```python
graph.add_edge("knowledge_node", "cta_node")
graph.add_edge("cta_node", "response_node")
```
No change to `safety_node(...)` itself, `SafetyOutput`, or any other edge — `safety_node` remains entirely unaware `cta_node` exists. Resulting full graph order:
```
understanding_node → knowledge_node → cta_node → response_node →
content_optimization_node → empathy_node → safety_node → END
```

### 8. `app/nodes/response_node.py`, `content_optimization_node.py`, `empathy_node.py` (edit — mechanical, recommended)
Per spec Section 10/20 step 5: apply the identical two-line insertion (import `cta_node`, `add_node` + edge swap as in Step 7 above) to each file's own isolated `build_response_graph()` / `build_content_optimization_graph()` / `build_empathy_graph()`. This is purely so `state["cta"]` is present for anyone invoking one of those isolated graphs directly — none of those three node *functions* read `state["cta"]`, so skipping this step would not break anything, only leave `cta` absent from those particular isolated test graphs' output.

### 9. `app/main.py` (edit)
Import `CTAResponse` and `build_cta_graph`; add a `cta_graph = None` global; build it in `lifespan(...)` alongside the other graphs; add an endpoint mirroring `/knowledge`'s exact shape:
```python
@app.post("/cta", response_model=CTAResponse)
def cta_endpoint(request: ChatRequest):
    if cta_graph is None:
        raise HTTPException(status_code=503, detail="CTA node is still starting up")
    history = session_histories.get(request.session_id, [])
    result = cta_graph.invoke({
        "user_message": request.message,
        "chat_history": _history_to_chat_turns(history),
        "understanding": None,
        "knowledge": None,
        "cta": None,
    })
    return CTAResponse(**result["cta"])
```
Do not touch `/chat`/`build_chain()` — that migration remains explicitly deferred, same as every prior phase.

---

## Test Plan

### `tests/test_cta_node.py` (new)
No `FakeLLM` anywhere in this file — every case is a plain, synchronous unit test against pure functions and plain dicts, per spec Section 15.

**Registry parsing (`load_cta_registry` / `_parse_line`) — spec 15.1:**
1. Well-formed fixture file parses into the expected `dict[str, str]`.
2. Blank lines and `#`/`##`-prefixed lines are skipped, not loaded as keys.
3. A line with no `=` is skipped; `WARNING` logged (assert via `caplog`).
4. A line with empty key or empty value is skipped.
5. A line whose value isn't `http://`/`https://`-prefixed is skipped, with `WARNING`.
6. A duplicate key keeps the first definition; `WARNING` logged identifying the duplicate.
7. A nonexistent path returns `{}` and logs `ERROR`, without raising.
8. **Integrity check:** the fixture file's mtime and exact byte content are unchanged after the call (the automated proof of FR-6/AC-5 — never writes the registry).

**`resolve_cta_key` — spec 15.2:**
9. Empty `retrieved_docs` → `(None, None)`.
10. Top doc with no `metadata` key → `(None, None)`, no `KeyError`.
11. Top doc with `metadata={}` → `(None, None)`.
12. Top doc with `metadata={"cta_key": "mnri"}` → `("mnri", <chunk_id>)`.
13. Two docs, different `cta_key`s — only `retrieved_docs[0]`'s is ever read (FR-4).

**`get_cta_url` / `format_final_response` — spec 15.3:**
14. A key present in a monkeypatched fixture registry returns its exact URL.
15. A key absent from the registry returns `None`.
16. `format_final_response(matched=False)` returns the input string identically (not just `==` — same object/no extra whitespace).
17. `format_final_response(matched=True)` returns `f"{original}\n\nLearn More:\n{url}"` exactly.

**`cta_node` — spec 15.4:**
18. `knowledge.retrieved_docs[0].metadata.cta_key == "mnri"` (registry monkeypatched to include it) → `{"cta": {"matched": True, "cta_key": "mnri", "cta_url": "...", ...}}`.
19. `knowledge.retrieved_docs == []` → `matched: False, cta_key: None, cta_url: None, error: None`.
20. Top doc's `cta_key` set but absent from registry → `matched: False`, `cta_key` still populated, `error: None`, `WARNING` logged.
21. `lookup_time_ms` is always a non-negative float.
22. `CTAOutput`'s validator rejects a hand-built `matched=True, cta_url=None` payload, and rejects `matched=False, cta_url="..."`.
23. `state["knowledge"] is None` (upstream never ran) → `matched: False`, no exception.

**Integration / passthrough — spec 15.5:**
24. Build `build_safety_graph()` (post Step 7), with `response_node`/`content_optimization_node`/`empathy_node`/`safety_node`'s LLM calls replaced by minimal scripted fakes (reusing the existing `FakeLLM` fixture classes already defined in `tests/test_safety_node.py`/`tests/test_empathy_node.py` purely as test scaffolding — `cta_node` itself still needs none). Run one full turn with a `knowledge.retrieved_docs[0].metadata.cta_key == "mnri"` fixture and assert `result["cta"]` is byte-identical to what `cta_node` alone produces from the same `knowledge` input.
25. Same run: assert the literal CTA URL string does **not** appear anywhere inside `result["safety"]["safe_response"]` — proving it never passed through any LLM-touched field (FR-9, spec Section 5.4).

**Acceptance-level — spec 15.6:**
26. `test_mnri_example()` — `format_final_response(safety.safe_response, cta)` for the brief's Example 1 input ends with `"Learn More:\nhttps://manascience.webflow.io/post/mnri"`.
27. `test_hello_example()` — general-chat turn → final text identical to `safe_response`, no `"Learn More:"` substring.
28. `test_primitive_reflexes_example()` — retrieved doc with no `cta_key` → final text again has no `"Learn More:"` substring.

### Existing test files — no changes required
`tests/test_understanding_node.py`, `tests/test_knowledge_node.py`, `tests/test_response_node.py`, `tests/test_content_optimization_node.py`, `tests/test_empathy_node.py`, `tests/test_safety_node.py`, `tests/test_validators.py` — none of these nodes read or write `state["cta"]`, and `cta_node` requires zero changes to `knowledge_node.py` (the metadata passthrough that surfaces `cta_key` already exists, per spec Section 7.1). Confirm this remains true after Step 8's `build_*_graph()` edits, since those edits only add a node/edge, never change an existing node's function body.

---

## Verification

1. **New unit tests:**
   ```bash
   cd /home/user/NEW_manasi && source venv/bin/activate
   pytest tests/test_cta_node.py -v
   ```
2. **Full regression** (confirms the wiring edits to `safety_node.py`/`response_node.py`/`content_optimization_node.py`/`empathy_node.py` don't break any existing phase):
   ```bash
   pytest tests/ -v
   ```
   Baseline today: **106 passed**. Expect 106 + (new `test_cta_node.py` cases, ~28) passed, 0 failed.
3. **Re-run knowledge ingestion** to pick up the new `cta_key` metadata tag on existing chunks (safe to re-run — `collection.upsert(...)` is keyed by `chunk_id`):
   ```bash
   python scripts/build_knowledge_index.py
   ```
   Expect the same `Ingested N chunks...` summary line as before, with no count change (this is a metadata refresh, not a re-chunk).
4. **File-structure sanity check** — confirm every edit besides the three new files is wiring/config-only:
   ```bash
   git diff --stat app/graph/state.py app/config.py app/models.py app/main.py \
     app/nodes/safety_node.py app/nodes/response_node.py \
     app/nodes/content_optimization_node.py app/nodes/empathy_node.py \
     scripts/build_knowledge_index.py
   ```
5. **Registry integrity check** — direct, manual proof of FR-6/AC-5 surviving a real run, not just the unit-test fixture check in test #8:
   ```bash
   sha256sum data/cta/cta_links.md > /tmp/cta_before.sha256
   pytest tests/ -q && python scripts/build_knowledge_index.py
   sha256sum -c /tmp/cta_before.sha256
   ```
   Expect `data/cta/cta_links.md: OK`.
6. **Live smoke test** (an `OPENAI_API_KEY` is already present in `.env`):
   ```bash
   uvicorn app.main:app --reload &
   curl -s -X POST localhost:8000/cta -H "Content-Type: application/json" \
     -d '{"message": "What is MNRI?", "session_id": "smoke1"}' | python3 -m json.tool
   curl -s -X POST localhost:8000/cta -H "Content-Type: application/json" \
     -d '{"message": "Hello", "session_id": "smoke2"}' | python3 -m json.tool
   curl -s -X POST localhost:8000/cta -H "Content-Type: application/json" \
     -d '{"message": "What are primitive reflexes?", "session_id": "smoke3"}' | python3 -m json.tool
   ```
   Expect: call 1 → `{"matched": true, "cta_key": "mnri", "cta_url": "https://manascience.webflow.io/post/mnri", ...}`; call 2 → `{"matched": false, "cta_key": null, "cta_url": null, ...}` (general-chat, no retrieval); call 3 → `{"matched": false, ...}` with `cta_key` either `null` or a non-`mnri`/`arrowsmith`/`neurofeedback` heading not in `THERAPY_HEADING_TO_CTA_KEY` (depending on which chunk retrieval ranks highest). Stop the server afterward (`kill %1` or `fg` + Ctrl-C).
7. **Full-chain smoke test** — confirm `cta` survives through `build_safety_graph()`'s now-seven-node order:
   ```bash
   curl -s -X POST localhost:8000/safety -H "Content-Type: application/json" \
     -d '{"message": "What is MNRI?", "session_id": "smoke4"}' | python3 -m json.tool
   ```
   This still returns the existing `SafetyResponse` shape (no `cta` field in the response model, since `/safety` was never asked to expose it) — the point of this check is simply that the call succeeds end-to-end through the seven-node graph without an exception, proving `cta_node`'s insertion didn't break `response_node`/`content_optimization_node`/`empathy_node`/`safety_node`'s existing behavior.
