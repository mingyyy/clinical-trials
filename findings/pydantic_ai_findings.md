# PydanticAI Findings — Day 3, June 3 2026

Framework: PydanticAI v1.104 | School: Type-safety | Run date: 2026-06-03

Model: claude-sonnet-4-6 (same as LangGraph — model is the constant, framework is the variable)

---

## Final Architecture

```
fetch_trials() → hard_filter_trials() → asyncio.gather(assess_trial ×N in batches of 12) → MatchingResult
```

No graph. No state machine. Three async functions and a Pydantic schema.

**Key components:**
- `EligibilityAssessment(BaseModel)` — the output contract. LLM must return this exactly.
- `Agent(model, output_type=EligibilityAssessment, system_prompt=..., retries=2)` — framework handles structured output and auto-retry.
- `asyncio.gather(...)` over batches of 12 — parallel fan-out in ~10 lines, no graph wiring.
- `result.usage` (property) → `usage.input_tokens + usage.output_tokens` — token tracking.

**What PydanticAI handles vs LangGraph:**

| Concern | LangGraph | PydanticAI |
|---------|-----------|------------|
| Parallel execution | `Send` API + Annotated reducers + routing function | `asyncio.gather` over coroutines |
| Output validation | Manual `json.loads` + `re.sub` + `if verdict not in (...)` | `output_type=` schema enforcement, auto-retry |
| State tracking | TypedDict with named nodes | Plain Python variables |
| Parse error handling | `except Exception → [parse error]` fallback | Framework retries, then raises |

---

## Run Summary

| Profile | Fetched | After filter | ELIGIBLE | UNCERTAIN | INELIGIBLE | LLM calls | Wall time |
|---------|---------|--------------|----------|-----------|------------|-----------|-----------|
| P001 — HER2+ stage II, NYC | 50 | 50 | 0 | 2 | 48 | 50 | 87.6s |
| P002 — TNBC BRCA1, LA | 50 | 50 | 0 | 25 | 25 | 50 | 103.8s |
| P003 — HR+ HER2−, Chicago | 38 | 37 | 0 | 5 | 32 | 37 | 84.6s |
| P004 — Melanoma brain mets, Seattle | 25 | 25 | 0 | 8 | 17 | 25 | 53.7s |
| P005 — HER2+ metastatic, Boston | 44 | 44 | 0 | 5 | 39 | 44 | 85.5s |
| **Total** | **206** | **206** | **0** | **45** | **161** | **206** | **~415s** |

---

## Cost

| Metric | LangGraph | PydanticAI | Delta |
|--------|-----------|------------|-------|
| Total LLM calls | 206 | 206 | — |
| Total tokens | 470,099 | 674,086 | **+43%** |
| Cost at $3/M | $1.41 | **$2.02** | +$0.61 |
| Wall time | 664.6s | **415.2s** | **−38% (1.6× faster)** |
| Parse errors | 0 | 0 | — |
| Model | claude-sonnet-4-6 | claude-sonnet-4-6 | — |

**Token overhead explanation:** PydanticAI uses Anthropic's tool_use (function calling) to enforce `output_type=`. Every request includes the full `EligibilityAssessment` JSON schema as a tool definition. This adds ~990 tokens per call (674,086 − 470,099 = 203,987 extra tokens ÷ 206 calls ≈ 990 tokens/call overhead vs LangGraph). This is the cost of schema enforcement — the LLM gets told exactly what fields to return, in exchange for more tokens.

**Wall time explanation:** PydanticAI 415s vs LangGraph 665s — 1.6× faster at identical BATCH_SIZE=12. Same number of parallel calls (206), same model, same batch structure. The difference is architectural overhead: LangGraph's graph compilation, state machine evaluation, and Annotated reducer merging add latency that doesn't appear in PydanticAI's plain `asyncio.gather`. The parallelism implementation in PydanticAI is ~10 lines vs ~40 lines in LangGraph; the simpler approach is also faster.

---

## Verdict Distribution Comparison

| Profile | LangGraph (E/U/I) | PydanticAI (E/U/I) | Shift |
|---------|------------------|-------------------|-------|
| P001 | 0/3/47 | 0/2/48 | −1 UNCERTAIN |
| P002 | 0/27/23 | 0/25/25 | −2 UNCERTAIN |
| P003 | 0/12/25 | 0/5/32 | **−7 UNCERTAIN** |
| P004 | **1/8/16** | **0/8/17** | −1 ELIGIBLE |
| P005 | 0/7/37 | 0/5/39 | −2 UNCERTAIN |
| **Total** | **1/57/148** | **0/45/161** | −1E, −12U, +13I |

PydanticAI is consistently more conservative: fewer UNCERTAIN, more INELIGIBLE, no ELIGIBLE. Same prompt, same model — the difference is the interaction mode (tool_use vs raw JSON).

Hypothesis: when the LLM is given a tool schema, it fills in fields more definitively. In raw JSON mode (LangGraph), the LLM is generating free text it must parse, which may produce more hedged outputs. In tool_use mode (PydanticAI), the LLM is "filling in a form" and produces crisper binary decisions.

This is an empirical observation, not a confirmed mechanism. Requires further testing to validate.

---

## P004 Spot-Check: NCT03452774

LangGraph marked this ELIGIBLE. PydanticAI marked it UNCERTAIN.

**Trial:** SYNERGY-AI — Artificial Intelligence Based Precision Oncology Clinical Trial Matching and Registry

**LangGraph reasoning:** Patient has solid malignancy (metastatic melanoma) with BRAF V600E, ECOG PS 2 does not exceed exclusion threshold. Marked ELIGIBLE.

**PydanticAI reasoning:** Same biomarker match, but flagged:
- Organ function (LFTs, renal function, CBC) not provided — "abnormal organ function" is an exclusion criterion
- Provider/patient decision to pursue clinical trial pre-screening not documented — required inclusion criterion
- Hospice enrollment not confirmed (unlikely but not stated)

**Which is correct?** PydanticAI's UNCERTAIN is more defensible. The provider consent criterion is a real requirement for a registry study, and the profile doesn't document it. LangGraph effectively assumed those criteria were met; PydanticAI surfaced them as unknowns.

This is the core finding: PydanticAI's tool_use mode makes the LLM slightly more thorough in surfacing unverifiable criteria. The same criteria were present in both prompts; the LLM acknowledged them more explicitly in PydanticAI's structured output.

---

## What PydanticAI Did Well

**1. Schema enforcement eliminated the parse error class entirely.**
LangGraph had 15–35% parse errors in early sequential runs (before parallel batching). The error class was eliminated by switching to parallel + prompt fixes, not by the framework. PydanticAI eliminates the same class architecturally: `output_type=EligibilityAssessment` means invalid outputs are retried, not silently passed through as `[parse error]`. The `retries=2` parameter was never triggered in this run — 0 retries needed. But the safety net exists.

**2. Parallelism is trivial to implement.**
`asyncio.gather(*[assess_trial(patient, t) for t in batch])` — that is the entire parallel fan-out implementation. LangGraph required: a routing function, `Annotated` reducers on three state fields, `conditional_edges`, and a `Send` call per batch. Both achieve the same result. The PydanticAI approach requires no framework knowledge beyond standard Python async.

**3. Type constraints enforce correctness at the contract level.**
`verdict: Literal["ELIGIBLE", "INELIGIBLE", "UNCERTAIN"]` in the schema means the LLM cannot return "UNKNOWN" or "MAYBE" without triggering a retry. In LangGraph this was enforced manually:
```python
if verdict not in ("ELIGIBLE", "INELIGIBLE", "UNCERTAIN"):
    verdict = "INELIGIBLE"
```
The LangGraph code silently coerces invalid values to INELIGIBLE. The PydanticAI approach would retry and surface the error. For production code, that is the right behavior.

---

## Failure Modes

**1. Prompt builder bug passed silently through both frameworks.**
The initial agent.py used `trial.get("protocolSection", {})` — the raw ClinicalTrials.gov API structure — when `fetch_trials()` returns a pre-flattened dict. Every trial had no NCT ID, no title, no criteria. Every verdict was UNCERTAIN. Neither PydanticAI's schema validation nor LangGraph's graph structure would catch this: both frameworks trust the content of the prompt. This is an application-layer bug, not a framework bug.

The difference: the all-UNCERTAIN output was actually correct (no criteria provided → can't determine eligibility). The schema validation caught nothing wrong because the output was structurally valid. A data pipeline bug upstream of the LLM is invisible to both frameworks.

**2. Token overhead from tool_use is a real cost.**
43% more tokens at identical functionality. For 206 trials, this is $0.61. At scale (10,000 patients × 50 trials each = 500,000 LLM calls), the cost is ~$3,400 (LangGraph) vs ~$4,900 (PydanticAI) — a meaningful cost difference for the same clinical output. Teams choosing PydanticAI for type safety should be aware of this overhead.

**3. The schema doesn't enforce clinical correctness.**
`confidence: float = Field(ge=0.0, le=1.0)` validates the range. It doesn't validate that the confidence value is calibrated or meaningful. A model that returns `confidence=0.9` on an incorrect verdict passes validation. Type safety is a structural guarantee, not a clinical accuracy guarantee.

---

## Criteria Accuracy: Spot-Checks

| Test case | Profile | Criterion tested | PydanticAI verdict | LangGraph verdict | Correct? |
|-----------|---------|-----------------|-------------------|--------------------|---------|
| HER2+ baseline | P001 | HER2− trials excluded | INELIGIBLE (HER2 cited) | INELIGIBLE | ✅ Both |
| Brain mets exclusion | P004 | Trials excluding active CNS | INELIGIBLE | INELIGIBLE | ✅ Both |
| Registry admin criteria | P004 (NCT03452774) | Provider consent required | UNCERTAIN | ELIGIBLE | ✅ PydanticAI more accurate |
| BRCA1 opens trials | P002 | PARP inhibitor trials | Not spot-checked | Not spot-checked | See Day 4 |

---

## Key Architectural Observation

PydanticAI's architectural claim is: "you shouldn't need to write validation code; the schema is the contract." This held. Zero parse errors, zero invalid verdict values, zero schema violations. The `EligibilityAssessment` schema correctly constrained the output without manual validation code.

But the 43% token overhead is the price. And the schema cannot catch upstream data bugs or clinical reasoning errors. PydanticAI enforces structural validity; it says nothing about semantic accuracy.

**The comparison so far:**

| Dimension | LangGraph | PydanticAI | Winner |
|-----------|-----------|------------|--------|
| Code to add parallelism | ~40 lines | ~10 lines | PydanticAI |
| Parse error protection | Manual (regex + coerce) | Framework (retry) | PydanticAI |
| Token cost | $1.41 | $2.02 | LangGraph |
| Wall time (BATCH=12) | 665s | 415s | PydanticAI |
| Observability | High (named nodes, state) | Low (function calls) | LangGraph |
| Code complexity | Higher | Lower | PydanticAI |
| UNCERTAIN conservatism | 57 total | 45 total | Depends on use case |

**The question for Day 4:** smolagents writes its own tool-calling loop — does a code-generation agent handle the three-state distinction differently when it constructs the prompt itself? And does Claude Direct — no framework at all — match or beat either when the prompt is carefully crafted?
