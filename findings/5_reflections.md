# Reflections — Clinical Trial Matching Week

**June 2–5, 2026 · Mindfuel independent learning week**

---

## What We Set Out To Do

The question was: a pharma client wants to build an AI system to match patients to clinical trials — which framework should they use?

The method: implement the same task four ways (LangGraph, PydanticAI, smolagents, Claude Direct), using the same model (claude-sonnet-4-6), the same 5 patient profiles, and the same ClinicalTrials.gov data source. Observe two autonomous agents (ml-intern, OpenHands) as additional data points outside the controlled comparison. Run Elicit first as a prior-question check — does this problem already have a specialist solution?

The expected output was a clean ranking: framework A wins on quality, framework B wins on cost, here is when to use each.

---

## What We Expected to Find

Going in, the working hypothesis was:

- Structured frameworks (LangGraph, PydanticAI) would produce more accurate and consistent verdicts than unstructured approaches, because the scaffolding would enforce the right behavior.
- smolagents' code-generation paradigm would be brittle — generating Python that breaks in edge cases, requiring human intervention.
- Claude Direct would be a rough baseline: fast and cheap but lower quality.
- ml-intern (described by every secondary source as "built on smolagents") would behave similarly to smolagents, with more autonomy and less reproducibility.
- OpenHands would be the most powerful autonomous option — CodeAct with full shell access, able to install packages and iterate on its own code.

---

## What Actually Happened

### Frameworks produced identical reasoning quality — but different operational behavior

Explanation quality across all four frameworks: 2.00 / 2.0 (LangGraph, PydanticAI), 1.95 (smolagents), 1.90 (Claude Direct). Zero parse errors across 662 LLM calls in the controlled rerun. The rankings were effectively flat.

The expected differentiation on reasoning quality didn't materialize. The LLM does the reasoning. The framework is scaffolding around it. When you give four frameworks the same model, the same prompt, and the same three-step task, the output is roughly equivalent regardless of the wrapper.

What did differentiate them was operational: LangGraph's named nodes made a prompt bug traceable in 5 minutes. PydanticAI's schema enforcement was never triggered — 0 validation failures — but the safety net requires zero application code. smolagents self-corrected from two failures (a blocked `import json` and a schema mismatch) without human intervention. Claude Direct had no recovery mechanism but also had no failures to recover from.

### The prompt mattered more than the framework

The highest-impact change in the entire project was one sentence added to the system prompt: *"Absence of information is NOT evidence of ineligibility."*

Before that change, P001 returned 2 ELIGIBLE verdicts — both false positives for trials requiring metastatic disease that a Stage II patient cannot access. After the change: 0 ELIGIBLE, 3 UNCERTAIN. The patient was correctly classified as ambiguous, not as eligible or ineligible. No framework produced or could have produced that insight. It came from understanding the clinical problem.

This was not expected going in. The assumption was that framework choice would be the primary lever. It wasn't.

### Assessment context produced a systematic verdict shift — and there are three distinct patterns, not two

Going in I understood there were two patterns: per-trial (LangGraph, PydanticAI, smolagents) and batch-10 (Claude Direct). Reading the ml-intern logs revealed a third.

| Approach | Trials per LLM call | ELIGIBLE |
|---|---|---|
| LangGraph / PydanticAI / smolagents | 1 | 1–2 |
| Claude Direct | 10 | 7 |
| ml-intern | all (68 for P001, 18 for P004) | 9 total |

ml-intern's Python scripts fetch, haversine-filter, and format all trials into a single text file. The `read` tool loads the entire file into the LLM's context. The LLM then generates the full assessment in one continuous response — for P001, that is 68 trials assessed simultaneously in one generation.

**Root cause of the verdict shift:** the number of trials in one context window determines how the LLM calibrates verdicts. With one trial per call, each assessment is made against an abstract implicit standard — no anchoring. With 10 trials, clearly ineligible neighbours shift the threshold upward for borderline trials. With all trials, the LLM sees the full landscape simultaneously and may reason differently about design intent.

**The P004 finding has a more precise — and more important — explanation.** After checking the actual output files and patient profile, there are three layers:

**We ran the controlled test — and the hypothesis was wrong.**

Three variants of P004 × NCT04511013, varying only the notes field:

| Variant | Notes | Verdict | Confidence |
|---|---|---|---|
| A | Researcher framing at end (current) | INELIGIBLE | 0.92 |
| B | Notes stripped entirely | INELIGIBLE | 0.92 |
| C | Researcher framing moved to top | INELIGIBLE | 0.90 |

Stripping the notes made no difference. The LLM reaches INELIGIBLE from the clinical data alone.

Variant B's explanation: *"the context of 'metastatic melanoma' as the current diagnosis and listing these as prior treatments strongly suggests they were given for metastatic disease."* The LLM is reasoning from the clinical facts, not following the researcher framing. Ipi+nivo combination is standard first-line metastatic melanoma therapy; the LLM correctly infers the metastatic setting from the diagnosis.

**What this reveals about the "absence of information" rule:** it has an implicit activation threshold. The LLM applies UNCERTAIN when data is genuinely absent with no basis for inference. When the clinical context strongly implies the answer — as it does here — the LLM treats it as inferred information, not absent information, and the UNCERTAIN rule does not fire. This is the correct behaviour. The alternative would make the system clinically useless.

**The P004 notes test told half the story.** The notes were irrelevant for P004 because the clinical signals were strong enough to anchor the verdict regardless of framing. The hypothesis required a genuinely borderline case to test properly.

**We then ran the same test on all 6 UNCERTAIN trials from the P001 LangGraph rerun — and got both expected and unexpected results.**

P001 notes: *"Baseline case. Should match several HER2+ trials in NYC area."* — expectation-setting framing. Three variants (A: notes at end, B: notes stripped, C: notes at top) run on all 6 UNCERTAIN trials from the rerun (vs 3 in the original 50-trial run):

| NCT ID | B (clean baseline) | A (notes end) | C (notes top) |
|---|---|---|---|
| NCT07211178 conf=0.65 | UNCERTAIN | **ELIGIBLE 0.82** | **ELIGIBLE 0.82** |
| NCT07192432 conf=0.55 | UNCERTAIN | UNCERTAIN | UNCERTAIN |
| NCT02945579 conf=0.45 | UNCERTAIN | UNCERTAIN | UNCERTAIN |
| NCT06253871 conf=0.45 | UNCERTAIN | UNCERTAIN | UNCERTAIN |
| NCT06220214 conf=0.45 | UNCERTAIN | **INELIGIBLE 0.80** | **INELIGIBLE 0.85** |
| NCT05232916 conf=0.30 | UNCERTAIN | UNCERTAIN | UNCERTAIN |

Two verdict changes — in opposite directions:

- **NCT07211178 (UNCERTAIN → ELIGIBLE):** Notes shifted the LLM from "need to confirm NED status → UNCERTAIN" to "no exclusion triggered → ELIGIBLE." Same missing data; the "should match" framing activated a permissive threshold.
- **NCT06220214 (UNCERTAIN → INELIGIBLE):** A neoadjuvant trial; the patient has already had surgery. Without notes, the LLM hedges. With notes, it commits — the framing apparently prompted more decisive assessment, and the clear clinical mismatch became a clean INELIGIBLE.

Position made no difference: A and C produced identical verdicts in all cases.

**The unified finding:** notes contamination is not inflationary — it is confidence-amplifying. Notes reduce appropriate uncertainty on borderline cases in whichever direction the clinical evidence leans. When there is no strong exclusion, notes pull toward ELIGIBLE. When there is a clear exclusion the LLM was hedging on, notes pull toward INELIGIBLE. The net effect is fewer UNCERTAIN verdicts — fewer appropriate flags for human review — without any trace in the output. This risk is highest in EHR data with embedded clinician framing, where the notes field carries an expert prior before the LLM reads a single eligibility criterion.

**What made this easy to miss:** both LangGraph and Claude Direct have a variable called `BATCH_SIZE`, which looked like the same choice at different scales. It isn't. In Claude Direct, `BATCH_SIZE=10` means 10 trials bundled into one LLM call. In LangGraph, `BATCH_SIZE=12` means 12 LLM calls running in parallel — each still assessing one trial. Same variable name, completely different semantics.

**This is not a framework property.** Any framework can implement any of the three patterns. The framework is a wrapper around the trials-per-call decision; it doesn't make the decision. Teams need to make it explicitly, because it determines the character of the system: conservative and auditable (per-trial), inclusive (batch-N), or maximally comparative but not reproducible (batch-all).

### ml-intern was not built on smolagents

Every secondary source before the week — blog posts, articles, the HuggingFace ecosystem coverage — described ml-intern as "built on smolagents." The planning documents carried this forward.

Reading the actual source code took 20 minutes. `agent/core/agent_loop.py` uses `litellm.acompletion` with standard JSON tool-calling. `agent/core/tools.py` uses fastmcp for MCP tools. There is no smolagents dependency anywhere in the codebase.

ml-intern is architecturally closer to "Claude Direct with richer tooling" than to smolagents' code-generation paradigm. The smolagents logo in the README is HuggingFace branding, not the underlying framework.

This meant the "smolagents → ml-intern as application layer" narrative in the planning was wrong, and the comparison started from a false premise. Secondary source research about rapidly-evolving open-source projects is unreliable. The correct method is to read the source code: `pyproject.toml` takes 30 seconds and is authoritative.

### The P004 divergence: both ml-intern and the four frameworks were wrong, by different mechanisms

ml-intern ran all 5 patients with the same model (claude-sonnet-4-6), same 100mi radius, API context provided in the prompt. Results: 9 ELIGIBLE across 186 trials assessed vs 1–7 for the four frameworks across 193 assessed.

The most striking divergence was P004 — metastatic melanoma, brain mets, ECOG 2, prior ipilimumab and nivolumab. The four frameworks found 0–1 ELIGIBLE. ml-intern found 5, with NCT04511013 ranked as "top priority referral." Reading ml-intern's actual criterion table for that trial reveals the mechanism:

> *"Prior ipi + nivo (both arms permitted) — trial explicitly includes prior-treated patients in one arm"*

This claim is factually wrong. NCT04511013 is a two-arm Phase 2 trial: Arm A (Encorafenib + Binimetinib + Nivolumab) vs. Arm B (Ipilimumab + Nivolumab as comparator). Both arms enroll **treatment-naive** patients for metastatic disease. The exclusion criterion states unambiguously: *"Participants must not have received prior systemic therapy for metastatic disease."* There is no arm permitting prior ICI history.

ml-intern read "vs. Ipilimumab + Nivolumab" in the trial title and conflated what the trial *administers* in one arm with what prior treatment history the trial *accepts*. It hallucinated a permissive design feature that does not exist.

The four frameworks' INELIGIBLE verdict is the more defensible one clinically: ipi+nivo combination is the standard first-line treatment for metastatic melanoma, so inferring that the patient's prior treatments were given for metastatic disease is reasonable. But the profile does not state the treatment setting explicitly — and adjuvant ipi+nivo is also FDA-approved for resected Stage III/IV melanoma. The prior treatment setting is genuinely absent data.

The LangGraph run acknowledged this: "Whether ipilimumab and nivolumab were given in the neoadjuvant/adjuvant setting vs. for metastatic disease" appears explicitly in uncertain_items. Yet the verdict is INELIGIBLE, not UNCERTAIN. The prompt rule says: *"Absence of information is NOT evidence of ineligibility."* The four frameworks listed the ambiguity, then ignored it.

**The correct verdict for NCT04511013 × P004 is UNCERTAIN.** Neither the frameworks nor ml-intern got there. The four frameworks converted a confident clinical inference into INELIGIBLE. ml-intern hallucinated its way to ELIGIBLE. Both errors are opaque in the output — the reasoning reads as authoritative either way.

The deeper question this raises: when the LLM's clinical knowledge provides a plausible inference, does the explicit UNCERTAIN rule still apply? The controlled test showed the rule fires for genuinely absent data but not when context is strong enough to anchor confidence. That threshold is not configurable, not visible, and not consistent across cases.

### OpenHands headless mode was completely blocked

The plan assumed all 5 patients could be run through OpenHands headlessly. What happened: OpenHands v0.40 hardcodes both `temperature` and `top_p` as non-None defaults in its `LLMConfig`. Every API call sends both. All current Anthropic models (Claude 4.x) reject requests where both are set simultaneously. The only models that accept both are Claude 3.x variants — which Anthropic has since deprecated (`not_found_error`).

Three workarounds failed: `LITELLM_DROP_PARAMS` env var (only drops unrecognized params, not conflicting valid params), null in config.toml (TOML has no null type), model substitution (all available models are Claude 4.x or deprecated 3.x). The GUI run succeeded because the browser UI leaves `top_p` unset by default — a different code path from headless `main.py`.

The root mistake: designing an experiment around a tool without first verifying that this version supports the current API. A 30-minute check of the OpenHands changelog and Anthropic API migration notes would have caught this before it became a blocked experiment. P001 via the GUI remains the only complete OpenHands observation.

### The prior question (Elicit) gave the right answer first

Elicit returned 5 ranked trials for P001 in 4 minutes at zero cost, with reasoning. The top result (NCT05232916, GLSI-100 vaccine at Columbia, 0.1 miles from NYC) was independently confirmed as the top match by LangGraph, smolagents, and ml-intern. Four approaches built over four days converged on the same trial that a specialist tool surfaced in minutes.

This isn't a failure of the week — it was the point of running Elicit first. The prior question is the right first question for any client considering a custom build.

---

## Final Results

The clean ranking I expected didn't emerge. What emerged instead was a more useful answer.

**Framework choice controls operational concerns, not reasoning quality.** All four frameworks produced equivalent clinical output. The choice between them should be driven by: does your team need to debug this in production? (LangGraph) Does schema stability matter at scale? (PydanticAI) Is the task genuinely open-ended? (smolagents) Is stability and cost the priority? (Claude Direct)

**The prompt is the first decision.** One sentence changed the clinical meaning of every output. No framework helps with a bad prompt; every framework works with a good one.

**Patient notes fields are a contamination risk, not free signal.** Researcher framing in the notes changes verdicts on genuinely borderline cases — bidirectionally. On NCT07211178 for P001, framing pushed UNCERTAIN → ELIGIBLE (permissive threshold). On NCT06220214 for P001, framing pushed UNCERTAIN → INELIGIBLE (more decisive on a clear mismatch). Four of six borderline trials were unaffected. The effect is not uniform — it depends on where the LLM's confidence already sits. The net result across all affected cases is fewer UNCERTAIN verdicts, meaning fewer appropriate flags for human review. Any patient profile field that carries a prior expectation is a contamination source.

**Assessment method is an architectural decision that most teams don't make explicitly.** Batch-10 vs per-trial is not a framework feature — it's a choice about how many trials share a context window. That choice has systematic effects on verdict distribution that the team needs to understand and own.

**The ml-intern vs. four-framework P004 divergence is the most instructive case in this study.** The four frameworks returned INELIGIBLE based on a defensible clinical inference. ml-intern returned ELIGIBLE based on a hallucinated trial design feature. Neither is correct per the prompt rule: the prior treatment setting is absent data, absent data means UNCERTAIN, not INELIGIBLE. What this reveals is that the LLM's confidence calibration — when to infer vs. when to surface uncertainty — operates below the level of explicit prompt rules and is not transparent in the output. This matters more in production than any framework choice.

**Infrastructure compatibility is a prerequisite, not an afterthought.** OpenHands v0.40 + Claude 4.x is blocked. Secondary sources about open-source tools are unreliable. Read the source code and check version compatibility before designing experiments around a tool.

**The right starting point is still Elicit.** Build vs buy should be answered before framework vs framework.

---

*Written June 5, 2026. Mindfuel independent learning week.*
