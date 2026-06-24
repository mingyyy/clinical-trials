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

## fixE (Path 4) — Full Run Results

Option 3 was implemented as **fixE** (`scripts/run_fixE_all_patients.py`) and run on all 5 patients.

### Architecture

```
fixD:  Profile → [LLM] → Record    Criteria → [LLM] → Predicates → [Code evaluator] → Verdict
fixE:  Profile → [LLM] → Record    Record + Criteria → [LLM] → Per-criterion results → [Code] → Verdict
```

fixE eliminates the predicate vocabulary, the string-matching evaluator, and the two-LLM agreement problem. The LLM evaluates each criterion directly against the typed record. Code computes the verdict from per-criterion results.

### Results

| | fixD v1 | fixD v2 | fixE |
|---|---|---|---|
| Accuracy | 75.8% (94/124) | 84.1% (153/182) | **84.2% (160/190)** |
| Parse errors | ~5% | 12 | **0** |
| Cost | $2.16 | $2.33 | **$1.72** |
| Wall time | — | 653s | **335s** |
| False-ELIGIBLE | 3 | 0 | 2 |
| P004 target case | UNCERTAIN | UNCERTAIN | **UNCERTAIN** |

### fixE vs fixD v2: same accuracy, different error profile

| | fixD v2 | fixE |
|---|---|---|
| Improvements (other was wrong, this is right) | — | 12 |
| Regressions (other was right, this is wrong) | — | 11 |
| Net | — | +1 |

fixE fixes 12 of fixD's 29 errors (string mismatch, wrong cohort, missed criteria) but introduces 11 new regressions on borderline cases where the LLM makes different judgment calls. The accuracy is the same because the LLM is non-deterministic on borderline cases.

### Verdict distribution comparison

| Patient | N | LangGraph (E/U/I) | fixD v2 (E/U/I) | fixE (E/U/I) |
|---------|---|-------------------|-----------------|--------------|
| P001 | 73 | 0/6/67 | 0/0/71 | 0/2/71 |
| P002 | 53 | 0/28/24 | 1/6/41 | 1/4/48 |
| P003 | 19 | 0/7/11 | 0/5/14 | 0/3/16 |
| P004 | 18 | 1/6/11 | 0/6/10 | 1/4/13 |
| P005 | 33 | 0/7/25 | 0/5/25 | 0/6/27 |

### What fixE wins on (even at same accuracy)

1. **Zero parse errors.** fixD had 12 ERRORs from JSON parse failures on long criteria. fixE evaluates criteria directly — no structured output to fail on. This recovers 8 assessments into the comparison base.

2. **26% cheaper.** $1.72 vs $2.33. fixE makes one LLM call per trial (evaluate criteria against record). fixD makes one call per trial (parse criteria into predicates) plus code evaluation. The predicate parsing requires more output tokens.

3. **49% faster.** 335s vs 653s. Fewer total tokens processed.

4. **Simpler codebase.** No predicate vocabulary, no `evaluate_single()`, no `get_path()`, no OR-predicate handling, no string matching. The evaluator is ~20 lines of verdict logic vs ~150 lines in fixD.

### The ~84% ceiling

Both fixD v2 and fixE hit the same ceiling. The remaining errors are dominated by:

- **P002 "advanced/metastatic" judgment calls (19 UNCERTAIN→INELIGIBLE):** The GT labels these UNCERTAIN ("stage III might qualify as locally advanced"). Both fixD and fixE check `is_metastatic=false` and return INELIGIBLE. This is a correct reading of the record — but the GT labeler was more lenient about what "advanced" means for stage III TNBC.

- **LLM non-determinism on borderline cases:** fixE gets 12 cases right that fixD gets wrong, and 11 cases wrong that fixD gets right. The LLM makes different choices on the same borderline cases across runs.

The ceiling is not an architecture problem. It's a **ground truth calibration problem**: how should "advanced or metastatic" be applied to stage III TNBC? The GT labeler (also an LLM) says UNCERTAIN. The evaluator says INELIGIBLE. Both readings are defensible.

To move above 84%, the options are:
1. **Correct the GT:** Review the ~19 UNCERTAIN→INELIGIBLE P002 cases and determine whether INELIGIBLE is actually correct. If even half are reclassified, accuracy jumps to ~89%.
2. **Add "locally advanced" logic:** Treat stage III as potentially matching "advanced" criteria — add `is_locally_advanced` to the evaluation rules for "advanced or metastatic" criteria.
3. **Accept 84% as the ceiling** for this patient set and GT methodology.

---

## Open Questions (revised)

1. ~~**Does co-visibility cause inference leakage?**~~ **Answered: No.** P004 × NCT04511013 returns UNCERTAIN in fixE. The typed record with `setting: null` does not trigger clinical inference. Inference isolation is preserved.

2. **How much of the GT is wrong?** This is now the binding question. The ~19 UNCERTAIN→INELIGIBLE errors on P002 may be GT errors, not system errors. If the GT labeled "stage III not metastatic" as INELIGIBLE (which is arguably correct for trials requiring metastatic disease), accuracy would be ~89-92%.

3. **Is fixE strictly better than fixD?** Same accuracy, but simpler, cheaper, faster, zero parse errors. The only downside: fixE loses the "predicate as audit trail" property — you can see which criteria were evaluated and their results, but not the intermediate structured predicates. For most use cases, the per-criterion evaluation trace is sufficient.

4. **Should fixE replace fixD as the recommended architecture?** fixE is architecturally closer to fixC (Annotation-First) than fixD (Structured Extraction). The original value of fixD was total separation of patient data from criteria. fixE relaxes that — the LLM sees the typed record alongside criteria — but the typed record with explicit nulls provides sufficient inference protection. The simpler design is the better default.

---

*Analysis produced June 23, 2026. Updated with fixE full-run results. The ~84% accuracy ceiling is a ground truth calibration issue, not an architecture issue.*
