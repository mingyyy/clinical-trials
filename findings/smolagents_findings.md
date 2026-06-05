# smolagents Findings — Day 4, June 3 2026

Framework: smolagents v1.26.0 | School: Code generation | Run date: 2026-06-03

Model: claude-sonnet-4-6 (same across all frameworks)

---

## Final Architecture

```
CodeAgent generates Python code → executes via LocalPythonExecutor → calls tools → repeat until done
```

Tools available to the agent:
- `search_clinical_trials` — wraps `fetch_trials()` from api_client.py
- `prefilter_trials` — wraps `hard_filter_trials()` from api_client.py
- `assess_trials_batch` — Anthropic SDK + ThreadPoolExecutor(max_workers=12), same three-state prompt
- `save_matching_result` — validates against MatchingResult schema, writes JSON

The agent is not told HOW to write the code. It receives a task description and generates Python code to fulfill it, self-correcting when execution fails.

---

## Run Summary

| Profile | Fetched | After filter | ELIGIBLE | UNCERTAIN | INELIGIBLE | LLM calls | Wall time |
|---------|---------|--------------|----------|-----------|------------|-----------|-----------|
| P001 — HER2+ stage II, NYC | 50 | 50 | 0 | 3 | 47 | 50 | 139.4s |
| P002 — TNBC BRCA1, LA | 50 | 50 | 0 | 24 | 26 | 50 | 281.0s |
| P003 — HR+ HER2−, Chicago | 38 | 37 | 0 | 11 | 26 | 37 | 163.4s |
| P004 — Melanoma brain mets, Seattle | 25 | 25 | 1 | 8 | 16 | 25 | 105.4s |
| P005 — HER2+ metastatic, Boston | 44 | 44 | 0 | 5 | 39 | 44 | 255.2s |
| **Total** | **206** | **206** | **1** | **51** | **154** | **206** | **944.4s** |

---

## Cost

| Metric | LangGraph | PydanticAI | smolagents | Notes |
|--------|-----------|------------|------------|-------|
| Total tokens | 470,099 | 674,086 | **469,984** | smolagents ≈ LangGraph |
| Cost at $3/M | $1.41 | $2.02 | **$1.41** | PydanticAI 43% more expensive |
| Wall time | 665s | 415s | **944s** | smolagents slowest |
| Parse errors | 0 | 0 | 0 | — |

**Token cost insight:** smolagents uses the Anthropic SDK directly in `assess_trials_batch` — raw API calls, no tool_use overhead. PydanticAI uses tool_use (function calling) which adds the EligibilityAssessment schema definition to every request (~990 extra tokens/call). smolagents and LangGraph produce identical per-call token counts because both use raw text JSON, not structured tool calls.

**Wall time insight:** 944s vs LangGraph's 665s — 1.4× slower despite identical parallel assessment. The extra ~280s comes from code generation overhead: each of the ~15-20 steps per profile involves an LLM call to generate code, evaluate results, and plan the next step. These "meta" LLM calls don't appear in the llm_calls count (those only count eligibility assessments via `assess_trials_batch`).

---

## Verdict Distribution Comparison

| Profile | LangGraph | PydanticAI | smolagents |
|---------|-----------|------------|------------|
| P001 (E/U/I) | 0/3/47 | 0/2/48 | **0/3/47** |
| P002 (E/U/I) | 0/27/23 | 0/25/25 | **0/24/26** |
| P003 (E/U/I) | 0/12/25 | 0/5/32 | **0/11/26** |
| P004 (E/U/I) | **1/8/16** | 0/8/17 | **1/8/16** |
| P005 (E/U/I) | 0/7/37 | 0/5/39 | **0/5/39** |

smolagents verdict distribution tracks closely with LangGraph, not PydanticAI. Both found P004's 1 ELIGIBLE trial (NCT03452774); PydanticAI marked it UNCERTAIN. Both use raw Anthropic API without tool_use — this alignment confirms the earlier hypothesis: tool_use mode (PydanticAI) makes the LLM more conservative, independent of the framework wrapper.

---

## Agent Behavior — Step-by-Step Trace

**P001 required 19 code-generation steps to complete. LangGraph requires 4 node executions.**

Key observations from the trace:

**Step 1 (every profile): `import json` fails.**
smolagents' `LocalPythonExecutor` restricts authorized imports to a safe set (`math`, `time`, `re`, `collections`, etc.). `json` is not in the default list. The agent tried to import it, hit an `InterpreterError`, and self-corrected in the next step by removing the import.

*This happened for both P001 and P002.* The agent did not learn this constraint across profiles — each profile gets a fresh agent instance (new `agent.run()` call). In-session learning doesn't persist across runs. This is a meaningful limitation for repetitive tasks.

**The agent's workaround for no `json`:**
```python
# Agent couldn't parse JSON, so it counted string occurrences
total_trials_fetched = trials_json.count('"nct_id"')
```
This is clever and correct. It's also a sign of genuine code-generation adaptability: the agent found a working alternative when its first approach was blocked.

**Step 5 (P001): Execution timeout.**
`assess_trials_batch` took 61s; smolagents default execution timeout is 30s. The executor fired a warning and the agent treated it as a failure:
```
Code execution exceeded the maximum execution time of 30 seconds
[Step 5: Duration 61.62 seconds]
```
The tool completed and returned results (ThreadPoolExecutor finished all calls), but the agent's execution context registered a timeout. The agent retried `assess_trials_batch` in subsequent steps — causing duplicate LLM calls in some profiles. P002's 281s wall time vs P001's 139s (same 50 trials) is likely caused by one or more retries.

**Step 10 (P001): ValidationError on `save_matching_result`.**
The agent constructed the result JSON incorrectly on first attempt — missing a required field or wrong type. `save_matching_result` validates against the `MatchingResult` Pydantic schema and raised `ValidationError`. The agent read the error message, corrected the JSON, and retried. Successful on the second attempt.

This is the correct behavior: the schema acted as a guardrail. The agent caught its own error from the validation feedback and fixed it without human intervention.

---

## What smolagents Did Well

**1. Self-correction from tool feedback.**
Two failures during the run — `import json` blocked and `ValidationError` from schema mismatch — were both caught and fixed autonomously within the same run. No human intervention. The code generation paradigm means errors are visible to the agent as Python tracebacks, which it can read and fix.

**2. Adaptive code structure.**
By P002, the agent combined multiple pipeline steps into fewer code blocks — learning from P001's step count that more concise code is better. This is behavioral adaptation within a session (between profiles, not across sessions).

**3. Correct handling of the three-state verdict.**
The task prompt specified the schema. The agent reproduced it correctly in its generated code and in the final JSON. The `save_matching_result` schema validation caught the one time it got it wrong.

**4. Verdict quality matches LangGraph.**
Same underlying API call mechanism (raw Anthropic, no tool_use) → same LLM behavior → same verdict distribution. Code generation didn't improve or degrade the core reasoning.

---

## Failure Modes

**1. No cross-run memory.**
The `import json` mistake was repeated for P002 despite having just encountered it in P001. Each `agent.run()` is stateless — no memory of previous runs. For repetitive tasks across N patients, this means N repetitions of the same initial errors. In production, you'd initialize the agent once with the constraint documented in the system prompt.

**2. Execution timeout causes redundant tool calls.**
The 30s LocalPythonExecutor timeout is incompatible with `assess_trials_batch` (which takes 55–95s for 25–50 trials in parallel). The agent retried the batch assessment in affected profiles. Fix: pass `additional_authorized_imports=["json"]` AND set a longer execution timeout in CodeAgent. Neither was done here — documenting it as a friction point with the default configuration.

**3. High step overhead.**
19 steps (P001) vs 4 nodes (LangGraph) for the same pipeline. The step overhead adds wall time (~280s across 5 profiles) and tokens (code-generation LLM calls not counted in llm_calls). The agent must reason about what to do next at each step, while LangGraph's graph structure prescribes the path with zero meta-reasoning.

**4. Sandbox limits code expressiveness.**
No `json` import, no `concurrent.futures`. The agent had to work around both. In practice this means the agent writes simpler, more verbose code than a human would. The ThreadPoolExecutor used inside `assess_trials_batch` is hidden inside the tool — the agent can't write parallelism directly.

---

## Code Generation Quality Observation

The code the agent generated was readable and structurally sound. Sample (Step 4, P001):

```python
filtered_trials_json = prefilter_trials(
    trials_json=trials_json,
    age=52,
    sex="FEMALE"
)
print(filtered_trials_json[:1000])
trials_after_hard_filter = filtered_trials_json.count('"nct_id"')
print(f"Trials after hard filter: {trials_after_hard_filter}")
```

The agent added diagnostic prints, used named arguments, and tracked intermediate counts. This is readable code that a junior developer would recognize as correct. The workaround (`count('"nct_id"')`) is hacky but works.

---

## Criteria Accuracy: Spot-Checks

| Test case | Profile | Criterion | smolagents | LangGraph | Correct? |
|-----------|---------|-----------|------------|-----------|---------|
| HER2+ exclusion | P001 | HER2− trials excluded | INELIGIBLE | INELIGIBLE | ✅ Both |
| TNBC biomarker match | P002 | TNBC-specific trials | UNCERTAIN/INELIGIBLE | UNCERTAIN/INELIGIBLE | ✅ Both |
| Registry admin criteria | P004 (NCT03452774) | Provider consent required | **ELIGIBLE** | **ELIGIBLE** | Both agreed; PydanticAI was outlier |
| Brain mets exclusion | P004 | Trials excluding active CNS | INELIGIBLE | INELIGIBLE | ✅ Both |

---

## The Autonomy-Reliability Tradeoff

smolagents is the most autonomous framework tested: the agent decides its own code structure, handles its own errors, and adapts its approach. But autonomy has a cost:

- **More steps** (19 vs 4) — each step is a meta-LLM call not counted in results
- **More wall time** (944s vs 665s) — 42% slower
- **Same cost** ($1.41) — because the core assessment tool is identical
- **Same accuracy** — the LLM is the same; the framework overhead doesn't improve reasoning

The value of code generation autonomy shows up in *different* tasks: open-ended exploration, multi-step data analysis, self-directed debugging. For a prescribed pipeline (fetch → filter → assess → save), the code generation overhead buys nothing over an explicit graph.

**The counter-argument:** if the pipeline had evolved mid-run (e.g., "add a step to check trial enrollment status" or "retry failed trials with a simpler query"), the code generation agent could adapt. LangGraph would require a graph rebuild.

---

## Summary vs Prior Frameworks

| Dimension | LangGraph | PydanticAI | smolagents | Winner |
|-----------|-----------|------------|------------|--------|
| Token cost | $1.41 | $2.02 | $1.41 | LG / SA tied |
| Wall time | 665s | 415s | 944s | PydanticAI |
| Parallelism code | ~40 lines | ~10 lines | in tool (hidden) | PydanticAI |
| Schema enforcement | manual | framework | schema validation | PydanticAI |
| Self-correction | no | auto-retry | yes, from errors | SA for complex |
| Observability | high (named nodes) | low | medium (step trace) | LangGraph |
| Code complexity | highest | lowest | medium | PydanticAI |
| Adaptability | none | none | high | smolagents |
| Prescribed pipeline fit | best | good | overkill | LangGraph |
