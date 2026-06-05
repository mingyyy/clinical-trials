# LangGraph Findings — Day 2, June 3 2026

Framework: LangGraph | School: State machine | Run date: 2026-06-03

---

## Final Architecture

```
fetch → filter → [analyze_batch ×N in parallel] → output
```

4 nodes. `analyze_batch` fans out via LangGraph `Send` API in batches of 12 trials.
All branches converge at `output` using `Annotated` reducers on `matches`, `llm_calls`, `total_tokens`.

**Graph nodes:**
- `fetch` — calls ClinicalTrials.gov API, no LLM
- `filter` — deterministic age/sex hard filter, no LLM
- `analyze_batch` — LLM assessment for one batch of 12 trials (parallel)
- `output` — serialize `MatchingResult` to JSON

---

## Run Summary (final: parallel fan-out + three-state verdict)

| Profile | Fetched | After filter | ELIGIBLE | UNCERTAIN | INELIGIBLE | LLM calls | Wall time |
|---------|---------|--------------|----------|-----------|------------|-----------|-----------|
| P001 — HER2+ stage II, NYC | 50 | 50 | 0 | 3 | 47 | 50 | 123s |
| P002 — TNBC BRCA1, LA | 50 | 50 | 0 | 27 | 23 | 50 | 143s |
| P003 — HR+ HER2−, Chicago | 37 | 37 | 0 | 12 | 25 | 37 | 162s |
| P004 — Melanoma brain mets, Seattle | 25 | 25 | 1 | 8 | 16 | 25 | 126s |
| P005 — HER2+ metastatic, Boston | 44 | 44 | 0 | 7 | 37 | 44 | 111s |
| **Total** | **206** | **206** | **1** | **57** | **148** | **206** | **~665s** |

**Parallel speedup:** Sequential (same prompt): ~2,058s total. Parallel (BATCH_SIZE=12): ~665s. Speedup: **3.1×**.

---

## Wall Time: Sequential vs Parallel

| Profile | Sequential (3-state) | Parallel (3-state) | Speedup |
|---------|---------------------|-------------------|---------|
| P001 (50 trials) | 447s | 123s | 3.6× |
| P002 (50 trials) | 537s | 143s | 3.8× |
| P003 (37 trials) | 438s | 162s | 2.7× |
| P004 (25 trials) | 253s | 126s | 2.0× |
| P005 (44 trials) | 383s | 111s | 3.4× |
| **Total** | **~2,058s (34 min)** | **~665s (11 min)** | **3.1×** |

P003 and P004 show lower speedup — smaller trial sets mean fewer batches, less parallelism opportunity. 37 trials = 4 batches of ~9; 25 trials = 3 batches. Speedup approaches theoretical max only when trial count >> BATCH_SIZE.

---

## Three-State Verdict: What Changed and Why It Matters

**Previous approach (binary):** `eligible: bool`. LLM defaulted to `False` when required information was absent from the patient profile. Missing data = ineligible. This was wrong.

**Current approach (three-state):** `verdict: ELIGIBLE | INELIGIBLE | UNCERTAIN`. Prompt explicitly states: *"Absence of information is NOT evidence of ineligibility."* Missing data → `UNCERTAIN` with a list of what's needed.

**Impact on P001:**
- Binary run: 2 ELIGIBLE
- Three-state run: 0 ELIGIBLE, 3 UNCERTAIN
- The 2 "eligible" trials in the binary run were misclassified — criteria the profile didn't address (e.g. HLA-A*02 typing, pCR status) were assumed to be met. They are genuinely unknown.

**Impact on P004 (brain mets):**
- Binary run: 5 ELIGIBLE
- Three-state run: 1 ELIGIBLE, 8 UNCERTAIN
- The 4 trials that moved from ELIGIBLE to UNCERTAIN require verification of brain met stability, ECOG PS borderline cases, and organ function labs — genuinely unknowns, not clear eligibilities.

**The clinical logic shift:** a binary system acts as a gatekeeper (decide now, from incomplete data). A three-state system acts as a screener (flag candidates, surface what's needed to decide). For clinical trial matching, screening is the right framing — false negatives (missing eligible patients) are more costly than false positives (sending candidates who need further workup).

---

## What LangGraph Did Well

**1. Explicit state transitions make reasoning auditable.**
Every node is named and returns typed state. When P004 returned 5 "eligible" in the binary run, the bug was traceable to the `match_node` prompt — not to the graph structure. The state machine makes failure modes locatable.

**2. Parallel fan-out via `Send` is clean and composable.**
Adding batched parallelism required ~20 lines: `Annotated` reducers on accumulating fields, a routing function, and a `conditional_edges` call. The rest of the graph was unchanged. This is the architectural payoff — you can add parallelism without restructuring your logic.

**3. Criteria reasoning quality is high when the prompt is right.**
Example — NCT07214532 (CDK4/6 inhibitor trial) assessed against P001:
> *"The patient is HER2-positive, which directly and clearly fails Inclusion Criterion #6, which requires HER2-negative breast cancer. This is a definitive exclusion regardless of other characteristics."*
Specific criterion number cited. Correct logic. Traceable.

**4. UNCERTAIN reveals what the profile is missing.**
Example — NCT02821013 (anti-PD-1 duration trial) assessed against P004:
> Uncertain items: "Stability of brain metastases: No information provided on whether brain mets are stable (no progression for at least 4 weeks, no new or enlarging lesions, or treated with surgery/SRS)"
This is exactly the right output for a pre-screening tool. The system identified the single most important piece of missing information.

---

## Failure Modes

**1. Hard filter eliminated 0 trials for every profile.**
All 206 trials passed the age/sex hard filter. For breast cancer profiles this makes sense (trials are designed for the right demographic). For P004 (male, melanoma), it also makes sense. The hard filter is correct but adds no value for these profiles — it would show value for a mixed-cancer query. Worth noting: the hard filter is a computational optimization, not a reasoning component.

**2. Parse errors from API overloads (529).**
In earlier runs (sequential, before three-state), ~15–35% of calls hit 529 overloaded errors. With parallel fan-out these became less frequent — the API handled batched requests better than the sustained sequential burst. No 529 errors observed in the final parallel run. UNCERTAIN from parse errors has been correctly reclassified (they no longer silently inflate INELIGIBLE counts).

**3. Retrieval misses trials registered with broad condition names.**
NCT05232916 (GLSI-100 — Elicit's top pick for P001) was missing from all LangGraph runs until `query.term` was added to `api_client.py`. Root cause: trial registered with condition = "Breast Cancer" and HER2-specificity only in keywords. Fix applied. General lesson: `query.cond` alone is insufficient; keyword and title search must be combined.

**4. The screener/gatekeeper framing must be chosen deliberately.**
The binary prompt produced plausible-looking results that were methodologically wrong. Nothing in the LangGraph architecture prevented this — the error was in the prompt design, not the graph. LangGraph's observability made the error traceable; it didn't prevent it. The framework gives you the tools to inspect and fix. It doesn't make the right framing choices for you.

---

## Criteria Accuracy: Failure Mode Spot-Checks

| Test case | Profile | Criterion tested | LangGraph verdict | Correct? |
|-----------|---------|-----------------|-------------------|---------|
| HER2+ baseline | P001 | HER2− trials correctly excluded | INELIGIBLE with HER2 cited | ✅ |
| Pre-operative exclusion | P001 | Neoadjuvant trials excluded (patient post-treatment) | INELIGIBLE — prior surgery cited | ✅ |
| Brain mets exclusion | P004 | Trials excluding active CNS mets | INELIGIBLE — brain mets cited | ✅ |
| Brain mets unknown stability | P004 | Trials allowing stable brain mets | UNCERTAIN — stability not documented | ✅ |
| ECOG borderline | P004 | ECOG 2 vs trial requiring 0-1 | INELIGIBLE — ECOG cited correctly | ✅ |
| BRCA1 opens trials | P002 | PARP inhibitor trials requiring BRCA mutation | Not spot-checked yet — see Day 3 |
| Treatment line count | P005 | Trials requiring "1-2 prior lines" | Not spot-checked yet — see Day 3 |

---

## Cost (from scoring.py on final parallel run)

| Metric | Value |
|--------|-------|
| Total LLM calls (5 profiles) | 206 |
| Total tokens | 470,099 |
| Cost at $3/M tokens | **$1.41** |
| Cost per profile (avg) | $0.28 |
| Parse errors | **0** (parallel run; sequential had 15–35% error rate) |
| Wall time (parallel, BATCH_SIZE=12) | ~11 min |
| Wall time (sequential, same prompt) | ~34 min |
| Model | claude-sonnet-4-6 |

Explanation quality: **2.0/2.0 across all profiles.** Three-state prompt produces rich explanations with specific criteria cited in every response. This score will not differentiate frameworks if all use the same prompt style — criteria accuracy spot-checks (manual) are the more informative quality dimension.

---

## Key Architectural Observation for Comparative Analysis

LangGraph's state machine is transparent by design. Every finding in this document was reachable by reading the node functions — there are no hidden steps. When the binary prompt produced wrong results, the fix was locatable in 5 minutes. When parallelism was needed, it was addable without restructuring the graph.

The framework does not improve reasoning quality. The LLM does the reasoning. LangGraph provides the scaffolding to inspect, fix, and scale it.

**The question for Days 3–4:** do PydanticAI's type constraints and auto-retry produce better reasoning quality from the same LLM? Does smolagents' code generation handle the three-state distinction differently when it writes its own tool-calling loop? Does Claude Direct — with no graph overhead — match this quality with a good prompt alone?
