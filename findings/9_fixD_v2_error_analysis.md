# Structured Extraction v2 — Error Analysis and Improvement Options

**Date:** June 23, 2026
**Context:** fixD v2 reached 84.1% accuracy (153/182) after three bug fixes. The remaining 29 errors + 12 parse ERRORs suggest the architecture is losing information on cases that should be straightforward. This document maps the error landscape and evaluates improvement paths.

---

## The Accuracy Problem

84% on a task that is largely mechanical — matching patient attributes against explicit eligibility text. The pipeline has three LLM-dependent steps, and errors compound:

| Step | What it does | Where it can fail |
|------|-------------|-------------------|
| Step 1 (LLM) | Extract typed patient record | Wrong field values, null when data is present |
| Step 2 (LLM) | Parse criteria into predicates | Wrong variable, wrong operator, missed criteria, wrong cohort |
| Step 3 (code) | Evaluate predicates | Logic bugs (fixed in v2), OR handling |

Step 3 is now solid. Steps 1 and 2 are both LLM calls, and their errors compound. A perfect Step 3 cannot compensate for bad inputs.

---

## Error Breakdown (29 wrong verdicts + 12 parse ERRORs)

### By type

| Error direction | Count | Meaning |
|----------------|-------|---------|
| UNCERTAIN→INELIGIBLE | 17 | fixD says INELIGIBLE, GT says UNCERTAIN. fixD is too aggressive. |
| INELIGIBLE→UNCERTAIN | 8 | fixD misses a failing criterion. Parser didn't generate the predicate. |
| ELIGIBLE→INELIGIBLE | 2 | fixD calls eligible patients ineligible. Parser misreads criteria. |
| ELIGIBLE→UNCERTAIN | 1 | fixD hedges on a clearly eligible patient. |
| INELIGIBLE→ELIGIBLE | 1 | fixD misses an exclusion entirely. Safety-critical. |
| Parse ERROR | 12 | JSON parse failure on long/complex eligibility text. Lost assessments. |
| **Total** | **41** | |

### By step attribution (traced all 29 errors)

All 29 error cases were re-run with full predicate traces. Each error was attributed by elimination: verify Step 1 (extraction) against source profile, verify Step 3 (evaluation) is deterministic, attribute remainder to Step 2 (parsing).

| Step | Errors | Verification method |
|------|--------|-------------------|
| Step 1 (extraction) | **0** | All 5 patient records verified field-by-field against source profiles. No extraction errors. |
| Step 2 (parsing) | **29** | Every error traces to wrong, missing, or mismatched predicates. |
| Step 3 (evaluation) | **0** | Deterministic code. Re-run produces identical results from same predicates. |

**Step 2 is the sole bottleneck.** Steps 1 and 3 are working correctly.

### Step 2 error sub-types

| Sub-type | Count | What's happening | Fixable by |
|----------|-------|-----------------|------------|
| **Missed criteria** | 10 | Parser didn't generate a predicate for a key criterion. No predicate = no failure = UNCERTAIN when should be INELIGIBLE. | Better prompt, more examples |
| **Primary condition string mismatch** | 7 | Parser generates `eq "breast cancer"` but record has `"HER2-positive breast cancer"`. Or `in_set ["TNBC"]` doesn't match `"Triple-negative breast cancer (TNBC)"`. Exact string matching fails. | Fuzzy matching in evaluator, or normalized extraction |
| **Wrong cohort** | 6 | Multi-cohort trial. Parser generates predicates from wrong cohort (e.g., checks endometrial cohort for breast cancer patient). | Patient-aware parsing (Option B) |
| **Other** | 5 | OR predicate issues, ECOG edge case (`gt 2` vs `gte 2` for ECOG=2), non-deterministic retrace differences. | Mixed — case-by-case |
| **is_advanced_measurable** | 1 | Genuine borderline: NED patient, trial requires advanced disease. | Judgment call — may not be an error |

**The two largest fixable sub-types — string mismatch (7) and wrong cohort (6) — account for 13 of 29 errors (45%).** Both have clear mechanical fixes.

### String mismatch examples

| Patient record value | Parser predicate value | Match? | Should match? |
|---------------------|----------------------|--------|---------------|
| `"HER2-positive breast cancer"` | `eq "breast cancer"` | No | Yes — HER2+ breast cancer is breast cancer |
| `"HER2-positive breast cancer"` | `eq "invasive breast cancer"` | No | Yes — same disease |
| `"Triple-negative breast cancer (TNBC)"` | `in_set ["TNBC"]` | No | Yes — different string, same entity |
| `"Triple-negative breast cancer (TNBC)"` | `in_set ["triple-negative breast cancer"]` | No | Yes — case + parenthetical mismatch |

The evaluator uses exact string matching (`eq`, `in_set`). The extraction uses descriptive strings. The parser uses abbreviated or clinical strings. They don't match even when they refer to the same entity.

### By patient

| Patient | Profile | Errors/Assessed | Accuracy | Primary error pattern |
|---------|---------|----------------|----------|---------------------|
| P001 | HER2+ stage II, NED | 3/71 | 95.8% | String mismatch (2), is_advanced_measurable (1) |
| P002 | TNBC stage III | 15/47 | 68.1% | String mismatch (5), wrong cohort (3), missed criteria (4) |
| P003 | HR+ HER2-, NED | 2/18 | 88.9% | Missed criteria (2) |
| P004 | Melanoma, brain mets | 2/16 | 87.5% | Wrong cohort (1), ECOG edge case (1) |
| P005 | HER2+ metastatic | 7/30 | 76.7% | Wrong cohort (2), missed criteria (4), string mismatch (1) |

P002 and P005 together account for 22 of 29 errors. Both patients face trials with complex multi-cohort structures.

---

## The Core Problem: Three Distinct Failures in Step 2

The trace attributed all 29 errors to Step 2 (criteria parsing). But "Step 2" contains three distinct failure modes with different root causes:

| Failure mode | Count | Root cause | Nature |
|-------------|-------|-----------|--------|
| String mismatch | 7 | Steps 1 and 2 produce different strings for the same entity | **Vocabulary alignment** — extraction says "HER2-positive breast cancer", parser says "breast cancer" |
| Wrong cohort | 6 | Parser doesn't know which cohort to focus on | **Missing context** — patient-agnostic design can't navigate multi-arm trials |
| Missed criteria | 10 | Parser doesn't generate predicates for key criteria | **Coverage** — long criteria text, parser misses important requirements |
| Other | 6 | Mixed (OR handling, ECOG edge case, non-determinism) | Various |

These need different solutions. A single fix won't address all three.

### The deeper design issue

String mismatch and wrong cohort are related: both stem from Steps 1 and 2 operating independently. The extraction produces descriptive strings; the parser produces clinical abbreviations. Neither knows what the other said. This is a **two-LLM agreement problem** — two separate LLM calls need to use compatible vocabulary, and they don't.

This suggests a structural option the original analysis didn't consider: what if we reduce the number of places where two LLM outputs need to agree?

---

## Revised Option Space (post-trace)

### ~~Option F: Improve extraction (Step 1)~~

**Eliminated.** The trace showed zero Step 1 errors. Extraction is working correctly.

### Option 1: String normalization in evaluator

**Addresses:** String mismatch (7 errors)

**Approach:** Replace exact string matching (`eq`, `in_set`) with normalized matching for `disease.primary_condition` and `disease.histology_subtype`. Implement a simple condition hierarchy or contains-matching in the evaluator code.

Example: `"HER2-positive breast cancer" eq "breast cancer"` → match, because the extracted value contains the required value. `"Triple-negative breast cancer (TNBC)" in_set ["TNBC"]` → match, because "TNBC" appears in the extracted value.

**Pros:**
- Pure code change — no LLM modification
- Zero risk of inference leakage
- Directly fixes 7 errors

**Cons:**
- Over-matching risk: `"non-small cell lung cancer"` contains `"lung cancer"` which contains `"cancer"` — need bounded matching
- Only fixes one of three failure modes
- Fragile — relies on string patterns rather than semantic understanding

**Expected improvement:** 7 errors fixed. Accuracy 84.1% → ~88%.

### Option 2: Patient-aware parsing (give tumor type to Step 2)

**Addresses:** Wrong cohort (6 errors), partially string mismatch (some)

**Approach:** Pass the patient's tumor type and key biomarkers to Step 2: "Parse criteria relevant to a patient with TNBC." Parser focuses on the right cohort and uses terminology aligned with the patient description.

**Pros:**
- Directly solves cohort confusion
- May also reduce string mismatch (parser uses patient's terminology)
- Single implementation change

**Cons:**
- Reintroduces patient information into the criteria-parsing step
- Risk: parser might adjust predicate strictness based on patient context (Finding 4 concern)
- Need to test inference leakage on P004 target case

**Expected improvement:** 6-10 errors fixed. Accuracy ~88-91%.

**Risk mitigation:** Only pass `disease.primary_condition` and `biomarkers.her2` — enough for cohort selection, not enough for eligibility inference. Never pass ECOG, prior treatments, or disease stage.

**Testable:** Run on P004 × NCT04511013. If it still returns UNCERTAIN, inference isolation is preserved.

### Option 3: Merged single-step assessment (LLM evaluates criteria against patient record)

**Addresses:** String mismatch (7), wrong cohort (6), partially missed criteria (some)

**Approach:** Eliminate the two-LLM agreement problem entirely. Instead of separate extraction and parsing, give the LLM both the **typed patient record** (from Step 1) and the eligibility criteria in one call. Ask it to evaluate each criterion against the record and return per-criterion results: `CONFIRMED_MET`, `CONFIRMED_FAILED`, or `DATA_MISSING` with the specific field checked.

Code still computes the verdict from the per-criterion results. The LLM never produces a verdict.

```
Current:  Profile → [LLM Step 1] → Record    Criteria → [LLM Step 2] → Predicates → [Code] → Verdict
Merged:   Profile → [LLM Step 1] → Record    Record + Criteria → [LLM Step 2'] → Evaluations → [Code] → Verdict
```

**Pros:**
- Eliminates string mismatch entirely — the LLM maps criteria to patient fields directly
- Handles multi-cohort naturally — LLM sees the record and knows which cohort applies
- Simpler pipeline (one LLM call per trial instead of one-parse + one-evaluate)
- Could also improve coverage (LLM reading criteria holistically, not generating predicates)

**Cons:**
- Reintroduces co-visibility of patient data and criteria — the exact design the original fixD avoided
- The LLM might apply clinical inference when evaluating criteria (the Finding 4 risk)
- Harder to verify — LLM evaluations are not deterministic like code evaluation
- Loses the "predicate as audit trail" property — you can't inspect what structured predicates were generated

**Key question:** Does co-visibility cause inference leakage when the LLM is asked for per-criterion evaluations rather than a verdict? The original Finding 4 showed that asking for a **verdict** caused inference override. But asking "does the patient record say X?" is a factual lookup, not a judgment call. The risk is lower — but not zero.

**Expected improvement:** Could fix 15-20 of 29 errors. Accuracy ~90-93%.

**Testable:** Run on P004 × NCT04511013. If `prior_treatments[*].setting = null` produces DATA_MISSING for the "no prior metastatic therapy" criterion, inference isolation works. If it produces CONFIRMED_FAILED, the design fails on the target case.

### Option 4: Predicate verification pass

**Addresses:** Missed criteria (10 errors)

**Approach:** After Step 2 generates predicates, add a verification LLM call: "Given these eligibility criteria, did the predicates cover: (a) disease type/stage requirements, (b) biomarker requirements, (c) prior treatment requirements, (d) performance status?" Return any missing criteria as additional predicates.

**Pros:**
- Directly addresses the coverage gap
- Still patient-agnostic — verifier checks completeness, not correctness
- Additive — doesn't change existing pipeline

**Cons:**
- Extra LLM call per trial (~$1 additional cost per full run)
- Verifier might miss the same criteria the parser missed
- Doesn't fix string mismatch or wrong cohort

**Expected improvement:** 5-7 of 10 missed criteria fixed. Accuracy ~87-89%.

### Option 5: Hybrid architecture (direct LLM + structured extraction)

**Addresses:** All error types, different mechanism

**Approach:** Direct LLM assessment for clear-cut cases (high-confidence ELIGIBLE or INELIGIBLE). Structured extraction only for borderline cases where inference protection matters.

1. First pass: direct LLM — "Clearly eligible, clearly ineligible, or needs detailed review?"
2. If needs review: run structured extraction pipeline
3. If clear-cut: use direct LLM verdict

**Pros:**
- Highest ceiling — LLM reading comprehension for easy cases, deterministic evaluation for hard ones
- Most errors are on easy cases that the LLM would get right (string matching, cohort selection)
- Potentially cheaper — most cases skip the full pipeline

**Cons:**
- Two code paths, harder to audit
- Confidence-override problem returns for clear-cut cases
- Need to calibrate the borderline threshold
- The "clear-cut" assessment itself could be wrong

**Expected improvement:** ~90-93%.

### Option 6: Fix parse errors (recover 12 lost assessments)

**Addresses:** 12 ERROR verdicts (lost assessments, not wrong verdicts)

**Approach:** Add retry logic, increase token budget, or use structured output (tool_use) for JSON generation. Independent of other options.

**Expected improvement:** Recovers ~8-10 assessments. Changes denominator, not numerator much.

---

## Combinations

The options are not mutually exclusive. Some natural combinations:

### Combo A: Option 1 + Option 2 (normalize + patient-aware)

Fix string matching in code, give tumor type to parser for cohort selection. Addresses 13 errors (7 string + 6 cohort). No architectural change — one code fix, one prompt edit.

**Expected accuracy: ~91%**. Low risk. Doesn't address the 10 missed criteria.

### Combo B: Option 3 alone (merged single-step)

Replace the two-LLM design with one LLM call that evaluates criteria against the patient record. Addresses string mismatch, wrong cohort, and some missed criteria in one change. More radical — replaces the core of the architecture.

**Expected accuracy: ~90-93%**. Medium risk (inference leakage). Must test on P004 target case.

### Combo C: Option 1 + Option 2 + Option 4 (normalize + patient-aware + verification)

Layer all three targeted fixes. String normalization (7), patient-aware parsing (6), verification pass for coverage (5-7 of 10).

**Expected accuracy: ~93-95%**. Higher cost (extra LLM call), more complex, but addresses all three failure modes independently.

### Combo D: Option 5 + Option 1 (hybrid + normalization)

Direct LLM for clear-cut, structured extraction with string normalization for borderline. Highest ceiling but most complex architecture.

**Expected accuracy: ~93-95%**. Highest complexity.

**Pros:**
- Fixes a category of errors at the source

**Cons:**
- Hard to know how many errors are extraction vs parsing without tracing each one
- May require patient-specific extraction rules

**Expected improvement:** ~3–5 errors fixed.

---

## Evaluation Framework

| Criterion | Weight | Notes |
|-----------|--------|-------|
| Accuracy improvement | High | Primary metric. Must measurably improve from 84.1%. |
| Safety (false-ELIGIBLE rate) | High | Must not regress. Currently 1 false-ELIGIBLE. |
| Inference isolation | Medium | The original Finding 4 motivates structured extraction. Solutions that reintroduce inference need justification. |
| Implementation complexity | Medium | Simpler is better. Each added LLM call adds cost and latency. |
| Cost | Low | Current run is $2.33. Doubling to $4.66 is acceptable if accuracy improves. |

---

## Recommended Exploration Order (revised after trace)

The choice depends on how much architectural change is acceptable.

### Path 1: Incremental (low risk, ~91%)

1. **Option 1 (string normalization)** — pure code, zero LLM change, fixes 7 errors. Do first.
2. **Option 2 (patient-aware parsing)** — one prompt edit, fixes 6 errors. Test inference leakage on P004.
3. **Option 6 (parse error recovery)** — independent, recovers 12 assessments.
4. **Option 4 (verification pass)** — if still below 90%, add coverage checking.

### Path 2: Structural (medium risk, ~93%)

1. **Option 3 (merged single-step)** — replaces the core two-LLM design. Test on P004 first. If inference isolation holds, this solves string mismatch + wrong cohort + some missed criteria in one change.
2. **Option 6 (parse error recovery)** — independent.

### Path 3: Pragmatic (highest ceiling, most complexity)

1. **Option 5 (hybrid)** — direct LLM for clear-cut, structured extraction for borderline. Addresses all error types by routing them to the right method.

**Projected improvement:**

| Path | Approach | Expected accuracy | Risk | Effort |
|------|----------|-------------------|------|--------|
| Current | — | 84.1% | — | — |
| Path 1 (Combo A) | Normalize + patient-aware | ~91% | Low | Small (code + prompt) |
| Path 1 (Combo C) | + verification pass | ~93-95% | Low | Medium (extra LLM call) |
| Path 2 (Option 3) | Merged single-step | ~90-93% | Medium (inference) | Medium (rewrite Step 2) |
| Path 3 (Option 5) | Hybrid | ~93-95% | Medium (complexity) | High (two code paths) |

---

## Open Questions

1. **Does co-visibility cause inference leakage?** Both Option 2 (patient-aware parsing) and Option 3 (merged single-step) reintroduce patient information into the criteria assessment. The critical test: P004 × NCT04511013. If `prior_treatments[*].setting = null` still produces DATA_MISSING (not CONFIRMED_FAILED), inference isolation works. This test should be run before committing to any approach that reintroduces co-visibility.

2. **How much of the GT is wrong?** The GT labeler is also an LLM. Some "errors" may be GT mistakes — particularly the UNCERTAIN→INELIGIBLE cases where the GT says UNCERTAIN but fixD's reading of the criteria seems defensible. A human review of the 17 UNCERTAIN→INELIGIBLE cases would sharpen the true accuracy number.

3. **Is 90% the right target?** In clinical screening, false-INELIGIBLE (patient silently excluded) is more dangerous than false-UNCERTAIN (patient flagged for human review). If the system is a screener feeding a human reviewer, 84% with a conservative bias may be acceptable. The 10 missed criteria errors all go in the conservative direction (UNCERTAIN when should be INELIGIBLE).

4. **Is Option 3 actually fixD anymore?** The merged single-step design is architecturally closer to fixC (Annotation-First) than fixD (Structured Extraction). The original value of fixD was that the LLM never saw patient and criteria together. If we merge them, we're designing a new architecture — call it fixE — not improving fixD. This is fine, but worth naming.

5. **Could we test Option 3 vs Combo A on the same 29 errors as a controlled comparison?** Both can be run on just the error cases (~30 API calls each) before committing to a full rerun. This would give data on whether the merged approach actually outperforms incremental fixes.

---

*Analysis produced June 23, 2026. Revised after full error trace attributed all 29 errors to Step 2 parsing.*

---

*Analysis produced June 23, 2026. Based on fixD v2 outputs, tightened ground truth (182 assessments), and manual review of all 29 error cases.*
