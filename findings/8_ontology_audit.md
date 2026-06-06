# Ontology Coverage Audit — Structured Extraction Predicate Vocabulary
## Breast Cancer Clinical Trials

**Date:** June 6, 2026
**Scope:** 4 breast cancer patients (P001, P002, P003, P005); 192 trial assessments
**Framework:** Structured Extraction (`test_prompt_fixD.py`)
**Ground truth:** Hand-labeled + verified by independent LLM agent (182 pairs)

---

## Executive Summary

The current Structured Extraction ontology covers approximately **65–70% of the clinical criteria encountered in breast cancer trials**. The framework makes 3 critical false-ELIGIBLE errors (safety risk) and 18 false-UNCERTAIN errors (review burden).

**Error mode:** Over-cautious (false UNCERTAIN rather than false ELIGIBLE) — clinically safer but operationally expensive.

**Root cause:** Five variable categories are missing or inadequately specified:
1. Disease activity requirements (measurable/active disease, NED vs active)
2. HER2 granularity (IHC score, HER2-low distinction)
3. Specific mutation predicates (TP53, PIK3CA)
4. Temporal variables (washout periods, time since progression, line counts)
5. Prior treatment outcomes (progression on drug, line-of-therapy ordinal)

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
| HER2 IHC score / HER2-low | **Missing** | Cannot distinguish IHC 3+ from IHC 2+/FISH− |
| TP53, PIK3CA mutations | **Missing** | No variables exist |
| Disease activity / NED / measurable disease | **Missing** | Critical gap — see below |
| Temporal variables (washout, time since progression) | **Missing** | Entirely absent |
| Prior line count (aggregate) | **Missing** | `line_of_therapy` per drug, no count logic |
| Progression on specific drug | **Missing** | Boolean outcome flag absent |
| HR+ composite (ER or PR positive) | Partially covered | ER and PR separate; no HR+ composite |
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
| Current | 75.8% (94/124) |
| After Phase 1 | ~83–85% |
| After Phase 1+2 | ~87–90% |
| After Phase 1+2+3 | ~90–92% |

---

## Structural Observation

**The extraction step is the binding constraint, not the ontology design.**

Adding variables to the schema is necessary but not sufficient. The EXTRACT_SYSTEM prompt must reliably populate them from free-text profiles:
- `her2_ihc_score` requires parsing pathology language ("IHC 3+", "score of 2")
- `months_since_completion` requires date arithmetic ("completed 5 years of tamoxifen" → how many months ago?)
- `line_of_therapy_ordinal` requires knowing standard-of-care regimens to infer sequence

Free-text oncology profiles are sparse on precision. The bottleneck is not ontology breadth — it is extraction fidelity.

**This is the same structural constraint that limits the original LLM-verdict approaches, but from the opposite side.** LLM-as-judge over-infers from sparse profiles (clinical priors fill in gaps). Structured Extraction under-infers (null fields = uncertainty). Neither is "more correct" — they make different tradeoffs between false confidence and false uncertainty. The right tradeoff depends on downstream cost: in clinical trial matching, false ELIGIBLE is more dangerous than false UNCERTAIN.

---

## Relation to Framework Comparison

The ontology gaps are domain-specific (breast cancer criteria vocabulary) and architectural (structured extraction design). They affect Structured Extraction's accuracy independent of its merits relative to LangGraph, PydanticAI, smolagents, or Claude Direct.

The four-framework comparison showed Structured Extraction costs 44% less than Annotation-First and is architecturally more robust (deterministic evaluation, no inference leakage). The ontology audit reveals the quality bottleneck is in Steps 1+2 (extraction + parsing), not Step 3 (evaluation). Improving the ontology improves accuracy; the deterministic evaluation advantage is preserved.

---

*Audit produced June 6, 2026 by independent LLM agent reading `test_prompt_fixD.py`, `findings/ground_truth.json`, full-run outputs (P001–P005), and `findings/ground_truth_verification_report.md`. Synthesized with human review.*
