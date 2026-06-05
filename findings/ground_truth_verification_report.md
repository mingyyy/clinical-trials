# Ground Truth Verification Report

*Generated 2026-06-06 — independent LLM agent vs hand-labeled ground truth*

## Summary

| | Count |
|--|--|
| Total assessments | 182 |
| Agent errors | 0 |
| Agreement | 148 / 182 = 81.3% |
| Disagreements | 34 |

## Ground Truth Data Issues (duplicates)

- P001/NCT06625775: appears in both 'ineligible' and 'ambiguous' — will use 'ambiguous'
- P001/NCT05514717: appears in both 'ineligible' and 'ambiguous' — will use 'ambiguous'
- P003/NCT07214532: appears in both 'ineligible' and 'ambiguous' — will use 'ambiguous'
- P005/NCT07029399: appears in both 'ineligible' and 'ambiguous' — will use 'ambiguous'

## Disagreements by Patient

### P001 (11 disagreements)

| NCT ID | Trial | GT | Agent | Conf | Reason |
|--------|-------|----|----|------|--------|
| NCT04683679 | A Study of Radiation Therapy With Pembrolizumab an... | UNCERTAIN | INELIGIBLE | 0.95 | The patient has HER2-positive (HER2+) breast cancer, but the trial requires eith... |
| NCT05514717 | A Study of XMT-2056 in Advanced/Recurrent Solid Tu... | UNCERTAIN | INELIGIBLE | 0.92 | The trial requires recurrent or metastatic solid tumors, but the patient has sta... |
| NCT05579366 | Rinatabart Sesutecan (Rina-S, PRO1184, GEN1184) fo... | UNCERTAIN | INELIGIBLE | 0.92 | The patient has HER2-positive, ER+/PR- breast cancer (non-metastatic, stage II),... |
| NCT05827081 | Phase IIIb Study of Ribociclib + ET in Early Breas... | UNCERTAIN | INELIGIBLE | 0.92 | The patient is HER2-positive, but the trial requires HER2-negative breast cancer... |
| NCT05879926 | Evaluating the Addition of Adjuvant Chemotherapy t... | UNCERTAIN | INELIGIBLE | 0.95 | The trial requires HER2-negative tumor as a mandatory inclusion criterion, but t... |
| NCT06106477 | Impact of Intermittent Fasting on Biomarkers of In... | UNCERTAIN | INELIGIBLE | 0.95 | The patient has HER2-positive breast cancer, which directly fails the mandatory ... |
| NCT06157892 | A Study of Disitamab Vedotin With Other Anticancer... | UNCERTAIN | INELIGIBLE | 0.85 | The patient has stage II non-metastatic breast cancer, but Cohort B (HER2+ Breas... |
| NCT06253871 | A Phase 1/1b Study of IAM1363 in HER2 Cancers | UNCERTAIN | INELIGIBLE | 0.85 | The trial requires relapsed/refractory HER2-altered malignancy with progression ... |
| NCT06625775 | Open-Label Study of BBO-10203 in Subjects With Adv... | UNCERTAIN | INELIGIBLE | 0.95 | The patient has stage II non-metastatic HER2-positive breast cancer, which does ... |
| NCT07211178 | Evaluating Minimal Residual Disease (MRD) Through ... | INELIGIBLE | ELIGIBLE | 0.82 | The patient is HER2+ (ER+, PR-), Stage II breast cancer treated with curative in... |
| NCT07214532 | Signatera-Guided CDK4/6 Inhibitor Therapy in Breas... | UNCERTAIN | INELIGIBLE | 0.95 | The patient has HER2-positive breast cancer, but inclusion criterion #6 requires... |

### P002 (13 disagreements)

| NCT ID | Trial | GT | Agent | Conf | Reason |
|--------|-------|----|----|------|--------|
| NCT04030507 | Screening Magnetic Resonance Imaging of the Brain ... | UNCERTAIN | INELIGIBLE | 0.90 | The patient has stage III (locally advanced, non-metastatic) TNBC with prior neo... |
| NCT04683679 | A Study of Radiation Therapy With Pembrolizumab an... | UNCERTAIN | INELIGIBLE | 0.85 | The trial requires metastatic or recurrent TNBC, but the patient has stage III (... |
| NCT05150691 | A Phase 1/2a Study of DB-1303/BNT323 in Advanced/M... | UNCERTAIN | INELIGIBLE | 0.85 | The primary inclusion criterion requires HER2-positive or HER2-expressing tumor ... |
| NCT05327608 | Neoadjuvant Breast Cancer Time Restricted Eating | INELIGIBLE | UNCERTAIN | 0.55 | The patient meets several key inclusion criteria (age, ECOG PS 1, TNBC/HER2-nega... |
| NCT05523947 | Clinical Trial of YH32367 in Patients With HER2 Po... | UNCERTAIN | INELIGIBLE | 0.97 | The trial requires HER2-positive tumors for all cohorts, but the patient's tumor... |
| NCT05573126 | Phase 1/2 Study to Evaluate EP0062 as Monotherapy ... | UNCERTAIN | INELIGIBLE | 0.97 | The patient has Triple-negative breast cancer (ER-, PR-, HER2-), which directly ... |
| NCT05579366 | Rinatabart Sesutecan (Rina-S, PRO1184, GEN1184) fo... | UNCERTAIN | INELIGIBLE | 0.75 | The patient has stage III (non-metastatic, resectable) TNBC, whereas Part A requ... |
| NCT05950945 | Trastuzumab Deruxtecan (T-DXd) in Patients Who Hav... | UNCERTAIN | INELIGIBLE | 0.90 | The trial requires unresectable and/or metastatic breast cancer with at least on... |
| NCT06157892 | A Study of Disitamab Vedotin With Other Anticancer... | UNCERTAIN | INELIGIBLE | 0.85 | The patient is HER2-negative (ER- PR- HER2-), which does not meet the HER2-low (... |
| NCT06526819 | SMP-3124LP in Adults With Advanced Solid Tumors | INELIGIBLE | UNCERTAIN | 0.55 | The patient has TNBC (ER- PR- HER2-) meeting the cancer type inclusion criterion... |
| NCT06625775 | Open-Label Study of BBO-10203 in Subjects With Adv... | UNCERTAIN | INELIGIBLE | 0.95 | The patient has triple-negative breast cancer (ER-, PR-, HER2-), which does not ... |
| NCT07029399 | A Study With NKT5097 for Adults With Advanced/Meta... | UNCERTAIN | INELIGIBLE | 0.85 | The patient has Stage III (not advanced unresectable or metastatic) TNBC, which ... |
| NCT07192432 | Gene Therapy for HER-Positive Cancer (SENTRY-HER2) | UNCERTAIN | INELIGIBLE | 0.95 | The trial requires HER2-positive solid tumor as a mandatory inclusion criterion,... |

### P003 (3 disagreements)

| NCT ID | Trial | GT | Agent | Conf | Reason |
|--------|-------|----|----|------|--------|
| NCT05827081 | Phase IIIb Study of Ribociclib + ET in Early Breas... | UNCERTAIN | INELIGIBLE | 0.82 | The patient completed 5 years of tamoxifen, meaning her prior ET start date was ... |
| NCT06106477 | Impact of Intermittent Fasting on Biomarkers of In... | UNCERTAIN | INELIGIBLE | 0.85 | Inclusion criterion 5 requires the subject to start the intermittent fasting int... |
| NCT07214532 | Signatera-Guided CDK4/6 Inhibitor Therapy in Breas... | UNCERTAIN | INELIGIBLE | 0.85 | The patient has completed 5 years of tamoxifen and is currently NED (no evidence... |

### P004 (1 disagreements)

| NCT ID | Trial | GT | Agent | Conf | Reason |
|--------|-------|----|----|------|--------|
| NCT03452774 | SYNERGY-AI: Artificial Intelligence Based Precisio... | UNCERTAIN | ELIGIBLE | 0.82 | Patient meets key inclusion criteria: solid malignancy (metastatic melanoma), BR... |

### P005 (6 disagreements)

| NCT ID | Trial | GT | Agent | Conf | Reason |
|--------|-------|----|----|------|--------|
| NCT04030507 | Screening Magnetic Resonance Imaging of the Brain ... | UNCERTAIN | INELIGIBLE | 0.85 | The patient has received 3 prior HER2-targeted lines of therapy (trastuzumab, pe... |
| NCT04585750 | The Evaluation of PC14586 in Patients With Advance... | UNCERTAIN | INELIGIBLE | 0.95 | The trial's key inclusion criterion requires a TP53 Y220C mutation in the patien... |
| NCT05523947 | Clinical Trial of YH32367 in Patients With HER2 Po... | UNCERTAIN | INELIGIBLE | 0.85 | The Dose Expansion Part explicitly excludes breast cancer patients from Cohort 2... |
| NCT05573126 | Phase 1/2 Study to Evaluate EP0062 as Monotherapy ... | UNCERTAIN | INELIGIBLE | 0.97 | The trial requires HER2-negative and ER-positive breast cancer (Inclusion Criter... |
| NCT05579366 | Rinatabart Sesutecan (Rina-S, PRO1184, GEN1184) fo... | UNCERTAIN | INELIGIBLE | 0.92 | The patient has HER2-positive breast cancer (ER-), which does not match any of t... |
| NCT05894239 | A Study to Evaluate the Efficacy and Safety of Ina... | INELIGIBLE | UNCERTAIN | 0.55 | The patient meets ECOG PS 1 and HER2+ criteria, but eligibility is uncertain bec... |

## Disagreement Type Breakdown

| GT → Agent | Count |
|-----------|-------|
| GT=UNCERTAIN Agent=INELIGIBLE | 29 |
| GT=INELIGIBLE Agent=UNCERTAIN | 3 |
| GT=INELIGIBLE Agent=ELIGIBLE | 1 |
| GT=UNCERTAIN Agent=ELIGIBLE | 1 |

## Interpretation

Disagreements do not automatically mean the ground truth is wrong — the LLM agent can also be wrong. Each disagreement is a flag for human review. High-priority flags: cases where the agent says INELIGIBLE with confidence ≥ 0.85 but GT says UNCERTAIN or ELIGIBLE.

### High-confidence disagreements (agent confidence ≥ 0.85)

| Patient | NCT ID | GT | Agent | Conf | Reason |
|---------|--------|----|----|------|--------|
| P002 | NCT05573126 | UNCERTAIN | INELIGIBLE | 0.97 | The patient has Triple-negative breast cancer (ER-, PR-, HER2-), which directly fails the mandatory ... |
| P002 | NCT05523947 | UNCERTAIN | INELIGIBLE | 0.97 | The trial requires HER2-positive tumors for all cohorts, but the patient's tumor is HER2-negative (E... |
| P005 | NCT05573126 | UNCERTAIN | INELIGIBLE | 0.97 | The trial requires HER2-negative and ER-positive breast cancer (Inclusion Criteria 4 and 5), but the... |
| P001 | NCT05879926 | UNCERTAIN | INELIGIBLE | 0.95 | The trial requires HER2-negative tumor as a mandatory inclusion criterion, but the patient is diagno... |
| P001 | NCT04683679 | UNCERTAIN | INELIGIBLE | 0.95 | The patient has HER2-positive (HER2+) breast cancer, but the trial requires either Triple-Negative B... |
| P001 | NCT06625775 | UNCERTAIN | INELIGIBLE | 0.95 | The patient has stage II non-metastatic HER2-positive breast cancer, which does not meet the inclusi... |
| P001 | NCT07214532 | UNCERTAIN | INELIGIBLE | 0.95 | The patient has HER2-positive breast cancer, but inclusion criterion #6 requires HER2-negative breas... |
| P001 | NCT06106477 | UNCERTAIN | INELIGIBLE | 0.95 | The patient has HER2-positive breast cancer, which directly fails the mandatory inclusion criterion ... |
| P002 | NCT06625775 | UNCERTAIN | INELIGIBLE | 0.95 | The patient has triple-negative breast cancer (ER-, PR-, HER2-), which does not match any of the req... |
| P002 | NCT07192432 | UNCERTAIN | INELIGIBLE | 0.95 | The trial requires HER2-positive solid tumor as a mandatory inclusion criterion, but the patient is ... |
| P005 | NCT04585750 | UNCERTAIN | INELIGIBLE | 0.95 | The trial's key inclusion criterion requires a TP53 Y220C mutation in the patient's tumor, which is ... |
| P001 | NCT05827081 | UNCERTAIN | INELIGIBLE | 0.92 | The patient is HER2-positive, but the trial requires HER2-negative breast cancer (defined as IHC 0, ... |
| P001 | NCT05579366 | UNCERTAIN | INELIGIBLE | 0.92 | The patient has HER2-positive, ER+/PR- breast cancer (non-metastatic, stage II), which does not meet... |
| P001 | NCT05514717 | UNCERTAIN | INELIGIBLE | 0.92 | The trial requires recurrent or metastatic solid tumors, but the patient has stage II non-metastatic... |
| P005 | NCT05579366 | UNCERTAIN | INELIGIBLE | 0.92 | The patient has HER2-positive breast cancer (ER-), which does not match any of the breast cancer sub... |
| P002 | NCT04030507 | UNCERTAIN | INELIGIBLE | 0.90 | The patient has stage III (locally advanced, non-metastatic) TNBC with prior neoadjuvant chemotherap... |
| P002 | NCT05950945 | UNCERTAIN | INELIGIBLE | 0.90 | The trial requires unresectable and/or metastatic breast cancer with at least one prior line of ther... |
| P001 | NCT06253871 | UNCERTAIN | INELIGIBLE | 0.85 | The trial requires relapsed/refractory HER2-altered malignancy with progression of disease after las... |
| P001 | NCT06157892 | UNCERTAIN | INELIGIBLE | 0.85 | The patient has stage II non-metastatic breast cancer, but Cohort B (HER2+ Breast Cancer) requires l... |
| P002 | NCT07029399 | UNCERTAIN | INELIGIBLE | 0.85 | The patient has Stage III (not advanced unresectable or metastatic) TNBC, which does not meet the in... |
| P002 | NCT04683679 | UNCERTAIN | INELIGIBLE | 0.85 | The trial requires metastatic or recurrent TNBC, but the patient has stage III (locally advanced, no... |
| P002 | NCT06157892 | UNCERTAIN | INELIGIBLE | 0.85 | The patient is HER2-negative (ER- PR- HER2-), which does not meet the HER2-low (IHC 1+ or IHC 2+/ISH... |
| P002 | NCT05150691 | UNCERTAIN | INELIGIBLE | 0.85 | The primary inclusion criterion requires HER2-positive or HER2-expressing tumor (except cohort 2h wh... |
| P003 | NCT06106477 | UNCERTAIN | INELIGIBLE | 0.85 | Inclusion criterion 5 requires the subject to start the intermittent fasting intervention within thr... |
| P003 | NCT07214532 | UNCERTAIN | INELIGIBLE | 0.85 | The patient has completed 5 years of tamoxifen and is currently NED (no evidence of disease) post-ma... |
| P005 | NCT04030507 | UNCERTAIN | INELIGIBLE | 0.85 | The patient has received 3 prior HER2-targeted lines of therapy (trastuzumab, pertuzumab, T-DM1), in... |
| P005 | NCT05523947 | UNCERTAIN | INELIGIBLE | 0.85 | The Dose Expansion Part explicitly excludes breast cancer patients from Cohort 2 ('metastatic solid ... |