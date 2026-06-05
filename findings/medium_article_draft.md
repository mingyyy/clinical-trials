# I Built the Same AI System Four Different Ways. The Framework Didn't Matter. Everything Else Did.

*A clinical trial matching experiment — four frameworks, five patients, one model, and the findings that had nothing to do with any of them.*

---

Three cancer patients are waiting. A 52-year-old woman in New York with HER2-positive breast cancer — a subtype driven by overexpression of a protein receptor, eligible for a specific class of targeted therapies. A 34-year-old woman in Los Angeles with triple-negative breast cancer, meaning her tumor lacks the three most common drug-targetable receptors and responds to very few approved treatments. A 55-year-old man in Seattle with metastatic melanoma — cancer that has spread from its original site — and tumors in the brain.

A pharma client wants to build an AI system to match patients like these to clinical trials. You have a week to figure out which framework they should use. You pick four — LangGraph, PydanticAI, smolagents, and a raw Claude API baseline — and run the same task through each: same model, same patients, same data source.

You expect a clean ranking: framework A wins on quality, B wins on cost, here is when to use each.

That ranking never arrived. What arrived instead was more useful.

---

## The Setup

Five fictional but clinically realistic patient profiles matched against recruiting trials pulled live from ClinicalTrials.gov. The same model throughout: claude-sonnet-4-6. The same three-step task: fetch trials within 100 miles, filter, assess eligibility.

Each framework ran across all five patients. Two autonomous agents — ml-intern and OpenHands — ran as additional data points outside the controlled comparison. A specialist tool called Elicit ran first as a "prior question" check: does a purpose-built tool already solve this? I scored all outputs against the same rubric.

The hypothesis going in: structured frameworks (LangGraph, PydanticAI) would produce more accurate verdicts. The code-generation approach (smolagents) would be brittle. The raw API baseline would be fast and cheap but lower quality.

None of that held.

---

## Finding 1: The Frameworks Were Statistically Flat

Explanation quality across all four frameworks: 2.00/2.0 for LangGraph and PydanticAI, 1.95 for smolagents, 1.90 for the raw Claude baseline. Zero parse errors across 662 LLM calls in the controlled rerun. The verdict distributions were nearly identical.

This shouldn't have been surprising — but it was, going in. The LLM does the reasoning. The framework is scaffolding around it. When you give four different wrappers the same model and the same prompt, the output is roughly equivalent regardless of the wrapper.

The real differences were operational. LangGraph's named nodes made a prompt bug traceable in five minutes. PydanticAI's schema enforcement caught zero validation errors — but you define the contract once, and it runs at zero marginal cost. smolagents self-corrected from two failures without human intervention. The raw baseline had no recovery mechanism — and nothing to recover from.

The framework choice is real. It's just not a quality choice. It's a maintainability and reliability choice.

---

## Finding 2: One Sentence Changed Everything

Before the full comparison, Patient 1 — a Stage II HER2+ breast cancer patient — returned two ELIGIBLE verdicts. (Stage II means localized disease; metastatic means cancer that has spread. This distinction matters for what follows.) Both verdicts were false positives: trials requiring metastatic disease that a localized patient cannot access.

The problem: the system prompt told the model to return INELIGIBLE only when there was "positive evidence" of failure. When eligibility data was simply absent, it was defaulting to ELIGIBLE — the worst possible failure mode in a clinical context. A patient gets referred to a trial they cannot join.

I added one sentence to the system prompt:

*"Absence of information is NOT evidence of ineligibility."*

After the change: 0 ELIGIBLE, 3 UNCERTAIN. The patient was correctly classified as ambiguous — needing further review — rather than incorrectly cleared. Every framework's output changed. No framework produced or prevented that insight. It came from understanding the clinical problem.

The most important technical decision in the entire project was a sentence.

---

## Finding 3: The Notes Field Is a Contamination Risk

The prompt was fixed, the obvious failure mode was closed — and then a subtler one appeared.

Each patient profile had a `notes` field. For Patient 1: *"Baseline case. Should match several HER2+ trials in NYC area."* For Patient 4 (the melanoma patient): *"Hard exclusion test. Brain mets is a common exclusion criterion. Most trials should be excluded."*

I suspected these researcher notes were contaminating the LLM's verdicts. To test this, I ran three variants of the same patient × trial assessment:

- **Variant A:** current setup, researcher framing at end of patient context
- **Variant B:** notes stripped entirely, clinical data only
- **Variant C:** researcher framing moved to the top, before all clinical data

For Patient 4, the result was definitive: all three variants returned INELIGIBLE at nearly identical confidence (0.90–0.92). The notes made no difference. The model reasoned from the clinical facts — a metastatic melanoma diagnosis plus prior ipilimumab and nivolumab (two immunotherapy drugs commonly combined as first-line treatment for advanced melanoma) — and inferred the treatment context from the data itself. Strong clinical signals made the framing irrelevant.

So I ran the same test on Patient 1's six borderline cases — the trials that came back UNCERTAIN in the LangGraph rerun (the expanded 100-mile run that assessed 73 trials, vs 3 UNCERTAIN in the original 50-trial run). Genuinely uncertain: low-confidence assessments where the patient might qualify but data was missing.

The results were different. And stranger.

Two of six trials changed verdict — in **opposite directions**:

| Trial | B: no notes | A: notes at end | C: notes at top |
|-------|-------------|-----------------|-----------------|
| NCT07211178 | UNCERTAIN 0.72 | ELIGIBLE 0.82 | ELIGIBLE 0.82 |
| NCT06220214 | UNCERTAIN 0.45 | INELIGIBLE 0.80 | INELIGIBLE 0.85 |

For NCT07211178, the framing "should match several HER2+ trials" activated a permissive threshold. The model had identical missing data in both variants. Without notes, Variant B's explanation flagged that she might not have NED status — No Evidence of Disease, the clinical state where no active cancer is detectable — and returned UNCERTAIN. With notes, it found no exclusion criterion clearly triggered and returned ELIGIBLE. Same gap in the data, different threshold.

For NCT06220214 — a trial requiring patients who had not yet had surgery — the notes pushed the opposite direction. Without notes, the model hedged on the obvious mismatch (this patient has already had surgery). With notes, it committed: INELIGIBLE at 0.80. The framing prompted more decisive assessment, and the clear clinical mismatch resolved into a confident verdict.

Moving the notes from end to top made no difference. Variants A and C produced identical verdicts every time. Position didn't matter — content did.

**The finding across both tests:** notes contamination is not inflationary. Framing amplifies confidence in whichever direction the clinical evidence already leans. When there is no strong exclusion, notes pull toward ELIGIBLE. When there is a clear exclusion the model was hedging on, notes pull toward INELIGIBLE. The net result is fewer UNCERTAIN verdicts — fewer appropriate flags for human review — with nothing in the output to indicate this happened.

In production, the patient notes field will contain clinician framing. Case summaries. Embedded priors. Every one of those is a contamination source.

---

## Finding 4: "Absence of Information = UNCERTAIN" Is a Soft Rule, Not a Hard One

Explicit rules in LLM prompts have soft authority, not hard enforcement. Here is what that looks like in practice.

The system prompt said: *"Absence of information is NOT evidence of ineligibility. If a criterion is not mentioned in the profile, mark it in uncertain_items — do NOT treat it as failed."*

For Patient 4, the most clinically matched trial was NCT04511013: a Phase 2 study for BRAF V600E melanoma — a genetic subtype affecting roughly half of all melanomas — with brain metastases, at a Seattle site. The trial was designed for exactly this patient's profile. But it carried a key exclusion: *"Participants must not have received prior systemic therapy for metastatic disease."*

The patient's profile lists prior treatments as ipilimumab and nivolumab — but does not say whether those were given to treat active metastatic disease or earlier, as preventive treatment after surgery on a less advanced tumor. The LangGraph run acknowledged this gap explicitly in uncertain_items: *"Whether ipilimumab and nivolumab were given in the neoadjuvant/adjuvant setting vs. for metastatic disease."*

Then it returned INELIGIBLE anyway.

The model reasoned from clinical context: this drug combination is the standard first-line regimen for metastatic melanoma, and the patient has metastatic melanoma. Therefore the treatments were almost certainly given for metastatic disease. Confidence: 0.92.

That inference is clinically reasonable. But it is an inference, not a fact. These same drugs are also FDA-approved as adjuvant therapy — given after surgery to remove a less advanced tumor — and the patient could have received them in that setting and later relapsed. The profile doesn't say.

The model acknowledged the ambiguity in uncertain_items. Then it overrode its own uncertainty with a clinical inference and returned a confident verdict.

**The "absence of information" rule has an implicit activation threshold.** It fires when data is truly absent with no basis for inference. When the model can construct a plausible inference from surrounding context, the UNCERTAIN rule doesn't activate — the model concludes it knows enough to decide. The threshold is not configurable, not visible in the output, and not consistent across cases. You cannot see from the verdict whether the model inferred or confirmed.

---

## Finding 5: How Many Trials Share a Context Window Is an Architectural Decision — and Nobody Makes It Explicitly

The raw Claude baseline returned 7 ELIGIBLE verdicts for Patient 1. The per-trial frameworks (LangGraph, PydanticAI, smolagents) returned 1–2. Same model, same prompt, same trials, dramatically different verdict distribution.

The cause is not a framework property — it is how many trials share a single LLM context window.

Claude Direct bundles 10 trials into one LLM call. The model sees nine neighbours alongside each trial it is assessing. When eight of those neighbours are clearly ineligible, they anchor the comparison — borderline trials look more eligible by contrast, and the threshold shifts upward. This is not a bug; it is how relative comparison works.

The per-trial frameworks assess each trial in isolation against an abstract standard. No anchoring. More conservative.

The autonomous agent ml-intern fetches all trials within 100 miles — roughly 68–74 for Patient 1 — and loads them into a single context. Maximum comparative anchoring.

Three distinct patterns, all using the same model:

| Pattern | Trials per LLM call | ELIGIBLE (Patient 1) |
|---------|---------------------|----------------------|
| LangGraph / PydanticAI / smolagents | 1 | 1–2 |
| Claude Direct | 10 | 7 |
| ml-intern | all (~68–74) | 2* |

\* ml-intern returned 2 ELIGIBLE for Patient 1; 9 ELIGIBLE total across all 5 patients.

Any framework can implement any of these patterns — LangGraph with a different `BATCH_SIZE` would behave like Claude Direct. What matters is the explicit choice, and most teams making this decision don't realize they are making it.

A naming trap: both LangGraph and Claude Direct expose a variable called `BATCH_SIZE`. In Claude Direct it means trials bundled per LLM call. In LangGraph it means max concurrent parallel calls — each still assessing one trial. Same name, opposite semantics.

The choice determines the character of the system:

- **Per-trial:** conservative, auditable, reproducible. Best when an ELIGIBLE verdict triggers a costly downstream step.
- **Batch-N:** more inclusive, lower threshold. Best when missing eligible patients is the primary risk.
- **Batch-all:** maximum comparative reasoning. Not reproducible — two runs on the same data may produce different verdicts.

---

## Finding 6: Every System Got It Wrong — Except the One That Didn't Try to Decide

Both errors read as confident and clinical. Neither output contains a signal that anything is wrong. That is what makes this case worth examining carefully.

The autonomous agent ml-intern found 5 ELIGIBLE trials for Patient 4, including NCT04511013. The four structured frameworks found 0–1. For a while, this looked like the most important finding of the week: autonomous reasoning finding matches that isolated per-trial calls miss.

Reading ml-intern's actual output changed that conclusion.

Its criterion table for NCT04511013 included this assessment:

> *"Prior ipi + nivo (both arms permitted) — trial explicitly includes prior-treated patients in one arm"*

This is factually wrong. NCT04511013 has two arms: Arm A (Encorafenib + Binimetinib + Nivolumab) and Arm B (Ipilimumab + Nivolumab as comparator). Both arms enroll treatment-naive patients. The trial title says "vs. Ipilimumab + Nivolumab" — describing what the trial *administers* in one arm, not what prior treatment history it *accepts*. ml-intern read the treatment comparator as a permissive eligibility criterion. That is a hallucination.

So the systems arrived at three different wrong answers:

- **Four structured frameworks:** INELIGIBLE — acknowledged the ambiguity in their own output, then overrode it with a clinical inference
- **ml-intern:** ELIGIBLE — based on a hallucinated trial design feature
- **OpenHands:** INELIGIBLE — stated the trial "excludes brain mets," which is factually wrong; the trial specifically enrolls patients with brain metastases

The correct verdict, per the explicit prompt rule, is UNCERTAIN. The prior treatment setting — whether ipi+nivo was given in the adjuvant or metastatic context — is absent from the profile. The trial itself explicitly permits prior adjuvant ipi+nivo. Without knowing when those drugs were given, the eligibility question cannot be resolved.

One system handled this correctly. After the framework runs, Elicit was given the same P004 query. It surfaced NCT04511013 as its top match — and explicitly left the prior treatment question unresolved:

> *"Because the protocol's own control arm is ipi+nivo, this does not look like a trivial adjuvant/metastatic misclassification."*

Elicit did not return a verdict. It returned a shortlist with a caveat: the trial fits the biology, the prior treatment question needs follow-up. That is the correct answer.

The LangGraph explanation cites the exclusion criterion accurately, acknowledges the ambiguity, and proceeds to INELIGIBLE. The ml-intern table presents a positive criterion assessment in clean structured format. The Elicit response says "I cannot resolve this from what I retrieved." All three read differently in tone. Only one got the epistemic posture right — and it was the one that declined to decide.

---

## What to Decide Before You Pick a Framework

**The framework decision is the last decision, not the first.** Use LangGraph when you need a team to debug the pipeline in production. Use PydanticAI when output schema stability at the API boundary is load-bearing. Use smolagents when the task is genuinely open-ended and the agent needs to decide what to do, not just how. Use the raw API when cost and simplicity are priorities and the pipeline is stable. None of these choices will affect clinical output quality.

**Start with the prompt, not the framework.** Before writing any framework code, write the system prompt, run it against representative cases, and read the outputs. The calibration — what counts as UNCERTAIN vs. INELIGIBLE, how to handle inference, what to do with missing data — lives in the prompt and cannot be delegated to framework scaffolding.

**Treat the patient notes field as a contamination risk by default.** Any field carrying researcher framing, clinician narrative, or expectation-setting language will shape verdict calibration on borderline cases. The effect is bidirectional and opaque. Separate clinical facts from contextual framing, and test explicitly with known-borderline cases.

**Make the trials-per-call decision explicitly.** Decide how many trials share a context window, document the reasoning, and verify the effect on verdict distributions before going to production. The choice is not a framework default — it is a design decision about how conservative or inclusive you want the screening to be.

**"Absence of information" rules are guidance, not enforcement.** In a patient profile derived from electronic health records (EHR), where clinical context is rich and implicit inferences are everywhere, the UNCERTAIN rule may almost never fire — not because it was removed, but because the model is confident enough not to need it. Test this explicitly with known-ambiguous cases.

**The prior question is still Elicit.** For Patient 1, Elicit returned 5 ranked trials in four minutes at no cost, and four approaches built over four days confirmed the same top result. For Patient 4 — the hardest case in the study — Elicit surfaced the correct trial and explicitly left the ambiguous prior-treatment criterion unresolved, while all four structured frameworks overrode their own uncertainty with a confident wrong inference. For a pharma client without proprietary data integration requirements, "build vs. buy" should be answered before "which framework."

---

## The Actual Lesson

The expectation going in was that framework choice would be the primary differentiator. A week of building showed the framework is the least interesting variable.

The interesting variables are: one sentence in a prompt, one field in a patient profile, how many trials share a context window, and how the model's confidence calibration interacts with explicit rules. None of these appear in framework benchmarks or architecture diagrams. They appear when you run the system on real cases and read every output carefully — including the ones that look correct.

---

*The full code, outputs, and findings from this experiment are available [on GitHub]. All patient profiles are fictional. ClinicalTrials.gov data is public.*

*Written June 5, 2026. Mindfuel independent learning week.*
