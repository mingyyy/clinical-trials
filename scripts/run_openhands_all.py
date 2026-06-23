"""
Run OpenHands for all 5 test patients sequentially in headless mode.
Logs saved to outputs/openhands/run_P00X.log.

Usage:
    .venv/bin/python run_openhands_all.py

Smoke test (run first to confirm headless mode works):
    .venv/bin/python run_openhands_all.py --smoke-test

Prerequisites:
    - Docker running
    - Images already pulled (no network pull during run):
        ghcr.io/all-hands-ai/openhands:0.40
        ghcr.io/all-hands-ai/runtime:0.40-nikolaik
    - ANTHROPIC_API_KEY in .env
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.test_profiles import TEST_PROFILES

OUTPUT_DIR = Path(__file__).parent / "outputs" / "openhands"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

RUNTIME_IMAGE = "ghcr.io/all-hands-ai/runtime:0.40-nikolaik"
APP_IMAGE = "ghcr.io/all-hands-ai/openhands:0.40"
CONFIG_FILE = Path(__file__).parent / "openhands_config.toml"

API_HINT = """\
Use the ClinicalTrials.gov v2 API to fetch trials directly:
  GET https://clinicaltrials.gov/api/v2/studies
  params: query.cond=<condition>, filter.overallStatus=RECRUITING, pageSize=50, format=json
  Pagination: use nextPageToken from the response to fetch up to 10 pages.
  Geographic filter: after fetching, compute haversine distance from patient coordinates \
to each trial site and keep only trials with at least one site within 100 miles.
Do not use web search to find trials. Go directly to the API first."""


def build_prompt(p) -> str:
    sex = "female" if p.sex == "FEMALE" else "male"
    biomarkers = ", ".join(p.biomarkers) if p.biomarkers else "none documented"
    treatments = ", ".join(p.prior_treatments) if p.prior_treatments else "none"
    condition = p.search_condition or p.diagnosis

    return (
        f"I have a patient with {p.diagnosis}, age {p.age}, {sex}, "
        f"located in {p.location} (coordinates: {p.lat}, {p.lon}). "
        f"Biomarkers: {biomarkers}. "
        f"Prior treatments: {treatments}. "
        f"ECOG performance status: {p.ecog_ps}. "
        f"Find recruiting clinical trials this patient may be eligible for within 100 miles "
        f"of their location and assess eligibility for each one. "
        f"Absence of information is NOT evidence of ineligibility — use UNCERTAIN for missing "
        f"data, not INELIGIBLE.\n\n"
        f"{API_HINT}\n\n"
        f"Search condition to use: {condition}"
    )


def run_patient(p, api_key: str) -> bool:
    log_path = OUTPUT_DIR / f"run_{p.patient_id}.log"
    prompt = build_prompt(p)
    container_name = f"openhands-{p.patient_id.lower()}"

    print(f"\n{'='*60}")
    print(f"Running {p.patient_id}: {p.diagnosis} ({p.location})")
    print(f"Log: {log_path}")
    print(f"{'='*60}")

    # Remove any stopped container with this name from a previous run
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    with open(log_path, "w") as log_file:
        log_file.write(f"Patient: {p.patient_id}\n")
        log_file.write(f"Diagnosis: {p.diagnosis}\n")
        log_file.write(f"Location: {p.location}\n")
        log_file.write(f"Model: claude-sonnet-4-6\n")
        log_file.write(f"Prompt: {prompt}\n")
        log_file.write("-" * 60 + "\n")
        log_file.flush()

        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--pull=never",
                "-e", f"SANDBOX_RUNTIME_CONTAINER_IMAGE={RUNTIME_IMAGE}",
                "-e", f"LLM_API_KEY={api_key}",
                "-v", "/var/run/docker.sock:/var/run/docker.sock",
                "-v", f"{Path.home()}/.openhands-state:/.openhands-state",
                "-v", f"{CONFIG_FILE}:/app/config.toml",
                "--add-host", "host.docker.internal:host-gateway",
                "--name", container_name,
                APP_IMAGE,
                "python", "-m", "openhands.core.main",
                "--config-file", "/app/config.toml",
                "-t", prompt,
                "-i", "50",
            ],
            stdout=log_file,
            stderr=log_file,
        )

    if result.returncode != 0:
        print(f"{p.patient_id}: FAILED (exit {result.returncode})")
        return False
    # Also check for agent-level errors (container exits 0 but agent errored)
    content = log_path.read_text()
    if "AgentState.ERROR" in content and "AgentState.FINISHED" not in content:
        print(f"{p.patient_id}: FAILED (agent error — check log)")
        return False
    print(f"{p.patient_id}: done")
    return True


def smoke_test(api_key: str) -> bool:
    """Quick sanity check: run a trivial task to confirm headless mode works."""
    print("Smoke test: running trivial headless task...")
    log_path = OUTPUT_DIR / "smoke_test.log"
    subprocess.run(["docker", "rm", "-f", "openhands-smoke"], capture_output=True)

    with open(log_path, "w") as log_file:
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--pull=never",
                "-e", f"SANDBOX_RUNTIME_CONTAINER_IMAGE={RUNTIME_IMAGE}",
                "-e", f"LLM_API_KEY={api_key}",
                "-v", "/var/run/docker.sock:/var/run/docker.sock",
                "-v", f"{Path.home()}/.openhands-state:/.openhands-state",
                "-v", f"{CONFIG_FILE}:/app/config.toml",
                "--add-host", "host.docker.internal:host-gateway",
                "--name", "openhands-smoke",
                APP_IMAGE,
                "python", "-m", "openhands.core.main",
                "--config-file", "/app/config.toml",
                "-t", "Print the string SMOKE_OK and nothing else.",
                "-i", "5",
            ],
            stdout=log_file,
            stderr=log_file,
            timeout=120,
        )

    if result.returncode == 0:
        content = log_path.read_text()
        if "SMOKE_OK" in content:
            print("Smoke test PASSED — headless mode working.")
        else:
            print("Smoke test WARNING — exit 0 but SMOKE_OK not in output. Check:", log_path)
        return True
    else:
        print(f"Smoke test FAILED (exit {result.returncode}). Check: {log_path}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true",
                        help="Run smoke test only, then exit")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set (check .env)")
        sys.exit(1)

    if args.smoke_test:
        ok = smoke_test(api_key)
        sys.exit(0 if ok else 1)

    print("OpenHands: running all 5 patients sequentially in headless mode")
    print(f"App image:     {APP_IMAGE}")
    print(f"Runtime image: {RUNTIME_IMAGE}")
    print(f"Output dir:    {OUTPUT_DIR}")

    results = {}
    for patient in TEST_PROFILES:
        results[patient.patient_id] = run_patient(patient, api_key)

    print("\n" + "=" * 60)
    print("Summary:")
    for pid, ok in results.items():
        print(f"  {pid}: {'done' if ok else 'FAILED'}")
