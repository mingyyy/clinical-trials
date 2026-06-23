"""
Fix 3 — Two-stage prompt: extract facts, then assess.

Stage 1: extract only facts explicitly stated in the patient profile (no inference).
Stage 2: assess eligibility using ONLY the extracted facts.

Run AFTER test_prompt_fixes.py (which covers baseline, fix1, fix2).

Run with: .venv/bin/python test_prompt_fix3.py
Output:   outputs/05_experiments/prompt_fixes/fix3/results.json
"""

import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.test_profiles import TEST_PROFILES

load_dotenv(Path(__file__).parent / ".env")

MODEL = "claude-sonnet-4-6"
OUT_DIR = Path(__file__).parent / "outputs" / "05_experiments" / "prompt_fixes" / "fix3"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PATIENT_P004 = next(p for p in TEST_PROFILES if p.patient_id == "P004")
PATIENT_P001 = next(p for p in TEST_PROFILES if p.patient_id == "P001")

TEST_CASES = [
    {"label": "TARGET  P004×NCT04511013", "patient": PATIENT_P004, "nct_id": "NCT04511013",
     "expect": "UNCERTAIN"},
    {"label": "SANITY1 P004×NCT06246916", "patient": PATIENT_P004, "nct_id": "NCT06246916",
     "expect": "INELIGIBLE"},
    {"label": "SANITY2 P004×NCT05727904", "patient": PATIENT_P004, "nct_id": "NCT05727904",
     "expect": "INELIGIBLE"},
    {"label": "SANITY3 P001×NCT07060807", "patient": PATIENT_P001, "nct_id": "NCT07060807",
     "expect": "INELIGIBLE"},
]

STAGE1_SYSTEM = (
    "You are a medical data extractor. Your job is to extract ONLY facts that are "
    "explicitly and directly stated in the patient profile text. "
    "Do NOT infer, interpret, or add clinical context. "
    "Do NOT use your medical knowledge to expand what the profile says. "
    "If a fact is not explicitly written, it does not exist in this profile.\n\n"
    "Return JSON with these keys:\n"
    "  age (int)\n"
    "  sex (string)\n"
    "  diagnosis (string — exact text from profile)\n"
    "  biomarkers (list of strings — exact text)\n"
    "  prior_treatments (list of strings — exact names, NO setting assumed)\n"
    "  ecog_ps (int)\n"
    "  location (string)\n"
    "  explicitly_stated_facts (list of strings — any other facts directly stated)\n"
    "  NOT_in_profile (list of strings — facts commonly relevant to oncology trials "
    "    that are NOT mentioned, e.g. treatment setting, lab values, prior radiation)"
)

STAGE2_SYSTEM = (
    "You are a clinical trial eligibility screener. "
    "Your job is to identify candidates for further review, not to make final enrollment decisions.\n\n"
    "You will be given:\n"
    "1. A set of CONFIRMED FACTS extracted from a patient profile (no inference, only explicit data)\n"
    "2. A trial's eligibility criteria\n\n"
    "Using ONLY the confirmed facts (no additional clinical inference), classify the patient as:\n"
    "  ELIGIBLE   — confirmed facts clearly meet all stated criteria\n"
    "  INELIGIBLE — confirmed facts clearly fail at least one criterion\n"
    "  UNCERTAIN  — confirmed facts are insufficient to determine eligibility\n\n"
    "CRITICAL: You may only use the facts listed under CONFIRMED FACTS. "
    "If a criterion requires information not present in CONFIRMED FACTS, it must be UNCERTAIN. "
    "Do not use your medical knowledge to fill gaps.\n\n"
    "Respond ONLY with valid JSON (no markdown) with keys:\n"
    "  verdict (string: ELIGIBLE|INELIGIBLE|UNCERTAIN)\n"
    "  confidence (float 0.0-1.0)\n"
    "  matched_criteria (list of strings — criteria met by confirmed facts)\n"
    "  exclusion_flags (list of strings — criteria failed by confirmed facts, citing the fact)\n"
    "  uncertain_items (list of strings — criteria requiring information absent from confirmed facts)\n"
    "  explanation (string, 1-3 sentences citing only confirmed facts)"
)


def fetch_trial(nct_id: str) -> dict:
    url = f"https://clinicaltrials.gov/api/v2/studies/{nct_id}"
    params = {"fields": "protocolSection.identificationModule,protocolSection.eligibilityModule"}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    ps = data["protocolSection"]
    return {
        "nct_id": nct_id,
        "title": ps["identificationModule"].get("briefTitle", ""),
        "eligibility": ps["eligibilityModule"].get("eligibilityCriteria", ""),
    }


def call_claude(system: str, user_content: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


def patient_profile_text(patient) -> str:
    return (
        f"Age: {patient.age}\n"
        f"Sex: {patient.sex}\n"
        f"Diagnosis: {patient.diagnosis}\n"
        f"Biomarkers: {', '.join(patient.biomarkers)}\n"
        f"Prior treatments: {', '.join(patient.prior_treatments)}\n"
        f"ECOG PS: {patient.ecog_ps}\n"
        f"Location: {patient.location}\n"
        f"Notes: {patient.notes}"
    )


def assess_fix3(patient, trial: dict) -> dict:
    # Stage 1: fact extraction
    s1_user = f"Extract facts from this patient profile:\n\n{patient_profile_text(patient)}"
    extracted = call_claude(STAGE1_SYSTEM, s1_user)

    # Stage 2: eligibility assessment against extracted facts only
    s2_user = (
        f"CONFIRMED FACTS (extracted from patient profile, no inference added):\n"
        f"{json.dumps(extracted, indent=2)}\n\n"
        f"Trial: {trial['nct_id']} — {trial['title']}\n\n"
        f"Eligibility criteria:\n{trial['eligibility']}\n\n"
        f"Assess eligibility using ONLY the confirmed facts above."
    )
    assessment = call_claude(STAGE2_SYSTEM, s2_user)
    assessment["stage1_extracted"] = extracted
    return assessment


def main():
    print("Fix 3 — Two-stage fact extraction + assessment")
    print("="*60)

    print("\nFetching trial data...")
    trials_cache = {}
    for nct_id in set(tc["nct_id"] for tc in TEST_CASES):
        print(f"  {nct_id}...", end=" ", flush=True)
        trials_cache[nct_id] = fetch_trial(nct_id)
        print("ok")

    results = []
    for tc in TEST_CASES:
        trial = trials_cache[tc["nct_id"]]
        print(f"\n{tc['label']}")
        t0 = time.time()
        try:
            response = assess_fix3(tc["patient"], trial)
            elapsed = time.time() - t0
            verdict = response.get("verdict", "ERROR")
            confidence = response.get("confidence", 0)
            passed = verdict == tc["expect"]
            flag = "PASS" if passed else "FAIL"
            print(f"  {flag}  verdict={verdict} (conf={confidence:.2f}) expected={tc['expect']} [{elapsed:.1f}s]")
            if not passed:
                print(f"  explanation: {response.get('explanation', '')[:200]}")
            print(f"  stage1 NOT_in_profile: {response.get('stage1_extracted', {}).get('NOT_in_profile', [])}")
            results.append({
                "variant": "fix3",
                "label": tc["label"],
                "nct_id": tc["nct_id"],
                "patient_id": tc["patient"].patient_id,
                "expected": tc["expect"],
                "verdict": verdict,
                "confidence": confidence,
                "passed": passed,
                "explanation": response.get("explanation", ""),
                "exclusion_flags": response.get("exclusion_flags", []),
                "uncertain_items": response.get("uncertain_items", []),
                "stage1_extracted": response.get("stage1_extracted", {}),
                "elapsed_s": round(elapsed, 1),
            })
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"variant": "fix3", "label": tc["label"], "error": str(e), "passed": False})
        time.sleep(0.5)

    passed_count = sum(1 for r in results if r.get("passed"))
    print(f"\n{'='*60}")
    print(f"Fix 3 results: {passed_count}/{len(TEST_CASES)} passed")
    (OUT_DIR / "results.json").write_text(json.dumps(results, indent=2))
    print(f"Results saved to {OUT_DIR}/results.json")


if __name__ == "__main__":
    main()
