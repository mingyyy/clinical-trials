# Ontology Coverage Audit — Structured Extraction Predicate Vocabulary
## Breast Cancer Clinical Trials

**Date:** June 6, 2026
**Scope:** 4 breast cancer patients (P001, P002, P003, P005); 192 trial assessments
**Framework:** Structured Extraction (`test_prompt_fixD.py`)
**Ground truth:** Hand-labeled + verified by independent LLM agent (182 pairs)

---

## Executive Summary

**v1 (June 6):** 75.8% accuracy. 3 false-ELIGIBLE (safety risk), 18 false-UNCERTAIN (review burden). Originally attributed to ontology gaps.

**v2 (June 23):** 87.5% accuracy. 0 false-ELIGIBLE, 16 remaining errors evenly split. The v1 errors were caused by three implementation bugs, not missing ontology variables. No ontology expansion was needed.

**Remaining genuine gaps** (for future work):
1. Temporal variables (washout periods, time since progression) — not in schema
2. Prior treatment outcomes (progression on drug) — not in schema

---

## Current Ontology

### Patient record schema (EXTRACT_SYSTEM)

**Scalar fields:**
- `age`, `sex`, `ecog_ps`
- `disease.primary_condition`, `disease.histology_subtype`, `disease.stage_numeric` (I/II/III/IV)
- `disease.is_metastatic`, `disease.is_locally_advanced`, `disease.is_unresectable`, `disease.is_recurrent`
- `disease.metastatic_sites`, `disease.brain_metastases_present`, `disease.brain_metastases_irradiated`, `disease.brain_metastases_measurable_unirradiated`
- `biomarkers.her2` (positive/negative/equivocal), `biomarkers.er`, `biomarkers.pr`
- `biomarkers.braf_status`, `biomarkers.braf_variant`, `biomarkers.brca1`, `biomarkers.brca2`
- `biomarkers.msi`, `biomarkers.pdl1_expression`

**List fields (per prior treatment):**
- `prior_treatments[*].drug`, `.drug_class`, `.setting` (adjuvant/neoadjuvant/metastatic/palliative)
- `.line_of_therapy`, `.completed`, `.irae_grade_3_4`

**Operators:** `eq`, `neq`, `lt`, `lte`, `gt`, `gte`, `is_true`, `is_false`, `in_set`, `not_in_set`, `list_any_eq`, `list_none_eq`, `list_any_null`, `or_predicates`

---

## Coverage by Category

| Category | Status | Notes |
|---|---|---|
| Demographics (age, sex, ECOG) | Covered | — |
| Disease stage (I–IV, metastatic, locally advanced) | Covered | — |
| HER2/ER/PR basic | Covered | Binary positive/negative only |
| BRAF, BRCA1/2, MSI | Covered | — |
| Brain metastases | Covered | present / irradiated / measurable-unirradiated |
| Prior drug names | Covered | — |
| Prior treatment setting | Covered | adjuvant / neoadjuvant / metastatic |
| HER2 IHC score / HER2-low | Covered (v2) | `her2_ihc_score` existed in schema; parser prompt fixed to use it |
| TP53, PIK3CA mutations | Covered | Variables exist; test profiles lack TP53/PIK3CA data (correctly returns UNCERTAIN) |
| Disease activity / NED / measurable disease | Covered (v2) | `is_advanced_measurable` existed; parser prompt fixed to use as standalone predicate |
| Temporal variables (washout, time since progression) | **Missing** | Genuinely absent — real ontology gap (Phase 2) |
| Prior line count (aggregate) | Covered (v2) | `prior_line_count_metastatic/total` derived fields; null-handling bug fixed |
| Progression on specific drug | **Missing** | Boolean outcome flag absent |
| HR+ composite (ER or PR positive) | Covered | `hormone_receptor_positive` derived field exists in `add_derived_fields()` |
| PD-L1 structured (CPS/TPS score) | Partially covered | String field only; no threshold comparison |
| Organ function (hepatic, renal, cardiac) | Not covered | Marked `parseable=false`; acceptable default |

---

## Critical Errors — Root Cause Analysis

### False-ELIGIBLE errors (3) — safety risk

**P003 × NCT06545331 and NCT05283330** (advanced/metastatic solid tumors required; P003 is NED):
- Parser found `is_metastatic=false` → CONFIRMED_FAILED
- Parser found `is_recurrent=null` → DATA_MISSING
- OR predicate: one branch DATA_MISSING → result = DATA_MISSING (not INELIGIBLE)
- No other exclusions → verdict ELIGIBLE
- **Fix:** Add `disease.is_advanced_measurable` (bool). P003's NED status → `false`. Predicate: `is_advanced_measurable is_true` (inclusion gate) → CONFIRMED_FAILED → INELIGIBLE.

**P005 × NCT06551116 (QuantifyHER)** (excludes HER2-overexpressing mBC, IHC 3+ or IHC 2+/FISH+):
- Current `biomarkers.her2 = "positive"` cannot encode IHC score
- No predicate generated for the HER2-overexpressing exclusion
- No failing check → ELIGIBLE
- **Fix:** Add `biomarkers.her2_ihc_score` (0/1/2/3). P005's profile would be IHC 3+. Predicate: `her2_ihc_score not_in_set [3]` for the exclusion → CONFIRMED_FAILED → INELIGIBLE.

### False-UNCERTAIN errors (18) — review burden

The dominant pattern: the predicate parser cannot generate a failing predicate for criteria it has no variable to express. No predicate → no failure → DATA_MISSING → UNCERTAIN.

Key subtypes:
- **Active/measurable disease required** (~10 cases, mostly P003 NED): no `is_advanced_measurable` variable
- **Relapsed/refractory within N months** (~5 cases): no temporal variables
- **≥2 prior lines required** (~4 cases): no line count aggregation
- **Specific mutation required** (TP53, PIK3CA, ~3 cases): no biomarker variables

---

## Prioritized Variable Additions

### Phase 1 — Critical (1–2 weeks, ~40 hours)

| Variable | Type | Values | Resolves |
|---|---|---|---|
| `disease.is_advanced_measurable` | bool | true/false/null | 2 false-ELIGIBLE, ~8 false-UNCERTAIN |
| `biomarkers.her2_ihc_score` | int | 0/1/2/3/null | 1 false-ELIGIBLE, HER2-low trials |
| `biomarkers.tp53_status` | string | "wildtype"/"mutant"/"Y220C"/null | ~2 false-UNCERTAIN |
| `biomarkers.pik3ca_status` | string | "wildtype"/"mutant"/null | ~3 false-UNCERTAIN |

**Expected improvement:** Eliminates all 3 false-ELIGIBLE errors; resolves 8–10 false-UNCERTAIN.

### Phase 2 — Temporal variables (2–3 weeks, ~50 hours)

| Variable | Type | Notes |
|---|---|---|
| `prior_treatments[*].months_since_completion` | int or null | Washout periods, adjuvant→metastatic windows |
| `disease.time_since_last_progression_months` | int or null | "Relapsed within 6 months" criteria |
| `prior_treatments[*].line_of_therapy_ordinal` | int or null | With aggregation logic to count lines in a setting |

**Expected improvement:** Resolves 8–12 additional false-UNCERTAIN cases.

**Caveat:** Temporal fields require the patient profile to contain dates or durations. Free-text profiles like the test set rarely include these. The extraction step is the bottleneck, not the ontology.

### Phase 3 — Composite/derived (ongoing)

| Variable | Type | Notes |
|---|---|---|
| `biomarkers.hormone_receptor_status` | "HR+"/"HR−" | Computed from ER + PR; many trials use HR+ as unit |
| `prior_treatments[*].progression_on_this_drug` | bool | "Progressed on fulvestrant" vs "received fulvestrant" |
| `disease.cns_disease_controlled` | bool | Brain mets prophylaxis trials |
| `biomarkers.pdl1_cps` / `pdl1_tps` | int | Replace string field with threshold-comparable integers |

**Expected improvement:** Resolves 4–6 additional false-UNCERTAIN cases.

---

## Projected Accuracy After Roadmap

| Phase | Expected Accuracy |
|---|---|
| Current (v1) | 75.8% (94/124) |
| After Phase 1 | ~83–85% |
| After Phase 1+2 | ~87–90% |
| After Phase 1+2+3 | ~90–92% |

---

## v2 Update — Bug Fixes, Not Ontology Expansion (June 23, 2026)

A closer investigation revealed that the Phase 1 variables (`is_advanced_measurable`, `her2_ihc_score`, `tp53_status`, `pik3ca_status`) **already existed in the ontology**. The original audit misdiagnosed the root cause. The actual issues were three bugs:

### Bug 1: Evaluator logic — exclusion CONFIRMED_MET not treated as failure

`compute_verdict()` only checked for `CONFIRMED_FAILED`. When an exclusion criterion evaluated as `CONFIRMED_MET` (meaning the exclusion applies to this patient), the result was silently ignored. The verdict fell through to ELIGIBLE.

**Fix:** Added `elif result == "CONFIRMED_MET" and ctype == "exclusion"` → append to failures.

**Impact:** Fixed P005 × NCT06551116 (HER2-overexpressing exclusion). The parser correctly generated `her2_ihc_score gte 3` as an exclusion predicate and it evaluated correctly — but the evaluator didn't treat it as a failure.

### Bug 2: Parser prompt — `is_advanced_measurable` buried in OR predicates

For criteria like "inoperable, locally advanced, or metastatic disease", the parser generated an OR predicate with branches: `is_unresectable`, `is_locally_advanced`, `is_metastatic`, and sometimes `is_advanced_measurable`. When some branches evaluated as `null` (DATA_MISSING), the OR conservatively returned DATA_MISSING even when `is_advanced_measurable=false` already answered the question.

**Fix:** Updated PARSE_SYSTEM prompt to instruct the parser to use `is_advanced_measurable` as a **standalone predicate** for active disease requirements, not as an OR branch. The variable is the broadest gate — it subsumes the individual stage checks.

**Impact:** Fixed P003 × NCT06545331 (solid tumor requiring advanced/metastatic disease; P003 is NED).

### Bug 3: Derived field — `prior_line_count_metastatic` returned 0 for unknown settings

`add_derived_fields()` counted treatments where `setting == "metastatic"`. When treatments had `setting=null` (unknown), the count was 0 — implying "zero metastatic-setting treatments" when the correct answer is "unknown."

**Fix:** Return `null` when any treatment has `setting=null`.

**Impact:** Fixed regression on the target case (P004 × NCT04511013). Without this fix, the evaluator bug fix caused a false INELIGIBLE: the exclusion "no prior systemic therapy for metastatic disease" mapped to `prior_line_count_metastatic eq 0`, which evaluated as CONFIRMED_MET (exclusion met → INELIGIBLE). With `null`, it correctly evaluates as DATA_MISSING → UNCERTAIN.

### v2 Results

| Patient | Assessed | v1 E/U/I | v2 E/U/I | Accuracy (v2) |
|---------|----------|----------|----------|---------------|
| P001 | 73 | 0/9/59 | 0/0/71 | 96.7% (58/60) |
| P002 | 53 | 1/9/39 | 1/6/41 | 40.0% (2/5)* |
| P003 | 19 | 2/9/7 | 0/5/14 | 88.9% (16/18) |
| P004 | 18 | 1/5/11 | 0/6/10 | 87.5% (14/16) |
| P005 | 33 | 1/3/26 | 0/5/25 | 75.9% (22/29) |
| **Total** | **196** | **5/35/142** | **1/22/161** | **87.5% (112/128)** |

\* P002: only 5 trials overlapped with ground truth due to API result differences across run dates.

**Overall: 75.8% → 87.5% (+11.7pp)**

| Metric | v1 | v2 |
|--------|-----|-----|
| False-ELIGIBLE (safety-critical) | 3 | **0** |
| UNCERTAIN→INELIGIBLE errors | 18 | **9** |
| INELIGIBLE→UNCERTAIN errors | 8 | **7** |
| Cost | $2.16 | $2.33 |
| Target case (P004 × NCT04511013) | UNCERTAIN | UNCERTAIN |

### What the v2 exercise revealed

**The audit's diagnosis was wrong — the ontology was already adequate.** The Phase 1 variables existed in the extraction schema, the parser variable list, and the patient profiles. The errors came from three implementation bugs: an evaluator logic gap, a prompt phrasing issue, and a derived field miscalculation.

**No ontology expansion was needed.** Three code/prompt fixes, each taking minutes, eliminated all safety-critical errors and raised accuracy by 11.7 percentage points.

**The remaining 16 errors are evenly split** (9 over-INELIGIBLE, 7 over-UNCERTAIN) — no longer a one-directional signature. This suggests the low-hanging fruit has been picked. Further improvements would require either richer test patient profiles or more precise predicate parsing, not ontology changes.

**The real Phase 2 work**, if pursued, would be temporal variables (washout periods, progression timing). These genuinely don't exist in the schema. But they're also blocked by test profile sparsity — none of the 5 profiles contain temporal data.

---

## Structural Observation

**The extraction step is the binding constraint, not the ontology design.**

Adding variables to the schema is necessary but not sufficient. The EXTRACT_SYSTEM prompt must reliably populate them from free-text profiles:
- `her2_ihc_score` requires parsing pathology language ("IHC 3+", "score of 2")
- `months_since_completion` requires date arithmetic ("completed 5 years of tamoxifen" → how many months ago?)
- `line_of_therapy_ordinal` requires knowing standard-of-care regimens to infer sequence

Free-text oncology profiles are sparse on precision. The bottleneck is not ontology breadth — it is extraction fidelity.

**This is the same structural constraint that limits the original LLM-verdict approaches, but from the opposite side.** LLM-as-judge over-infers from sparse profiles (clinical priors fill in gaps). Structured Extraction under-infers (null fields = uncertainty). Neither is "more correct" — they make different tradeoffs between false confidence and false uncertainty. The right tradeoff depends on downstream cost: in clinical trial matching, false ELIGIBLE is more dangerous than false UNCERTAIN.

**The v2 exercise adds a third lesson:** before expanding a schema, check whether the existing schema is being used correctly. Implementation bugs — evaluator logic, prompt phrasing, derived field calculation — can masquerade as ontology gaps. The diagnostic signal is the same (one-directional errors), but the fix is different (code review vs schema design).

---

## Relation to Framework Comparison

The bugs fixed in v2 are specific to the Structured Extraction architecture. They affect its accuracy independent of its merits relative to LangGraph, PydanticAI, smolagents, or Claude Direct.

The four-framework comparison showed Structured Extraction costs 44% less than Annotation-First and is architecturally more robust (deterministic evaluation, no inference leakage). The v2 fixes confirm this: once the evaluator logic was corrected, the deterministic evaluation correctly handled all cases that the parser and extraction steps set up properly. The architecture's advantage — rules enforced in code, not in prompts — is preserved and validated.

---

*Original audit produced June 6, 2026. v2 update June 23, 2026 — three bug fixes, no ontology expansion, 75.8% → 87.5% accuracy.*
