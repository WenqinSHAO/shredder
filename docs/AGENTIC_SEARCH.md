# Agentic Search End-to-End Spec

Last updated: 2026-03-21

## 1) Purpose and Scope

This document specifies the current end-to-end behavior of agentic academic paper search in `src/orchestrator/agentic.py`.

Goals:
- plan and execute multi-hop web retrieval for paper metadata
- keep high recall for paper titles while improving precision via post-extract cleanup
- preserve debug traceability across agent/tool/stateless-LLM calls

Non-goals:
- authoritative citation enrichment for every final paper (DOI/arXiv may remain empty)
- timeout/performance tuning policy finalization (tracked separately)

## 2) End-to-End Flow

Runtime loop per cycle:
1. `agent_llm` receives current state and emits `{action, params, plan_update, progress_update}`.
2. Action execution:
   - `search_web`: query searxng and shortlist URL hits.
   - `fetch_content`: fetch URL content snapshots.
   - `extract_content`: extract candidate paper rows (deterministic + stateless LLM).
3. For `extract_content`, candidates are merged into `final_candidates`.
4. Post-extract canonicalization (`canonicalize_llm`) runs on merged final candidates.
5. Decision engine chooses `continue`/`stop`.

Important invariant:
- Canonicalization can merge/normalize/drop existing rows only. It cannot create new paper rows.

## 3) Prompt and Context Evolution

## 3.1 Main Agent (`agent_llm`) initiation prompt

Main agent prompt is composed of:
- system instruction:
  - role: academic paper agentic search planner
  - SOP: cue decomposition -> planning -> shortlisting -> fetch -> extract -> progress updates
  - action catalog and constraints
  - output contract (strict JSON)
- user payload:
  - `task=select_next_action`
  - `cycle_index`, `max_cycles`, `user_prompt`
  - `previous_cycle_summary`
  - `current_candidates`
  - `plan_state` and recent `plan_progress`
  - `available_actions`, `constraints`, `output_contract`

Main agent context is stateful across cycles through:
- `previous_cycle_summary` (condensed prior action output)
- `plan_state` (`cue_breakdown`, `steps`, `active_step_id`, `todo`)
- `plan_progress` (recent progress log entries)
- shortlisted/final candidate state carried in orchestrator payload

## 3.2 Stateless extractor (`extract_llm`) initiation prompt

Extractor prompt is composed of:
- system instruction:
  - extract row-level paper facts from listing/content segments
  - return strict JSON `items[]`
  - enforce row-local match semantics and non-paper suppression
- user payload:
  - `task=extract_paper_rows`
  - `user_prompt`
  - `url`, `url_title`
  - `filters`
  - resolved `intent` (`must_match`, `return_fields`, policy)
  - `segments` (selected/ranked content chunks)

Extractor context is intentionally stateless:
- each call only sees current `segments` + intent/filter payload
- no chat-memory from prior extractor calls is assumed
- cross-call continuity is handled by orchestrator cursors/coverage state, not by LLM memory

## 3.3 Stateless canonicalizer (`canonicalize_llm`) initiation prompt

Canonicalizer prompt is composed of:
- system instruction:
  - clean + dedup existing extracted candidates
  - allow only `keep/drop` + canonical mapping
  - disallow creation of new papers/candidate IDs
- user payload:
  - `task=canonicalize_extracted_candidates`
  - `policy` (`no_new_papers`, allowed actions, drop reasons)
  - `user_prompt`
  - extraction `intent`
  - `candidates[]` (title/id/url/evidence/confidence + metadata fields)

Canonicalizer context is stateless:
- operates on one candidate set snapshot at a time
- no dependency on prior canonicalizer calls
- orchestrator validates and applies result deterministically

## 3.4 Context evolution by cycle

Within one cycle:
1. main agent proposes action
2. tool action runs (`search/fetch/extract`)
3. condensed summary is generated
4. state (`plan`, `progress`, candidates) is updated
5. next cycle main agent sees updated state snapshot

Across cycles:
- only main agent receives evolving workflow context
- extractor/canonicalizer receive bounded, action-scoped payloads
- this design limits context pollution and keeps specialized LLM calls focused

## 4) Action Contracts

## 4.1 `search_web`

Input (`params`):
- `queries: list[str]`
- `shortlist_hints.prefer: list[str]` (optional)

Output (`action_result`):
- `web_rows`
- `raw_candidates`
- `notes` with executed query summary

Semantics:
- one query per venue is expected for multi-venue prompts
- shortlist priority biases official venue pages and DOI/publisher sources

## 4.2 `fetch_content`

Input (`params`):
- either `targets[{url,title,why}]` or queue-driven fallback

Output:
- `fetched_records` with fetched text/segments/raw artifact references

Semantics:
- URL-driven fetch only; extraction filters are not applied here

## 4.3 `extract_content`

Input (`params`):
- `target_ids` and/or `urls`
- `filters`: `{author,institution,venue,topic,year_gte}`
- `intent` (optional, but preferred for explicit constraints)
- `auto_fetch` (optional)
- `coverage`: batching/coverage controls

Output:
- `extracted_records` (fact rows)
- `paper_candidates`
- `extract_windows_trace`
- `extract_intent`

Semantics:
- combines deterministic extraction and stateless LLM extraction
- uses `match_decision` filtering
- supports coverage continuation over large listing pages

## 5) Data Contracts

## 5.1 Extractor Row Schema (stateless LLM)

Expected row keys:
- `is_paper`
- `paper_title_raw`
- `paper_title_normalized`
- `authors`
- `affiliations`
- `venue`
- `year`
- `doi`
- `arxiv_id`
- `abstract_snippet`
- `institution_hits`
- `match_decision` (`match|uncertain|non_match`)
- `decision_reason`
- `evidence_span`
- `confidence`

Post-parse row-quality fields (internal):
- `field_presence`: `{authors, affiliations, abstract_snippet}`
- `missing_fields: list[str]`
- `row_completeness: complete|partial`

Policy:
- strong title rows are preserved even if partial metadata
- missing metadata is expected to be enrichable in later steps/tools

## 5.2 Candidate Schema

Candidate fields include:
- title + identifiers (`doi`, `arxiv_id`)
- source/url/year/score
- propagated metadata (`authors`, `affiliations`, `abstract_snippet`)
- row completeness metadata (`missing_fields`, `row_completeness`)

## 5.3 Canonicalizer Output Schema

Per item:
- `candidate_id`
- `decision: keep|drop`
- `canonical_candidate_id`
- `canonical_title`
- `verification: match|uncertain|non_match`
- `reason`

Validation rules:
- unknown `candidate_id` ignored
- unknown `canonical_candidate_id` falls back to self-id
- `verification=non_match` forces drop
- hallucinated canonical titles are rejected if not close to representative row title

## 5.4 Final Result Artifact (`agentic_result.yaml`)

Contains:
- run metadata: query/status/stop_reason/cycle_count/model
- trace refs: trajectory + raw
- coverage summary
- final `papers[]` list only (canonicalized output rows)

`papers[]` fields:
- `title`, `doi`, `arxiv_url`, `source_url`, `venue`, `year`
- `authors`, `affiliations`, `abstract_snippet`
- `confidence`, `evidence_ref`

## 6) Precision/Recall Strategy

Precision controls:
- row-local `match_decision` filtering
- non-paper text suppression in extraction parsing
- post-extract canonicalization and alias merging
- canonicalizer `non_match` hard drop

Recall controls:
- keep strong-title rows even when metadata fields are partial
- deterministic fallback when LLM extraction/canonicalization fails
- coverage continuation across listing segments

## 7) Trace Model

Two trace tiers:
1. Raw trace (`agentic_raw.ndjson`):
   - full `agent_request/response`
   - extractor/canonicalizer requests and responses
   - paired `op_start/op_end` timing events
2. User-facing trajectory (`agentic_trajectory.yaml`):
   - compact step/action/result/progress snapshots
   - debug summary references

Timing observability:
- operation-level latency is reconstructed from paired op events (`agent_llm`, `web_search_query`, `web_fetch`, `extract_llm`, `canonicalize_llm`)

## 8) Known Gaps and Improvement Hooks

Current known gaps:
- row completeness is marked but not enforced as a strict acceptance gate
- dedup fallback still relies mainly on title/year when identifiers are absent
- timeout policy and adaptive batching need additional tuning

Improvement hooks:
- add metadata enrichment action/tool for partial rows
- stronger author canonicalization and overlap scoring
- explicit preprocess/postprocess op timings in trace
- tighter confidence calibration and threshold reporting
