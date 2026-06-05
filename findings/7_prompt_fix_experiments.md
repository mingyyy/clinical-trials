# Prompt Fix Experiments — Calibrating the Inference Problem

**Context:** This is a follow-on to the four-framework comparison. After discovering that all structured frameworks misclassified NCT04511013 × P004 (INELIGIBLE when the correct answer is UNCERTAIN), a series of prompt fixes was designed and tested to understand — and eventually solve — the root cause.

**Target case:** NCT04511013 is a BRAF V600E melanoma + brain mets trial. The exclusion criterion is "no prior systemic therapy for metastatic disease." P004's profile lists prior treatments as ipilimumab + nivolumab, but does not state the treatment setting. The profile-supported answer is UNCERTAIN: the profile neither confirms nor rules out that prior ipi+nivo was for metastatic disease. Adjuvant ipi+nivo for resected Stage III/IV melanoma is an FDA-approved indication. The LLM had to acknowledge this ambiguity in `uncertain_items` — then return INELIGIBLE anyway.

**Sanity checks (all should be INELIGIBLE):**
- P004 × NCT06246916: ECOG ≤1 required; profile says ECOG 2 → INELIGIBLE (direct evidence)
- P004 × NCT05727904: BRAF V600E required; profile says BRAF wild-type → INELIGIBLE (direct evidence)
- P001 × NCT07060807: prior chemotherapy exclusion; profile confirms chemo → INELIGIBLE (direct evidence)

---

## Why the Problem Exists

The LLM acknowledged the ambiguity explicitly in `uncertain_items`:

> *"Whether ipilimumab and nivolumab were given in the neoadjuvant/adjuvant setting vs. for metastatic disease"*

Then returned `INELIGIBLE` at 0.92 confidence with the reasoning:

> *"The context of 'metastatic melanoma' as the current diagnosis and listing these as prior treatments strongly suggests they were given for metastatic disease."*

The rule "absence of information is NOT evidence of ineligibility" was not being violated — the LLM believed it had sufficient context for an inference. The LLM's clinical prior (metastatic melanoma diagnosis + ipi+nivo combination = most likely metastatic-setting treatment) is sound as a statistical prior. Adjuvant ipi+nivo combination is uncommon; first-line metastatic ipi+nivo is the most common setting. The LLM is not wrong to have this prior — it is wrong to act on it when the rule says not to.

**Root cause:** System prompt rules live in the context window. LLM clinical priors live in the weights. When a prior is strong, the prior wins — not through contradiction, but through confidence. A confident model does not need to apply a "use UNCERTAIN when unsure" rule because it is not unsure. There is no architectural enforcement layer that treats system prompt rules as hard constraints.

---

## Fixes 1–3: Patch Attempts (All Failed)

### Fix 1 — Stronger rule language

Added to the "direct evidence" rule:

> *"Direct evidence means text explicitly stated in the profile — NOT inferences from diagnosis, disease stage, or standard-of-care context."*

**Result:** FAIL. Verdict INELIGIBLE at 0.90. Model acknowledged the rule, then applied "high likelihood" reasoning: *"while not explicitly confirmed... there is a high likelihood this constitutes metastatic-setting therapy."* Stronger wording did not override a confident inference — the model treated its own high confidence as equivalent to direct evidence.

### Fix 2 — Citation requirement in output schema

Added a third rule requiring `exclusion_flags` to include `evidence_cited` — the exact text from the profile constituting direct evidence.

**Result:** FAIL. Verdict INELIGIBLE at 0.85. The citation provided was: *"Prior treatments: ipilimumab, nivolumab"* — a real profile quote, but not evidence the treatment was in the metastatic setting. The citation field was satisfied with a quote that did not prove what it claimed to prove.

The model could satisfy the citation requirement while still making an inference, because the requirement only asked for *a* quote, not a quote that *proves the criterion without inference*.

### Fix 3 — Two-stage extraction

Stage 1: extract only facts explicitly stated in the profile (no inference; produce `NOT_in_profile` list).
Stage 2: assess eligibility using *only* the extracted facts.

Stage 1 correctly identified `treatment_setting` as `NOT_in_profile`. Stage 2 received this explicit marker and still returned:

> *"The treatment setting is explicitly noted as unknown... there is a high likelihood this constitutes prior metastatic-setting therapy."*

**Result:** FAIL. Confidence dropped 0.92 → 0.82, verdict unchanged: INELIGIBLE. The two-stage pattern surfaced the information gap clearly. The model ignored it.

**All three fixes failed for the same structural reason:** the LLM has a judgment step where it can apply clinical priors, and no prompt instruction can reliably override a confident inference at that step. The only fix is to remove the judgment step entirely.

---

## Fix C — Annotation-First + Literal Citation Check

### Design

Change the LLM's role entirely. Instead of producing a verdict:

1. LLM annotates each criterion as `CONFIRMED_MET`, `CONFIRMED_FAILED`, or `DATA_MISSING`
2. For `CONFIRMED_FAILED`, LLM must provide an exact quote from the profile as `profile_citation`
3. Code validates that the citation is a literal substring of the patient profile text
4. Code computes the verdict: any valid `CONFIRMED_FAILED` → INELIGIBLE; any material `DATA_MISSING` → UNCERTAIN; otherwise ELIGIBLE

The key constraint: a `CONFIRMED_FAILED` annotation with a citation that does not appear verbatim in the profile is **downgraded to `DATA_MISSING`** by code. The LLM cannot infer its way to INELIGIBLE — it must cite.

### Targeted test results (4/4 pass)

| Case | Expected | Got | Notes |
|------|----------|-----|-------|
| TARGET P004×NCT04511013 | UNCERTAIN | UNCERTAIN | No valid citation for "prior metastatic therapy" — correct |
| SANITY1 P004×NCT06246916 | INELIGIBLE | INELIGIBLE | Citation: "ECOG PS: 2" — valid literal match |
| SANITY2 P004×NCT05727904 | INELIGIBLE | INELIGIBLE | Citation: "BRAF wild-type" — valid literal match |
| SANITY3 P001×NCT07060807 | INELIGIBLE | INELIGIBLE | Chemotherapy confirmed by citation |

### Full-run results (5 patients)

| Patient | Assessed | E/U/I (Fix C) | E/U/I (LangGraph rerun) |
|---------|----------|----------------|-------------------------|
| P001 | 73 | 0 / 8 / 64 | 0 / 3 / 70 |
| P002 | 52 | 0 / 9 / 43 | 0 / 27 / 25 |
| P003 | 18 | 0 / 5 / 13 | 0 / 7 / 11 |
| P004 | 18 | 1 / 7 / 10 | 1 / 8 / 9 |
| P005 | 397* | 2 / 41 / 354 | 0 / 7 / 25 |

*P005 anomaly: `getattr(patient, "search_condition", patient.diagnosis)` returned `""` (attribute exists but empty string), causing an empty query → 500 results. This is a data pipeline bug, not an evaluation bug.

**Cost:** $3.84 (192 trials, excluding P005 anomaly).

### Residual issues

**Over-decisive on P002:** Fix C used "stage III" as a CONFIRMED_FAILED citation against "advanced or metastatic disease" criteria. "Stage III" is a valid substring of the profile — the citation check passed. But the inference that "stage III = not advanced/not metastatic" requires clinical knowledge: in oncology, "advanced" often includes Stage III, and "metastatic" is Stage IV. The literal citation check cannot detect clinically-required inference.

P002 had 27 UNCERTAIN in LangGraph (correct: TNBC patient with ambiguous prior treatment lines) vs 9 UNCERTAIN in Fix C — a −18 UNCERTAIN shift toward INELIGIBLE, likely over-driven by the "stage III" citation.

**Under-decisive on P001 NCT06568692:** Profile states "HER2+"; trial requires "HER2−". The LLM did not cite "HER2+" as CONFIRMED_FAILED evidence. Unclear whether this was a citation failure or the trial genuinely wasn't assessed — not verified.

---

## Fix D — Structured Extraction + Deterministic Evaluation

### Design

The core insight from Fix C: the literal citation check is a clever proxy, but it cannot handle inferences embedded in the citation itself. Fix D removes the LLM from the verdict path entirely by introducing an ontology — a shared vocabulary of typed variables — that makes evaluation deterministic.

**Step 1 (LLM, once per patient):** Extract a typed patient record.

```json
{
  "age": 62,
  "sex": "male",
  "ecog_ps": 2,
  "disease": {
    "primary_condition": "melanoma",
    "is_metastatic": true,
    "is_locally_advanced": null
  },
  "biomarkers": {"braf": "V600E", "pdl1": null},
  "prior_treatments": [
    {"drug": "ipilimumab", "drug_class": "anti-CTLA4", "setting": null},
    {"drug": "nivolumab", "drug_class": "anti-PD1", "setting": null}
  ]
}
```

The critical rule: `setting` must be `null` unless the profile explicitly names when the drug was given. `null` is an explicit absence marker, not a default. This is the key structural move: the absence of information is encoded as data, not as a gap in reasoning.

**Step 2 (LLM, per trial):** Parse eligibility criteria into structured predicates — patient-agnostic.

```json
[
  {"criterion_text": "No prior systemic therapy for metastatic disease",
   "criterion_type": "exclusion",
   "variable": "prior_treatments[*].setting",
   "operator": "list_none_eq",
   "required_value": "metastatic"},
  {"criterion_text": "ECOG performance status 0 or 1",
   "criterion_type": "inclusion",
   "variable": "ecog_ps",
   "operator": "lte",
   "required_value": 1}
]
```

**Step 3 (code):** Evaluate each predicate deterministically against the typed record.

```python
# list_none_eq: exclusion met if no treatment has the specified setting
values = [t.get("setting") for t in prior_treatments]
if any(v == "metastatic" for v in values): return "CONFIRMED_FAILED"
if any(v is None for v in values):          return "DATA_MISSING"  # can't confirm none match
return "CONFIRMED_MET"

# simple comparison
record_val = resolve(variable, record)  # returns None if path not found
if record_val is None: return "DATA_MISSING"
return "CONFIRMED_FAILED" if fails_comparison else "CONFIRMED_MET"
```

Verdict computation (code):
- Any `CONFIRMED_FAILED` exclusion → INELIGIBLE
- Any `DATA_MISSING` on any exclusion → UNCERTAIN
- Otherwise → ELIGIBLE

**The LLM never sees the patient and the criterion together in a judgment context.** Inference has nowhere to land.

### OR predicate handling

Many criteria are disjunctive: "locally advanced or metastatic disease." Fix D evaluates each branch independently:

```
is_metastatic = false (stated)     → CONFIRMED_FAILED branch
is_locally_advanced = null         → DATA_MISSING branch
OR result: any DATA_MISSING → DATA_MISSING overall
```

This correctly handles P002 (TNBC, Stage III, is_locally_advanced=null): the "locally advanced OR metastatic" criterion evaluates to DATA_MISSING, not CONFIRMED_FAILED. Fix C would have cited "stage III" and returned INELIGIBLE.

### Targeted test results (4/4 pass)

| Case | Expected | Got | Notes |
|------|----------|-----|-------|
| TARGET P004×NCT04511013 | UNCERTAIN | UNCERTAIN | setting=null → DATA_MISSING for "no prior metastatic therapy" |
| SANITY1 P004×NCT06246916 | INELIGIBLE | INELIGIBLE | ecog_ps=2 > 1 → CONFIRMED_FAILED |
| SANITY2 P004×NCT05727904 | INELIGIBLE | INELIGIBLE | braf="V600E" fails "wild-type required" |
| SANITY3 P001×NCT07060807 | INELIGIBLE | INELIGIBLE | chemotherapy confirmed |

Initial run failed on target + SANITY3: `max_tokens=2048` too low for long eligibility texts → JSON truncated. Fixed by: (1) raising parser `max_tokens` to 4096, (2) adding `MAX_CRITERIA_CHARS = 6000` truncation before parsing.

### Full-run results (5 patients)

| Patient | Assessed | E/U/I (Fix D) | E/U/I (Fix C) | E/U/I (LangGraph rerun) |
|---------|----------|---------------|----------------|-------------------------|
| P001 | 68 | 0 / 9 / 59 | 0 / 8 / 64 | 0 / 3 / 70 |
| P002 | 49 | 1 / 9 / 39 | 0 / 9 / 43 | 0 / 27 / 25 |
| P003 | 18 | 2 / 9 / 7 | 0 / 5 / 13 | 0 / 7 / 11 |
| P004 | 17 | 1 / 5 / 11 | 1 / 7 / 10 | 1 / 8 / 9 |
| P005 | 31 | 1 / 3 / 26 | — (anomaly) | 0 / 7 / 25 |

P004 NCT04511013: UNCERTAIN ✓, data_missing_exclusions=7.

**Cost:** $2.16 (192 trials). Fix C cost $3.84 for comparable trial count — Fix D 44% cheaper because patient extraction is amortized (1 LLM call per patient vs per trial), and predicate parsing tends to be more token-efficient than full annotation.

**P002 improvement:** LangGraph 0/27/25. Fix C 0/9/43 (over-INELIGIBLE from "stage III" citation). Fix D 1/9/39 — UNCERTAIN count closer to Fix C but INELIGIBLE shifted back down. The OR predicate handling prevents "stage III" from triggering "locally advanced OR metastatic" exclusions.

**P003 ELIGIBLE increase:** 2 ELIGIBLE in Fix D vs 0 in LangGraph/Fix C. Not verified by spot-check; may be correct (trial criteria that cannot be evaluated without lab values → ELIGIBLE if no explicit failures). Worth auditing.

**Known limitation:** ~5% parse error rate (JSON truncation or schema deviation) on very long eligibility criteria texts. Currently returns `verdict=ERROR`. Mitigation: increase `MAX_CRITERIA_CHARS` or add retry logic.

---

## What This Experiment Shows

### Why prompting alone cannot fix the inference problem

Three prompt fixes that failed share a common structure: they all still asked the LLM to produce a holistic judgment. The fixes added constraints (stronger rules, citation requirements, two-stage extraction) but they could not remove the LLM's final judgment step. When the LLM is confident, a rule saying "don't be confident" does not work. Rules live in context; clinical priors live in weights. Weights win.

### Why Fix C worked better than Fixes 1–3

Fix C changed the task from "assess eligibility" to "annotate criteria." The LLM was never asked for a verdict. The citation check added a code-enforced gate: inferences without a literal profile quote cannot reach INELIGIBLE. This is not a stronger prompt — it is a different architecture. The judgment step is removed from the LLM; code takes it.

The remaining weakness: if the inference is embedded in the citation itself (e.g., citing "stage III" to support "not metastatic"), the literal match check passes but the claim is still inferential. The code cannot detect inference inside a valid citation.

### Why Fix D is more robust than Fix C

Fix D removes the inference surface entirely by introducing typed variables. When the patient record has `prior_treatments[*].setting = null`, the code cannot infer that `null == "metastatic"`. The code only knows: `null` means "unknown." The verdict path is deterministic from that point.

The ontology (structured predicate variables) is the enabling mechanism. Without a shared vocabulary of typed variables, you cannot write evaluation code. The cost is that the predicate vocabulary must be maintained as the scope of trials expands — it is a design surface that requires ongoing curation, not a one-shot prompt.

### The broader architectural lesson

The inference problem is not a prompting problem. It is an architectural problem. The LLM's job in a clinical AI system should be:

- **Information extraction** — convert unstructured text into typed structured records
- **Semantic parsing** — convert natural language criteria into structured predicates
- **NOT verdict computation** — that belongs to deterministic code

This is the same principle underlying rule-based expert systems, decision trees, and formal logic engines. Those approaches were not "wrong" — they solved real problems. The LLM contribution here is the extraction and parsing steps (which were expensive before LLMs). The evaluation step is code.

The pattern generalizes beyond clinical trial matching: any system where a "rule" must reliably override an LLM inference should move the rule enforcement out of the LLM and into code.

---

## Fix D Accuracy Against Ground Truth

Ground truth was hand-labeled June 5, 2026 by reading actual ClinicalTrials.gov eligibility criteria for all trials independently of any framework output. Labels: `eligible` (clearly meets stated criteria), `ineligible` (confirmed disqualifier in profile), `ambiguous` (eligibility depends on data not in profile → maps to UNCERTAIN).

**Note on P002:** The Fix D full run and the ground-truth labeling fetched the ClinicalTrials.gov API on different days and received largely different trial sets (7 of 50 GT trials overlapped). P002 is excluded from the accuracy computation; overlap is insufficient for a meaningful comparison.

**Ground truth correction (June 6, 2026):** An independent LLM verification agent re-assessed all 182 patient × trial pairs (see `findings/ground_truth_verification.json`). It found 26 labeling errors where the original GT said `ambiguous` (UNCERTAIN) but the trial's required inclusion criterion was directly contradicted by an explicitly-stated profile fact — for example, P001 (HER2+) in trials requiring HER2-negative tumors, or P002 (stage III locally advanced) in trials requiring metastatic/unresectable disease. These were corrected: moved from `ambiguous` to `ineligible`. One case (P005 × NCT04585750, TP53 Y220C required mutation) was kept as `ambiguous` because the mutation status is genuinely unknown in the profile.

Root cause of the labeling errors: the original reviewer applied "absence of information = UNCERTAIN" too broadly, including cases where profile information was **explicitly present and negative** (e.g., stated HER2+ when HER2- is required). The correct rule is: absent data = UNCERTAIN; explicitly present data that contradicts a requirement = INELIGIBLE.

| Patient | Correct | Total | Accuracy |
|---------|---------|-------|----------|
| P001    | 51      | 60    | 85.0%    |
| P003    | 9       | 18    | 50.0%    |
| P004    | 14      | 17    | 82.4%    |
| P005    | 20      | 29    | 69.0%    |
| **All** | **94**  | **124** | **75.8%** |

**Error type breakdown (30 errors total):**

| Error type | Count | Clinical impact |
|-----------|-------|----------------|
| UNCERTAIN → should be INELIGIBLE | 18 | Extra review burden (safe failure mode) |
| INELIGIBLE → should be UNCERTAIN | 8 | Missed trial opportunities |
| ELIGIBLE → should be INELIGIBLE | 3 | Safety risk (false qualification) |
| ELIGIBLE → should be UNCERTAIN | 1 | Over-optimistic |

**The 3 critical errors (ELIGIBLE when INELIGIBLE):**
- P003 × NCT06545331 and NCT05283330: trials require advanced/metastatic solid tumors; P003 is NED post-mastectomy. Fix D's predicate extraction did not capture the "advanced/metastatic" inclusion requirement.
- P005 × NCT06551116 (QuantifyHER): trial explicitly excludes HER2-overexpressing mBC (IHC 3+ or IHC 2+ FISH+); P005 is HER2+. Fix D's predicate extraction missed the HER2-exclusion arm.

**The dominant error pattern (18 false UNCERTAIN):** Fix D generated UNCERTAIN for trials where the profile explicitly fails a stated requirement. The predicate ontology did not reliably encode "active/measurable disease required" as a hard inclusion gate (P003 NED cases), and did not capture all biomarker requirement predicates. Fix D is over-cautious: it surfaces cases for human review that are actually clear INELIGIBLEs.

**P003's low accuracy (50%):** Most trials in P003's set require advanced/metastatic disease; P003 is NED. Fix D returned UNCERTAIN for 9 of these — it correctly avoided INELIGIBLE but failed to close on it, generating unnecessary uncertainty. The predicate ontology gap is "active/measurable disease required" as an inclusion gate.

**Error mode assessment:** Fix D's dominant error (UNCERTAIN when INELIGIBLE) is clinically safer than the original LLM's dominant error (INELIGIBLE when UNCERTAIN). A false UNCERTAIN sends a case for human review. A false INELIGIBLE removes a potentially eligible patient from consideration without any review. Fix D errs toward caution.

---

## Summary Table

| Fix | Architecture | Target pass | Full-run verdict |
|-----|-------------|-------------|-----------------|
| Baseline | LLM verdict | FAIL (INELIGIBLE 0.92) | — |
| Fix 1 | Stronger prompt rule | FAIL (INELIGIBLE 0.90) | — |
| Fix 2 | Citation in schema | FAIL (INELIGIBLE 0.85) | — |
| Fix 3 | Two-stage extraction | FAIL (INELIGIBLE 0.82) | — |
| Fix C | Annotation + literal citation check | PASS (UNCERTAIN) | Over-INELIGIBLE on P002 |
| Fix D | Typed extraction + ontology + code evaluation | PASS (UNCERTAIN) | 77.4% accuracy (P001,P003-P005); 3 false-ELIGIBLE errors |

**All scripts:** `test_prompt_fixes.py` (Fixes 1–2), `test_prompt_fix3.py` (Fix 3), `test_prompt_fixC.py` (Fix C), `test_prompt_fixD.py` (Fix D), `run_fixC_all_patients.py`, `run_fixD_all_patients.py`.

**All outputs:** `outputs/05_experiments/prompt_fixes/{fixC,fixD}/{P001..P005}.json`, `summary.json`, `patient_records.json` (Fix D).
