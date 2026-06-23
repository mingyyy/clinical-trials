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

## The Core Problem: Step 2 Is Losing Information

The architecture was designed so Step 2 (criteria parsing) is **patient-agnostic** — the LLM sees only the eligibility text, not the patient. This was intentional: it prevents the LLM from applying clinical priors to reach a verdict (the Finding 4 problem).

But patient-agnostic parsing has a cost: **for multi-cohort trials, the parser doesn't know which cohort to focus on.** A trial with 6 cohorts (NSCLC, HNSCC, TNBC, HR+/HER2-, ovarian, colorectal) generates predicates for all cohorts. The evaluator has no way to select the relevant cohort. If the parser emphasizes the wrong one — or flattens them into contradictory predicates — the verdict is wrong.

This is a fundamental tension:
- **Patient-agnostic parsing** prevents inference bias but can't handle multi-cohort trials
- **Patient-aware parsing** handles cohorts correctly but reintroduces inference risk

---

## Option Space

### Option A: Better parser prompt (few-shot examples)

**Approach:** Add examples to PARSE_SYSTEM showing how to handle multi-cohort trials. Instruct the parser to generate separate predicate blocks per cohort, with a cohort-selection predicate (e.g., "Cohort: TNBC" → check `disease.primary_condition`).

**Pros:**
- No architectural change
- Low risk — still patient-agnostic
- Could fix ~5–7 errors from cohort confusion

**Cons:**
- Doesn't solve the fundamental problem — parser still guesses which cohorts matter
- Prompt length increases, higher token cost
- May not help with trials where cohort selection depends on biomarker combinations

**Expected improvement:** ~5–7 errors fixed. Accuracy ~86–88%.

### Option B: Patient-aware parsing (give tumor type to Step 2)

**Approach:** Pass the patient's tumor type and key biomarkers to Step 2 so the parser can focus on the relevant cohort. Still no verdict — just "parse criteria relevant to a TNBC patient."

**Pros:**
- Directly solves multi-cohort confusion — parser knows which cohort matters
- Could fix ~10–12 errors
- Minimal inference risk — tumor type is a fact, not a judgment

**Cons:**
- Reintroduces some patient information into the criteria-parsing step
- Risk: parser might subtly adjust predicate strictness based on the patient (the original Finding 4 concern)
- Need to test whether inference leakage actually occurs

**Expected improvement:** ~10–12 errors fixed. Accuracy ~88–91%.

**Risk mitigation:** Only pass `disease.primary_condition` and `biomarkers.her2` — enough for cohort selection, not enough for eligibility inference. The parser still never sees ECOG, prior treatments, or disease stage.

### Option C: Two-pass parsing

**Approach:**
1. First pass (patient-aware): "Which cohort/arm of this trial would apply to a patient with [tumor type]?" → returns cohort identifier
2. Second pass (patient-agnostic): "Parse the eligibility criteria for [cohort X] into predicates." → returns predicates for that cohort only

**Pros:**
- Clean separation: cohort selection is patient-aware, criteria parsing stays patient-agnostic
- No inference leakage in the predicate generation step
- Could fix ~10–12 errors

**Cons:**
- Doubles the LLM calls for Step 2 (cost ~2×)
- More complex pipeline
- Cohort identification itself might be error-prone

**Expected improvement:** Similar to Option B (~88–91%), at higher cost.

### Option D: Hybrid architecture (direct LLM + structured extraction)

**Approach:** Use direct LLM assessment for clear-cut cases, structured extraction only for borderline ones.

1. First pass: direct LLM call — "Is this patient clearly eligible, clearly ineligible, or borderline?" with high-confidence threshold (>0.90)
2. If borderline: run structured extraction pipeline
3. If clear-cut: use direct LLM verdict

**Pros:**
- Best of both worlds: LLM reading comprehension for easy cases, deterministic evaluation for hard ones
- The 80% of cases that are straightforward get higher accuracy from direct LLM
- The 20% that are borderline get the inference-protection of structured extraction
- Potentially cheaper — most cases skip Step 2 entirely

**Cons:**
- Reintroduces the confidence-override problem for clear-cut cases (but those cases have strong signal anyway)
- More complex architecture with two code paths
- Need to calibrate the "borderline" threshold
- Harder to audit — two different methods produce verdicts

**Expected improvement:** Could reach ~90–93% if the direct LLM handles easy cases well.

### Option E: Fix parse errors (the 12 ERRORs)

**Approach:** The 12 parse ERRORs are JSON failures on long/complex eligibility text. These are lost assessments, not wrong verdicts — but they reduce the denominator.

- Increase `MAX_CRITERIA_CHARS` or add retry logic
- Use structured output (tool_use) instead of raw JSON generation
- Fall back to a simpler assessment on parse failure

**Pros:**
- Recovers 12 lost assessments
- Straightforward engineering

**Cons:**
- Doesn't fix the 29 wrong verdicts
- May introduce new errors if long texts confuse the parser further

**Expected improvement:** Recovers 12 assessments. If ~8 of those would be correct, accuracy goes from 153/182 to ~161/194 = 83.0% (similar but on a larger base).

### Option F: Improve extraction (Step 1)

**Approach:** Some errors trace to Step 1 extracting wrong values. For example, P002's `disease.primary_condition` is extracted as "breast cancer" when it should be "triple-negative breast cancer" — this matters when the parser checks `primary_condition eq "TNBC"`.

- Add extraction validation (check extracted values against known patterns)
- Add more specific extraction rules for tumor subtypes

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

The trace changes the priority. String mismatch is the easiest win, wrong cohort is the highest-impact, and missed criteria is the hardest.

1. **String normalization (new — addresses 7 errors)** — Normalize `disease.primary_condition` during extraction to a controlled vocabulary (e.g., `"breast cancer"`, `"TNBC"`, `"melanoma"`). Or add fuzzy/contains matching to the evaluator. Mechanical fix, no LLM change needed. **Do this first.**

2. **Option B (patient-aware parsing — addresses 6 errors)** — Pass tumor type to Step 2 so the parser focuses on the right cohort. Test on P002 and P005 errors. Check inference leakage on P004 target case.

3. **Option E (fix parse errors — recovers 12 assessments)** — Independent of the above. Add retry logic or structured output for long criteria text.

4. **Missed criteria (10 errors) — hardest to fix.** The parser generates 15-30 predicates per trial but misses key ones. Options: more detailed parser prompt, longer output budget, or a verification pass ("did you cover all inclusion criteria?"). This may be inherent to the single-pass parsing approach.

5. **Option D (hybrid)** — if the above don't close the gap to 90%.

**Projected improvement:**

| Fix | Errors addressed | Expected accuracy |
|-----|-----------------|-------------------|
| Current | — | 84.1% (153/182) |
| + String normalization | 7 | ~88% (160/182) |
| + Patient-aware parsing | 6 | ~91% (166/182) |
| + Parse error recovery | ~8 of 12 | ~90% (166/190 base) |
| + Missed criteria (partial) | ~5 of 10 | ~93% |

---

## Open Questions

1. **Does patient-aware parsing actually cause inference leakage?** This is testable: run Option B on the P004 target case (NCT04511013). If it still returns UNCERTAIN, inference isolation is preserved. If it flips to INELIGIBLE, the risk is real.

2. **How much of the GT is wrong?** The GT labeler is also an LLM. Some "errors" may be GT mistakes. A human review of the 17 UNCERTAIN→INELIGIBLE cases would sharpen the true accuracy number.

3. **Is 90% the right target?** In clinical screening, the cost of false-INELIGIBLE (patient silently excluded) vs false-UNCERTAIN (patient flagged for human review) matters. If the system is a screener feeding a human reviewer, 84% with a conservative bias may be acceptable.

---

*Analysis produced June 23, 2026. Based on fixD v2 outputs, tightened ground truth (182 assessments), and manual review of all 29 error cases.*
