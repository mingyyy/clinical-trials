"""
PydanticAI implementation — clinical trial matching.

School of thought: type-safety. The Pydantic schema IS the output contract.
PydanticAI validates LLM responses against the model and auto-retries on
validation failure — no manual JSON parsing, no regex stripping of markdown fences.

Key differences from LangGraph:
- No explicit graph or state machine. Just an Agent and async functions.
- output_type= enforces schema at the framework level, not the application level.
- retries= means a bad LLM response is retried automatically with a correction prompt.
- asyncio.gather replaces LangGraph's Send API for parallel fan-out.

The question for Day 3: does schema enforcement catch errors that LangGraph's
manual parse missed, or does the same prompt produce the same output regardless?

Run with: .venv_pydantic/bin/python implementations/pydantic_ai/agent.py
Output:   outputs/pydantic_ai/P00X.json
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pipeline.api_client import fetch_trials, hard_filter_trials
from pipeline.config import MAX_PAGES, MAX_TRIALS_FETCHED, MODEL, SEARCH_RADIUS_MILES
from pipeline.patient_schema import MatchingResult, PatientProfile, TrialMatch
from pipeline.test_profiles import TEST_PROFILES

load_dotenv(Path(__file__).parent.parent.parent / ".env")

BATCH_SIZE = 12  # match LangGraph for fair wall-time comparison


# ---------------------------------------------------------------------------
# LLM output schema — only what the LLM generates
# nct_id and title come from the API; the LLM doesn't produce them
# ---------------------------------------------------------------------------

class EligibilityAssessment(BaseModel):
    """
    Structured output the LLM must return. PydanticAI validates this and
    auto-retries with a correction prompt if the LLM returns an invalid
    verdict value, a confidence outside 0-1, or missing required fields.

    This is the architectural difference from LangGraph: validation is
    framework-enforced, not manually coded in the application.
    """
    verdict: Literal["ELIGIBLE", "INELIGIBLE", "UNCERTAIN"]
    confidence: float = Field(ge=0.0, le=1.0)
    matched_criteria: list[str] = Field(default_factory=list)
    exclusion_flags: list[str] = Field(default_factory=list)
    uncertain_items: list[str] = Field(default_factory=list)
    explanation: str


# ---------------------------------------------------------------------------
# Agent — defined once, reused across all patients and trials
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a clinical trial eligibility screener. "
    "Your job is to identify candidates for further review, not to make final enrollment decisions. "
    "Given a patient profile and trial eligibility criteria, classify the patient as:\n"
    "  ELIGIBLE   — patient clearly meets all stated criteria based on available information\n"
    "  INELIGIBLE — patient clearly fails at least one criterion (requires positive evidence of failure)\n"
    "  UNCERTAIN  — patient may be eligible but the profile is missing data needed to confirm\n\n"
    "CRITICAL RULES:\n"
    "1. Absence of information is NOT evidence of ineligibility. "
    "If a criterion requires data not present in the profile, classify as UNCERTAIN, not INELIGIBLE.\n"
    "2. Only mark INELIGIBLE when the profile contains direct evidence of failing a criterion.\n"
    "3. For UNCERTAIN: list specifically what additional data would confirm or deny eligibility.\n"
    "4. Cite specific criteria by name or number when available."
)

_model = AnthropicModel(MODEL)

agent: Agent[None, EligibilityAssessment] = Agent(
    model=_model,
    output_type=EligibilityAssessment,
    system_prompt=SYSTEM_PROMPT,
    retries=2,  # auto-retry if schema validation fails (e.g. invalid verdict value)
)


# ---------------------------------------------------------------------------
# Prompt builder (identical framing to LangGraph for fair comparison)
# ---------------------------------------------------------------------------

def build_prompt(patient: PatientProfile, trial: dict) -> str:
    # fetch_trials returns a flat dict: nct_id, title, eligibility, min_age, max_age, sex, phases, sponsor
    nct_id = trial.get("nct_id", "UNKNOWN")
    title = trial.get("title", "No title")
    criteria = trial.get("eligibility", "No criteria provided")
    min_age = trial.get("min_age", "not specified")
    max_age = trial.get("max_age", "not specified")
    sex_req = trial.get("sex", "ALL")
    phases = ", ".join(trial.get("phases", [])) if trial.get("phases") else "N/A"
    sponsor = trial.get("sponsor", "Unknown")

    return (
        f"PATIENT PROFILE:\n"
        f"- ID: {patient.patient_id}\n"
        f"- Age: {patient.age}\n"
        f"- Sex: {patient.sex}\n"
        f"- Diagnosis: {patient.diagnosis}\n"
        f"- Biomarkers: {', '.join(patient.biomarkers) if patient.biomarkers else 'Not specified'}\n"
        f"- Prior treatments: {', '.join(patient.prior_treatments) if patient.prior_treatments else 'Not specified'}\n"
        f"- ECOG PS: {patient.ecog_ps if patient.ecog_ps is not None else 'Not documented'}\n"
        f"- Notes: {patient.notes if patient.notes else 'None'}\n"
        f"\n"
        f"TRIAL: {nct_id} — {title}\n"
        f"Phase: {phases} | Sponsor: {sponsor}\n"
        f"Age requirement: {min_age} to {max_age}\n"
        f"Sex requirement: {sex_req}\n"
        f"\n"
        f"ELIGIBILITY CRITERIA:\n"
        f"{criteria}\n"
        f"\n"
        f"Assess whether this patient is ELIGIBLE, INELIGIBLE, or UNCERTAIN for this trial."
    )


# ---------------------------------------------------------------------------
# Per-trial coroutine
# ---------------------------------------------------------------------------

async def assess_trial(patient: PatientProfile, trial: dict) -> tuple[TrialMatch, int]:
    """Assess one trial asynchronously. Returns (TrialMatch, tokens_used)."""
    nct_id = trial.get("nct_id", "UNKNOWN")
    title = trial.get("title", "No title")

    prompt = build_prompt(patient, trial)
    result = await agent.run(prompt)
    assessment: EligibilityAssessment = result.output

    match = TrialMatch(
        nct_id=nct_id,
        title=title,
        verdict=assessment.verdict,
        eligible=(assessment.verdict == "ELIGIBLE"),
        confidence=assessment.confidence,
        matched_criteria=assessment.matched_criteria,
        exclusion_flags=assessment.exclusion_flags,
        uncertain_items=assessment.uncertain_items,
        explanation=assessment.explanation,
    )

    tokens = 0
    usage = result.usage  # property in PydanticAI v1.104, not a method
    if usage:
        tokens = (usage.input_tokens or 0) + (usage.output_tokens or 0)

    return match, tokens


# ---------------------------------------------------------------------------
# Per-patient pipeline
# ---------------------------------------------------------------------------

async def assess_patient(patient: PatientProfile) -> MatchingResult:
    """Full fetch -> filter -> parallel assess pipeline for one patient."""
    t0 = time.time()

    # Fetch
    condition = patient.search_condition or patient.diagnosis
    trials = fetch_trials(condition, patient.lat, patient.lon, SEARCH_RADIUS_MILES, MAX_TRIALS_FETCHED, max_pages=MAX_PAGES)
    total_fetched = len(trials)

    # Hard filter (deterministic age/sex — same logic as LangGraph)
    filtered = hard_filter_trials(trials, patient.age, patient.sex)
    total_filtered = len(filtered)

    # Parallel assessment in batches
    all_matches: list[TrialMatch] = []
    total_tokens = 0
    llm_calls = 0

    batches = [filtered[i:i + BATCH_SIZE] for i in range(0, len(filtered), BATCH_SIZE)]
    for batch in batches:
        results = await asyncio.gather(*[assess_trial(patient, t) for t in batch])
        for match, tokens in results:
            all_matches.append(match)
            total_tokens += tokens
            llm_calls += 1

    wall_time = round(time.time() - t0, 1)

    return MatchingResult(
        patient_id=patient.patient_id,
        framework="pydantic_ai",
        matches=all_matches,
        total_trials_fetched=total_fetched,
        trials_after_hard_filter=total_filtered,
        llm_calls=llm_calls,
        total_tokens=total_tokens,
        wall_time_seconds=wall_time,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    output_dir = Path(__file__).parent.parent.parent / "outputs" / "02_rerun" / "pydantic_ai"
    output_dir.mkdir(parents=True, exist_ok=True)

    for patient in TEST_PROFILES:
        print(f"\n--- {patient.patient_id}: {patient.diagnosis} ---")
        result = await assess_patient(patient)

        eligible = sum(1 for m in result.matches if m.verdict == "ELIGIBLE")
        uncertain = sum(1 for m in result.matches if m.verdict == "UNCERTAIN")
        ineligible = sum(1 for m in result.matches if m.verdict == "INELIGIBLE")

        print(f"  Fetched: {result.total_trials_fetched} | After filter: {result.trials_after_hard_filter}")
        print(f"  ELIGIBLE: {eligible} | UNCERTAIN: {uncertain} | INELIGIBLE: {ineligible}")
        print(f"  LLM calls: {result.llm_calls} | Tokens: {result.total_tokens:,} | Wall time: {result.wall_time_seconds}s")

        out_path = output_dir / f"{patient.patient_id}.json"
        out_path.write_text(result.model_dump_json(indent=2))
        print(f"  Written: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
