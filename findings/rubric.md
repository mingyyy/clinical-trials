# Evaluation Rubric — Clinical Trial Matching Comparison

Locked: Day 1. Do not modify after frameworks begin running.

---

## Scoring Dimensions (total: 100 points)

| Dimension | Weight | What it measures |
|-----------|--------|-----------------|
| Recall | 30% | Did the framework surface the trials that matter? Missing a truly eligible trial is the worst failure mode in this domain. |
| Precision | 25% | Of trials marked eligible, how many actually are? False positives waste clinician time. |
| Criteria accuracy | 20% | Does the framework correctly interpret specific eligibility clauses (biomarker requirements, prior line counts, exclusions like brain mets)? |
| Explanation quality | 15% | Can a clinician understand why a trial was included or excluded? Is the reasoning traceable? |
| Cost efficiency | 10% | LLM calls × token count. Proxy for operational cost at scale. Lower is better per correct match. |

---

## Ground Truth Construction

Ground truth is hand-labeled per patient profile. For each (patient, trial) pair:
- **Eligible**: patient clearly meets inclusion criteria and has no exclusion hits
- **Ineligible**: at least one hard exclusion applies
- **Ambiguous**: eligibility depends on data not in the profile (lab values, imaging, etc.)

Ambiguous pairs are excluded from precision/recall scoring. They may appear in qualitative notes.

Ground truth labeled by: [human reviewer — complete before Day 2]

---

## Dimension Definitions

### Recall (30 pts)
```
recall = true_positives / (true_positives + false_negatives)
```
A false negative = eligible trial not returned or marked ineligible.
Scored per patient, averaged across P001–P005.

### Precision (25 pts)
```
precision = true_positives / (true_positives + false_positives)
```
A false positive = ineligible trial marked eligible.
Scored per patient, averaged across P001–P005.

### Criteria Accuracy (20 pts)

Assessed on the five designed failure modes:

| Test case | Profile | Failure mode tested |
|-----------|---------|---------------------|
| HER2+ baseline | P001 | Correct positive matching |
| BRCA1 + TNBC | P002 | Biomarker-conditional eligibility |
| Oral preference | P003 | Preference reasoning (route of administration) |
| Brain mets hard exclusion | P004 | Explicit exclusion criterion detection |
| Prior line count | P005 | Counting prior treatment lines correctly |

Score: 0/1/2 per case × 2 (max 20). Partial credit if reasoning is partially correct.

### Explanation Quality (15 pts)

Each match explanation rated on a 3-point scale:
- 0 — No explanation, or explanation does not reference specific criteria
- 1 — References criteria but vaguely ("patient may not meet age requirement")
- 2 — Cites specific criterion text, states why it applies or doesn't apply

Score: mean across all matches × (15 / 2).

### Cost Efficiency (10 pts)

```
cost_score = 10 × (1 - (framework_cost / max_cost))
```
where cost = total_tokens × 0.000003 (proxy at $3/M tokens, Claude Sonnet-class).
Max cost = highest cost across the four frameworks.
A framework that uses 50% fewer tokens than the worst gets 5 extra points.

---

## Notes on Scoring

- **Hard filter is not scored** — it is shared infrastructure, not a framework capability.
- **ml-intern and OpenHands are not scored** — they are paradigm observations. Qualitative notes only.
- **Elicit is not scored** — prior question, not a framework.
- **Ties** are noted but not broken artificially. The goal is insight, not a leaderboard.
- **Missing output files** score zero for all dimensions for that patient.

---

## Score Sheet Template

| Framework | P001 | P002 | P003 | P004 | P005 | Recall | Precision | Criteria | Explanation | Cost | Total |
|-----------|------|------|------|------|------|--------|-----------|----------|-------------|------|-------|
| LangGraph | | | | | | | | | | | |
| PydanticAI | | | | | | | | | | | |
| smolagents | | | | | | | | | | | |
| Claude Direct | | | | | | | | | | | |

Populate in `evaluate.py` on Day 2.
