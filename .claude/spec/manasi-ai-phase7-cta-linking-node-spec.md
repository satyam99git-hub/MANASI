# Manasi AI — Phase 7 Technical Specification
## CTA (Call-To-Action) Linking System

**Project:** Manasi AI
**Organization:** ManaScience
**Phase:** 7 of N — CTA Linking Node
**Status:** Draft for implementation
**Audience:** Python + FastAPI + LangGraph engineer
**Depends on:** Phase 1 — Understanding Node (`manasi-ai-phase1-understanding-node-spec.md`), Phase 2 — Knowledge Node (`manasi-ai-phase2-knowledge-node-spec.md`), Phase 3 — Response Generation Node (`manasi-ai-phase3-response-generation-node-spec.md`), Phase 6 — Content Optimization Node (`manasi-ai-phase6-content-optimization-node-spec.md`), Phase 4 — Empathy & Personality Node (`manasi-ai-phase4-empathy-personality-node-spec.md`), Phase 5 — Safety, Trust & Boundary Node (`manasi-ai-phase5-safety-trust-boundary-node-spec.md`)
**Runtime position note:** The CTA Node's *lookup* runs immediately after `knowledge_node` and before `response_node`, exactly where the product brief's diagram places it. The brief's diagram, however, predates Phases 5 and 6 and does not show `content_optimization_node` or `safety_node`, both of which already sit downstream of `response_node` in the real production graph (`build_safety_graph()`, `app/nodes/safety_node.py:136-162`). This document keeps the CTA Node's position as specified, carries its result untouched through all four downstream nodes, and is explicit that the literal text concatenation happens once, after `safety_node`, not inside the CTA Node itself — see Section 5.4 for the full rationale.

---

## 0. Non-Negotiable Constraints (recap of the product brief)

These are restated verbatim-in-spirit because every design decision in this document is constrained by them, and any future change to this spec must be checked against this list first:

* The CTA registry (`data/cta/cta_links.md`) is manually maintained and is the single source of truth.
* The system MUST NOT: rename CTA keys, normalize CTA keys, generate CTA links, modify CTA entries, use fuzzy matching, use semantic matching, guess links, or rewrite the CTA file.
* All lookups MUST use exact matching.
* The system MUST be lightweight, deterministic, and independent of the LLM.
* Answer normally; identify the most relevant knowledge document; retrieve its corresponding CTA link; append it; return a normal response with no CTA when none exists.

---

## 1. Executive Summary

By the end of Phase 6, Manasi has an answer that is accurate (Phase 3), shaped (Phase 6), warm (Phase 4), and safety-reviewed (Phase 5). Nothing in Phases 1–6 ever points the user *off* the chat turn and onto the ManaScience platform itself — every answer, however good, is a dead end. ManaScience already maintains a hand-curated map from specific topics (MNRI, ADHD, the subscription page, the community hub) to specific Webflow URLs; today nothing in the pipeline reads it.

This document specifies **Phase 7 only: the CTA Linking Node** — a small, fifth node inserted into the existing six-node graph, whose entire job is to ask one question every turn: *does the knowledge this turn was actually grounded in have a manually-registered CTA link attached to it?* If yes, the link rides through the rest of the pipeline unchanged and is appended to the final response. If no, nothing happens and the turn proceeds exactly as it does today. The node makes no LLM call, has no prompt file, and cannot itself alter, rename, or regenerate a single entry in `data/cta/cta_links.md`.

The node's single most important behavioral guarantee is the inverse of every other phase's: where Phases 1–6 are about producing something new and are explicitly tuned to *never block*, Phase 7 is about **never inventing**. It would rather attach no link at all than attach a wrong, guessed, or fuzzily-matched one. A missing CTA is a correct, expected outcome (Section 0); a guessed one is a defect.

---

## 2. Business Requirements

* **BR-1 — Drive platform engagement from informational answers.** A user who asks "What is MNRI?" and gets a good answer has no reason to ever visit `manascience.webflow.io`. A correctly-attached CTA is the only thing in the current pipeline that converts an answer into a platform visit.
* **BR-2 — Never let an AI-controlled process touch monetization- or trust-relevant links.** `subscription`, `community_hub`, and `about` are business-critical URLs. The brief's prohibition on fuzzy matching, semantic matching, and link generation (Section 0) exists specifically so these links are never auto-derived by a model that could hallucinate or drift — a human chooses, by editing one file, which conversations are CTA-eligible and what each CTA points to.
* **BR-3 — Keep the registry editable by non-engineers without a deploy.** `data/cta/cta_links.md` is plain `key=value` text. A content/marketing owner should be able to add a row and have it take effect on the next process restart, without touching Python.
* **BR-4 — Support a future CMS migration without a pipeline rewrite.** The brief calls this out explicitly ("Support future CMS migration"). The node's contract with the rest of the system is `{key} -> {url}`; whether that mapping is parsed from a Markdown file today or fetched from a Webflow/CMS API tomorrow is an internal detail of one function (Section 12, `load_cta_registry`), not a change to `cta_node`, `GraphState`, or any downstream node.
* **BR-5 — Zero added latency or cost budget for a feature that isn't core to answering the question.** Every other node in this pipeline spends an LLM call's worth of latency and money. This one must not, because its value (a link) is strictly additive to an answer that is already complete without it.

---

## 3. Functional Requirements

### FR-1: Deterministic, LLM-Free Lookup
The node SHALL resolve a CTA link using only Python control flow and a single in-memory dictionary lookup. It SHALL NOT invoke a chat completion model, embedding model, or any other generative or similarity-based component at any point.

### FR-2: Exact-Match-Only Resolution
The node SHALL resolve a CTA URL only when a lookup key resolves, via exact string equality (`dict.get`), to a key already present in the parsed registry (Section 12.1). It SHALL NOT lowercase, strip punctuation from, stem, embed, or otherwise transform a registry key for comparison purposes (Section 7.2 explains the one narrow exception, which is not a transform of the registry).

### FR-3: Source of the Lookup Key Is Never Free Text
The node SHALL derive its lookup key only from `state["knowledge"]["retrieved_docs"][0]["metadata"]["cta_key"]` — a value set once, manually, at knowledge-ingestion time (Section 7.1) — and SHALL NOT derive it from `understanding["topic"]`, `understanding["search_query"]`, `response["answer"]`, or any other LLM-generated free text. Section 7.3 explains why free-text fields are categorically unsuitable for an exact-match contract.

### FR-4: Single-Document Scope
The node SHALL consider only the single most relevant retrieved document, `retrieved_docs[0]` (already similarity-ranked by `knowledge_node`, `app/nodes/knowledge_node.py:106-112`). It SHALL NOT inspect, merge, or fall back to `retrieved_docs[1:]`.

### FR-5: No-Match Is a Valid, Non-Error Outcome
When `retrieved_docs` is empty, the top document has no `cta_key`, or the resolved `cta_key` is not present in the registry, the node SHALL return `matched: false`, `cta_url: null`, and `error: null` — this is a normal, expected result (Section 0's "return a normal response if no CTA exists"), not a failure condition.

### FR-6: Read-Only Registry Access
The node, and every function it calls, SHALL treat `data/cta/cta_links.md` as strictly read-only. No code path introduced by this phase SHALL write to, rename, or otherwise mutate that file.

### FR-7: No Per-Request File I/O
The registry SHALL be parsed once per process lifetime, at import time (Section 12.3), and held in memory. The node SHALL NOT re-read the file from disk on every invocation.

### FR-8: Never Block the Pipeline
A missing, unreadable, or malformed registry file SHALL NOT raise an exception that propagates out of `cta_node`. The node SHALL fall back to an empty registry (every lookup resolves to "no CTA") and SHALL log the condition (Section 13).

### FR-9: The URL Reaches the User Unmodified by Any LLM
Once a `cta_url` is resolved, no node downstream of `cta_node` (`response_node`, `content_optimization_node`, `empathy_node`, `safety_node`) SHALL read, rewrite, summarize, or otherwise process it. The literal text concatenation onto the user-facing string SHALL happen exactly once, after all LLM-driven processing for the turn has finished (Section 5.4).

### FR-10: Statelessness
The node SHALL be a pure function of `state["knowledge"]["retrieved_docs"]`. It SHALL NOT mutate any input state object and SHALL NOT persist any per-turn data across invocations (the registry itself is process-lifetime, not per-turn, state).

### FR-11: Structured Output Only
The node SHALL emit a single JSON-serializable object conforming to Section 11. It SHALL NOT wrap its output in commentary or prose, since nothing about this node's output is ever shown to a user directly — it is consumed by the final-assembly step (Section 5.4) and by observability tooling (Section 13).

---

## 4. Non-Functional Requirements

### 4.1 General NFRs

| Category | Requirement |
|---|---|
| **Latency** | The node SHALL complete in under 5ms p99. There is no LLM call and no network call on any path (FR-1, FR-7) — the entire operation is a Python dict lookup. |
| **Reliability** | The node MUST always return a valid, schema-conforming output (Section 11). It MUST NOT raise an unhandled exception under any input, including a missing or malformed registry file (FR-8). |
| **Determinism** | Given the same `retrieved_docs[0]` and the same registry contents, the node MUST return byte-identical output on every invocation, with no temperature, sampling, or model-version dependency of any kind — the strongest determinism guarantee of any node in this pipeline. |
| **Statelessness** | Per FR-10. The node SHALL NOT call the retriever, the embeddings model, or any chat model directly. |
| **Testability** | The node MUST be unit-testable with zero mocking infrastructure — no `FakeLLM` (Phase 1/4/5/6's pattern) is needed at all, since there is nothing generative to fake (Section 15). |
| **Observability** | Every invocation SHOULD log `matched`, `cta_key`, and `lookup_time_ms`. A resolved key that is *not* found in the registry (an authoring-drift signal) SHOULD log at WARNING, distinct from the ordinary "no key present" case, which logs at INFO (Section 13). |
| **Cost** | Zero marginal cost per turn — no API call of any kind is made (BR-5). |
| **Integrity** | The registry file's mtime and content hash MUST be unchanged after any number of node invocations (FR-6) — this is a direct, testable expression of "never rewrite the CTA file" (Section 15.4). |

### 4.2 Performance Targets

| Metric | Target | Notes |
|---|---|---|
| Per-invocation lookup latency | p99 < 1ms | One dict `.get()` plus a handful of attribute accesses; no I/O on the hot path (FR-7). |
| One-time registry parse latency (process startup) | < 10ms | Parsing ~20 lines of `key=value` text (Section 12.1); re-measured only if the registry grows by orders of magnitude. |
| Memory footprint | O(number of registry entries) | A single `dict[str, str]`; 16 entries today (Section 6.1), negligible at any realistic scale for a hand-maintained file. |
| Added latency to a full pipeline turn | < 5ms out of a multi-second turn | Negligible relative to the ~1,800–2,500ms p95 of any single LLM-calling node in this pipeline (Phase 5 Section 4.2, Phase 6 Section 4.2). |

---

## 5. Architecture Design

### 5.1 Position in the Pipeline

```
understanding_node
      │
      ▼
knowledge_node          (produces retrieved_docs, source, confidence)
      │
      ▼
cta_node                 ◄── THIS SPEC (Phase 7)
      │   produces: matched, cta_key, cta_url, source_chunk_id
      ▼
response_node           (Phase 3; unaware cta_node ran — reads knowledge/understanding only)
      │
      ▼
content_optimization_node (Phase 6; unaware cta_node ran)
      │
      ▼
empathy_node             (Phase 4; unaware cta_node ran)
      │
      ▼
safety_node              (Phase 5; unaware cta_node ran)
      │
      ▼
format_final_response()  ◄── deterministic, code-only, appends cta.cta_url iff matched (Section 5.4)
      │
      ▼
END — text returned to user
```

### 5.2 Why Immediately After `knowledge_node`

`cta_node` needs exactly one thing: the metadata of the document `knowledge_node` decided was most relevant for this turn (Section 7.1). That document is fully resolved the moment `knowledge_node` returns — nothing `response_node`, `content_optimization_node`, `empathy_node`, or `safety_node` do afterward changes which document was retrieved or what its metadata says. Running the lookup this early also means a CTA decision is available, logged, and testable independently of whether the rest of the turn's generation succeeds or falls back — consistent with this codebase's existing practice of giving each node the narrowest input slice it actually needs (Phase 4 Section 11.2, Phase 6 FR-15).

### 5.3 Why the Node Itself Never Touches Response Text

`cta_node` returns a structured fact (`{matched, cta_key, cta_url, ...}`), never a piece of user-facing prose. This is a deliberate, narrower contract than every other node in the pipeline, which each produce or rewrite an `answer`/`summary`/`final_answer`/`safe_response` string. Giving `cta_node` that same kind of responsibility — e.g., having it eagerly append `"\n\nLearn More:\n{url}"` onto something — would require it to either (a) write into a field three more LLM-driven nodes are about to read and reprocess, risking the URL being paraphrased, summarized away, or "humanized" into something that no longer renders as a clickable link, or (b) own the very last field in the chain, which contradicts its specified position right after `knowledge_node`. Keeping its output purely structured data sidesteps both problems.

### 5.4 Where the Literal Append Actually Happens — and Why It Must Not Be Inside `empathy_node` or `safety_node`

This is the one place this document deliberately goes beyond a literal reading of the brief's flow diagram, and the reasoning matters enough to spell out:

If the CTA text were concatenated onto the answer anywhere between `cta_node` and `safety_node`, it would pass through `content_optimization_node` (which compresses and restructures text, Phase 6 FR-3), `empathy_node` (which rewrites text for tone, Phase 4 FR-1), and `safety_node` (which is explicitly permitted to rewrite or replace text when a guard fires, Phase 5 FR-8). Every one of those is a generative or rewriting step. A URL sitting inside the text handed to any of them is at risk of being shortened, dropped as "not load-bearing," reworded, or caught by a length guard — and Section 0's prohibition on the system ever **modifying CTA entries** is most naturally read to include "modifying them in transit," not just in the registry file.

The node therefore does not append anything itself. It resolves and carries `{matched, cta_url}` as ordinary state (Section 5.1) — untouched by every downstream node, the same way `understanding` and `knowledge` already ride untouched all the way to `safety_node` today without any node needing to special-case preserving them (LangGraph's partial-dict state merge does this for free; no node has ever needed to explicitly pass through a field it doesn't use). The literal string concatenation happens in exactly one place: `format_final_response()` (Section 12.4), a pure, code-only function called once `safety_node` has produced `safety.safe_response` — the system's already-established "final word for a turn" (Phase 5 Section 4.1, "Finality"). This is the only point in the pipeline guaranteed to run after every LLM call for the turn has already completed.

Today, no single endpoint chains all seven nodes end-to-end yet — each phase ships its own isolated `build_*_graph()` and the live `/chat` endpoint still runs the older, separate `chat_chain` (`app/main.py:42,60-77`), a gap already flagged as a deferred integration milestone in Phase 5 (Section 13.8) and Phase 6 (Section 13.11). `format_final_response()` is specified now, as a small pure function in `app/services/cta_service.py` (Section 12.4), precisely so that whoever eventually wires the one production graph has the correct, already-tested place to call it — rather than that future integration work inventing its own ad hoc string concatenation.

---

## 6. System Components

| Component | Responsibility | LLM involved? |
|---|---|---|
| `data/cta/cta_links.md` | The manually maintained, single-source-of-truth registry. Read-only at runtime. | No |
| `app/services/cta_service.py` (new) | Parses the registry once; exposes `get_cta_url`, `resolve_cta_key`, `format_final_response`. All business logic lives here, mirroring `empathy_service.py`/`safety_service.py`'s role for their phases. | No |
| `app/nodes/cta_node.py` (new) | Thin LangGraph orchestration: reads `state["knowledge"]`, calls `cta_service`, validates, times, logs, returns `{"cta": {...}}`. | No |
| `app/graph/state.py` (edited) | Adds the `CTA` TypedDict and `cta: Optional[CTA]` to `GraphState`. | — |
| `scripts/build_knowledge_index.py` (edited) | The one ingestion-time location where a human tags a knowledge chunk with the `cta_key` it corresponds to (Section 7.1). | No |
| `app/config.py` (edited) | Adds `cta_links_path` so the registry location is environment-configurable, matching every other phase's config pattern. | — |
| `app/models.py` / `app/main.py` (edited) | `CTAResponse` Pydantic model and a `/cta` isolated-testing endpoint, mirroring `/knowledge`, `/respond`, etc. | — |

### 6.1 The Registry as It Exists Today

`data/cta/cta_links.md` is already present in the repository (currently untracked in git). As of this writing it defines 16 keys across 9 markdown-header-delimited categories:

| Category (comment only) | Keys |
|---|---|
| Therapy | `therapy`, `therapy-library`, `mnri`, `arrowsmith`, `neurofeedback` |
| Conditions | `conditions`, `anxiety`, `depression`, `adhd` |
| Subscription | `subscription` |
| Courses | `courses` |
| Neuroplasticity | `neuroplasticity` |
| About | `about` |
| Community Hub | `community_hub` |
| Privacy | `privacy_guidelines` |
| FAQ | `faq` |

The `## Category` headers (and the file's own `# cta_links.md` title line) are pure human organization — Section 12.1's parser treats any line beginning with `#`, at any heading level, as a comment to skip, never as data.

---

## 7. CTA Node Design

### 7.1 The Lookup Key: A Manually-Tagged `cta_key`, Not a Derived One

The brief's instruction is "identify the most relevant knowledge document; retrieve the corresponding CTA link." The most relevant document is already computed for us — it's `knowledge["retrieved_docs"][0]`, ranked by similarity score (`knowledge_node._decide_source`, `app/nodes/knowledge_node.py:106-112`). The open design question is: what field on that document is the CTA key?

This spec's answer is a new, optional, manually-set metadata field, `cta_key`, attached to a chunk at ingestion time — set by the same person maintaining `data/cta/cta_links.md`, at the same time they add a markdown section, so the two files are authored together and the key is, by construction, identical on both sides. Concretely, in `scripts/build_knowledge_index.py`'s `load_therapy_chunks` (`scripts/build_knowledge_index.py:53-83`), the existing mechanically-derived `therapy_name` (already extracted from the nearest markdown heading) is mapped through one new, explicit, hand-written table:

```python
THERAPY_HEADING_TO_CTA_KEY = {
    "MNRI® (Masgutova Neurosensorimotor Reflex Integration)": "mnri",
    "Arrowsmith Program®": "arrowsmith",
    "Neurofeedback": "neurofeedback",
}
```

and `type_metadata` gains one more key: `"cta_key": THERAPY_HEADING_TO_CTA_KEY.get(therapy_name)`. Headings with no entry in the table (Feldenkrais, Jill Stowell, Access Consciousness, Vision Therapy, Lynn Valley, the Brain & Gut Health entries, the Listening entries, and the intro/outro non-named-therapy chunks) get `cta_key: None` — correctly "no CTA," matching the brief's Example 3 exactly. This is the *only* ingestion-side change this phase requires; it is additive to existing metadata, not a restructuring of it, and flows to `knowledge.retrieved_docs[0]["metadata"]["cta_key"]` automatically via the existing pass-through mechanism that already forwards every non-common metadata key (`type_specific = {k: v for k, v in metadata.items() if k not in _COMMON_METADATA_KEYS}`, `app/nodes/knowledge_node.py:115-126`) — `cta_node` requires zero changes to `knowledge_node.py` itself.

### 7.2 The One Permitted String Operation, and Why It Is Not "Normalization" of the Registry

The registry's own keys (`mnri`, `adhd`, `subscription`, …) are never transformed, reordered, renamed, or rewritten by any code in this system — that satisfies Section 0 literally. The lookup key on the document side (`cta_key`) is written by hand to already equal the registry key byte-for-byte (`"cta_key": "mnri"`, never `"MNRI"` or `"mnri "`), so the comparison in Section 12.2 is plain Python `dict.get(key)` — no `.lower()`, no `.strip()`, no punctuation stripping, anywhere in the match path. This is stricter than even a case-insensitive match would be, and was chosen deliberately: the brief's prohibition list bans normalizing keys, and the cleanest way to honor that with zero ambiguity is to never call any string-transform function on either side of the comparison at all.

### 7.3 Why `understanding.topic` and `response.answer` Were Rejected as the Lookup Key

Phase 1's `topic` field is explicitly "a short normalized topic string" (`app/prompts/understanding_prompt.txt:69`) — but "normalized" there means "concise," not "drawn from a fixed vocabulary." Real examples from the Phase 1 spec include `"ManaScience therapies"`, `"evidence for neuroplasticity-based therapy"`, and `"ManaScience account signup"` (`.claude/spec/manasi-ai-phase1-understanding-node-spec.md`, Section 6) — multi-word, model-generated phrases with no guarantee of ever equaling a registry key like `mnri` or `adhd`, even when the underlying question is squarely about that topic. Using `topic` as an exact-match key would mean a turn's CTA eligibility silently depends on phrasing the model happened to choose that run — which is a property of the LLM, not of the system, and directly conflicts with FR-1 and FR-3 ("independent of the LLM," "never derived from free text"). The same reasoning rules out `response["answer"]`, `content_optimization["summary"]`, and `empathy["final_answer"]` — every one of those is LLM output, and matching against any of them, however "exact" the string comparison itself is, makes the *outcome* of the match non-deterministic across runs. `retrieved_docs[0]["metadata"]["cta_key"]`, by contrast, is fixed at ingestion time by a human and never touched by a model — it is the only field in the entire turn's data that satisfies "deterministic and independent of the LLM" without qualification.

### 7.4 Known Current-Data Limitation (Not a Defect)

As of today, `cta_key` is only ever set for `mnri`, `arrowsmith`, and `neurofeedback` (Section 7.1) — there is no knowledge document yet for `adhd`, `anxiety`, `depression`, `subscription`, `courses`, `neuroplasticity`, `about`, `community_hub`, `privacy_guidelines`, or `faq` (the ingestion script's own comment already notes "the other six content types... have no real ManaScience content yet," `scripts/build_knowledge_index.py:139-141`). A user asking "What is ADHD?" today will correctly get a normal answer with no CTA, exactly per Section 0's contract — not a bug, and not something this phase's code needs to special-case. Closing this gap is a content-authoring task (tagging a future conditions/subscription/etc. document with the matching `cta_key`), not a code change, and is explicitly out of scope here (Section 20).

---

## 8. Data Flow Diagram

```
                                   data/cta/cta_links.md  (read-only, parsed once at import)
                                              │
                                              ▼
                                    CTA_REGISTRY: dict[str, str]
                                              │
user_message                                 │
     │                                       │
     ▼                                       │
understanding_node ──► understanding          │
     │            {topic, intent, ...}        │
     ▼                                       │
knowledge_node ──► knowledge                  │
     │        {retrieved_docs[], source, ...} │
     │                                       │
     │  retrieved_docs[0].metadata.cta_key    │
     ▼                                       │
cta_node ─────────────────────────────────────┘
     │
     ▼
cta {matched, cta_key, cta_url, source_chunk_id, lookup_time_ms, error}
     │
     ▼  (passthrough — untouched by every node below; none of them read or write `cta`)
response_node ──► content_optimization_node ──► empathy_node ──► safety_node
                                                                       │
                                                                       ▼
                                                          safety {safe_response, ...}
                                                                       │
                                              format_final_response(safe_response, cta)
                                                       (pure, code-only, no LLM)
                                                                       │
                                                                       ▼
                                                     final text returned to the user
```

---

## 9. Sequence Diagram

Worked example for the brief's Example 1 ("What is MNRI?"):

```
User          API/Graph        understanding_node   knowledge_node      cta_node            cta_service
 │  "What is MNRI?"  │                  │                  │                │                    │
 │──────────────────►│                  │                  │                │                    │
 │                    │─────invoke──────►│                  │                │                    │
 │                    │◄── {intent: therapy_information, topic: "MNRI"} ─────│                    │
 │                    │──────────────────────invoke────────►│                │                    │
 │                    │                  │   similarity_search("MNRI", ...)  │                    │
 │                    │                  │   ◄── chunk(metadata.cta_key="mnri") ──                │
 │                    │◄── knowledge {retrieved_docs:[...], source:"rag"} ───│                    │
 │                    │─────────────────────────────────invoke──────────────►│                    │
 │                    │                                     │                │── resolve_cta_key()►│
 │                    │                                     │                │◄── ("mnri", chunk_id)│
 │                    │                                     │                │── get_cta_url("mnri")►│
 │                    │                                     │                │◄── ".../post/mnri" ──│
 │                    │◄────────── cta {matched: true, cta_key: "mnri", cta_url: ".../post/mnri"} ──│
 │                    │   (response_node → content_optimization_node → empathy_node → safety_node;
 │                    │    cta carried untouched through each — none of them read or write it)
 │                    │◄── safety {safe_response: "MNRI is a therapy focused on...", approved} ─────│
 │                    │─────────────────────format_final_response(safe_response, cta)──────────────►│
 │                    │◄── "MNRI is a therapy focused on...\n\nLearn More:\nhttps://.../post/mnri" ──│
 │◄───────────────────│                  │                  │                │                    │
```

For Example 2 ("Hello") and Example 3 ("What are primitive reflexes?"), the flow is identical except `cta_node` resolves `matched: false` — either because `understanding.intent == "general_chat"` short-circuits `knowledge_node` to an empty `retrieved_docs` (Example 2; mirrors `knowledge_node._skipped_result`, `app/nodes/knowledge_node.py:74-84`), or because the top retrieved document's metadata has no `cta_key` (Example 3 — a real, retrieved, on-topic document that simply has no manually-tagged CTA, per Section 7.4). `format_final_response` then returns `safe_response` byte-for-byte unchanged.

---

## 10. Folder Structure

```
app/
    graph/
        state.py                          (edited — add CTA TypedDict + GraphState.cta field)
    nodes/
        cta_node.py                        (new)
        knowledge_node.py                  (unchanged — cta_key already flows through existing metadata passthrough)
        response_node.py                   (edited — build_response_graph() wiring only, optional)
        content_optimization_node.py       (edited — build_content_optimization_graph() wiring only, optional)
        empathy_node.py                    (edited — build_empathy_graph() wiring only, optional)
        safety_node.py                     (edited — build_safety_graph() wiring only, required for the production graph)
    services/
        cta_service.py                     (new)
    config.py                              (edited — cta_links_path)
    models.py                              (edited — CTAResponse)
    main.py                                (edited — /cta endpoint, build_cta_graph wiring)

scripts/
    build_knowledge_index.py               (edited — THERAPY_HEADING_TO_CTA_KEY, Section 7.1)

data/
    cta/
        cta_links.md                        (existing; manually maintained; read-only at runtime)

tests/
    test_cta_node.py                        (new)
```

The "optional" edits to `response_node.py`, `content_optimization_node.py`, and `empathy_node.py` are two-line additions to each file's own isolated `build_*_graph()` test helper (inserting `cta_node` into that helper's chain so `state["cta"]` is present for anyone invoking that specific isolated graph). None of those three nodes ever read `state["cta"]`, so omitting the edit does not break them — it only means a caller of, say, `build_empathy_graph()` directly (skipping `build_safety_graph()`) won't see a `cta` key in the returned state. The edit to `safety_node.py`'s `build_safety_graph()` is the one that matters in practice, since that function is this codebase's de facto "full pipeline so far" (Phase 5 Section 13.8, Phase 6 Section 13.8).

### 10.1 File-by-File Responsibility

| File | Responsibility |
|---|---|
| `app/graph/state.py` | Adds the `CTA` TypedDict (Section 11.4) and `cta: Optional[CTA]` to `GraphState`, inserted between `knowledge` and `response` to mirror actual data-flow order (no runtime effect on `TypedDict` field order, per Phase 6 Section 13.4's identical note). |
| `app/nodes/cta_node.py` | Defines `CTAOutput` (Section 11.5) and its validator; implements `cta_node(state) -> dict` (Section 12.2) and `build_cta_graph()` (Section 12.5) for isolated Phase 7 testing/deployment. |
| `app/services/cta_service.py` | All business logic: `load_cta_registry`, the module-level `CTA_REGISTRY` cache, `get_cta_url`, `resolve_cta_key`, and `format_final_response` (Section 12). |
| `scripts/build_knowledge_index.py` | Gains `THERAPY_HEADING_TO_CTA_KEY` and a one-line addition to `load_therapy_chunks`'s `type_metadata` dict (Section 7.1). |
| `app/config.py` | Gains `cta_links_path: Path`, following the existing `BASE_DIR / os.getenv(...)` pattern used by every other path setting. |
| `app/models.py` | Gains `CTAResponse`, mirroring `KnowledgeResponse`/`SafetyResponse`'s flat-field style. |
| `app/main.py` | Gains a `/cta` endpoint mirroring `/knowledge`, and imports `build_cta_graph` for app startup, mirroring every other phase's wiring in `lifespan()`. |
| `tests/test_cta_node.py` | Unit tests for the parser, the resolver, the node, and the registry-integrity guarantee (Section 15). |

---

## 11. Class Design

### 11.1 `CTA` (TypedDict, `app/graph/state.py`)

```python
class CTA(TypedDict):
    matched: bool
    cta_key: Optional[str]
    cta_url: Optional[str]
    source_chunk_id: Optional[str]
    lookup_time_ms: float
    error: Optional[str]
```

### 11.2 `GraphState` Addition (`app/graph/state.py`)

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

This is a strict additive edit — no existing field changes shape, identical in spirit to every prior phase's `GraphState` edit (Phase 5 Section 13.4, Phase 6 Section 13.4).

### 11.3 `CTAResponse` (Pydantic, `app/models.py`)

```python
class CTAResponse(BaseModel):
    matched: bool
    cta_key: str | None
    cta_url: str | None
    source_chunk_id: str | None
    lookup_time_ms: float
    error: str | None
```

### 11.4 `CTAOutput` (Pydantic validator, `app/nodes/cta_node.py`)

```python
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
```

`cta_key` MAY be non-null while `matched` is `False` — this is the deliberate, valid case where a document carried a `cta_key` that does not (yet, or due to an authoring typo) exist in the registry (Section 13's `WARNING`-level log). Only a non-null `cta_url` without `matched=True` is treated as an invariant violation, mirroring the asymmetric consistency checks already established by `SafetyOutput._validate_status_consistency` (Phase 5 Section 11.5) and `KnowledgeOutput._validate_source_consistency` (`app/nodes/knowledge_node.py:61-71`).

---

## 12. Function Definitions

### 12.1 `load_cta_registry(path: Path) -> dict[str, str]` — `app/services/cta_service.py`

```python
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
```

### 12.2 `_parse_line(line: str) -> Optional[tuple[str, str]]` — internal helper

```python
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
```

### 12.3 Module-Level Cache

```python
CTA_REGISTRY: dict[str, str] = load_cta_registry(settings.cta_links_path)
```

Loaded once, at import time — mirroring `understanding_node.py`'s identical pattern for its prompt template (`PROMPT_PATH = ...; _PROMPT_TEMPLATE = PROMPT_PATH.read_text(...)`, `app/nodes/understanding_node.py:15-16`). Per FR-7, there is no re-read on the hot path.

### 12.4 `get_cta_url`, `resolve_cta_key`, `format_final_response`

```python
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
    text (Section 5.4). Called once, by the eventual unified pipeline entry point,
    after the full graph has produced both `safety` and `cta`."""
    if not cta.get("matched"):
        return safe_response
    return f"{safe_response}\n\nLearn More:\n{cta['cta_url']}"
```

### 12.5 `cta_node` and `build_cta_graph` — `app/nodes/cta_node.py`

```python
def cta_node(state: GraphState) -> dict:
    """LangGraph node: resolve a deterministic CTA link for the current turn from
    state["knowledge"]["retrieved_docs"], via the manually maintained CTA registry.
    Makes no LLM call and never modifies the registry file.

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
        validated["matched"], validated["cta_key"], validated["lookup_time_ms"],
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
```

### 12.6 Required Edit to `safety_node.py`'s `build_safety_graph()`

```python
# Before (Phase 6 as shipped):
graph.add_edge("knowledge_node", "response_node")

# After (Phase 7 integration):
graph.add_node("cta_node", cta_node)
graph.add_edge("knowledge_node", "cta_node")
graph.add_edge("cta_node", "response_node")
```

The resulting full graph order:

```
understanding_node → knowledge_node → cta_node → response_node →
content_optimization_node → empathy_node → safety_node → END
```

No other line in `safety_node.py` changes — `safety_node` itself still only reads `empathy`, `user_message`, and `knowledge` (Phase 5 Section 13.2); it is entirely unaware `cta_node` exists, exactly as intended (Section 5.3).

---

## 13. Error Handling

| Code (`cta.error`) | Meaning | Behavior |
|---|---|---|
| `null` | Clean resolution — matched or not, both are "clean" (FR-5). | Normal. |
| `"cta_lookup_failure"` | An unexpected exception occurred inside the node's own try block (not a registry-load failure, which is handled separately and never raises — Section 12.1). | Falls back to `matched: false`, logs at ERROR. Never blocks the turn. |

Registry-load failure (missing/unreadable file) is **not** surfaced via `cta.error` at all — it is handled entirely inside `load_cta_registry` at import time (Section 12.1), resulting in an empty `CTA_REGISTRY`. Every subsequent `cta_node` invocation then resolves `matched: false` through the *ordinary* "key not found" path, with `error: null`, since from the node's perspective nothing went wrong — the registry legitimately has no entries. The one-time load failure itself is logged once, at ERROR, when the process starts.

A resolved `cta_key` that is absent from the registry (an authoring-drift signal: a document was tagged with a key that was renamed or removed from `cta_links.md`, or never added) is also **not** an `error` — it is logged at WARNING (Section 12.5) specifically so a maintainer notices the drift, while the user still receives a completely normal answer with no CTA, per Section 0.

This error model is the inverse of every LLM-driven phase's: Phases 3–6 reserve `error` for *infrastructure* failures (a failed API call) because their main path can also fail on *quality* grounds (a guard rejecting bad output) and need a separate signal for that. Phase 7 has no quality dimension at all — a lookup either resolves or doesn't, and "doesn't" is success, not failure (FR-5) — so `error` here is reserved purely for the one truly unexpected case: a bug.

---

## 14. Logging Requirements

* **INFO**, every invocation: `cta_node ok: matched=%s cta_key=%s elapsed_ms=%.2f` (Section 12.5). This is the single log line for the overwhelming majority of turns, matched or not.
* **WARNING**, when a resolved `cta_key` is not present in the registry: `cta_node: cta_key=%r not found in registry` — the authoring-drift signal (Section 13).
* **WARNING**, at registry parse time, for each skipped malformed line, duplicate key, empty key/value, or non-URL value (Section 12.2) — every one of these indicates a typo in a hand-edited file that a maintainer should see, without ever blocking startup.
* **ERROR**, at most once per process lifetime, if `data/cta/cta_links.md` itself is missing or unreadable (Section 12.1).
* **ERROR**, on the rare unhandled-exception fallback path (Section 13's `"cta_lookup_failure"`).

No log line emitted by this phase includes the full retrieved document content or the full user message — only `cta_key`, `matched`, chunk identifiers, and timing, consistent with every other node's logging scope (e.g. `knowledge_node`'s `logger.info("knowledge_node ok: query=%r source=%s confidence=%.2f ...")`, `app/nodes/knowledge_node.py:199-205`).

---

## 15. Testing Strategy

Because this node has no LLM call, it needs none of the `FakeLLM` scaffolding every other phase's test suite relies on (Phase 1/4/5/6's shared pattern) — every test below is a plain, synchronous unit test against pure functions and plain dicts.

### 15.1 `cta_service.load_cta_registry` / `_parse_line`

* Parses a well-formed fixture file into the expected `dict[str, str]`.
* Skips blank lines and `#`/`##`-prefixed header lines without including them as keys.
* Skips a line with no `=` and logs a warning (assert via `caplog`).
* Skips a line with an empty key or empty value.
* Skips a line whose value is not `http://`/`https://`-prefixed.
* On a duplicate key, keeps the **first** definition and logs a warning.
* Returns `{}` and logs an error, without raising, when given a nonexistent path.
* **Never writes to the fixture file** — assert the file's mtime and byte content are identical before and after the call (the direct, automated expression of FR-6).

### 15.2 `cta_service.resolve_cta_key`

* Empty `retrieved_docs` → `(None, None)`.
* Top document with no `metadata` key at all → `(None, None)` (no `KeyError`).
* Top document with `metadata={}` → `(None, None)`.
* Top document with `metadata={"cta_key": "mnri"}` → `("mnri", <that doc's chunk_id>)`.
* Multiple documents, only `retrieved_docs[0]`'s `cta_key` is ever read, even when `retrieved_docs[1]` has a different one set (FR-4).

### 15.3 `cta_service.get_cta_url` / `format_final_response`

* A key present in `CTA_REGISTRY` (monkeypatched to a small fixture dict for test isolation) returns its exact URL.
* A key absent from the registry returns `None` — never a guess, never the closest match.
* `format_final_response` with `matched=False` returns the input string completely unchanged (identity check, not just equality — guards against an accidental extra newline).
* `format_final_response` with `matched=True` returns `f"{original}\n\nLearn More:\n{url}"` exactly.

### 15.4 `cta_node`

* Given a state whose `knowledge.retrieved_docs[0].metadata.cta_key == "mnri"` (and `"mnri"` present in a test-fixture registry), returns `{"cta": {"matched": True, "cta_key": "mnri", "cta_url": "...", ...}}`.
* Given `knowledge.retrieved_docs == []` (general-chat / no-retrieval turn), returns `matched: False, cta_key: None, cta_url: None, error: None`.
* Given a top document whose `cta_key` is set but absent from the registry, returns `matched: False` with `cta_key` still populated and `error: None`, and asserts a `WARNING` was logged.
* `lookup_time_ms` is always a non-negative float.
* `CTAOutput`'s validator rejects a hand-constructed `matched=True, cta_url=None` payload (Section 11.4) — a direct test of the consistency guard.

### 15.5 Integration: Passthrough Through the Real Graph

* Build `build_safety_graph()` (post Section 12.6 edit) with every LLM-calling node's `llm` replaced by a minimal scripted fake (mirroring `tests/test_safety_node.py`'s existing fixtures), run one full turn for "What is MNRI?", and assert `result["cta"]["matched"] is True` **and** that `result["cta"]` is byte-identical to what `cta_node` alone would have produced from the same `knowledge` output — proving `response_node`, `content_optimization_node`, `empathy_node`, and `safety_node` neither read nor mutate it (FR-9, Section 5.4).
* The same integration test asserts `result["safety"]["safe_response"]` contains no instance of the literal CTA URL — proving the URL never leaked into any LLM-touched field before `format_final_response` runs (Section 5.4's central guarantee, made concrete and automatable).

### 15.6 Acceptance-Level Tests (mirroring the brief's three examples literally)

* `test_mnri_example()` — given the brief's Example 1 input, the fully-assembled final text (post `format_final_response`) ends with `"Learn More:\nhttps://manascience.webflow.io/post/mnri"`.
* `test_hello_example()` — given a `general_chat` turn, the final text is identical to `safety.safe_response`, with no `"Learn More:"` substring anywhere in it.
* `test_primitive_reflexes_example()` — given a retrieved document with no `cta_key` (an untagged section of `manascience_therapies.md`), the final text again contains no `"Learn More:"` substring.

---

## 16. Acceptance Criteria

* **AC-1:** Given a turn whose top retrieved document carries `metadata.cta_key == "mnri"`, the final user-facing response ends with the literal `mnri` URL from `data/cta/cta_links.md`, appended after a `"Learn More:"` line.
* **AC-2:** Given a turn with no retrieved documents (e.g. a greeting), the final response is unchanged from `safety.safe_response` — no CTA section is appended, and no exception occurs.
* **AC-3:** Given a turn whose top retrieved document exists but carries no `cta_key`, the final response is unchanged from `safety.safe_response`.
* **AC-4:** No code path introduced by this phase ever calls an LLM, embedding model, or any network service.
* **AC-5:** No code path introduced by this phase ever opens `data/cta/cta_links.md` in a write or append mode, under any input, including a malformed line or a duplicate key.
* **AC-6:** A key present in `retrieved_docs[0].metadata.cta_key` but absent from the registry never resolves to any URL — not the closest key, not a default, not `None`-coerced-to-empty-string-then-something. It resolves to exactly `cta_url: None`.
* **AC-7:** `cta_node`'s p99 latency, measured over at least 1,000 invocations against the real registry, is under 5ms.
* **AC-8:** Removing `cta_node` from `build_safety_graph()`'s wiring (a one-line revert) restores the pipeline to its exact pre-Phase-7 behavior, with no other file requiring a change — the rollback test for Section 20's reversibility claim.

---

## 17. Edge Cases

| # | Scenario | Required behavior |
|---|---|---|
| 1 | `retrieved_docs` is empty (general-chat or low-confidence-retrieval turn). | `matched: False`, no exception (FR-5, AC-2). |
| 2 | Top document exists but has no `metadata` key, or `metadata` is `{}`. | `matched: False` — `resolve_cta_key` treats a missing key as absent, not as an error (Section 15.2). |
| 3 | Top document's `cta_key` is set to a value not present in the registry (authoring drift, e.g. the registry entry was renamed). | `matched: False`, `cta_key` still reported (for observability), `WARNING` logged, **no fuzzy fallback to a similarly-named key** (AC-6). |
| 4 | `data/cta/cta_links.md` is deleted or unreadable at process startup. | `CTA_REGISTRY = {}`; every turn thereafter resolves `matched: False`; one `ERROR` logged at startup; the process does not fail to start (FR-8). |
| 5 | The registry file contains a duplicate key (two `mnri=...` lines, e.g. a merge artifact). | First definition wins; a `WARNING` is logged identifying the duplicate, so a human notices before the second, silently-ignored definition causes confusion (Section 12.1). |
| 6 | A registry line's value is present but is not a URL (e.g. a stray note added by a non-engineer editor: `draft=coming soon`). | Line is skipped entirely (not loaded as a broken "link"), with a `WARNING` (Section 12.2) — this is a parse-time sanity check, not link generation or guessing, since the system invents nothing; it only declines to trust a value that cannot possibly be a link. |
| 7 | Two retrieved documents both carry a (different) `cta_key`. | Only `retrieved_docs[0]`'s key is ever considered (FR-4) — the second document's key is never read, logged, or surfaced. |
| 8 | The same registry key legitimately maps to two near-identical URLs in different sections (today's actual `therapy` vs `therapy-library` keys, which happen to share a URL). | Each key is independent; this phase does not deduplicate, merge, or warn about same-valued keys — that is an authoring choice for the registry's maintainer, not a defect in the registry. |
| 9 | The registry is edited (a key added or a URL changed) while the application process is already running. | Not picked up until the next process restart (FR-7's load-once-at-startup is deliberate, per Section 4.1's determinism/latency guarantees); this is a documented limitation, not a bug — see Section 20 for the rollout implication. |
| 10 | A future content/conditions/subscription knowledge document is added without a corresponding `cta_key` tag. | Identical to Edge Case 2 — `matched: False`. No code change is needed for new untagged content to behave correctly by default (Section 7.4). |
| 11 | `cta_node` is invoked directly (e.g. via `build_cta_graph()`) on a state where `state["knowledge"]` is `None` (upstream never ran). | `retrieved_docs = []` via `knowledge["retrieved_docs"] if knowledge else []`; resolves `matched: False`, no `KeyError`/`TypeError`. |
| 12 | The pipeline retries or re-invokes a node after a downstream failure (e.g. `response_node` falls back). | `cta` was already computed once, upstream of `response_node`, and is never recomputed or re-appended on a retry — `format_final_response` is called exactly once, at final assembly, so no double-append is possible (FR-9, Section 5.4). |

---

## 18. Performance Considerations

* The dominant cost in this phase is the one-time registry parse at process startup (Section 4.2: <10ms for ~20 lines today). This scales linearly with file size; even a 10x growth in registry entries (160 keys) would remain well under 100ms and would still happen exactly once per process lifetime, never per request.
* The per-request path (Section 12.4–12.5) is a single dict `.get()` plus a handful of dict/attribute accesses on an already-in-memory Python object — no I/O, no serialization beyond the existing Pydantic validation every other node already pays for. This is the cheapest node in the entire pipeline by a wide margin (Section 4.2's "<5ms p99" vs. every LLM-calling node's 1,800ms+ p95).
* Because the registry is held as a single small `dict[str, str]`, memory overhead is negligible (a few KB) and does not scale with traffic — it scales only with the number of hand-authored registry entries, which by Section 0's own design (a manually maintained file) will never approach a size where this matters.
* No caching layer beyond the simple module-level dict is needed; no TTL, no LRU, no external cache (Redis, etc.) is justified for a file this size that changes this infrequently — introducing one would add operational complexity with no measurable latency benefit (Section 4.2 is already sub-millisecond).

---

## 19. Security Considerations

* **Trusted input only.** `data/cta/cta_links.md` is a repository-controlled, manually-authored file, not user input — there is no injection surface in `_parse_line` (Section 12.2): it performs a string `.partition("=")` and prefix checks, never `eval`, never template rendering, never a regex with attacker-controlled input (the only "attacker-controlled" string in this entire phase is the user's chat message, which `cta_node` never reads at all — it only ever reads `knowledge["retrieved_docs"]`, themselves derived from the trusted, pre-ingested knowledge base, not from the live user message).
* **URL allow-listing by construction.** Because every registry value is validated at parse time to start with `http://` or `https://` (Section 12.2) and is never user-suppliable, this phase cannot be used as an open-redirect or arbitrary-URL-injection vector — the complete set of URLs `cta_node` can ever return is exactly the set a human has written into the registry file, enumerable by reading that one file.
* **No secrets in the registry.** `cta_links.md` contains only public, already-published Webflow page URLs; nothing in this phase introduces a new credential, token, or internal-network address into a logged or user-facing field. Logging `cta_url` at INFO (Section 14) is therefore safe.
* **Output is plain text, not rendered markup.** `format_final_response` appends the URL as plain text (`"Learn More:\n{url}"`), not as an HTML `<a href>` or unescaped markdown link — whatever surface eventually renders `safe_response` to a user (web widget, voice transcript, etc.) is responsible for safely rendering or linkifying that plain-text URL using its own existing output-encoding discipline; this phase does not introduce a new HTML-injection path because it never emits HTML itself.
* **No write capability, ever.** FR-6/AC-5 are, among other things, a security property: a future bug, a malicious chunk-ingestion input, or a compromised dependency in this phase's code path has no function call available to it that opens the registry file for writing — `load_cta_registry` only ever calls `.read_text()`.

---

## 20. Deployment Plan

* **No new external dependencies.** Every function in this phase uses only the Python standard library (`pathlib`, string methods) plus the `pydantic`/`langgraph` already in `requirements.txt`.
* **No data migration.** `data/cta/cta_links.md` already exists in the working tree (currently untracked in git per the repository's current status) — this rollout's first concrete step is committing that file alongside the code, since the node is non-functional without it (it degrades safely to "no CTA ever," per FR-8, but provides no value).
* **Rollout sequence:**
  1. Add `cta_links_path` to `app/config.py` and commit `data/cta/cta_links.md`.
  2. Add `app/services/cta_service.py` and `app/nodes/cta_node.py`, plus `tests/test_cta_node.py`; merge and run independently of the production graph (via `build_cta_graph()` and the `/cta` endpoint) for at least one verification pass.
  3. Apply the `THERAPY_HEADING_TO_CTA_KEY` edit to `scripts/build_knowledge_index.py` and re-run the ingestion script against the existing Chroma collection (`scripts/build_knowledge_index.py`'s `collection.upsert(...)` is already idempotent per chunk ID, so re-running it is safe and simply refreshes metadata on existing chunks).
  4. Apply the two-line `build_safety_graph()` edit (Section 12.6) to insert `cta_node` into the production graph order.
  5. Optionally apply the same mechanical insertion to `build_response_graph()`, `build_content_optimization_graph()`, and `build_empathy_graph()` for consistency (Section 10), though nothing depends on this.
* **Reversibility.** Step 4's edit is a pure graph-wiring change with no schema migration behind it — reverting it (removing `cta_node` from `build_safety_graph()`) instantly restores pre-Phase-7 behavior with no other cleanup required (AC-8). No feature flag is introduced or needed: the node is zero-cost and zero-risk to leave permanently wired in (Section 4, Section 18), so the only "off switch" that will ever be exercised is a plain code revert, not a runtime toggle.
* **Operational note on registry edits.** Because the registry loads once per process (Edge Case 9), a content/marketing change to `cta_links.md` in production requires a process restart (or redeploy) to take effect — this should be documented for whoever owns that file day-to-day, since it is the one operational behavior that differs from "just edit the file and it's live."
* **Future CMS migration path (BR-4).** When `cta_links.md` is eventually replaced by a CMS-backed source, only `load_cta_registry`'s implementation changes (e.g., an HTTP fetch instead of `path.read_text()`) — `get_cta_url`, `resolve_cta_key`, `cta_node`, `CTA`, and every downstream consumer of `state["cta"]` are unaffected, because none of them know or care how `CTA_REGISTRY` was populated.

