# Claude Direct Findings — Day 4, June 3 2026

Framework: None (raw Anthropic SDK) | School: Zero-framework baseline | Run date: 2026-06-03

Model: claude-sonnet-4-6 (same across all frameworks)

---

## Final Architecture

```
fetch_trials() → hard_filter_trials() → asyncio.gather(assess_batch ×N) → MatchingResult
```

No framework. No graph. No schema validation. No code generation.

One `AsyncAnthropic` client, one system prompt, batch calls of 10 trials per LLM call.

**Distinctive approach:** Every other framework assesses trials one-at-a-time (1 LLM call per trial). Claude Direct sends BATCH_SIZE=10 trials in a single call and receives 10 verdicts in one JSON array response. The model sees trials in comparative context, not isolation.

| Component | Implementation |
|-----------|---------------|
| Parallelism | `asyncio.gather` — all batches run concurrently |
| Batch size | 10 trials per call (5 calls for 50 trials) |
| Output validation | Manual: `json.loads` + verdict coercion |
| Parse error handling | Try/except → mark all in batch UNCERTAIN |
| Total code | ~100 lines |

---

## Run Summary

| Profile | Fetched | After filter | ELIGIBLE | UNCERTAIN | INELIGIBLE | LLM calls | Wall time |
|---------|---------|--------------|----------|-----------|------------|-----------|-----------|
| P001 — HER2+ stage II, NYC | 50 | 50 | 0 | 9 | 41 | 5 | 56.5s |
| P002 — TNBC BRCA1, LA | 50 | 50 | 1 | 28 | 21 | 5 | 71.8s |
| P003 — HR+ HER2−, Chicago | 38 | 37 | 0 | 21 | 16 | 4 | 75.1s |
| P004 — Melanoma brain mets, Seattle | 25 | 25 | 1 | 6 | 18 | 3 | 72.0s |
| P005 — HER2+ metastatic, Boston | 44 | 44 | 3 | 6 | 35 | 5 | 53.8s |
| **Total** | **206** | **206** | **5** | **70** | **131** | **22** | **329s** |

---

## Cost — Complete Comparison

| Metric | LangGraph | PydanticAI | smolagents | Claude Direct |
|--------|-----------|------------|------------|---------------|
| LLM calls | 206 | 206 | 206 | **22** |
| Total tokens | 470,099 | 674,086 | 469,984 | **362,664** |
| Cost at $3/M | $1.41 | $2.02 | $1.41 | **$1.09** |
| Wall time | 665s | 415s | 944s | **329s** |
| Parse errors | 0 | 0 | 0 | 0 |

Claude Direct is the cheapest ($1.09 vs $1.41 for LangGraph) and fastest (329s vs 415s for PydanticAI).

**Why fewer tokens despite same content?** Each individual-call framework sends the patient profile header with every trial (50 repetitions of the same ~200-token header). Claude Direct sends it once per batch of 10, amortizing the header cost across 10 assessments. 362,664 tokens ÷ 22 calls = **16,485 tokens/call** vs 470,099 ÷ 206 = **2,282 tokens/call** (LangGraph). The per-call overhead of sending patient context is eliminated.

**Why fastest despite batching?** 22 parallel calls vs 206 parallel calls — less network overhead, fewer API round trips. Each batch call takes ~12–18s but there are only 3–5 per patient. asyncio.gather on 5 calls completes faster than asyncio.gather on 50 individually smaller calls.

---

## Verdict Distribution — Full Comparison

| Profile | LangGraph | PydanticAI | smolagents | Claude Direct |
|---------|-----------|------------|------------|---------------|
| P001 (E/U/I) | 0/3/47 | 0/2/48 | 0/3/47 | **0/9/41** |
| P002 (E/U/I) | 0/27/23 | 0/25/25 | 0/24/26 | **1/28/21** |
| P003 (E/U/I) | 0/12/25 | 0/5/32 | 0/11/26 | **0/21/16** |
| P004 (E/U/I) | 1/8/16 | 0/8/17 | 1/8/16 | **1/6/18** |
| P005 (E/U/I) | 0/7/37 | 0/5/39 | 0/5/39 | **3/6/35** |
| **Total E** | **1** | **0** | **1** | **5** |
| **Total U** | **57** | **45** | **51** | **70** |

Claude Direct is the most permissive framework: 5 ELIGIBLE vs 0–1 for others. It also has the most UNCERTAIN (70 vs 51 for smolagents), making it simultaneously more willing to commit to ELIGIBLE AND more willing to flag data gaps. This is not a contradiction — it's the batch context effect.

---

## The Batch Context Effect

**Claude Direct's key difference**: the LLM assesses 10 trials simultaneously, not in isolation.

This changes the reasoning in observable ways:

**1. More ELIGIBLE verdicts (5 vs 0–1 in other frameworks).**

P005 example — three trials that LangGraph marked UNCERTAIN, Claude Direct marked ELIGIBLE:
- NCT05150691 (HER2-targeted ADC trial): Patient meets HER2+ and ECOG requirements. Claude Direct: "No clear exclusion criteria triggered by available information → ELIGIBLE." LangGraph: "Missing organ function labs and CNS status documentation → UNCERTAIN."
- The prompt rule ("absence of information is NOT evidence of ineligibility") is being applied more broadly in batch context.

Hypothesis: When assessing 10 trials together, the LLM uses other trials in the batch as anchors. If several are clearly INELIGIBLE (HER2− requirement when patient is HER2+), the LLM may lower its threshold for ELIGIBLE on trials where exclusion isn't explicit — a comparative normalization effect.

**2. More UNCERTAIN in P001 and P003.**

P001 had 9 UNCERTAIN (vs 3 in LangGraph). P003 had 21 UNCERTAIN (vs 12 in LangGraph). The same batch effect that produces more ELIGIBLE in easy cases may produce more UNCERTAIN in genuinely ambiguous cases — the model has more contextual information to reason about what's missing.

**3. P002 ELIGIBLE (NCT06422455 — genetic testing access study).**
This is likely correct. A broad observational registry for TNBC patients seeking genetic testing. The criterion is "TNBC diagnosis + age ≥ 18." Patient qualifies on both counts. All other frameworks missed this, possibly because individual-call context is narrower — the model sees only one trial at a time and may apply stricter screening criteria.

---

## Spot-Checks

| Test case | Profile | Criterion | Claude Direct | LangGraph | Assessment |
|-----------|---------|-----------|---------------|-----------|------------|
| HER2+ exclusion | P001 | HER2− trials excluded | INELIGIBLE | INELIGIBLE | ✅ Both correct |
| Genetic testing registry | P002 (NCT06422455) | TNBC ≥18yo required | **ELIGIBLE** | UNCERTAIN | CD likely correct — very low bar |
| ADC trial eligibility | P005 (NCT05150691) | HER2+, prior anti-HER2, ECOG 0-1 | **ELIGIBLE** (0.78) | UNCERTAIN | Ambiguous — missing organ function labs |
| T-DXd history gap | P005 (NCT06157892) | T-DXd or TI-ADC required for progression | **ELIGIBLE** (0.72) | UNCERTAIN | CD more lenient — absence rule applied broadly |
| Brain mets exclusion | P004 | Trials excluding active CNS | INELIGIBLE | INELIGIBLE | ✅ Both correct |

---

## What Claude Direct Did Well

**1. Cheapest and fastest by large margins.**
$1.09 vs $1.41 (LangGraph), $2.02 (PydanticAI). 329s vs 415s (PydanticAI), 665s (LangGraph). The batch approach eliminates repeated patient context overhead and reduces network round trips. For a production system doing thousands of matches per day, this cost difference is meaningful.

**2. Zero framework overhead.**
100 lines of code. No dependency beyond `anthropic`. No graph compilation, no schema enforcement, no code generation sandbox. For teams that understand prompting, this is the path of least friction.

**3. Catches genuinely inclusive trials others missed.**
NCT06422455 for P002 — a broadly inclusive observational study — was correctly identified as ELIGIBLE while three other frameworks missed it. Batch context may help the model recognize trials with low enrollment bars vs trials with strict scientific criteria.

**4. 0 parse errors.**
The batch response format (JSON array with 10 objects) parsed correctly in all 22 calls. The model reliably maintained the array structure across varying batch sizes (3–10 trials).

---

## Failure Modes

**1. Batch context inflates ELIGIBLE count.**
5 ELIGIBLE vs 1 for LangGraph and smolagents. At least 2–3 of the 5 are genuinely ambiguous (missing organ function labs, T-DXd history). Comparative batch context is lowering the bar for ELIGIBLE in a way that individual-call frameworks don't exhibit. Whether this is "better" depends on the use case: a screener that over-includes may be preferable to one that under-includes, but the effect is systematic and uncontrolled.

**2. No schema enforcement — silent wrong types are possible.**
The `verdict` coercion (`if verdict not in ("ELIGIBLE", "INELIGIBLE", "UNCERTAIN"): verdict = "INELIGIBLE"`) silently degrades bad LLM output. PydanticAI would retry; LangGraph would log a parse error. In this run, all 22 calls returned correctly structured arrays. In production at scale, silent coercions would accumulate invisibly.

**3. Batch failure is all-or-nothing.**
If one batch of 10 fails to parse (malformed JSON), all 10 trials are marked UNCERTAIN. In LangGraph/PydanticAI, a single trial failure affects only that trial. Claude Direct's larger blast radius per error is a reliability consideration at scale.

**4. No intrinsic parallel speedup from parallelism implementation.**
`asyncio.gather` on 5 calls is simple and effective. But for a patient with 50 trials, 5 parallel batch calls of 10 each is structurally similar to 50 parallel individual calls. The speedup comes from fewer total calls (22 vs 206), not from the async pattern itself.

---

## The Core Question: What Does a Framework Buy You?

**Cost and speed:** Claude Direct wins on both. No framework overhead means lower cost, fewer network calls, faster wall time.

**Reliability guarantees:** PydanticAI > LangGraph > smolagents ≈ Claude Direct. Schema enforcement and auto-retry are real protections. Claude Direct has no safety net beyond try/except.

**Reasoning quality (by proxy — verdict consistency):** LangGraph, smolagents, and Claude Direct cluster together. PydanticAI is the outlier (tool_use produces more conservative verdicts). Claude Direct is the outlier in the other direction (batch context produces more ELIGIBLE).

**Observability:** LangGraph > smolagents > Claude Direct > PydanticAI. LangGraph's named nodes make it easy to trace which step produced a bug. Claude Direct is a black box: one call in, one response out.

**For a prescribed, stable pipeline:** Claude Direct or LangGraph. The framework adds overhead that the pipeline doesn't need.

**For a pipeline that will evolve, be debugged, or be maintained by a team:** LangGraph's named nodes and PydanticAI's schema enforcement pay for themselves in maintainability.
