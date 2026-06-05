"""
smolagents implementation — clinical trial matching.

School of thought: code generation. The agent writes and executes Python code
to orchestrate the pipeline. Instead of a prescribed graph (LangGraph) or typed
contracts (PydanticAI), the agent reasons about the task and writes code to solve it.

Key architectural difference: the LLM writes a Python program that calls tools.
The tools do the actual work (API fetch, eligibility assessment). The agent decides
the structure of the program — which tools to call, in what order, how to handle
the data between steps.

Key question: does code-generation autonomy produce better or worse orchestration
than a prescribed pipeline? Does the agent write clean, correct code, or does it
hallucinate trial details and bypass the output schema?

Run with: .venv_smolagents/bin/python implementations/smolagents/agent.py
Output:   outputs/smolagents/P00X.json
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from smolagents import CodeAgent, LiteLLMModel, tool

from pipeline.api_client import fetch_trials, hard_filter_trials
from pipeline.config import MAX_PAGES, MAX_TRIALS_FETCHED, MODEL, SEARCH_RADIUS_MILES
from pipeline.patient_schema import MatchingResult, PatientProfile, TrialMatch
from pipeline.test_profiles import TEST_PROFILES


# ---------------------------------------------------------------------------
# Tools the agent can call from its generated code
# ---------------------------------------------------------------------------

@tool
def search_clinical_trials(
    condition: str,
    lat: float,
    lon: float,
    radius_miles: int = 250,
    max_results: int = 50,
) -> str:
    """
    Fetch recruiting clinical trials from ClinicalTrials.gov for a given condition
    and geographic location.

    Args:
        condition: Medical condition to search for (e.g. "HER2-positive breast cancer")
        lat: Latitude of patient location
        lon: Longitude of patient location
        radius_miles: Search radius in miles (default 250)
        max_results: Maximum number of trials to fetch (default 50)

    Returns:
        JSON string of trial list. Each trial has: nct_id, title, eligibility,
        min_age, max_age, sex, phases, sponsor, locations.
    """
    trials = fetch_trials(condition, lat, lon, radius_miles, max_results, max_pages=MAX_PAGES)
    return json.dumps(trials)


@tool
def prefilter_trials(trials_json: str, age: int, sex: str) -> str:
    """
    Apply deterministic hard filter to eliminate trials where patient's age
    or sex is an explicit mismatch. No LLM needed — rule-based.

    Args:
        trials_json: JSON string of trials (output of search_clinical_trials)
        age: Patient age in years
        sex: Patient sex ("MALE" or "FEMALE")

    Returns:
        JSON string of filtered trial list.
    """
    trials = json.loads(trials_json)
    filtered = hard_filter_trials(trials, age, sex)
    return json.dumps(filtered)


@tool
def assess_trials_batch(patient_json: str, trials_json: str) -> str:
    """
    Assess eligibility for ALL trials in parallel using LLM reasoning.
    Returns a three-state verdict for each trial: ELIGIBLE, INELIGIBLE, or UNCERTAIN.

    CRITICAL RULES applied in assessment:
    - ELIGIBLE: patient clearly meets all stated criteria
    - INELIGIBLE: patient clearly fails at least one criterion (requires positive evidence)
    - UNCERTAIN: profile is missing data needed to confirm eligibility
    - Absence of information is NOT evidence of ineligibility

    Args:
        patient_json: JSON string with patient fields: patient_id, age, sex, diagnosis,
                      biomarkers (list), prior_treatments (list), ecog_ps, notes
        trials_json: JSON string of trial list (output of prefilter_trials)

    Returns:
        JSON string list of assessments, each with:
        nct_id, title, verdict (ELIGIBLE|INELIGIBLE|UNCERTAIN), confidence (0-1),
        matched_criteria (list), exclusion_flags (list), uncertain_items (list),
        explanation (string)
    """
    import anthropic

    patient = json.loads(patient_json)
    trials = json.loads(trials_json)

    system_prompt = (
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
        "4. Cite specific criteria by name or number when available.\n\n"
        "Respond ONLY with valid JSON (no markdown) with keys: "
        "verdict, confidence, matched_criteria, exclusion_flags, uncertain_items, explanation"
    )

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    def assess_one(trial: dict) -> dict:
        nct_id = trial.get("nct_id", "UNKNOWN")
        title = trial.get("title", "No title")
        criteria = trial.get("eligibility", "No criteria provided")
        min_age = trial.get("min_age", "not specified")
        max_age = trial.get("max_age", "not specified")
        sex_req = trial.get("sex", "ALL")
        phases = ", ".join(trial.get("phases", [])) if trial.get("phases") else "N/A"
        sponsor = trial.get("sponsor", "Unknown")

        prompt = (
            f"PATIENT PROFILE:\n"
            f"- ID: {patient.get('patient_id', '')}\n"
            f"- Age: {patient.get('age', '')}\n"
            f"- Sex: {patient.get('sex', '')}\n"
            f"- Diagnosis: {patient.get('diagnosis', '')}\n"
            f"- Biomarkers: {', '.join(patient.get('biomarkers', [])) or 'Not specified'}\n"
            f"- Prior treatments: {', '.join(patient.get('prior_treatments', [])) or 'Not specified'}\n"
            f"- ECOG PS: {patient.get('ecog_ps') if patient.get('ecog_ps') is not None else 'Not documented'}\n"
            f"- Notes: {patient.get('notes') or 'None'}\n"
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

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            # Strip markdown fences if present
            import re
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
            result = json.loads(raw)
            verdict = result.get("verdict", "INELIGIBLE").upper()
            if verdict not in ("ELIGIBLE", "INELIGIBLE", "UNCERTAIN"):
                verdict = "INELIGIBLE"
            tokens = (response.usage.input_tokens or 0) + (response.usage.output_tokens or 0)
        except Exception as e:
            result = {}
            verdict = "UNCERTAIN"
            tokens = 0

        return {
            "nct_id": nct_id,
            "title": title,
            "verdict": verdict,
            "eligible": (verdict == "ELIGIBLE"),
            "confidence": float(result.get("confidence", 0.0)),
            "matched_criteria": result.get("matched_criteria", []),
            "exclusion_flags": result.get("exclusion_flags", []),
            "uncertain_items": result.get("uncertain_items", []),
            "explanation": result.get("explanation", ""),
            "_tokens": tokens,
        }

    # Parallel assessment — 12 at a time, same as LangGraph/PydanticAI
    assessments = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(assess_one, t): t for t in trials}
        for future in as_completed(futures):
            assessments.append(future.result())

    # Restore original order (as_completed is unordered)
    trial_order = {t["nct_id"]: i for i, t in enumerate(trials)}
    assessments.sort(key=lambda a: trial_order.get(a["nct_id"], 999))

    return json.dumps(assessments)


@tool
def save_matching_result(result_json: str, patient_id: str) -> str:
    """
    Validate and save a MatchingResult JSON to outputs/smolagents/<patient_id>.json.

    Args:
        result_json: JSON string conforming to MatchingResult schema.
                     Required fields: patient_id, framework, matches, total_trials_fetched,
                     trials_after_hard_filter, llm_calls, total_tokens, wall_time_seconds.
                     Each match needs: nct_id, title, verdict (ELIGIBLE|INELIGIBLE|UNCERTAIN),
                     eligible (bool), confidence, matched_criteria, exclusion_flags,
                     uncertain_items, explanation.
        patient_id: Patient identifier (e.g. "P001")

    Returns:
        Confirmation string with output path and match counts.
    """
    out_dir = Path(__file__).parent.parent.parent / "outputs" / "02_rerun" / "smolagents"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{patient_id}.json"

    data = json.loads(result_json)
    result = MatchingResult(**data)
    out_path.write_text(result.model_dump_json(indent=2))

    eligible = sum(1 for m in result.matches if m.verdict == "ELIGIBLE")
    uncertain = sum(1 for m in result.matches if m.verdict == "UNCERTAIN")
    ineligible = sum(1 for m in result.matches if m.verdict == "INELIGIBLE")
    return (
        f"Saved to {out_path} — "
        f"{len(result.matches)} matches: {eligible}E / {uncertain}U / {ineligible}I"
    )


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

def build_agent() -> CodeAgent:
    model = LiteLLMModel(
        model_id=f"anthropic/{MODEL}",
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
    )
    return CodeAgent(
        tools=[search_clinical_trials, prefilter_trials, assess_trials_batch, save_matching_result],
        model=model,
        max_steps=20,
    )


# ---------------------------------------------------------------------------
# Task prompt
# ---------------------------------------------------------------------------

TASK_TEMPLATE = """
You are matching a patient to recruiting clinical trials. Use the provided tools in order.

PATIENT PROFILE:
- patient_id: {patient_id}
- Age: {age}, Sex: {sex}
- Diagnosis: {diagnosis}
- Biomarkers: {biomarkers}
- Prior treatments: {prior_treatments}
- ECOG PS: {ecog_ps}
- Location: {location} (lat={lat}, lon={lon})
- Notes: {notes}

STEPS:
1. Call search_clinical_trials(condition="{search_condition}", lat={lat}, lon={lon}) to fetch trials.
2. Call prefilter_trials(trials_json=<result>, age={age}, sex="{sex}") to apply hard filter.
3. Call assess_trials_batch(patient_json=<patient dict as JSON string>, trials_json=<filtered trials JSON>)
   to get three-state verdicts (ELIGIBLE/INELIGIBLE/UNCERTAIN) for all trials in parallel.
4. From the assessments, compute:
   - total_tokens: sum of _tokens field across all assessments
   - llm_calls: number of assessments
   Remove the _tokens field from each assessment before saving.
5. Call save_matching_result with this exact JSON structure:
   {{
     "patient_id": "{patient_id}",
     "framework": "smolagents",
     "matches": [<list of assessments, each with nct_id/title/verdict/eligible/confidence/matched_criteria/exclusion_flags/uncertain_items/explanation>],
     "total_trials_fetched": <int from step 1>,
     "trials_after_hard_filter": <int from step 2>,
     "llm_calls": <count from step 4>,
     "total_tokens": <sum from step 4>,
     "wall_time_seconds": {wall_time_placeholder},
     "notes": "<any observations about the process or unusual findings>"
   }}

IMPORTANT: verdict must be "ELIGIBLE", "INELIGIBLE", or "UNCERTAIN" — not a boolean.
eligible must be a boolean: true only when verdict == "ELIGIBLE".
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== smolagents: Clinical Trial Matching ===")
    out_dir = Path(__file__).parent.parent.parent / "outputs" / "02_rerun" / "smolagents"
    out_dir.mkdir(parents=True, exist_ok=True)

    agent = build_agent()

    for patient in TEST_PROFILES:
        print(f"\n--- {patient.patient_id}: {patient.diagnosis} ---")
        t0 = time.time()

        task = TASK_TEMPLATE.format(
            patient_id=patient.patient_id,
            age=patient.age,
            sex=patient.sex,
            diagnosis=patient.diagnosis,
            biomarkers=", ".join(patient.biomarkers),
            prior_treatments=", ".join(patient.prior_treatments),
            ecog_ps=patient.ecog_ps,
            location=patient.location,
            lat=patient.lat,
            lon=patient.lon,
            notes=patient.notes,
            search_condition=patient.search_condition or patient.diagnosis,
            wall_time_placeholder=0,  # agent fills this after timing
        )

        try:
            agent.run(task)
            elapsed = round(time.time() - t0, 1)
            print(f"  Completed in {elapsed}s")

            # Patch wall_time_seconds into output (agent can't know it in advance)
            out_path = out_dir / f"{patient.patient_id}.json"
            if out_path.exists():
                data = json.loads(out_path.read_text())
                data["wall_time_seconds"] = elapsed
                out_path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            print(f"  [ERROR] {e} (after {elapsed}s)")

    print("\n=== smolagents done ===")
