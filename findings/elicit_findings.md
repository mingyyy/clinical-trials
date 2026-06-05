# Elicit Findings — Day 1, June 2 2026

**Tool:** Elicit (elicit.com) — AI research assistant with ClinicalTrials.gov integration
**Session date:** 2026-06-02
**Cost:** $0 (free tier)
**Raw outputs:** `outputs/04_agents/elicit/elicit_observation.md`, `outputs/04_agents/elicit/elicit_p001_trial_ranking.md`

---

## Purpose

Run Elicit before building any framework to answer the prior question: **does a specialist tool already solve this problem?** If yes, the framework comparison becomes a build-vs-buy question, not a which-framework question.

---

## Session A — Literature Query

Query: AI systems for matching patients to clinical trial eligibility criteria.

Elicit searched academic literature (4 searches, max 10 results each). It did not perform patient-to-trial matching — it surfaced papers describing AI systems built for this purpose.

Key papers surfaced:

| Study | Task | Result |
|-------|------|--------|
| Beck et al. | Breast cancer record screening (997 records, 3 trials) | 81–96% agreement with manual review; 90-patient screening cut from 110 min to 24 min |
| Meystre et al. | EHR note extraction + eligibility decisions | Precision 89.7%, recall 90.9%; per-trial AUC 75.5–89.8% |
| Kascovich et al. | Pediatric leukemia patient-centric matcher | 75% pooled accuracy; ~4 seconds vs several hours |

Pattern: solid prototypes in controlled settings, few production deployments. No off-the-shelf tool surfaced by the literature.

---

## Session B — Direct Patient Matching (P001)

Query: 52yo female, HER2+ stage II breast cancer, post-trastuzumab, NYC area.

Elicit searched ClinicalTrials.gov and returned 5 ranked results with reasoning. Elicit's caveat: "I couldn't fully verify city-level site lists from the registry snippets, so this is a biology/eligibility ranking first."

| Rank | NCT ID | Trial | Elicit's assessment |
|------|--------|-------|---------------------|
| 1 | NCT05232916 | GLSI-100 (HER2/neu peptide vaccine) | Best biological fit: enrolls HER2/neu+ who completed neoadjuvant + adjuvant standard-of-care. Matches treatment history. |
| 2 | NCT07192432 | SENTRY-HER2 gene therapy | Plausible — early-stage HER2+ at risk of relapse after prior systemic treatment. Only 6 US sites; NYC site unverified. |
| 3 | NCT07612215 | elacestrant + trastuzumab/pertuzumab | NYC-adjacent (NYU Langone sponsor). Weaker biological fit — framed around triple-positive + ESR1 mutation. |
| 4 | NCT06876714 | ShortStop-HER2 | Design mismatch: requires pCR after neoadjuvant chemo, then randomizes HER2 therapy duration. Doesn't match. |
| 5 | NCT06058377 | durvalumab + chemo | Neoadjuvant initiation trial — patient is post-treatment. Wrong phase. |

**Top result validation:** NCT05232916 (GLSI-100 at Columbia, 0.1 miles from NYC) was independently confirmed as the top match by LangGraph, smolagents, and ml-intern — four approaches built over four days converged on the same trial Elicit surfaced first.

---

## What Elicit Can and Cannot Do

**Can:**
- Search ClinicalTrials.gov and retrieve recruiting trials
- Rank by biological fit with specific reasoning per trial
- Cite trial design features and explain mismatches
- Return results in under a minute at no cost

**Cannot:**
- Verify geographic site availability at city level
- Apply full inclusion/exclusion criteria systematically (biology-only ranking)
- Guarantee complete trial recall (may miss relevant trials)
- Integrate proprietary patient data or EHR systems

---

---

## Session C — Direct Patient Matching (P004)

**Date:** 2026-06-05
**Query:** 55yo male, metastatic melanoma with brain metastases, BRAF V600E mutant, prior ipilimumab + nivolumab, ECOG 2, Seattle WA.
**Raw output:** `outputs/04_agents/elicit/elicit_p004_trial_ranking.md`

Elicit surfaced one clearly on-target recruiting trial:

| Rank | NCT ID | Trial | Elicit's assessment |
|------|--------|-------|---------------------|
| 1 | NCT04511013 | Encorafenib + Binimetinib + Nivolumab vs. Ipilimumab + Nivolumab | Best biologic match: brain mets, BRAF V600E, recruiting, phase 2. Prior ipi+nivo **not resolved as definitive exclusion** — "because the protocol's own control arm is ipi+nivo, this does not look like a trivial adjuvant/metastatic misclassification." |

Elicit's caveats: could not confirm Seattle-area site (331 US sites listed, no city-level breakdown); ECOG 2 eligibility not resolved from snippet; search scope narrower than structured frameworks.

**The critical observation:** Elicit did not infer that prior ipi+nivo is a definitive exclusion — the only system in this study that correctly left the question open. Its reasoning was sound: the trial's control arm *administers* ipi+nivo, which signals the protocol treats prior ipi+nivo history as nuanced. This is functionally equivalent to UNCERTAIN on the NCT04511013 assessment.

For comparison:
- Four structured frameworks: INELIGIBLE — all acknowledged the ambiguity in `uncertain_items`, then overrode it with a clinical inference
- ml-intern: ELIGIBLE — hallucinated that the comparator arm signals the trial accepts prior-treated patients
- OpenHands: INELIGIBLE — stated the trial "excludes brain mets" (factually wrong; the trial specifically enrolls brain mets patients)
- **Elicit: Effectively UNCERTAIN** — correct trial, prior treatment question unresolved, flagged for follow-up

---

## Finding

**The prior question answer is nuanced.** Elicit is a shortlist generator, not a replacement for systematic eligibility screening. It covers roughly 60% of the problem (biological fit, initial matching) and stops before the hard part (full exclusion criteria, geographic verification, data gaps).

The P004 session adds a sharper finding: **on the hardest case in this study, Elicit's biology-first approach with epistemic humility outperformed all four structured frameworks.** The frameworks over-inferred from context and returned confident wrong verdicts. Elicit surfaced the right trial and explicitly left the ambiguous criterion unresolved.

For a pharma client without proprietary data integration requirements, Elicit should be evaluated before committing to a custom build. For a client needing auditable per-criterion verdicts, EHR integration, or systematic recall across all recruiting trials, a custom pipeline is necessary — and the framework comparison in this study applies.

---

*Written June 5, 2026. Source: `outputs/04_agents/elicit/`.*
