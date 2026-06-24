# Implementation Plan — Manasi AI Phase 6: Content Optimization Node

**Source spec:** `.claude/spec/manasi-ai-phase6-content-optimization-node-spec.md` (1241 lines, authored this session, read in full)

## Context

Phases 1–5 are implemented and working: `understanding_node -> knowledge_node -> response_node -> empathy_node -> safety_node` (`build_safety_graph()` in `app/nodes/safety_node.py`). `response_node` has no upper bound on answer length, and `empathy_node` is explicitly *not* a second generation layer (Phase 4 FR-8) — neither compresses, titles, or extracts structure from content. The Phase 6 spec adds a new node, `content_optimization_node`, that runs **between `response_node` and `empathy_node`** (confirmed earlier in this session via user decision — this differs from the product brief's literal diagram, which doesn't match the real pipeline; the brief's own source list, "LLM-generated content"/"mixed RAG+LLM," only makes sense post-`response_node`). It normalizes whatever it receives into a small structured payload (`title`, `summary`, `description`, `key_points`, `content_type`, `source_type`, `confidence_score`) plus full metadata passthrough, then `empathy_node` is updated to humanize `content_optimization["summary"]` instead of `response["answer"]`. `safety_node` requires no logic changes — only a graph-wiring update — since `source`/`confidence` continue to pass straight through unchanged (spec Section 13.6).

Conventions confirmed directly from the live codebase: `app/nodes/empathy_node.py` / `app/services/empathy_service.py` are the direct structural analogs (thin node + business-logic service, `model_validator`-based Pydantic output model, `_safe_fallback_result(...)` that bypasses Pydantic validation, `build_*_graph()` with sibling-node imports done locally inside the function). `app/config.py` is a flat `Settings` class, one `os.getenv(...)` line per field. `app/prompts/*.txt` use plain-text `{{placeholder}}` string-replace substitution. Test files (`tests/test_empathy_node.py`) use a `FakeLLM`/`_FakeResponse` class duplicated per file, inline builder functions (`make_response`, `make_state`), no `conftest.py`.

**Confirmed test-file impact:** `tests/test_empathy_node.py`'s `make_state(...)` currently builds `state["response"] = make_response(...)` and asserts against `response["answer"]`; since `empathy_node` will now read `state["content_optimization"]` instead, this file's fixtures must be rewritten (`make_response` → `make_content_optimization`, `answer` key → `summary` key, all `response["answer"]` assertions → `content_optimization["summary"]`). `tests/test_safety_node.py` builds `state["empathy"]` directly (never runs `empathy_node` or reads `state["response"]`/`state["content_optimization"]`) — **confirmed no changes needed there.**

---

## Implementation Plan

Files in dependency order: config → prompt → service → state/models edits → node → empathy edits → safety wiring → main.py → tests.

### 1. `app/config.py` (edit)
Append after the existing `safety_*` settings block, before `def validate`, per spec Section 14.5:
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
Also append the matching commented block to `.env.example` mirroring the existing `# SAFETY_*` lines.

### 2. `app/prompts/content_optimization_prompt.txt` (new)
Copy from spec **Section 12.3** (the full `text` fence block) — placeholders `{{summary_min_words}}`, `{{summary_max_words}}`, `{{description_min_words}}`, `{{description_max_words}}`, `{{title_instructions}}`, `{{content_text}}`. Plain text, same substitution style as `safety_prompt.txt`/`empathy_prompt.txt`.

### 3. `app/services/content_optimization_service.py` (new)
The business-logic core, direct analog of `empathy_service.py`/`response_generator.py`, combined with a small adapter layer (spec Section 6).

**`RawContentInput` (TypedDict):** `text`, `title: Optional[str]`, `source_type`, `content_type: Optional[str]`, `metadata: dict` (spec Section 6.1).

**Adapters (spec Section 6.2–6.3):**
- `_from_pipeline_state(response: dict, knowledge: dict) -> dict` — the only one wired into the live graph; lift verbatim from spec Section 6.3.
- `_from_markdown_file(path) -> dict`, `_from_webflow_cms_item(item) -> dict`, `_from_chroma_chunks(docs) -> dict`, `_from_raw_text(text, *, source_type, title=None, content_type=None) -> dict` — implemented per their spec Section 6.2 responsibilities; these are not called by the live graph but must exist and be unit-tested directly (spec FR-1 / Section 16.6's source-agnosticism acceptance criterion).

**Constants:** `CONTENT_OPTIMIZATION_BANNED_PHRASES` (Section 7.6), `TITLE_INSTRUCTIONS_KNOWN`/`_UNKNOWN` (Section 12.3), corrective suffixes `CORRECTIVE_REPROMPT_SUFFIX_FABRICATION`/`_FACT_DROP`/`_LENGTH`/`_REFUSAL`/`_MALFORMED` (Section 7.7).

**Guard/helper functions:**
- `_significant_tokens(text)` — reuse the exact regex approach from `empathy_service.py:105-111` (numbers/percentages + capitalized multi-letter words).
- `_fact_retention_ratio(source_text, summary)` / `_fails_fact_retention(...)` (Section 7.4).
- `_fabrication_ratio(source_text, summary)` / `_fails_fabrication(...)` (Section 7.5).
- `_target_bounds(original_word_count, floor, ceiling) -> tuple[int, int]` (Section 7.3).
- `_is_valid_title_candidate(candidate, body_text) -> bool` (Section 8.2).
- `_resolve_title(raw, llm_candidate, body_text) -> Optional[str]` (Section 8.1).
- `_confidence_score(*, source_type, used_llm_call, retries_used, fact_retention_ratio, hit_length_cap) -> float` — lift verbatim formula from Section 10.2.
- `_contains_banned_phrase(text)`, `_count_violations(...)`, `_corrective_suffix_for(...)` — mirror the shape of `response_generator.py`'s equivalents, covering all four guards.
- `_strip_code_fences(...)`, `_parse_optimization_result(raw_text) -> dict` (parses `{title, summary, description, key_points}`), `_build_llm()` (`ChatOpenAI(model=settings.content_optimization_model, temperature=settings.content_optimization_temperature)`), `_invoke(llm, prompt)`.
- `_skip_result(raw: dict) -> dict` (Section 7.1 — zero-LLM-call deterministic path).
- `_select_fallback(raw: dict, llm_call_failed: bool) -> dict` (Section 11.8 — never-block path).

**Entry point — `optimize_content(raw: dict, llm: Optional[Any] = None) -> dict`:**
1. Skip check (Section 7.1): `raw.get("error")` is not applicable here (adapters don't carry `error` — that check happens one level up in the node, see Step 6 below) — within the service, skip purely on `len(raw["text"].split()) < settings.content_optimization_skip_min_words`. Return `_skip_result(raw)` with `error=None`.
2. Otherwise run the attempt loop (max `1 + settings.content_optimization_max_retries`): compute `_target_bounds` for summary/description, pick `TITLE_INSTRUCTIONS_KNOWN`/`_UNKNOWN` based on `raw["title"]`, build prompt, invoke LLM, parse JSON, resolve title via `_resolve_title`, run all four guards, accept on clean pass, else build corrective suffix and retry.
3. On exhaustion (guard failure both attempts, or LLM raises both attempts): return `_select_fallback(raw, llm_call_failed=...)`.
4. Compute `confidence_score` from the final selected attempt's signals (Section 11.7 — after retry resolution, never mid-retry).
5. Never raises; always returns a dict with `title`, `summary`, `description`, `key_points`, `content_type`, `source_type`, `confidence_score`, `error` — the *non-passthrough* subset of the schema. (Passthrough fields `source`/`answer_type`/`topic`/`intent`/`confidence`/`grounded_chunk_ids`/`original_answer` are assigned by the **node**, not the service, mirroring how `generate_response`/`humanize_response` never produce their own passthrough fields either.)

### 4. `app/graph/state.py` (edit)
Insert a `ContentOptimization` TypedDict between the existing `Response` and `Empathy` classes, and add `content_optimization: Optional[ContentOptimization]` to `GraphState` between `response` and `empathy`, lifted verbatim from spec **Section 13.4**. Strictly additive otherwise.

### 5. `app/models.py` (edit)
Add `ContentOptimizationResponse(BaseModel)` per spec **Section 14.6**, placed after `SafetyResponse`.

### 6. `app/nodes/content_optimization_node.py` (new)
Thin wrapper mirroring `empathy_node.py`.
- `ContentOptimizationOutput(BaseModel)` with the `model_validator`s lifted verbatim from spec **Section 11.4** (`_validate_summary_quality`, `_validate_key_points_shape`, `_validate_confidence_score_bounds`).
- `content_optimization_node(state, llm=None) -> dict`: builds `raw = _from_pipeline_state(state["response"], state["knowledge"])`; if `state["response"]["error"] is not None`, short-circuits straight to `_skip_result(raw)`-equivalent (the `response.error` check belongs at the node level since it's a `GraphState`-specific signal the service layer doesn't otherwise see, per spec Section 7.1 condition 1) — otherwise calls `optimize_content(raw, llm=llm)`; assembles the full output dict by merging the service's result with the node's own passthrough assignment (`source`, `answer_type`, `topic`, `intent`, `confidence`, `grounded_chunk_ids` copied from `state["response"]`; `original_answer = state["response"]["answer"]`); times the call; validates via `ContentOptimizationOutput`; returns `{"content_optimization": {...}}`.
- `_safe_fallback_result(response, knowledge, start)` — hand-built, bypasses `ContentOptimizationOutput` validation entirely (belt-and-suspenders for an unexpected exception escaping `optimize_content` itself, which is contracted never to raise — same defensive pattern as every other node's fallback helper).
- `build_content_optimization_graph()` — lift verbatim from spec **Section 13.8** (five-node chain: understanding → knowledge → response → content_optimization → empathy).

### 7. `app/nodes/empathy_node.py` (edit)
Per spec **Section 13.5**: change `response = state["response"]` to `content_optimization = state["content_optimization"]`, and pass `content_optimization` (renamed locally to whatever variable name, but the dict itself) into `humanize_response(...)`. Update `_safe_fallback_result` to read from `content_optimization` (e.g. `content_optimization.get("summary", "")` instead of `response.get("answer", "")`) and update `build_empathy_graph()` to insert `content_optimization_node` into its own chain (`response_node -> content_optimization_node -> empathy_node`) so Phase 4's isolated test graph stays internally consistent.

### 8. `app/services/empathy_service.py` (edit)
Per spec Section 13.5: in `humanize_response(response: dict, emotional_state, llm=None)`, change the one read `answer = response["answer"]` to `answer = response["summary"]`. Everything else (`EMOTIONAL_TONE_INSTRUCTIONS`, guard functions, retry loop, the final passthrough dict's `response["source"]`/`response["answer_type"]`/etc. reads) is unchanged, since `content_optimization` carries every one of those keys with identical names. (Keep the parameter name `response` as-is to minimize the diff — it's now semantically "the dict the node handed me," matching the existing pattern where the parameter name doesn't encode its caller's identity.)

### 9. `app/nodes/safety_node.py` (edit)
Per spec **Section 13.8**: in `build_safety_graph()` only, add `from app.nodes.content_optimization_node import content_optimization_node`, `graph.add_node("content_optimization_node", content_optimization_node)`, and change the edges so `response_node -> content_optimization_node -> empathy_node` (removing the direct `response_node -> empathy_node` edge). No change to `safety_node(...)` itself or `SafetyOutput` — confirmed by Section 13.6's passthrough argument.

### 10. `app/main.py` (edit)
Per spec **Section 13.10/14.7**: import `ContentOptimizationResponse` and `build_content_optimization_graph`; add a `content_optimization_graph = None` global, build it in `lifespan(...)`; add `POST /optimize-content` mirroring `/humanize`'s exact shape. Update the `empathy_graph`/`build_empathy_graph()` and `safety_graph`/`build_safety_graph()` call sites only if their internal wiring changed shape (it has, per steps 7 and 9) — no endpoint signature changes needed for `/humanize` or `/safety` themselves. **Do not touch** `/chat`/`build_chain()` (that migration remains explicitly deferred, per every prior phase's spec).

---

## Test Plan

### `tests/test_content_optimization_node.py` (new)
Same structure as `test_empathy_node.py` (`FakeLLM`/`_FakeResponse`, inline builders, no `conftest.py`). Cases drawn from spec Section 16.1–16.6:
1. Happy path — RAG-grounded input produces schema-complete output with all passthrough fields correct.
2. Title taken from `knowledge.retrieved_docs[0]["source_title"]`, never overridden even if the LLM JSON proposes a different one.
3. No structural title + no valid LLM candidate → `title is None`.
4. LLM-proposed title >8 words → rejected → `None`.
5. LLM-proposed title sharing no significant token with the body → rejected → `None`.
6. Short input (`< summary_min_words`) does not get padded — output word count ≈ input word count, not stretched to the floor.
7. Fabrication guard triggers retry then succeeds (assert corrective suffix in `llm.calls[1]`).
8. Fact-retention guard triggers retry then succeeds.
9. Length-bound guard triggers retry then succeeds.
10. Banned-refusal-phrase guard triggers retry then succeeds.
11. Both attempts fail a guard → fallback: `summary == raw text`, `key_points == []`, `confidence_score == 0.3`, `error == "quality_guard_exhausted"`.
12. Both LLM calls raise → fallback with `error == "llm_call_failure"`.
13. Malformed JSON on attempt 1 → retries → succeeds on attempt 2.
14. Skip path: input below `content_optimization_skip_min_words` → zero LLM calls, `key_points == []`.
15. Skip path: `state["response"]["error"]` set → zero LLM calls regardless of word count.
16. `confidence_score` lower for `source_type == "llm"` than an equivalent `source_type == "rag"` case.
17. `confidence_score` lower when a retry was consumed vs. a clean first attempt.
18. State immutability — `state["response"]`/`state["knowledge"]` unchanged after the call.
19. Node does not read `user_message`, `chat_history`, or `understanding`.
20. `key_points` deduplicated case-insensitively when the LLM returns near-duplicate entries.
21. Source-agnosticism: feed equivalent text through `_from_pipeline_state` and `_from_raw_text` directly (bypassing the node) and confirm both produce schema-valid `optimize_content(...)` output differing only in `source_type`/`title` resolution.
22. `_from_markdown_file`/`_from_webflow_cms_item`/`_from_chroma_chunks` each unit-tested directly against a small fixture, confirming correct `RawContentInput` field mapping.

### `tests/test_empathy_node.py` (edit — required, not optional)
Rewrite fixtures to match the new input contract:
- Rename `make_response(...)` → `make_content_optimization(...)`, with its first parameter renamed `answer` → `summary`, and the returned dict's `"answer"` key renamed to `"summary"` (add the remaining `ContentOptimization`-only fields — `title`, `description`, `key_points`, `content_type`, `source_type`, `confidence_score`, `original_answer`, `optimization_time_ms` — with simple realistic defaults so the fixture is schema-shaped, even though `empathy_node` itself only reads a subset).
- `make_state(...)`: replace its `response=` parameter/key with `content_optimization=`, defaulting to `make_content_optimization()`; keep `"response": None` in the returned state dict (still a valid `GraphState` shape, just unused by `empathy_node` after the edit).
- Update every assertion currently comparing against `response["answer"]` (e.g. `test_happy_path_passes_through_all_response_fields`, `test_both_attempts_fail_guard_falls_back_to_verbatim_answer`, `test_both_llm_calls_raise_falls_back_with_llm_call_failure`, `test_mixed_failure_first_raises_second_fails_guard_is_quality_guard_exhausted`) to compare against `content_optimization["summary"]` instead. Function/test names may stay as-is (they still describe true behavior) except where "response" appears in a name describing the *fixture* rather than the behavior — leave names alone to minimize churn; only fixture plumbing changes.
- `test_node_does_not_read_user_message_or_chat_history_or_knowledge` → rename to `..._or_understanding`'s sibling stays correct as-is (it already doesn't test `content_optimization` exclusion, no change needed beyond the fixture rename cascading through it).

### `tests/test_safety_node.py`
**No changes** — confirmed above; it builds `state["empathy"]` directly and never touches `state["response"]`/`state["content_optimization"]`.

---

## Verification

1. **New unit tests:**
   ```bash
   cd /home/user/NEW_manasi && source venv/bin/activate
   pytest tests/test_content_optimization_node.py -v
   ```
2. **Full regression** (confirms the `empathy_node`/`empathy_service` edits don't break Phase 4's existing guarantees, and Phase 5 needs zero changes):
   ```bash
   pytest tests/ -v
   ```
   Baseline today: 78 passed. Expect 78 + (new content-optimization tests) passed, 0 failed.
3. **File-structure sanity check** — confirm edits to `app/graph/state.py`, `app/nodes/safety_node.py` are additive/wiring-only, and the pre-existing `RetrievedDocument.content_type` typo (`practit_info`/etc., out of scope) remains untouched:
   ```bash
   git diff --stat app/graph/state.py app/nodes/safety_node.py app/nodes/empathy_node.py app/services/empathy_service.py app/config.py app/models.py app/main.py
   ```
4. **Live smoke test** (an `OPENAI_API_KEY` is already present in `.env`):
   ```bash
   uvicorn app.main:app --reload &
   curl -s -X POST localhost:8000/optimize-content -H "Content-Type: application/json" \
     -d '{"message": "What is neuroplasticity?", "session_id": "smoke1"}' | python3 -m json.tool
   curl -s -X POST localhost:8000/humanize -H "Content-Type: application/json" \
     -d '{"message": "What is neuroplasticity?", "session_id": "smoke2"}' | python3 -m json.tool
   curl -s -X POST localhost:8000/safety -H "Content-Type: application/json" \
     -d '{"message": "What is neuroplasticity?", "session_id": "smoke3"}' | python3 -m json.tool
   ```
   Expect: call 1 → schema-valid `content_optimization` JSON with a non-null `title` (RAG-grounded) and `summary` close in substance to a typical Phase 3 answer; call 2 → `/humanize`'s `final_answer` reads as a humanized version of that same summary; call 3 → `/safety` still resolves correctly end-to-end through the now-six-node production chain. Stop the server afterward (`kill %1` or `fg`+Ctrl-C).
5. **Save the plan artifact** to `.claude/plan/manasi-ai-phase6-content-optimization-node-plan.md`, matching the existing repo convention (`manasi-ai-phase3-...-plan.md`, `manasi-ai-phase5-...-plan.md`), as the first action after this plan is approved.
