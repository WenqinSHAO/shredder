# Third-Party Code Review: Agentic Search Implementation

**Review Date:** 2026-04-02  
**Reviewer:** CodeArts Agent  
**Scope:** Agentic search implementation against design intent, identified failure modes, and workspace trajectory traces

---

## Executive Summary

This code review examines the agentic search implementation against its documented design intentions, identified failure modes from TODO.md, and actual trajectory traces from workspace runs. The review confirms that significant architectural improvements have been made, but several critical gaps remain between design intent and implementation, causing the failure modes documented in TODO.md.

**Key Findings:**
- State transition pipeline is structurally present but violated by direct mutation
- Planner memory reconstruction exists but missing critical dimensions
- Page contract is not implemented as a first-class structure
- Multiple failure modes stem from incomplete query archetype awareness
- State consistency issues are foundational and must be fixed first

---

## 1. Design Intent Alignment Analysis

### 1.1 State Transition Pipeline

**Status: PARTIALLY ALIGNED**

**Design Intent (DESIGN.md lines 55-67):**
```
action result -> state_apply -> canonical state -> view/result/trace
```

**Implementation Reality:**
- ✅ **POSITIVE:** The pipeline structure exists in `agentic_loop.py`
- ❌ **CRITICAL GAP:** Direct state mutation in extract runtime violates this principle
- ❌ **CRITICAL GAP:** Runtime payload creates dual-path for state changes

**Evidence from Code Review:**
1. `agentic_extract_runtime.py` lines 273-297: Directly mutates `extract_state_by_url` before returning results
2. `agentic_loop.py` lines 549-575: `_ActionRuntime` payload is mutated by action executors, bypassing state_apply

**Impact:** This causes state consistency issues documented in TODO.md line 90: "extract coverage / todo / planner memory are still not fully consistent"

**Recommendation:**
- Extract runtime should return state deltas in action result
- `agentic_state_apply.py` should apply those deltas
- Add assertion that no direct mutation occurs during action execution

---

### 1.2 Planner Memory Reconstruction

**Status: ALIGNED but INCOMPLETE**

**Design Intent (DESIGN.md lines 69-80):**
- Explicit state reconstruction each turn
- Not naive chat history accumulation
- `_build_agent_memory()` as the domain state interface

**Implementation Reality:**
- ✅ **POSITIVE:** Memory is reconstructed before each planner turn (agentic_loop.py lines 582-595)
- ✅ **POSITIVE:** `_build_agent_memory()` exists and is called correctly
- ❌ **CRITICAL GAP:** Memory missing key dimensions

**Missing Dimensions (TODO.md lines 86-87):**
- Explicit `latest` recency signal
- Year-range coverage tracking
- Query archetype classification (author-led vs venue-led vs topic-led)
- Source quality indicators
- Page type distribution

**Evidence from workspace/mem:**
The query "latest paper on agentic memory from top AI conferences" failed to prioritize direct hits (A-MEM, Agent Workflow Memory, MemoryAgentBench) over giant listings, resulting in only 1 irrelevant paper (HiCM²) being found.

**Recommendation:**
- Add `query_profile.is_latest` boolean
- Add `query_profile.year_range_progress` dict
- Add `query_profile.archetype` enum
- Add per-URL quality scores
- Add page type distribution counts

---

### 1.3 Page Contract

**Status: NOT IMPLEMENTED**

**Design Intent (TODO.md line 88, DESIGN.md lines 19-23):**
```
papers[]
candidate_urls[]
page_status
```

**Implementation Reality:**
- ❌ **CRITICAL GAP:** No structured `PageResult` dataclass exists
- The fields are returned separately in action results but not as a first-class contract

**Impact:** This makes it harder to reason about extraction behavior and contributes to the "mixed-purpose troubleshooting bottleneck" mentioned in TODO.md.

**Recommendation:**
- Define `PageResult` dataclass in `agentic_contracts.py`
- Use it as the return type for extract actions
- Enforce the contract through type checking

---

### 1.4 Ownership Boundaries

**Status: WELL-ALIGNED**

**POSITIVE FINDINGS:**
- Module responsibilities match DESIGN.md table (lines 167-183)
- Clear separation: planner contract, loop runtime, action dispatch, search execution, extract modules, state apply, projections
- No obvious mixed-purpose modules remaining after refactors

**MINOR GAP:**
- `agentic_extract.py` (123 lines) is now a "LLM helper module" but the name suggests it might still be a magnet for runtime code
- Consider renaming to `agentic_extract_helpers.py` to clarify it's not the runtime owner

---

## 2. Failure Mode Analysis

### 2.1 Venue+Institution Queries (workspace/alibabanew)

**Observed Failure:**
- Query: "papers by alibaba at SIGCOMM from 2020 to 2022"
- Result: Only 5 papers from 2020, missing 2021 and 2022
- Stop reason: max_cycles_reached after 5 cycles
- Coverage shows 6 URLs but only 2 completed

**Root Causes Identified:**

1. **Multi-year planning failure:**
   - Planner only searched 2020, then 2021, never reached 2022
   - No year-range progress tracking in memory
   - TODO.md line 248 confirms: "planner underused the cycle budget"

2. **Same-host sibling venue confusion:**
   - HotNets 2021 accepted page was treated as SIGCOMM 2021 target
   - Both share usenix.org host but are different venues
   - TODO.md line 248: "planner memory also let HotNets 2021 accepted masquerade as the next best SIGCOMM 2021 extract target"

3. **State consistency drift:**
   - TODO.md line 90: "completed-vs-in-progress drift, wasted retries on already completed URLs"
   - Extract coverage and planner todo state diverged

**Recommendation:**
- Add year-range progress tracking to query_profile
- Include venue acronym in page_family computation
- Simplify todo-URL matching to use explicit references

---

### 2.2 Venue+Author Queries (workspace/ennan)

**Observed Failure:**
- Query: "papers by Zhai Ennan at SIGCOMM 2023"
- Expected: CellFusion, XRON (ground truth from TODO)
- Actual: ChameleMon, ZGaming (WRONG papers)
- Note: Ennan Zhai IS in the author list for both results, but these are NOT his papers

**Root Cause Identified:**

**Title/author block segmentation:**
- TODO.md line 249: "The segmented extract batches split paper titles from their author lines, so the LLM mispaired the CellFusion author block with the neighboring ChameleMon title"
- The accepted-list page has titles and authors in separate DOM elements
- Segmentation splits them into different batches
- LLM sees title in one batch, authors in next batch, and mispairs them

**Critical Observation:**
The abstract field in the result contains the author list text, not an actual abstract:
```
abstract: 'Yunzhe Ni (Alibaba Cloud & Peking Univ.); Zhilong Zheng, Xianshang Lin, ...'
```

This indicates the extraction is pulling wrong content from the page.

**Recommendation:**
- Detect author-query mode from query archetype
- For accepted pages in author-query mode, use block-preserving segmentation
- Keep title+author pairs together in same segment
- Add abstract quality validation

---

### 2.3 Venue+Topic Narrow (workspace/congestion)

**Observed Failure:**
- Query: "papers related to congestion control at SIGCOMM 2025"
- Expected: CClinguist, LeoCC, Falcon
- Actual: CClinguist, LeoCC, plus false positive ByteDance Jakiro
- Missing: Falcon

**Root Causes Identified:**

1. **Same-page extraction restart vs continuation:**
   - TODO.md line 250: "each pass used a different lexical/semantic filter pack, so the runtime kept re-ranking and effectively restarting the same page"
   - Scope signature doesn't include filter pack
   - Filter changes re-rank segments without changing signature
   - segments_done resets to 0, causing restart

2. **Surfaced-title self-poisoning:**
   - TODO.md line 250: "the planner fed already surfaced paper titles back as literal filters in a later pass"
   - Planner sees matched_papers in memory
   - Uses those titles as text_filters.literal_any in subsequent calls
   - Causes over-matching on already-found titles

**Recommendation:**
- Include filter pack in scope signature
- Add restart_reason to trace when segments_done resets
- Sanitize text_filters to remove already-matched titles
- Add planner guidance to NOT use surfaced titles as literal filters

---

### 2.4 Venue+Topic Broad (workspace/aiinfra)

**Observed Failure:**
- Query: "papers on AI infra for LLM training and inference in SIGCOMM 2025"
- Expected: MixNet, InfiniteHBD, DistTrain, MegaScale-Infer, Astral, ByteScale, HACK
- Actual: MixNet, InfiniteHBD, DistTrain, plus false positives
- Missing: MegaScale-Infer, Astral, ByteScale, HACK

**Root Causes Identified:**

1. **Repeated same-page scope resets:**
   - TODO.md line 251: "re-extracts accepted-papers four times under different filter packs, with ranked-window counts drifting from 151 to 51 to 66 and back to 151"
   - Same as Issue 2.3: scope signature doesn't account for filter changes

2. **Broad lexical semantic matching:**
   - TODO.md line 251: "broad AI/LLM lexical filters are over-selecting early windows"
   - Simple text matching for filters
   - No semantic similarity matching
   - No cap on candidate count per page
   - Early windows consume budget, later windows never reached

**Recommendation:**
- Cap window count for broad filters
- Prefer continuation over restart when page has partial progress
- Track filter pack history per page to avoid repeating failed packs
- Add semantic similarity matching for topic queries

---

### 2.5 Latest/Cross-Venue Semantic (workspace/mem)

**Observed Failure:**
- Query: "latest paper on agentic memory from top AI conferences such as ICLR, ICML, AAAI, etc."
- Expected: A-MEM, Agent Workflow Memory, MemoryAgentBench (from search results)
- Actual: HiCM² (irrelevant AAAI 2025 paper about video captioning)
- Only 1 paper found despite 5 cycles

**Root Causes Identified:**

1. **Direct hits not prioritized:**
   - TODO.md line 252: "Search did surface stronger candidates early (A-MEM, Agent Workflow Memory, MemoryAgentBench), but the planner still decomposed the task into only venue listing searches"
   - priority_direct_hits is computed but not strong enough signal
   - Planner chose giant listing pages (ICLR 2025: 85 segments, DBLP AAAI 2025: 61 segments) over direct paper hits

2. **Missing latest/recency planning:**
   - Query says "latest" but planner didn't treat this as recency-aware
   - No explicit `is_latest` boolean in query_profile
   - Planner decomposed into "ICLR accepted papers agentic memory" etc., missing the semantic intent

3. **Wrong page type investment:**
   - Cycles spent on: ICLR 2025 listing (85 segments), DBLP AAAI 2025 (61 segments), arXiv PDF, OpenReview PDF
   - Never converged on strong paper-detail hits from search

**Coverage Analysis:**
- 5 URLs shortlisted
- 2 completed (arXiv PDF, ICML virtual)
- 2 in_progress (ICLR listing: 72/85 segments, DBLP AAAI: 36/61 segments)
- 1 new (OpenReview PDF)

The loop invested heavily in giant listing pages and PDFs instead of direct paper hits.

**Recommendation:**
- Add explicit `is_latest` boolean to query_profile
- Strengthen priority_direct_hits weighting for latest/semantic queries
- Consider auto-extracting direct hits when they exceed quality threshold
- Add planner contract rule: for latest queries, extract direct hits before searching listings

---

## 3. State Consistency Issues

### 3.1 Extract Coverage vs Planner Todo Drift

**TODO.md line 90:**
"extract coverage / todo / planner memory are still not fully consistent; the latest workspace/alibabanew run shows completed-vs-in-progress drift, wasted retries on already completed URLs, and planner-visible todo state that does not match actual per-page coverage"

**Evidence from Code:**
1. `agentic_loop.py` lines 234-318: `_reconcile_extract_todos_from_coverage` is 85 lines of complex matching logic
2. The reconciliation tries to match todos to URLs by text matching, which is fragile
3. No validation that reconciliation succeeded

**Recommendation:**
- Make todos reference URLs explicitly (by URL or target_id)
- Remove text matching logic
- Derive todo status from coverage state
- Add validation that reconciliation succeeded

---

### 3.2 No-Op Extract Loops

**TODO.md line 249:**
"cycles 3-5 are mostly no-op replays of the same completed page, because the planner keeps asking for more detail extraction even though runtime marks the page completed and performs 0 LLM calls"

**Evidence from workspace/ennan:**
- Cycle count: 5
- Only 1 URL completed (list-accepted.html)
- Cycles 3-5 likely wasted on completed page

**Root Cause:**
- Planner doesn't see clear enough signal that page is completed
- Or planner memory doesn't update fast enough to show completion

**Recommendation:**
- Return early from extract runtime with `skipped` status for completed pages
- In loop, detect skipped targets and avoid full finalize
- Show skip reason in CLI and trace

---

### 3.3 Wasted Retries on Completed URLs

**TODO.md line 90:**
"wasted retries on already completed URLs"

**Evidence from Code:**
- `agentic_extract_runtime.py` lines 320-326: Failed pages are retried
- But completed pages should not be retried unless scope changes
- No tracking of retry count per page

**Recommendation:**
- Only retry failed pages, not completed pages
- Track retry count per page to limit retries
- Show retry reason in trace

---

## 4. Planner Memory Quality Issues

### 4.1 Missing Query Intent Dimensions

**Current agent_memory includes:**
- goal, query_profile, active_step, todo, next_todos
- priority_extract_urls, priority_direct_hits, known_urls
- matched_papers, suggested_urls, blockers, last_step, last_change

**Missing (per TODO.md lines 86-87):**
1. **Explicit latest boolean** - not just in modes list
2. **Year range progress** - for multi-year queries
3. **Query archetype** - author/venue/topic/semantic classification
4. **Page type distribution** - how many listings vs details vs PDFs
5. **Source quality scores** - official vs mirror, rich vs sparse
6. **Extract progress per venue family** - for multi-venue queries

**Impact:**
- workspace/alibabanew: Multi-year query failed to track year progress
- workspace/mem: Latest query failed to prioritize recency
- workspace/ennan: Author query not detected, wrong segmentation used

**Recommendation:**
Extend `_build_agent_memory` to compute and expose these dimensions.

---

### 4.2 priority_direct_hits Not Strong Enough

**TODO.md line 252:**
"Search did surface stronger candidates early (A-MEM, Agent Workflow Memory, MemoryAgentBench), but the planner still decomposed the task into only venue listing searches"

**Evidence from workspace/mem:**
- Direct hits were surfaced in search
- priority_direct_hits is computed (agentic_view.py lines 617-687)
- But planner still chose listing pages

**Root Cause:**
- priority_direct_hits is optional guidance, not strong obligation
- For latest/semantic queries, direct hits should be preferred automatically
- No quality threshold that triggers auto-extraction

**Recommendation:**
- Strengthen priority_direct_hits weighting for latest/semantic queries
- Add quality threshold configuration
- Consider auto-extracting direct hits above threshold

---

## 5. Extraction Quality Issues

### 5.1 Title/Author Block Segmentation

**TODO.md line 249:**
"The segmented extract batches split paper titles from their author lines, so the LLM mispaired the CellFusion author block with the neighboring ChameleMon title"

**Evidence from workspace/ennan:**
- Results have wrong papers (ChameleMon, ZGaming instead of CellFusion, XRON)
- Abstract field contains author list text, not actual abstract
- Ennan Zhai appears in author list but for wrong papers

**Root Cause:**
- `agentic_text.py` lines 273-315: Segmentation splits on `<li>`, `<tr>`, `<article>` tags
- On accepted-list pages, titles and authors may be in separate DOM elements
- Different segments go to different LLM batches
- LLM mispairs them

**Recommendation:**
- Detect author-query mode from query archetype
- For accepted pages in author-query mode, use block-preserving segmentation
- Keep title+author pairs together in same segment

---

### 5.2 Same-Page Extraction Restart vs Continuation

**TODO.md line 250:**
"each pass used a different lexical/semantic filter pack, so the runtime kept re-ranking and effectively restarting the same page while the CLI trace only showed repeated segment batches"

**Evidence from workspace/congestion and workspace/aiinfra:**
- Same page extracted multiple times
- Ranked window counts drift (151 -> 51 -> 66 -> 151)
- Trace doesn't show restart vs continuation

**Root Cause:**
- Scope signature doesn't include filter pack
- Filter changes re-rank segments without changing signature
- segments_done resets to 0, causing restart
- Trace doesn't distinguish restart from continuation

**Recommendation:**
- Include filter pack in scope signature
- Add `restart_reason` field to trace when segments_done resets
- Show effective filter deltas in CLI output

---

### 5.3 Broad Lexical Semantic Matching

**TODO.md line 251:**
"broad AI/LLM lexical filters are over-selecting early windows"

**Evidence from workspace/aiinfra:**
- Query: "papers on AI infra for LLM training and inference in SIGCOMM 2025"
- Accepted page contains ground truth: MixNet, InfiniteHBD, DistTrain, MegaScale-Infer, Astral, ByteScale, HACK
- Result: Only MixNet, InfiniteHBD, DistTrain plus false positives
- Broad filters over-select early windows, miss later relevant rows

**Root Cause:**
- Simple text matching for filters (agentic_extract_candidates.py lines 185-208)
- No semantic similarity matching
- No cap on candidate count per page
- Early windows consume budget, later windows never reached

**Recommendation:**
- Add semantic similarity matching, not just substring matching
- Cap candidate count per page
- Require multiple filter matches for broad filters

---

## 6. Trace and Debuggability Issues

### 6.1 Insufficient Human-Facing Projection

**TODO.md line 93:**
"user-facing trace readability is still insufficient for debugging agentic runs quickly; the raw trace is rich enough, but the human-facing projection is not yet carrying the right explanations"

**Evidence:**
- CLI output hides selected extract URLs
- CLI output hides candidate-URL proposal step
- CLI output prints raw `None` fields
- No distinction between continuation and restart
- No filter delta shown
- No skip/retry reasons shown

**Recommendation:**
- Show continuation vs restart explicitly
- Show effective filter/semantic-focus deltas
- Show when extracted rows were later dropped
- Show why pages were skipped or retried
- Fix `None` field printing

---

## 7. Missing or Suboptimal TODO Items

### 7.1 Missing Regression Tests

**TODO.md E20:**
"Add fixtures/replays for workspace/ennan, workspace/congestion, workspace/aiinfra, multi-year SIGCOMM 2020-2022, workspace/mem, direct-hit retention, and trajectory continuation-vs-restart projection."

**Current State:**
- tests/test_retrieval_agentic_i1.py exists with 88+ tests
- But no tests specifically for query archetypes

**Gap:**
- E20 is marked "Active" but no concrete test file or test names specified
- No breakdown of what each test should validate

---

### 7.2 Missing State Consistency Validators

**TODO.md H11:**
"Keep loop state canonical and planner memory derived from it"

**Current State:**
- No assertions or tests that verify state consistency

**Gap:**
- H11 is marked "In progress" but no item for adding state consistency validators
- No item for adding assertions after each cycle

---

### 7.3 Missing Design Enforcement

**TODO.md lists design principles but doesn't enforce them:**

1. **"action modules produce compact action results, not direct loop mutations"**
   - Violated by direct state mutation in extract runtime
   - No item for fixing this violation

2. **"candidate URLs are suggestions, not automatic new url_hits"**
   - Need to verify this is not violated
   - No item for adding assertion

3. **"planner receives explicit reconstructed state, not raw transcript"**
   - Implemented but memory incomplete
   - No item for validating memory completeness

---

### 7.4 Suboptimal Prioritization

**Current TODO Priority Stack:**
- P0: Query-aware planner memory
- P1: Query-aware extraction behavior
- P2: Canonical state and result retention
- P3: Human-facing trace
- P4: Metadata/result cleanup
- P5: Regression matrix

**Issues with Current Prioritization:**

1. **P2 should be P0:** State consistency is foundational. Without it, planner memory (P0) will be wrong regardless of how many dimensions are added.

2. **P5 should be P1:** Regression tests should be added NOW for already-identified failure modes, not after more fixes.

3. **Missing item:** Fix direct state mutation in extract runtime should be P0.

4. **Missing item:** Add state consistency validators should be P0.

---

## 8. Major Gaps Not Mentioned in TODO.md

### 8.1 No Query Archetype Detection

**Issue:** The system doesn't classify queries into archetypes (author-led, venue-led, topic-led, semantic, multi-year, latest).

**Impact:**
- Can't adjust extraction strategy per archetype
- Can't prioritize memory dimensions per archetype
- Can't set appropriate stop conditions per archetype

**Recommendation:** Add query classification in `agentic_view.py` and use it to:
- Adjust segmentation strategy (author queries need block-preserving segmentation)
- Prioritize memory dimensions (latest queries need recency signals)
- Set extraction budgets (semantic queries need different budgets than venue queries)

---

### 8.2 No Page Type Quality Scoring

**Issue:** URLs are not scored for quality (official vs mirror, rich vs sparse, listing vs detail).

**Impact:**
- Third-party mirrors compete with official pages
- Low-quality pages consume extraction budget
- Planner can't prioritize high-quality sources

**Recommendation:** Add quality scoring based on:
- Domain authority (official conference domains vs third-party)
- Page role (accepted/program pages are high quality)
- Content richness (pages with abstracts vs listing-only)
- Source type (paper detail > listing > bibliography > PDF)

---

### 8.3 No Extraction Budget Management

**Issue:** No explicit budget management for extraction effort per page type.

**Impact:**
- Giant listing pages (85+ segments) consume all budget
- Direct paper hits (3 segments) get same budget as listings
- No early termination for low-quality pages

**Recommendation:** Add budget tiers:
- Direct paper hits: full budget
- Official listing pages: medium budget
- Third-party listings: low budget
- Bibliographies: very low budget
- PDFs: special handling

---

### 8.4 No Abstract Quality Validation

**Issue:** Extracted abstracts are not validated for quality.

**Evidence from workspace/ennan:**
- Abstract field contains author list text, not actual abstract
- No detection that this is wrong

**Recommendation:** Add abstract quality checks:
- Minimum length
- Not just author names
- Not just title repetition
- Contains actual content words

---

## 9. Consolidated Fix Recommendations

Many identified issues can be addressed by the same foundational fixes. Here are the consolidated action points:

### Fix 1: State Consistency Foundation (Addresses Issues 1, 7, 17, 27)
**Scope:** Make state transitions explicit and validated
- Extract runtime returns state deltas, not direct mutations
- All state changes go through state_apply
- Add state consistency validators after each cycle
- Simplify todo-URL matching to use explicit references

### Fix 2: Query Archetype Detection (Addresses Issues 3, 8, 12, 18)
**Scope:** Classify queries and adjust behavior accordingly
- Add query archetype classification (author/venue/topic/semantic/multi-year/latest)
- Store in query_profile.archetype
- Use archetype to:
  - Adjust segmentation strategy
  - Prioritize memory dimensions
  - Set extraction budgets
  - Choose appropriate stop conditions

### Fix 3: Planner Memory Completeness (Addresses Issues 3, 12, 14, 18)
**Scope:** Add missing dimensions to agent_memory
- query_profile.is_latest
- query_profile.year_range_progress
- query_profile.archetype
- Per-URL quality scores
- Page type distribution
- Strengthen priority_direct_hits for latest/semantic queries

### Fix 4: Extraction Scope Management (Addresses Issues 9, 10, 11)
**Scope:** Fix same-page extraction restart vs continuation
- Include filter pack in scope signature
- Detect and track restart vs continuation
- Add restart_reason to trace
- Prevent surfaced-title feedback poisoning
- Track filter pack history per page

### Fix 5: Extraction Quality by Archetype (Addresses Issues 8, 22)
**Scope:** Adjust extraction strategy per query type
- Author queries: block-preserving segmentation for accepted pages
- Topic queries: semantic similarity matching, cap window count
- Semantic queries: abstract-level evidence weighting
- All queries: abstract quality validation

### Fix 6: Page Quality Scoring (Addresses Issues 13, 19)
**Scope:** Score and prioritize pages by quality
- Add quality scoring based on domain, page role, content richness
- Use scores to prioritize extraction order
- Filter low-quality pages from priority lists
- Add budget tiers per page type

### Fix 7: Trace Readability (Addresses Issues 24, 25)
**Scope:** Make human-facing projection explanatory
- Show continuation vs restart
- Show filter deltas
- Show skip/retry reasons
- Show when extracted rows were later dropped
- Fix None field printing

### Fix 8: Regression Test Matrix (Addresses Issue 26)
**Scope:** Add tests for all identified failure modes
- Author query test (workspace/ennan replay)
- Topic narrow test (workspace/congestion replay)
- Topic broad test (workspace/aiinfra replay)
- Multi-year test (SIGCOMM 2020-2022)
- Latest semantic test (workspace/mem replay)
- State consistency tests
- Memory quality tests

---

## 10. Summary

The agentic search implementation has achieved good architectural separation but still has critical gaps between design intent and implementation. The main issues are:

1. **State consistency violations** - Direct mutation bypasses state_apply, causing coverage/todo drift
2. **Incomplete planner memory** - Missing key dimensions for different query archetypes
3. **Extraction quality issues** - Segmentation, scope management, and filter handling cause wrong results
4. **Missing regression coverage** - No tests for identified failure modes
5. **Suboptimal TODO planning** - Some critical issues not mentioned, priorities not aligned with root causes

The highest leverage fixes are in state consistency and planner memory quality, not in adding more heuristics or increasing budgets. The TODO.md should be updated to reflect these findings and re-prioritize accordingly.

**Recommended Priority Order:**
1. Fix 1: State Consistency Foundation (CRITICAL)
2. Fix 8: Regression Test Matrix (HIGH - do in parallel)
3. Fix 2: Query Archetype Detection (HIGH)
4. Fix 3: Planner Memory Completeness (HIGH)
5. Fix 4: Extraction Scope Management (HIGH)
6. Fix 5: Extraction Quality by Archetype (MEDIUM)
7. Fix 6: Page Quality Scoring (MEDIUM)
8. Fix 7: Trace Readability (MEDIUM)
