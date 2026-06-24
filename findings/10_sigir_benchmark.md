# SIGIR Benchmark — fixE on TrialGPT Dataset

**Date:** June 24, 2026
**Context:** fixE achieved 86.3% on our 5-patient dataset with LLM-generated ground truth. To validate against expert annotations, we benchmarked against the SIGIR cohort from the TrialGPT dataset (58 patients, 3,141 patient-trial pairs with human relevance judgments).

---

## Dataset

**Source:** TrialGPT (https://github.com/ncbi-nlp/TrialGPT), SIGIR 2016 cohort.

| Property | Value |
|----------|-------|
| Patients | 58 (synthetic but clinically realistic) |
| Patient-trial pairs | 3,141 |
| Patient format | Free-text clinical notes (200-800 chars) |
| Trial format | Pre-parsed: inclusion_criteria, exclusion_criteria, brief_title, diseases, drugs |
| Labels | 0 = not relevant, 1 = excluded, 2 = eligible |
| Label source | Human expert judgments (SIGIR 2016 track) |

**Key difference from our dataset:** Patients are free-text clinical narratives (e.g., "A 58-year-old African-American woman presents to the ER with episodic pressing/burning anterior chest pain..."), much richer and more realistic than our structured 5-patient profiles. Trials span all disease areas, not just oncology.

---

## Initial Results (3 patients, 215 trials)

### Binary accuracy (eligible vs not-eligible)

| Metric | Value |
|--------|-------|
| Binary accuracy | **83.3%** (179/215) |
| 3-way exact accuracy | 23.7% (51/215) |

The 3-way accuracy is misleading — fixE maps both INELIGIBLE and UNCERTAIN to "excluded" (label 1), so all "not relevant" (label 0) trials score as wrong. The binary metric (eligible vs not-eligible) is the fair comparison.

### Confusion matrix

| | fixE: Excluded (0/1) | fixE: Eligible (2) |
|---|---|---|
| GT: Not relevant (0) | 128 | 10 |
| GT: Excluded (1) | 48 | 3 |
| GT: Eligible (2) | 23 | 3 |

### Per-label accuracy

| GT Label | Correct | Total | Accuracy |
|----------|---------|-------|----------|
| Not relevant (0) | 0/138 mapped to excluded | — | N/A (fixE can't distinguish 0 from 1) |
| Excluded (1) | 48 | 51 | **94.1%** |
| Eligible (2) | 3 | 26 | **11.5%** |

### Per-patient breakdown

| Patient | Trials | E/U/I | Accuracy |
|---------|--------|-------|----------|
| sigir-20141 | 101 | 5/21/75 | 26.7% (3-way) |
| sigir-20142 | 28 | 0/5/23 | 14.3% (3-way) |
| sigir-20143 | 86 | 11/32/43 | 23.3% (3-way) |

Cost: $1.24 for 215 assessments. Zero parse errors.

---

## The Key Finding: High Precision, Low Recall

fixE is very good at identifying ineligible trials (94.1% of excluded trials correctly identified) but misses most eligible trials (only 11.5% recall). Of 26 eligible trials across 3 patients, fixE found only 3.

The missed eligible trials break into two categories:
- **INELIGIBLE (7/23 misses):** fixE found specific criteria failures — but the expert judges apparently considered these met. fixE is being too strict.
- **UNCERTAIN (16/23 misses — estimated):** fixE found DATA_MISSING on exclusion criteria, pushing trials to UNCERTAIN. The expert judges had enough information to clear them.

**Example:** Patient sigir-20141 (chest pain presentation, suspected acute coronary syndrome). 11 GT-eligible trials, fixE found 1. The other 10 were labeled INELIGIBLE (7) or UNCERTAIN (3). Typical failure: fixE's typed record has `null` for fields that the clinical note implicitly addresses but doesn't state as structured data.

---

## Root Cause: The Extraction Bottleneck

fixE's architecture:
```
Clinical note → [LLM Step 1] → Typed record (lots of nulls) → [LLM Step 2] → Per-criterion evaluation → [Code] → Verdict
```

The typed record is the firewall against clinical inference (Finding 4). But on rich clinical notes, it's also an **information bottleneck**:

1. **Clinical notes contain implicit information.** "A 58-year-old woman presents to the ER with chest pain... she is known to have hypertension and obesity" — the note doesn't say "no diabetes" but a clinician would note the absence. The extraction produces `diabetes: null`. The evaluator sees `null` and returns DATA_MISSING for a "no diabetes" exclusion criterion.

2. **The extraction schema was designed for oncology.** Fields like `biomarkers.her2`, `disease.is_metastatic`, `prior_treatments[*].setting` are oncology-specific. The SIGIR dataset spans cardiology, endocrinology, obstetrics, etc. The schema doesn't capture relevant fields for these domains (e.g., cardiac enzymes, troponin levels, gestational age).

3. **Free-text notes are richer than structured profiles.** Our 5-patient profiles had 6-8 fields each. SIGIR clinical notes have 10-20 relevant clinical facts embedded in narrative text. The extraction loses many of them.

### This is a fundamental tension in the architecture

| Property | Typed record (fixE) | Raw note (TrialGPT) |
|----------|-------------------|---------------------|
| Inference protection | Strong — nulls prevent inference | Weak — LLM sees full narrative |
| Information preservation | Low — extraction is lossy | High — full note context |
| Domain flexibility | Low — schema is oncology-specific | High — works on any domain |
| Eligible recall | Low (11.5%) | Higher (TrialGPT claims 90%+ retrieval recall) |
| Excluded precision | High (94.1%) | Lower (TrialGPT doesn't separately report) |

**The typed record solves the inference problem but creates a recall problem.** For a clinical screening system where missing eligible patients is dangerous, 11.5% recall is unacceptable.

---

## Comparison with TrialGPT

| Metric | fixE (3 patients) | TrialGPT (published) |
|--------|-------------------|---------------------|
| Architecture | Extract typed record → evaluate against record | Direct LLM: note + criteria → per-criterion assessment |
| Criterion-level accuracy | — | 87.3% |
| Expert agreement | — | 88.7-90.0% |
| Binary accuracy | 83.3% | — |
| Eligible recall | 11.5% | Not separately reported |
| Cost per assessment | ~$0.006 | Not reported (Azure OpenAI GPT-4) |

TrialGPT's approach is architecturally simpler: it gives the LLM the raw patient note and the criteria directly, with a detailed prompt asking for per-criterion assessment. No extraction step. No typed record. The LLM sees the full note and reasons about each criterion in context.

This is essentially the approach Finding 4 warned against — the LLM sees patient data and criteria together, with risk of inference override. But TrialGPT achieves 87.3% criterion-level accuracy, close to expert performance. The inference problem may be less severe in practice than our P004 case suggested, or TrialGPT's prompt engineering mitigates it.

---

## Implications for fixE

### The 86.3% on our dataset was misleading

Our 5-patient dataset had simple structured profiles where extraction lost almost nothing. The SIGIR benchmark with real clinical notes exposes the extraction bottleneck. 86.3% on our data vs 83.3% binary accuracy on SIGIR — but the SIGIR number hides the 11.5% eligible recall problem.

### Options to improve

1. **Expand the extraction schema** to handle non-oncology domains (cardiology, endocrinology, etc.). This is a lot of work and still can't capture everything in a narrative note.

2. **Add a "relevant facts" free-text field** to the typed record — let the extraction dump anything not captured by structured fields into a catch-all. This preserves some narrative context while maintaining the typed structure for key fields.

3. **Use TrialGPT's approach (no extraction)** — give the raw note + criteria to the LLM. Accept the inference risk. This is what TrialGPT does and it achieves near-expert accuracy.

4. **Hybrid: extraction for inference-sensitive criteria, raw note for others.** Use the typed record for criteria where inference is dangerous (e.g., prior treatment setting), but pass the raw note for routine criteria (age, disease type, lab values).

### The honest assessment

fixE's architecture was designed to solve a specific problem (Finding 4: LLM confidence overriding explicit rules on ambiguous prior treatment settings). It solves that problem well — P004 × NCT04511013 correctly returns UNCERTAIN. But it pays a heavy recall cost on the general clinical trial matching task.

For a production system, the question is: **how often does the inference problem actually occur, and is the recall cost worth the protection?** If 1% of cases have Finding 4-style ambiguity and 89% of eligible trials are missed, the tradeoff is bad.

---

## Controlled Comparison: Three Approaches on the Same 3 Patients

To isolate what drives recall, we tested three approaches on the same 215 trials:

| Approach | Architecture | Absence rule | Binary acc | Eligible recall |
|----------|-------------|-------------|-----------|----------------|
| **fixE** | Extract record → evaluate against record | null = DATA_MISSING (conservative) | 83.3% | 11.5% (3/26) |
| **Direct (strict)** | Raw note → evaluate criteria | null = DATA_MISSING (conservative) | 87.4% | 7.7% (2/26) |
| **TrialGPT-style** | Raw note → evaluate criteria | absent = assume not present (permissive) | 83.7% | **42.3% (11/26)** |

### What this reveals

1. **Extraction is NOT the bottleneck.** The direct approach (no extraction, raw note) had *worse* recall than fixE (7.7% vs 11.5%). Removing the typed record didn't help.

2. **The prompt's absence rule is the bottleneck.** The only difference between "Direct (strict)" and "TrialGPT-style" is one instruction: *"if the note does not mention a medically important fact, you can assume that the fact is not true for the patient."* This tripled eligible recall from 7.7% to 42.3%.

3. **Binary accuracy is similar across all three (~83-87%).** The architecture (extraction vs direct) and the absence rule both change *which* trials are labeled eligible vs not, but the overall binary accuracy is stable. The tradeoff is precision vs recall within the not-eligible bucket.

### The core tradeoff

| Rule | Effect | Best for |
|------|--------|----------|
| "Absence = DATA_MISSING" (fixE, Finding 4) | Conservative. Misses eligible trials. Protects against false positives. | Systems where false-ELIGIBLE triggers costly downstream action |
| "Absence = assume not present" (TrialGPT) | Permissive. Finds more eligible trials. Risks false positives. | Screening systems where missing eligible patients is the primary risk |

This is the same tradeoff identified in Finding 2 (per-trial vs batch assessment) and Finding 4 (inference override), now observed at the prompt level. **The absence rule is a policy decision, not a technical one.** Both interpretations are defensible. The right choice depends on the downstream cost of each error type.

### Implications

The 86.3% accuracy we achieved on our 5-patient dataset reflected the conservative absence rule performing well on simple, structured profiles where nulls were genuinely absent. On rich clinical notes with implicit information, the same rule kills recall.

To benchmark fairly against TrialGPT's published 87.3% criterion-level accuracy, we should use the TrialGPT-style prompt — same absence rule, apples-to-apples comparison.

---

## Deep Dive: Why TrialGPT Gets Better Recall (and What's Still Missing)

### What TrialGPT actually does (three steps, not one)

Reading TrialGPT's source code reveals a **three-step architecture** that our comparison missed:

```
Step 1: Matching    — per-criterion assessment (inclusion and exclusion in separate LLM calls)
Step 2: Aggregation — SECOND LLM call: takes all per-criterion results + trial summary →
                      outputs two scores: Relevance (R: 0-100) and Eligibility (E: -R to R)
Step 3: Ranking     — sort trials by E score → return ranked list
```

This is fundamentally different from our binary verdict approach. Three key design choices:

**1. Two-score output instead of binary verdict.** TrialGPT separates "is this trial about the right disease?" (Relevance) from "does the patient strictly meet every criterion?" (Eligibility). A trial can be highly relevant (R=90) but have uncertain eligibility (E=30). Our approach collapses these into one binary: ELIGIBLE or not.

**2. Administrative criteria are assumed met.** In the aggregation step, TrialGPT literally injects: `"The patient will provide informed consent, and will comply with the trial protocol without any practical issues."` This bypasses the administrative criteria (consent, willingness, compliance) that push our system to UNCERTAIN.

**3. Soft scoring instead of hard failures.** TrialGPT's eligibility score is a continuous range (-R to R), not a hard pass/fail. A trial with 8 inclusion criteria met and 1 unknown doesn't fail — it gets a score like E=60. Our system: any DATA_MISSING on exclusion → UNCERTAIN. Any CONFIRMED_FAILED → INELIGIBLE. One criterion kills the whole trial.

### What the SIGIR "eligible" label actually means

The SIGIR qrel labels are **clinical relevance judgments**, not strict criterion-by-criterion compliance:
- **Eligible (2):** "a clinician would consider this trial for this patient" — disease relevance + broadly plausible eligibility
- **Excluded (1):** "the trial is relevant but the patient clearly doesn't qualify"
- **Not relevant (0):** "the trial is about a different disease entirely"

This explains why expert-labeled "eligible" trials fail our criterion-level evaluation. Examples from patient sigir-20141 (chest pain, suspected ACS):

| Trial | Why expert said ELIGIBLE | Why fixE said INELIGIBLE/UNCERTAIN |
|-------|------------------------|-----------------------------------|
| NCT00143195 (Angina study) | Patient has angina symptoms | "Outpatient setting" — patient is in ER |
| NCT00005485 (Jackson Heart Study) | African-American with CV risk | "Residents of Jackson, Mississippi" — not in note |
| NCT00683813 (Cardiac rehab) | IHD patient | "Regular Internet access" — not in note |
| NCT00952744 (Copeptin biomarker) | ACS presentation | "Unable to provide consent" exclusion → DATA_MISSING |

The experts applied **clinical judgment**: "would a doctor suggest this trial?" Our system applied **strict logic**: "does the patient pass every stated criterion?" Both are valid — for different use cases.

### Why TrialGPT's recall is also below 50%

Even with the permissive absence rule and soft scoring, TrialGPT's published recall isn't high. The 87.3% criterion-level accuracy translates to much lower trial-level recall because:

1. **Error compounding.** A trial with 15 criteria and 87% per-criterion accuracy has only ~13% chance of getting ALL criteria right (0.87^15 ≈ 0.13). One wrong criterion can flip the trial-level verdict.

2. **The aggregation helps but doesn't fully compensate.** The R/E scoring smooths per-criterion errors, but a strong "not included" on a key inclusion criterion still drives E negative.

3. **Some eligible trials have criteria the patient genuinely can't meet from the note alone.** Location, insurance, willingness to comply — information that doesn't appear in clinical notes.

---

## The Gap Between Criterion-Level and Trial-Level Accuracy

This is the core insight from the benchmark:

| Level | What's measured | Our performance | TrialGPT | Expert |
|-------|----------------|----------------|----------|--------|
| Criterion-level | % of individual criteria correctly assessed | Not yet measured | 87.3% | 88.7-90.0% |
| Trial-level (binary) | % of trials correctly labeled eligible/not | 83.3% (fixE) | Not directly reported | Varies |
| Trial-level (eligible recall) | % of eligible trials found | 11.5-42.3% | Not separately reported | — |

**Criterion-level accuracy and trial-level recall are different problems.** You can have 90% criterion-level accuracy and still miss most eligible trials, because errors compound and one wrong criterion is enough to flip a verdict.

The solutions are different:
- **Criterion-level accuracy** → better prompts, better extraction
- **Trial-level recall** → better aggregation logic (soft scoring, criterion weighting, administrative criteria handling)

---

## Improvement Directions

### Direction 1: Add aggregation step (TrialGPT-style)

After per-criterion evaluation, add a second LLM call that:
- Sees all per-criterion results + trial summary
- Outputs a relevance score (0-100) and eligibility score (-R to R)
- Assumes administrative criteria are met
- Weights clinical criteria higher than administrative/logistical ones

**Expected impact:** Should significantly improve recall. TrialGPT's aggregation is what turns per-criterion results into usable trial-level scores.

**Risk:** Adds cost (one more LLM call per trial) and reintroduces LLM judgment at the trial level.

### Direction 2: Criterion classification before evaluation

Not all criteria are equal. Classify criteria into:
- **Clinical** (disease type, biomarkers, prior treatment): must evaluate strictly
- **Administrative** (consent, compliance, willingness): assume met
- **Logistical** (location, internet access, travel): evaluate but don't hard-fail
- **Lab/vital** (organ function, blood counts): DATA_MISSING unless in note

Then apply different failure rules per category. Clinical CONFIRMED_FAILED → INELIGIBLE. Administrative DATA_MISSING → assume met. Logistical DATA_MISSING → flag but don't block.

**Expected impact:** Directly addresses the administrative criteria problem. Lower cost than full aggregation (classification can be done in the same per-criterion call).

**Risk:** Classification itself might be error-prone.

### Direction 3: Soft scoring in code (no extra LLM call)

Instead of binary verdict, compute a score from per-criterion results:
```
score = sum(weights[criterion_type] * result_value for each criterion)
where: CONFIRMED_MET = +1, DATA_MISSING = 0, CONFIRMED_FAILED = -1
and: clinical criteria weight > administrative > logistical
```

Threshold the score for the final verdict: score > X → ELIGIBLE, score < Y → INELIGIBLE, else UNCERTAIN.

**Expected impact:** Moderate improvement. Avoids one-criterion-kills-all but needs good weighting.

**Risk:** Weights are hard to calibrate without training data.

### Direction 4: Improve criterion-level accuracy first

Before changing aggregation, measure our actual criterion-level accuracy against TrialGPT's 87.3% benchmark. If we're significantly below, fix that first — better per-criterion accuracy makes aggregation easier.

**Expected impact:** Foundational. If criterion accuracy is already ~87%, aggregation is the bottleneck. If it's much lower, fix criteria first.

### Recommended order

1. **Measure criterion-level accuracy** (Direction 4) — establishes baseline, cheap
2. **Add criterion classification** (Direction 2) — targeted fix for the biggest recall killer (administrative criteria), no extra LLM call
3. **Add aggregation step** (Direction 1) — if classification isn't enough, add soft scoring
4. **Soft code scoring** (Direction 3) — alternative to Direction 1 if LLM cost is a concern

---

## Next Steps

1. Measure criterion-level accuracy on the 3-patient sample
2. Prototype criterion classification (clinical vs administrative vs logistical)
3. Re-evaluate recall after classification
4. Full SIGIR run only after recall improves on the 3-patient sample

---

*Benchmark run June 24, 2026. 3 of 58 SIGIR patients. Key finding: the recall gap is caused by hard binary verdicts on a task that requires soft relevance scoring. TrialGPT's three-step architecture (matching → aggregation → ranking) handles this; our single-verdict approach does not.*
