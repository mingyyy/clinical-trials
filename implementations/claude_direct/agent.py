"""
Claude Direct implementation — clinical trial matching.

School of thought: zero framework. Raw Anthropic SDK, a single carefully
engineered system prompt, and batch calls. No graph, no type enforcement,
no code generation overhead.

Distinctive architecture: BATCH_SIZE=10 trials per LLM call (vs 1 per call
in LangGraph and PydanticAI). The model sees 10 trials simultaneously and
produces 10 verdicts in one response. Fewer calls, larger context per call.

Key questions:
- Does batching produce different verdict quality than individual calls?
- Does seeing trials together (comparative context) improve or hurt accuracy?
- What does a framework buy you over a well-crafted prompt?

Run with: .venv/bin/python implementations/claude_direct/agent.py
Output:   outputs/claude_direct/P00X.json
"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pipeline.api_client import fetch_trials, hard_filter_trials
from pipeline.config import BATCH_SIZE, MAX_PAGES, MAX_TOKENS_BATCH, MAX_TRIALS_FETCHED, MODEL, SEARCH_RADIUS_MILES
from pipeline.patient_schema import MatchingResult, PatientProfile, TrialMatch
from pipeline.test_profiles import TEST_PROFILES

load_dotenv(Path(__file__).parent.parent.parent / ".env")

client = anthropic.AsyncAnthropic()

SYSTEM = """You are a clinical trial eligibility screener.
Your job is to identify candidates for further review, not to make final enrollment decisions.
You will receive a patient profile and a list of clinical trials with full eligibility criteria.

For each trial, classify the patient as ELIGIBLE, INELIGIBLE, or UNCERTAIN:
  ELIGIBLE   — patient clearly meets all stated criteria based on available information
  INELIGIBLE — patient clearly fails at least one criterion (requires positive evidence of failure)
  UNCERTAIN  — patient may be eligible but the profile is missing data needed to confirm

CRITICAL RULES:
1. Absence of information is NOT evidence of ineligibility.
   If a criterion requires data not in the profile, classify as UNCERTAIN — not INELIGIBLE.
2. Only mark INELIGIBLE when the profile contains direct evidence of failing a criterion.
3. For UNCERTAIN: list specifically what additional data would confirm or deny eligibility.
4. Cite specific criteria by name or number when available.

Respond ONLY with a JSON array — one object per trial — in this exact structure:
[
  {
    "nct_id": "...",
    "verdict": "ELIGIBLE" | "INELIGIBLE" | "UNCERTAIN",
    "confidence": 0.0 to 1.0,
    "matched_criteria": ["specific criterion text the patient clearly meets", ...],
    "exclusion_flags": ["specific exclusion criterion that is clearly triggered", ...],
    "uncertain_items": ["specific data needed to confirm or deny eligibility", ...],
    "explanation": "1-3 sentences citing specific criteria and evidence"
  }
]

No markdown fences. No preamble. JSON array only."""


async def assess_batch_async(patient: PatientProfile, trials: list[dict]) -> tuple[list[TrialMatch], int]:
    """
    Assess a batch of trials in a single async LLM call.
    Claude Direct's distinctive approach: 10 trials per call, all assessed together.
    Returns (matches, total_tokens).
    """
    trial_text = ""
    for i, t in enumerate(trials, 1):
        trial_text += (
            f"\n--- Trial {i}: {t['nct_id']} ---\n"
            f"Title: {t['title']}\n"
            f"Phase: {', '.join(t['phases']) if t['phases'] else 'N/A'} | Sponsor: {t['sponsor']}\n"
            f"Age: {t['min_age']} to {t['max_age']} | Sex: {t['sex'] or 'ALL'}\n"
            f"Eligibility criteria:\n{t['eligibility']}\n"
        )

    user_msg = (
        f"PATIENT PROFILE:\n"
        f"- ID: {patient.patient_id}\n"
        f"- Age: {patient.age}, Sex: {patient.sex}\n"
        f"- Diagnosis: {patient.diagnosis}\n"
        f"- Biomarkers: {', '.join(patient.biomarkers) if patient.biomarkers else 'Not specified'}\n"
        f"- Prior treatments: {', '.join(patient.prior_treatments) if patient.prior_treatments else 'Not specified'}\n"
        f"- ECOG PS: {patient.ecog_ps if patient.ecog_ps is not None else 'Not documented'}\n"
        f"- Notes: {patient.notes or 'None'}\n"
        f"\n"
        f"Assess the following {len(trials)} trial(s):\n"
        f"{trial_text}"
    )

    response = await client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS_BATCH,
        system=SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )

    total_tokens = response.usage.input_tokens + response.usage.output_tokens
    raw_text = response.content[0].text.strip()
    raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text)

    trial_lookup = {t["nct_id"]: t["title"] for t in trials}
    matches = []

    try:
        items = json.loads(raw_text)
        for item in items:
            nct_id = item.get("nct_id", "")
            verdict = item.get("verdict", "INELIGIBLE").upper()
            if verdict not in ("ELIGIBLE", "INELIGIBLE", "UNCERTAIN"):
                verdict = "INELIGIBLE"
            matches.append(TrialMatch(
                nct_id=nct_id,
                title=trial_lookup.get(nct_id, ""),
                verdict=verdict,
                eligible=(verdict == "ELIGIBLE"),
                confidence=float(item.get("confidence", 0.0)),
                matched_criteria=item.get("matched_criteria", []),
                exclusion_flags=item.get("exclusion_flags", []),
                uncertain_items=item.get("uncertain_items", []),
                explanation=item.get("explanation", ""),
            ))
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        # Batch parse failure: mark all in batch as parse error
        for t in trials:
            matches.append(TrialMatch(
                nct_id=t["nct_id"],
                title=t["title"],
                verdict="UNCERTAIN",
                explanation=f"[batch parse error: {e}]",
            ))

    return matches, total_tokens


async def assess_patient_async(patient: PatientProfile) -> MatchingResult:
    """Fetch, filter, then assess all batches in parallel."""
    t0 = time.time()

    condition = patient.search_condition or patient.diagnosis
    trials = fetch_trials(condition, patient.lat, patient.lon, SEARCH_RADIUS_MILES, MAX_TRIALS_FETCHED, max_pages=MAX_PAGES)
    total_fetched = len(trials)

    filtered = hard_filter_trials(trials, patient.age, patient.sex)
    total_filtered = len(filtered)

    # Batch into groups of BATCH_SIZE=10, then run ALL batches in parallel
    batches = [filtered[i:i + BATCH_SIZE] for i in range(0, len(filtered), BATCH_SIZE)]
    results = await asyncio.gather(*[assess_batch_async(patient, b) for b in batches])

    all_matches: list[TrialMatch] = []
    total_tokens = 0
    for batch_matches, tokens in results:
        all_matches.extend(batch_matches)
        total_tokens += tokens

    wall_time = round(time.time() - t0, 1)
    llm_calls = len(batches)

    return MatchingResult(
        patient_id=patient.patient_id,
        framework="claude_direct",
        matches=all_matches,
        total_trials_fetched=total_fetched,
        trials_after_hard_filter=total_filtered,
        llm_calls=llm_calls,
        total_tokens=total_tokens,
        wall_time_seconds=wall_time,
        notes=f"Batch size: {BATCH_SIZE} trials/call. {llm_calls} batch calls, parallel.",
    )


async def main() -> None:
    out_dir = Path(__file__).parent.parent.parent / "outputs" / "claude_direct"
    out_dir.mkdir(parents=True, exist_ok=True)

    for patient in TEST_PROFILES:
        print(f"\n--- {patient.patient_id}: {patient.diagnosis} ---")
        result = await assess_patient_async(patient)

        eligible = sum(1 for m in result.matches if m.verdict == "ELIGIBLE")
        uncertain = sum(1 for m in result.matches if m.verdict == "UNCERTAIN")
        ineligible = sum(1 for m in result.matches if m.verdict == "INELIGIBLE")

        print(f"  Fetched: {result.total_trials_fetched} | After filter: {result.trials_after_hard_filter}")
        print(f"  ELIGIBLE: {eligible} | UNCERTAIN: {uncertain} | INELIGIBLE: {ineligible}")
        print(f"  LLM calls: {result.llm_calls} | Tokens: {result.total_tokens:,} | Wall time: {result.wall_time_seconds}s")

        out_path = out_dir / f"{patient.patient_id}.json"
        out_path.write_text(result.model_dump_json(indent=2))
        print(f"  Written: {out_path.name}")


if __name__ == "__main__":
    asyncio.run(main())
