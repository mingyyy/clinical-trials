# Clinical Trial Matching: Framework Comparison — Final Analysis

**Project:** Mindfuel independent learning week, June 2–5 2026
**Question:** A pharma client wants to build an AI system to match patients to clinical trials. Which framework?
**Method:** Same task, same model (claude-sonnet-4-6), same 5 patient profiles, 4 framework implementations.

---

## The Question Before The Question

Before answering "which framework," the right answer to "which AI system for clinical trial matching?" may be: **have you looked at Elicit?**

Elicit returned 5 ranked P001 trials in 4 minutes with reasoning. It cost $0. It is a specialist tool built for exactly this problem. A custom build would need to match that quality before the framework choice matters.

The framework comparison is still worth doing — the portability of the finding (which patterns generalize) is the real output. But the prior question is the right starting point for any client conversation.

---

## Results at a Glance

Numbers from the matched-pipeline rerun (100mi radius, 10 pages, 193 trials assessed). Original run in parentheses where different.

| Framework | School of thought | LLM calls | Tokens | Cost | Wall time | ELIGIBLE | UNCERTAIN | Explanation |
|-----------|-------------------|-----------|--------|------|-----------|----------|-----------|-------------|
| LangGraph | State machine | 193 (206) | 410k | $1.23 ($1.41) | 646s | 1 | 54 | 2.00/2.0 |
| PydanticAI | Type safety | 193 (206) | 598k | $1.79 ($2.02) | 395s | 1 | 45 | 2.00/2.0 |
| smolagents | Code generation | 254 (206) | 564k | $1.69 ($1.41) | 1163s | 2 | 51 | 1.95/2.0 |
| Claude Direct | Zero framework | 22 | 310k | $0.93 ($1.09) | 307s | 7 | 46 | 1.90/2.0 |

All frameworks: claude-sonnet-4-6, 5 patients, 100mi radius, 10 pages, same three-state verdict prompt. 0 parse errors.

Additional data points (different methodology — not directly comparable):

| Approach | Paradigm | Patients | Trials assessed | ELIGIBLE | UNCERTAIN | Reproducibility |
|----------|----------|----------|-----------------|----------|-----------|-----------------|
| ml-intern | Autonomous tool-calling agent | 5 | 186 (haversine 100mi) | **9** | ~84 | Low |
| OpenHands | CodeAct (code generation) | 5 (GUI) | ~20/patient (string matching) | **14** | **36** | Low |

---

## What Each Framework Actually Did

### LangGraph — State machine
Graph prescribed before execution: `fetch → filter → [analyze_batch ×N parallel] → output`. Every step named. Every state transition explicit. Parallel fan-out via `Send` API with `Annotated` reducers. ~300 lines of code for the full implementation.

**Architectural contribution:** Observability. When the binary prompt produced wrong results, the bug was traceable to a named node in 5 minutes. When parallelism was needed, it was addable without restructuring the graph.

### PydanticAI — Type safety
`Agent(output_type=EligibilityAssessment, retries=2)` — no graph, no state machine. The Pydantic schema is the contract. Invalid LLM output triggers auto-retry, not silent coercion. `asyncio.gather` replaces `Send` in ~10 lines. ~150 lines total.

**Architectural contribution:** Schema enforcement. The `verdict: Literal["ELIGIBLE", "INELIGIBLE", "UNCERTAIN"]` field cannot be violated without the framework retrying. In this run, `retries=2` was never triggered — 0 validation failures. But the safety net exists and requires zero application code.

**Cost penalty:** PydanticAI uses tool_use (function calling). Every request includes the `EligibilityAssessment` schema as a tool definition. This adds ~990 tokens/call — 43% more tokens than frameworks using raw API calls. The type safety has a fixed per-call price.

### smolagents — Code generation
`CodeAgent` writes Python code to solve the task. The agent decided the structure: fetch → count trials → prefilter → assess batch → compute totals → validate → save. Adapted its approach between profiles (fewer steps on P002 after learning from P001). ~230 lines including tools.

**Architectural contribution:** Adaptability. The agent self-corrected from two failures (import blocked, schema validation error) without human intervention. For a pipeline that evolves mid-run or handles unexpected edge cases, this matters.

**Cost:** Same as LangGraph in the original run ($1.41) — both use raw Anthropic API, no tool_use overhead. In the rerun, smolagents cost $1.69 vs LangGraph $1.23: smolagents generated its own API fetch code and retrieved more trials than the shared pipeline (P001: 92 fetched vs 74, P005: 44 vs 32), adding 61 extra assessments. The code-generation steps (meta-LLM calls) are not counted in the reported llm_calls but add ~280s wall time overhead.

**Limitation:** 19 steps per patient (vs 4 LangGraph nodes) for the same pipeline. `import json` was blocked by the sandbox, causing the agent to count `'"nct_id"'` occurrences as a workaround. The sandbox restrictions limit what code the agent can write.

### Claude Direct — Zero framework
Raw `AsyncAnthropic` client. One system prompt. `BATCH_SIZE=10`: 10 trials bundled into **one LLM call**, assessed together in a single context window. 22 total calls vs 193 for the other frameworks. ~100 lines.

**Architectural contribution:** None intentional. The absence of framework overhead produced the best cost ($0.93) and best wall time (307s).

**Distinctive effect:** Batch context changes verdicts — see Finding 2 below. The 10-trials-per-call design is the root cause.

---

## The Three Key Findings

### 1. API mode matters more than framework choice

LangGraph and smolagents cost identically ($1.41) despite completely different architectures. PydanticAI costs 43% more. The difference is not the framework — it's whether the framework uses raw API calls or tool_use (function calling).

```
Raw API (LangGraph, smolagents, Claude Direct): ~2,282 tokens/call
Tool use (PydanticAI): ~3,272 tokens/call
```

A team choosing PydanticAI for type safety must account for this overhead. At 10,000 patients × 50 trials each, the cost is ~$3,400 (LangGraph) vs ~$4,900 (PydanticAI) per run — for identical clinical output.

### 2. Assessment context changes verdicts — and the root cause is trials-per-call, not the framework

The four frameworks and ml-intern use three distinct assessment architectures, all with the same model and the same system prompt:

| Approach | Trials per LLM call | LLM calls (P001) | ELIGIBLE | UNCERTAIN |
|--|---------------------|------------------|----------|-----------|
| LangGraph | **1** | 73 | 1 | 54 |
| smolagents | **1** | 73+ | 2 | 53 |
| PydanticAI | **1** | 73 | 1 | 45 |
| Claude Direct | **10** | ~8 | 7 | 46 |
| ml-intern | **all 68–74** | 1 | 2 (P001) | ~21 (P001) |

**Root cause:** the number of trials the LLM sees simultaneously in one context window determines how it calibrates verdicts.

- **1 trial per call (LangGraph, PydanticAI, smolagents):** each trial assessed against an abstract implicit standard. No anchoring. Conservative — borderline trials trend UNCERTAIN or INELIGIBLE.
- **10 trials per call (Claude Direct):** the LLM sees 9 neighbours alongside each trial. Clearly ineligible neighbours anchor the comparison and pull borderline trials toward ELIGIBLE. 7 ELIGIBLE vs 0–1 from per-trial frameworks.
- **All trials per call (ml-intern):** Python scripts fetch, haversine-filter, and format all P001 trials into a single text file (68 post-haversine-filter; 74 total fetched within 100mi). The `read` tool loads the entire file into the LLM's context in one step. The LLM then generates the full eligibility assessment as one continuous response. Maximum comparative context.

The batch-all architecture is one contributing factor, but the P004 finding has a more precise explanation with three distinct layers.

**What the controlled experiment showed:**

A notes contamination test was run directly — three variants of the same P004 × NCT04511013 assessment:

| Variant | Notes | Verdict | Confidence |
|---|---|---|---|
| A (existing LangGraph run) | Researcher framing at end | INELIGIBLE | 0.92 |
| A (fresh run) | Researcher framing at end | INELIGIBLE | 0.92 |
| B | Notes stripped entirely | INELIGIBLE | 0.92 |
| C | Researcher framing moved to top | INELIGIBLE | 0.90 |

**The notes contamination hypothesis was not confirmed.** Stripping the researcher framing entirely produced the same verdict at the same confidence. The LLM does not need "most trials should be excluded" to reach INELIGIBLE — it reasons from the clinical data itself.

Variant B's explanation: *"the context of 'metastatic melanoma' as the current diagnosis and listing these as prior treatments strongly suggests they were given for metastatic disease."* The LLM is applying clinical inference, not following the notes.

**The actual finding — the "absence of information" rule has an implicit scope.** The LLM applies UNCERTAIN when data is genuinely absent with no basis for inference: ECOG score (can't infer from other fields), lab values, imaging results. It does not apply UNCERTAIN when the clinical context strongly implies the answer. Metastatic melanoma diagnosis + ipi+nivo combination as prior treatments → the LLM infers the most clinically plausible interpretation: metastatic-setting treatment. This is sound clinical reasoning. Ipi+nivo is a standard first-line metastatic melanoma regimen; adjuvant ipi+nivo combination is rare.

**The divergence from ml-intern, examined directly.** Reading ml-intern's actual criterion table for NCT04511013 reveals why it returned ELIGIBLE: *"Prior ipi + nivo (both arms permitted) — trial explicitly includes prior-treated patients in one arm."* This claim is factually wrong. NCT04511013 has two arms — Encorafenib+Binimetinib+Nivo vs. Ipilimumab+Nivo as comparator — and both enroll treatment-naive patients. The exclusion is unambiguous: "Participants must not have received prior systemic therapy for metastatic disease." ml-intern read the Ipi+Nivo comparator arm (what the trial *gives* patients) as an arm permitting prior Ipi+Nivo history (what it *accepts*). That is a hallucination of a trial design feature.

**What the four frameworks got right, and what they still missed.** The four frameworks correctly identified the relevant exclusion and applied it based on clinical inference: metastatic melanoma diagnosis + prior ipi+nivo → most likely metastatic-setting treatment. This inference is defensible. What they missed is that the inference is not certain — the profile does not state the treatment setting, and adjuvant ipi+nivo is FDA-approved for resected Stage III/IV melanoma. The LangGraph run acknowledged this ambiguity explicitly in uncertain_items ("Whether ipilimumab and nivolumab were given in the neoadjuvant/adjuvant setting vs. for metastatic disease") and then returned INELIGIBLE anyway. Per the explicit prompt rule, the correct verdict for NCT04511013 × P004 is UNCERTAIN. Both the four frameworks and ml-intern produced the wrong answer — by different mechanisms.

**The broader implication — why contextual inference overrides explicit rules:**

The test shows that "absence of information" rules have an implicit activation condition: they fire when data is truly absent. When the LLM can construct a confident inference from surrounding context, the uncertainty rule does not activate — the LLM concludes it is confident enough that the rule is not needed.

This reveals a structural property of how LLMs process rules: **rules are applied conditionally on the LLM's confidence about the case at hand.** A concrete, specific claim in the patient context ("metastatic melanoma, prior ipi+nivo") raises confidence enough that an abstract safety rule ("use UNCERTAIN for missing data") does not fire. There is no architectural hierarchy that forces rule compliance regardless of confidence. The system prompt has soft authority — it can be overridden not by contradiction but simply by the LLM becoming sufficiently confident from other context signals.

This is not specific to claude-sonnet-4-6. It is a structural property of all autoregressive LLMs, for two reasons: the training data shapes models to behave like humans who reason from context, and there is no architectural enforcement layer that treats system prompt rules as hard constraints. The risk in clinical systems is not that the LLM ignores rules — it is that the LLM's confidence is shaped by contextual signals that may not be reliable. EHR notes written by a clinician who has already formed a view, case summaries with embedded framing, or structured fields with implicit expectation can all raise the LLM's confidence on a case before it reads the eligibility criteria — and that confidence may suppress appropriate uncertainty without leaving any trace in the verdict output.

**The P001 notes test: 2/6 borderline trials changed verdict, in opposite directions.**

To test whether notes matter on genuinely borderline cases, the same three-variant test was run on all 6 UNCERTAIN trials from the P001 LangGraph rerun. P001 notes: *"Baseline case. Should match several HER2+ trials in NYC area."* — expectation-setting framing.

| NCT ID | LangGraph baseline | A (notes end) | B (clean) | C (notes top) |
|---|---|---|---|---|
| NCT07211178 | UNCERTAIN 0.65 | **ELIGIBLE 0.82** | UNCERTAIN 0.72 | **ELIGIBLE 0.82** |
| NCT07192432 | UNCERTAIN 0.55 | UNCERTAIN 0.55 | UNCERTAIN 0.55 | UNCERTAIN 0.55 |
| NCT02945579 | UNCERTAIN 0.45 | UNCERTAIN 0.45 | UNCERTAIN 0.50 | UNCERTAIN 0.45 |
| NCT06253871 | UNCERTAIN 0.45 | UNCERTAIN 0.45 | UNCERTAIN 0.45 | UNCERTAIN 0.45 |
| NCT06220214 | UNCERTAIN 0.45 | **INELIGIBLE 0.80** | UNCERTAIN 0.45 | **INELIGIBLE 0.85** |
| NCT05232916 | UNCERTAIN 0.30 | UNCERTAIN 0.30 | UNCERTAIN 0.35 | UNCERTAIN 0.35 |

Two verdict changes — in **opposite directions**:

- **NCT07211178 (UNCERTAIN → ELIGIBLE):** The framing "should match" pushed the LLM to interpret missing data permissively. Without notes (B): "need to confirm NED status → UNCERTAIN." With notes (A/C): "no exclusion triggered → ELIGIBLE." Same missing data, different threshold.
- **NCT06220214 (UNCERTAIN → INELIGIBLE):** This is a neoadjuvant trial; the patient has already had surgery. Without notes (B), the LLM hedges on the mismatch. With notes, it commits — the framing "should match several" apparently prompted the LLM to be more decisive about clear non-matches, confirming the obvious exclusion (post-surgical patient, pre-surgical trial).

Position made no difference: A and C produced identical verdicts in all six cases.

**Contrast with P004:** The P004 notes test showed zero effect — notes didn't change any verdict because the clinical signals (metastatic diagnosis + ipi+nivo) were strong enough to anchor the LLM regardless of framing. P001 showed a 33% verdict change rate because these trials were genuinely borderline — low-confidence UNCERTAIN — and the notes framing could tip the scale.

**The unified finding across both tests:** Notes contamination is not uniformly inflationary. It is **confidence-amplifying** — notes framing reduces appropriate uncertainty, making the LLM more decisive on borderline cases in whichever direction the clinical evidence leans. When there is no strong exclusion signal, notes pull toward ELIGIBLE. When there is a clear exclusion that the LLM was hedging on, notes pull toward INELIGIBLE. The net effect is fewer UNCERTAIN verdicts, which in a clinical screening context means fewer appropriate flags for human review.

**This is not a framework property. It is a trials-per-call property.** Any framework can implement any of these patterns. LangGraph with batch-all would behave like ml-intern. ml-intern with per-trial calls would behave like LangGraph. The framework is a wrapper around that choice; it does not make the choice.

**Note on confusing variable names:** both LangGraph and Claude Direct have a variable called `BATCH_SIZE`, but it means different things. In Claude Direct (`BATCH_SIZE=10`): trials bundled per LLM call. In LangGraph (`BATCH_SIZE=12`): concurrent parallel LLM calls — each still assesses exactly one trial. LangGraph's 193 calls = 193 individual per-trial assessments, run 12-at-a-time.

The effect is systematic and held across both runs and all five profiles. The team must choose which pattern they want intentionally:

- **Per-trial:** auditable, reproducible, conservative — best when ELIGIBLE triggers a costly downstream step
- **Batch-N:** more inclusive, lower threshold — best when missing eligible patients is the primary risk
- **Batch-all:** maximum comparative reasoning, most capable on complex multi-factor patients — not reproducible across runs; different runs may produce different structure and verdicts

### 3. Framework adds observability and reliability guarantees — not reasoning quality

The LLM does the reasoning. The framework provides scaffolding.

- LangGraph's named nodes made bugs locatable, not preventable
- PydanticAI's schema enforcement caught zero validation errors in this run — the prompt was the protection
- smolagents' self-correction recovered from two failures the other frameworks wouldn't have encountered
- Claude Direct produced the same explanation quality as LangGraph (2.0/2.0 on most profiles) with no framework at all

The single highest-impact decision made during this project was adding "Absence of information is NOT evidence of ineligibility" to the system prompt. That change moved P001 from 2 ELIGIBLE (false positives) to 0 ELIGIBLE / 3 UNCERTAIN (accurate). No framework produced or prevented that insight.

---

## Failure Mode Comparison

| Failure mode | LangGraph | PydanticAI | smolagents | Claude Direct |
|-------------|-----------|------------|------------|---------------|
| Parse error / invalid schema | Manual coerce to INELIGIBLE | Auto-retry, then raise | Manual coerce | Manual coerce |
| Upstream data bug | Not caught | Not caught | Not caught | Not caught |
| Prompt miscalibration | Observable (named nodes) | Observable (retries count) | Observable (step trace) | Hard to trace |
| Wrong verdict | Not caught by framework | Not caught by framework | Not caught by framework | Not caught by framework |
| Batch failure blast radius | 1 trial | 1 trial | 1 trial | **10 trials** |

All frameworks share the same fundamental limitation: they validate structure, not clinical accuracy. A well-formed JSON response with the wrong verdict passes every framework's checks.

---

## When to Use Which

**LangGraph** — when the pipeline will be debugged, extended, or maintained by a team. Named nodes make failure modes locatable. The graph structure is the documentation. Overhead: ~300 lines, highest code complexity.

**PydanticAI** — when output schema stability matters more than cost, and the pipeline is simple. Type enforcement is automatic; parallelism is trivial. Accept the 43% token premium. Best for teams who want the LLM-to-application contract to be machine-enforced.

**smolagents** — when the task is open-ended, or the pipeline changes at runtime. Not appropriate for a prescribed pipeline — code generation adds overhead that buys nothing. Appropriate when the agent needs to decide what to do, not just how to do it.

**Claude Direct** — when the pipeline is stable, the team understands prompting, and cost efficiency is a priority. The batch context effect must be understood and accepted. Appropriate for high-volume production pipelines where prompt stability can be guaranteed.

---

## ml-intern — A Fifth Data Point

ml-intern was run twice: first on P001 only (follow-up run to validate the cold-start finding), then on all 5 patients using the same model (claude-sonnet-4-6), same 100mi radius, and same API context hint. Full trace in `findings/6_ml_intern_openhands_observations.md`.

**How it works:** Tool-calling agent (LiteLLM + fastmcp). Not smolagents. Makes a plan, calls `bash` to curl the API, writes Python to compute haversine distances, assesses eligibility via LLM reasoning over full criteria text, produces narrative output.

**5-patient results:**

| Patient | Pages fetched | Assessed (100mi) | ELIGIBLE | UNCERTAIN | INELIGIBLE |
|---------|--------------|-----------------|----------|-----------|------------|
| P001 — HER2+ BC, NYC | 8 (391 trials) | 74 | 2 | ~21 | ~51 |
| P002 — TNBC, LA | ~4 | 52 | 2 | 35 | 15 |
| P003 — HR+ HER2−, Chicago | 3 (147 trials) | 19 | 0 | 16 | 3 |
| P004 — Melanoma+brain mets, Seattle | 4 (183 trials) | 18 | **5** | 5 | 8 |
| P005 — HER2+ metastatic, Boston | ~2 | 23 | 0 | 7 | 16 |
| **Total** | | **186** | **9** | **~84** | **~93** |

**vs four-framework rerun (193 trials assessed):** LangGraph 1 ELIGIBLE, PydanticAI 1, smolagents 2, Claude Direct 7. ml-intern: 9 ELIGIBLE.

**The P004 finding — closer examination changes the conclusion.** P004 is the hard test case (metastatic melanoma, brain mets, ECOG 2, prior ipi+nivo). ml-intern found 5 ELIGIBLE; the four frameworks found 0–1.

ml-intern's top result was NCT04511013, ranked as "top priority referral." But its justification — "trial explicitly includes prior-treated patients in one arm" — is wrong. The trial has two arms (Enco+Bini+Nivo vs. Ipi+Nivo), both first-line only. ml-intern confused the treatment comparator with a permissive eligibility arm. NCT04511013 for P004 is a hallucinated match, not a found match.

The other four ml-intern ELIGIBLE results (NCT06047379 — oral NEO212 in brain mets; NCT06500455 — fractionated SRS; NCT05098210 — neoantigen vaccine; NCT03452774 — matching registry) are more defensible. The four frameworks assessed all of these and most returned UNCERTAIN, not INELIGIBLE — the divergence on those trials is a batch context effect, not a criteria reading difference.

**What the P004 case actually shows:** Every system in this study produced a wrong answer for NCT04511013 — via different reasoning paths:

| System | Verdict | Error |
|--------|---------|-------|
| Four structured frameworks | INELIGIBLE | Acknowledged ambiguity in `uncertain_items`, then overrode the UNCERTAIN rule with a clinical inference |
| ml-intern | ELIGIBLE | Hallucinated that the comparator arm signals the trial accepts prior-treated patients |
| OpenHands | INELIGIBLE | Stated the trial "excludes brain mets" — factually wrong, the trial specifically enrolls brain mets patients |
| **Elicit** | **Effectively UNCERTAIN** | Surfaced NCT04511013 as the top match, explicitly left the prior ipi+nivo question unresolved: "because the protocol's own control arm is ipi+nivo, this does not look like a trivial adjuvant/metastatic misclassification" |

Elicit was the only system that correctly handled this case. Its biology-first approach with explicit epistemic humility — "I cannot verify from the snippet whether prior ipi+nivo is an exclusion" — produced an answer closer to correct than all four purpose-built frameworks. The structured frameworks' problem was not retrieval or schema or framework choice: it was that their LLM confidence calibration overrode an explicit prompt rule when a plausible inference was available. Elicit, returning a shortlist with caveats rather than a verdict, avoided this failure mode entirely.

**How it differed from the four frameworks (rerun):**

| Dimension | Four frameworks (rerun) | ml-intern (5-patient) |
|---|---|---|
| Trials assessed | 193 total | 186 total |
| Geographic filter | API geo 100mi | Haversine per site 100mi |
| Assessment method | Per-trial LLM call (or batch-10) | Full-text narrative per trial |
| Output format | Structured JSON (ELIGIBLE/UNCERTAIN/INELIGIBLE) | Rich narrative, priority tiers, distances |
| ELIGIBLE total | 1–7 per framework | **9** |
| Reproducibility | High | Low |

---

## OpenHands — A Sixth Data Point

After the four framework runs, OpenHands v0.40 (CodeAct paradigm) was run via its web UI on all 5 patient profiles. Full trace in `findings/6_ml_intern_openhands_observations.md`. Headless runs were blocked (see below) — all 5 patients completed via GUI.

**What it did (P001):** 6 steps. MCP fetch blocked by robots.txt → immediate fallback to Python requests → API call succeeded → three IPython cells to process and classify trials → one `think` step → `finish`.

**5-patient results:**

| Patient | Events | ELIGIBLE | UNCERTAIN | INELIGIBLE |
|---------|--------|----------|-----------|------------|
| P001 — HER2+ BC, NYC | 28 | 1 | 1 | ~13 |
| P002 — TNBC, LA | 45 | 7 | 13 | 0 |
| P003 — HR+/HER2-, Chicago | 57 | 4 | 10 | 5 |
| P004 — Melanoma+brain, Seattle | 69 | 0 | 9 | 9 |
| P005 — HER2+ metastatic, Boston | 81 | 2 | 3 | 15 |
| **Total** | | **14** | **36** | **~42** |

P001: LangGraph and smolagents returned 0 ELIGIBLE — they marked NCT07214532 UNCERTAIN (possible match, needs review), not ELIGIBLE. The agreement is on the clinical conclusion (Stage II excluded from most trials), not the verdict label. P004 correctly returned 0 ELIGIBLE, unlike ml-intern (5 ELIGIBLE via hallucination). NCT04511013 was listed INELIGIBLE but for the wrong reason (OpenHands stated it "excludes brain mets" — actually the trial specifically enrolls brain mets patients; the real exclusion is prior metastatic systemic therapy). Event count grew monotonically (28→81), suggesting later profiles required more reasoning steps.

**How it differed from the four frameworks:**

| Dimension | Four frameworks | OpenHands |
|-----------|----------------|-----------|
| Eligibility assessment method | LLM call per trial (same prompt) | Python string matching (no LLM per trial) |
| Steps for 20 trials | 20–50 LLM calls | 6 steps, 0 per-trial LLM calls |
| Output format | Structured JSON (TrialMatch schema) | Narrative prose |
| Prompt engineering required | Yes — calibrated three-state prompt | None — Python logic |
| Self-correction | smolagents only | Yes — MCP blocked, fell back immediately |
| Reproducibility | High (same prompt → same structure) | Low (different runs → different code) |

**The finding it adds:** A well-scoped task given to a general-purpose coding agent produced a correct clinical answer in 6 steps with no framework, no prompt engineering, and no shared pipeline code. The answer was consistent with the four frameworks on the key verdict for P001 (Stage II excluded from most trials). P004 0 ELIGIBLE is the stronger result — it agreed with the structured frameworks against ml-intern's hallucinated ELIGIBLE. But it got the reasoning wrong. This is the clearest evidence in the study that verdict and explanation are separable: a system can reach the right verdict via the wrong path.

**What OpenHands did NOT do:** It did not use the Anthropic API for per-trial reasoning (despite being asked to), did not apply the three-state verdict schema, did not produce structured output, and did not handle the "absence of information" rule explicitly. For a production system requiring auditable, structured verdicts, the four-framework approach is still correct. OpenHands is appropriate for one-off analysis where narrative output is acceptable.

**Headless runs: blocked, resolved via GUI.** After the P001 GUI run, an automated headless script (`run_openhands_all.py`) was written. All 5 patients failed within seconds — root cause: OpenHands v0.40 hardcodes both `temperature` and `top_p` as non-None defaults in `LLMConfig`; Claude 4.x rejects both being set simultaneously; all Claude 3.x fallbacks are now deprecated (return `not_found_error`). Workarounds via env var, config.toml, and model substitution all failed. All 5 patients were rerun via GUI, which leaves `top_p` unset by default. Fix for future work: OpenHands v0.41+.

---

## The Portability Test

The closing question was: **"A pharma client wants to build an AI system to match patients to clinical trials. Which framework?"**

The honest answer, in order:

1. **First: is this problem already solved?** Elicit does trial matching natively, costs less, and requires no engineering. For most pharma companies, the right answer is to use a specialist tool, not build one.

2. **If you're building it anyway — what is the operational context?**
   - Small team, changing requirements, open-ended task: start with Claude Direct to prove the prompt works, then add LangGraph when you need maintainability.
   - Large engineering team, production at scale, stable pipeline: Claude Direct for cost efficiency. Add PydanticAI if schema stability at the API boundary matters.
   - Research/exploration context where the pipeline will evolve: LangGraph for observability.
   - Autonomous agent that handles unexpected cases: smolagents, with eyes open on the overhead.
   - One-off analysis, narrative output acceptable, no reproducibility requirement: OpenHands or any CodeAct agent. Fast, no engineering, correct answer — but not a system.

3. **The framework is not the bottleneck.** The system prompt is. The retrieval strategy (which trials get fetched) is. The verdict schema (binary vs three-state) is. All four frameworks produced identical explanation quality when given the same prompt. The engineering choice that moved the needle was "absence of information is NOT evidence of ineligibility" — and that was a one-line prompt change. OpenHands reached the same conclusion without any of that, using Python string matching.

---

## Rerun — Matched Pipeline (June 3 2026)

**Why rerun:** Original run used 1 page (50 trials, 250mi radius). ml-intern follow-up fetched 10 pages (500 trials, 100mi haversine). To compare fairly, the four frameworks were rerun with matching parameters.

**Changes from original:**
- `SEARCH_RADIUS_MILES`: 250 → **100** (matches ml-intern's haversine threshold)
- `MAX_PAGES`: 1 → **10** (up to 500 trials fetched per patient)
- All other parameters unchanged: same model, same prompt, same 5 patients, same `hard_filter_trials()` age/sex pre-filter

**Effective trial counts (verified pre-run):**

| Patient | Fetched | After hard filter |
|---------|---------|-------------------|
| P001 | 74 | 73 |
| P002 | 52 | 52 |
| P003 | 19 | 18 |
| P004 | 18 | 18 |
| P005 | 32 | 32 |
| **Total** | **195** | **193** |

Note: tighter radius removes more trials than pagination restores — 193 assessed vs 206 in the original. Condition-relevant trials are geographically concentrated.

**Results:**

| Framework | LLM calls | Tokens | Cost | Wall time | ELIGIBLE | UNCERTAIN |
|-----------|-----------|--------|------|-----------|----------|-----------|
| LangGraph | 193 | 410k | $1.23 | 646s | 1 | 54 |
| PydanticAI | 193 | 598k | $1.79 | 395s | 1 | 45 |
| smolagents | 254 | 564k | $1.69 | 1163s | 2 | 53 |
| Claude Direct | 22 | 310k | $0.93 | 307s | 7 | 46 |

**Delta vs original run:**

| Framework | Cost | ELIGIBLE | UNCERTAIN | Notes |
|-----------|------|----------|-----------|-------|
| LangGraph | $1.41 → $1.23 (−13%) | 1 → 1 | 57 → 54 | Stable |
| PydanticAI | $2.02 → $1.79 (−11%) | 0 → 1 | 45 → 45 | One new ELIGIBLE |
| smolagents | $1.41 → $1.69 (+20%) | 1 → 2 | 51 → 53 | Agent added extra steps (254 vs 193 calls) |
| Claude Direct | $1.09 → $0.93 (−15%) | 5 → 7 | 70 → 46 | ELIGIBLE up, UNCERTAIN down |

**What changed — and what didn't:**

Tighter radius (100mi) removed more trials than pagination added back: 193 assessed vs 206 original, so the rerun is actually cheaper. All relative rankings hold: Claude Direct still cheapest and fastest, PydanticAI still highest token cost (tool_use overhead), smolagents still slowest.

The verdict distributions are stable. The per-trial frameworks (LangGraph, PydanticAI) converge on 1 ELIGIBLE each. Claude Direct's batch context effect persists — 7 ELIGIBLE vs 1 in per-trial frameworks, same direction as before. The structural finding (assessment context changes verdicts) survives the pipeline change unchanged.

The one notable shift: Claude Direct's UNCERTAIN count dropped 70 → 46. With a tighter geographic filter, fewer ambiguous trials appear in each batch context, likely reducing the LLM's tendency to hedge. Worth noting but hard to interpret without ground truth.

---

## Numbers Summary

Model: claude-sonnet-4-6 | June 3 2026 | 5 patients | 193 trials assessed (rerun) | 206 trials assessed (original)

| Dimension | Winner | Rerun value | Original value |
|-----------|--------|-------------|----------------|
| Cheapest | Claude Direct | $0.93 | $1.09 |
| Fastest | Claude Direct | 307s | 329s |
| Fewest calls | Claude Direct | 22 | 22 |
| Best schema safety | PydanticAI | framework-enforced | framework-enforced |
| Best observability | LangGraph | named nodes | named nodes |
| Most adaptable | smolagents | self-corrects | self-corrects |
| Most conservative verdicts | PydanticAI | 1 ELIGIBLE, 45 UNCERTAIN | 0 ELIGIBLE, 45 UNCERTAIN |
| Most permissive verdicts | Claude Direct | 7 ELIGIBLE, 46 UNCERTAIN | 5 ELIGIBLE, 70 UNCERTAIN |
| Most consistent with LangGraph | smolagents | nearly identical distribution | nearly identical distribution |
| Explanation quality | All tied* | 2.00/2.0 (LG, PA), 1.95 (SA), 1.90 (CD) | same |
| Most ELIGIBLE verdicts (5 patients) | ml-intern | 9 ELIGIBLE / 186 assessed | — |
| Most ELIGIBLE on complex patient | ml-intern | 5 ELIGIBLE for P004 vs 0–1 frameworks (but NCT04511013 based on hallucinated arm) | — |

*Explanation quality scores converge because all frameworks use the same underlying model and prompt. Differentiation requires manual criteria accuracy review, not automated scoring.

**Rubric coverage note:** The rubric allocated 20% to Criteria Accuracy (spot-check of specific failure modes) and 55% to Recall + Precision (ground truth NCT ID matching). Criteria Accuracy was partially spot-checked per framework (see individual findings files) but never aggregated into a final score. Recall and Precision were not scored — ground truth NCT IDs were never labeled (see `findings/ground_truth.json`). The comparison above covers the 45% of the rubric that was systematically scored: explanation quality (20%) and cost/operational metrics (25%). The unscored dimensions would require a labeled ground truth set and a full per-trial criteria audit.

---

## Closing

This project asked: a pharma client wants to build an AI system to match patients to clinical trials — which framework?

After two runs (original 250mi/1-page, rerun 100mi/10-page), three autonomous agent comparisons (ml-intern, OpenHands), and a specialist tool (Elicit), the answer is layered:

**The framework choice is not where the value is.** Every ranking that held in the original run held in the rerun. Cost, speed, schema safety, observability, adaptability — all stable. The verdict distributions shifted by noise, not signal. A team that chooses LangGraph for observability in June will still be right to have chosen it in December; a team that chooses PydanticAI for schema enforcement will still be paying the tool_use premium.

**The retrieval strategy mattered more.** Changing from 250mi/50 trials to 100mi/500-fetched (193 assessed) produced a cleaner trial set and slightly lower cost — but the same clinical conclusions. ml-intern's autonomous approach of fetching 10 pages and haversine-filtering per site produced the same top match (NCT05232916) that Elicit, LangGraph, and smolagents all converged on independently. The trial was findable from the first 50. Retrieval breadth helps at the margin; it doesn't change which trials are genuinely eligible.

**The prompt was the product.** One sentence — "Absence of information is NOT evidence of ineligibility" — changed the clinical meaning of every output. No framework produced or could have produced that insight. It came from understanding the clinical problem.

**Autonomous agents found more eligible trials overall — but the P004 case is more instructive than the headline count suggests.** ml-intern found 9 ELIGIBLE across 5 patients vs 1–7 for the four frameworks. The most significant case: P004 got 5 ELIGIBLE from ml-intern vs 0–1 from frameworks. But ml-intern's top match, NCT04511013, was based on a hallucinated design feature — it read the Ipi+Nivo comparator arm as a permissive prior-treatment eligibility criterion. The four frameworks returned INELIGIBLE through a different error: confident clinical inference that overrode their own UNCERTAIN rule. Both errors read as authoritative in the output. The P004 case is the clearest evidence in this study that LLM confidence calibration — not retrieval depth, not framework choice — is the hardest problem in production clinical AI.

OpenHands answered correctly in 6 steps for P001 with Python string matching and no prompt engineering — consistent with LangGraph and smolagents. Both approaches get the right answer for simple patients. The gap opens for patients like P004 where eligibility depends on recognizing a specific combination across brain mets, ECOG, prior ICI history, and BRAF mutation status simultaneously. For a one-off analysis, autonomous agents are faster and more capable. For a production system where a clinician must audit every verdict, the four-framework approach is still correct.

**The right starting point is still Elicit.** A specialist tool returned the correct answer in 4 minutes at no cost. For a pharma client without proprietary data integration requirements, the build vs buy question should be answered before the framework question is asked.

If you are building it: start with Claude Direct (100 lines, cheapest, fastest). Add LangGraph when you need a team to debug it in production. Add PydanticAI when schema stability at the API boundary is load-bearing. Add smolagents when the task is genuinely open-ended. Use ml-intern or OpenHands for exploratory one-off analysis where speed matters more than reproducibility. The framework decision is the last decision, not the first.
