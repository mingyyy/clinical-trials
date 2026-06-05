"""
Run ml-intern for all 5 test patients sequentially.
Logs saved to outputs/ml_intern/run_P00X.log.

Usage:
    .venv/bin/python run_ml_intern_all.py
"""

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.test_profiles import TEST_PROFILES

ML_INTERN_DIR = Path(__file__).parent.parent / "ml-intern"
OUTPUT_DIR = Path(__file__).parent / "outputs" / "ml_intern"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

API_HINT = """\
Use the ClinicalTrials.gov v2 API to fetch trials directly:
  GET https://clinicaltrials.gov/api/v2/studies
  params: query.cond=<condition>, filter.overallStatus=RECRUITING, pageSize=50, format=json
  Pagination: use nextPageToken from the response to fetch up to 10 pages.
  Geographic filter: after fetching, compute haversine distance from patient coordinates to each trial site and keep only trials with at least one site within 100 miles.
Do not use web search to find trials. Go directly to the API first."""


def build_prompt(p) -> str:
    sex = "female" if p.sex == "FEMALE" else "male"
    biomarkers = ", ".join(p.biomarkers) if p.biomarkers else "none documented"
    treatments = ", ".join(p.prior_treatments) if p.prior_treatments else "none"
    condition = p.search_condition or p.diagnosis

    prompt = (
        f"I have a patient with {p.diagnosis}, age {p.age}, {sex}, "
        f"located in {p.location} (coordinates: {p.lat}, {p.lon}). "
        f"Biomarkers: {biomarkers}. "
        f"Prior treatments: {treatments}. "
        f"ECOG performance status: {p.ecog_ps}. "
        f"Find recruiting clinical trials this patient may be eligible for within 100 miles "
        f"of their location and assess eligibility for each one. "
        f"Absence of information is NOT evidence of ineligibility — use UNCERTAIN for missing data, "
        f"not INELIGIBLE.\n\n"
        f"{API_HINT}\n\n"
        f"Search condition to use: {condition}"
    )
    return prompt


def run_patient(p) -> bool:
    log_path = OUTPUT_DIR / f"run_{p.patient_id}.log"
    prompt = build_prompt(p)

    print(f"\n{'='*60}")
    print(f"Running {p.patient_id}: {p.diagnosis} ({p.location})")
    print(f"Log: {log_path}")
    print(f"{'='*60}")

    env = os.environ.copy()

    with open(log_path, "w") as log_file:
        log_file.write(f"Patient: {p.patient_id}\n")
        log_file.write(f"Diagnosis: {p.diagnosis}\n")
        log_file.write(f"Location: {p.location}\n")
        log_file.write(f"Model: anthropic/claude-sonnet-4-6\n")
        log_file.write(f"Max iterations: 50\n")
        log_file.write(f"Prompt: {prompt}\n")
        log_file.write("-" * 60 + "\n")
        log_file.flush()

        result = subprocess.run(
            [
                "uv", "run", "python", "-m", "agent.main",
                "--model", "anthropic/claude-sonnet-4-6",
                "--max-iterations", "50",
                "--no-stream",
                prompt,
            ],
            cwd=ML_INTERN_DIR,
            env=env,
            stdout=log_file,
            stderr=log_file,
        )

    status = "done" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    print(f"{p.patient_id}: {status}")
    return result.returncode == 0


if __name__ == "__main__":
    print("ml-intern: running all 5 patients sequentially")
    print(f"ml-intern dir: {ML_INTERN_DIR}")
    print(f"Output dir: {OUTPUT_DIR}")

    results = {}
    for patient in TEST_PROFILES:
        results[patient.patient_id] = run_patient(patient)

    print("\n" + "="*60)
    print("Summary:")
    for pid, ok in results.items():
        print(f"  {pid}: {'done' if ok else 'FAILED'}")
