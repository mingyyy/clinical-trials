"""
Prompt fix experiment — test three prompt variants against a targeted set of
patient × trial pairs.

Goal: verify that each fix causes NCT04511013 × P004 to return UNCERTAIN
(the target) without inflating UNCERTAIN on cases that are clearly INELIGIBLE.

Test cases:
  TARGET  : P004 × NCT04511013  → expect UNCERTAIN (prior treatment setting absent)
  SANITY1 : P004 × NCT06246916  → expect INELIGIBLE (explicit prior ipi+nivo exclusion in clear text)
  SANITY2 : P004 × NCT05727904  → expect INELIGIBLE (brain mets exclusion + ECOG 0-1 only)
  SANITY3 : P001 × NCT07060807  → expect INELIGIBLE (HER2+ patient, trial requires HER2-)

Run with: .venv/bin/python test_prompt_fixes.py
Outputs:  outputs/05_experiments/prompt_fixes/fix{1,2,3}/results.json
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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024
OUT_BASE = Path(__file__).parent / "outputs" / "05_experiments" / "prompt_fixes"

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

# ---------------------------------------------------------------------------
# Prompt variants
# ---------------------------------------------------------------------------

BASE_RULES = (
    "CRITICAL RULES:\n"
    "1. Absence of information is NOT evidence of ineligibility. "
    "   If a criterion is not mentioned in the profile, mark it in uncertain_items — do NOT treat it as failed.\n"
    "2. Only mark INELIGIBLE when the profile contains direct evidence that an exclusion criterion is triggered "
    "   or an inclusion criterion is clearly not met.\n"
    "3. For UNCERTAIN: list specifically what additional data would confirm or deny eligibility.\n\n"
)

SYSTEM_PROMPTS = {
    "baseline": (
        "You are a clinical trial eligibility screener. "
        "Your job is to identify candidates for further review, not to make final enrollment decisions. "
        "Given a patient profile and trial eligibility criteria, classify the patient as:\n"
        "  ELIGIBLE   — patient clearly meets all stated criteria based on available information\n"
        "  INELIGIBLE — patient clearly fails at least one criterion (requires positive evidence)\n"
        "  UNCERTAIN  — patient may be eligible but the profile is missing data needed to confirm\n\n"
        + BASE_RULES +
        "Respond ONLY with valid JSON (no markdown) with keys:\n"
        "  verdict (string: ELIGIBLE|INELIGIBLE|UNCERTAIN)\n"
        "  confidence (float 0.0-1.0)\n"
        "  matched_criteria (list of strings — criteria clearly met)\n"
        "  exclusion_flags (list of strings — criteria clearly failed, with evidence from profile)\n"
        "  uncertain_items (list of strings — criteria that cannot be evaluated due to missing data)\n"
        "  explanation (string, 1-3 sentences citing specific criteria and evidence)"
    ),

    "fix1": (
        "You are a clinical trial eligibility screener. "
        "Your job is to identify candidates for further review, not to make final enrollment decisions. "
        "Given a patient profile and trial eligibility criteria, classify the patient as:\n"
        "  ELIGIBLE   — patient clearly meets all stated criteria based on available information\n"
        "  INELIGIBLE — patient clearly fails at least one criterion (requires positive evidence)\n"
        "  UNCERTAIN  — patient may be eligible but the profile is missing data needed to confirm\n\n"
        "CRITICAL RULES:\n"
        "1. Absence of information is NOT evidence of ineligibility. "
        "   If a criterion is not mentioned in the profile, mark it in uncertain_items — do NOT treat it as failed.\n"
        "2. Only mark INELIGIBLE when the profile contains direct evidence that an exclusion criterion is triggered "
        "   or an inclusion criterion is clearly not met. "
        "   Direct evidence means text explicitly stated in the profile — "
        "   NOT inferences from diagnosis, disease stage, or standard-of-care context.\n"
        "3. For UNCERTAIN: list specifically what additional data would confirm or deny eligibility.\n\n"
        "Respond ONLY with valid JSON (no markdown) with keys:\n"
        "  verdict (string: ELIGIBLE|INELIGIBLE|UNCERTAIN)\n"
        "  confidence (float 0.0-1.0)\n"
        "  matched_criteria (list of strings — criteria clearly met)\n"
        "  exclusion_flags (list of strings — criteria clearly failed, with evidence from profile)\n"
        "  uncertain_items (list of strings — criteria that cannot be evaluated due to missing data)\n"
        "  explanation (string, 1-3 sentences citing specific criteria and evidence)"
    ),

    "fix2": (
        "You are a clinical trial eligibility screener. "
        "Your job is to identify candidates for further review, not to make final enrollment decisions. "
        "Given a patient profile and trial eligibility criteria, classify the patient as:\n"
        "  ELIGIBLE   — patient clearly meets all stated criteria based on available information\n"
        "  INELIGIBLE — patient clearly fails at least one criterion (requires positive evidence)\n"
        "  UNCERTAIN  — patient may be eligible but the profile is missing data needed to confirm\n\n"
        "CRITICAL RULES:\n"
        "1. Absence of information is NOT evidence of ineligibility. "
        "   If a criterion is not mentioned in the profile, mark it in uncertain_items — do NOT treat it as failed.\n"
        "2. Only mark INELIGIBLE when the profile contains direct evidence that an exclusion criterion is triggered "
        "   or an inclusion criterion is clearly not met. "
        "   Direct evidence means text explicitly stated in the profile — "
        "   NOT inferences from diagnosis, disease stage, or standard-of-care context.\n"
        "3. For each item in exclusion_flags, you must provide the exact text from the patient profile "
        "   that constitutes direct evidence. If you can only infer — not cite — that an exclusion applies, "
        "   it must appear in uncertain_items instead of exclusion_flags.\n"
        "4. For UNCERTAIN: list specifically what additional data would confirm or deny eligibility.\n\n"
        "Respond ONLY with valid JSON (no markdown) with keys:\n"
        "  verdict (string: ELIGIBLE|INELIGIBLE|UNCERTAIN)\n"
        "  confidence (float 0.0-1.0)\n"
        "  matched_criteria (list of strings — criteria clearly met)\n"
        "  exclusion_flags (list of strings — criteria clearly failed, WITH the exact profile text cited)\n"
        "  uncertain_items (list of strings — criteria that cannot be evaluated due to missing data, "
        "                   including criteria where only an inference was possible)\n"
        "  explanation (string, 1-3 sentences citing specific criteria and profile evidence)"
    ),
}

# ---------------------------------------------------------------------------
# Fetch trial from ClinicalTrials.gov
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Call Claude
# ---------------------------------------------------------------------------

def call_claude(system_prompt: str, patient, trial: dict) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    user_content = f"""Patient:
- Age: {patient.age}, Sex: {patient.sex}
- Diagnosis: {patient.diagnosis}
- Biomarkers: {', '.join(patient.biomarkers)}
- Prior treatments: {', '.join(patient.prior_treatments)}
- ECOG PS: {patient.ecog_ps}
- Notes: {patient.notes}

Trial: {trial['nct_id']} — {trial['title']}

Eligibility criteria:
{trial['eligibility']}

Assess eligibility and respond with JSON only."""

    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = msg.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_variant(variant_name: str, system_prompt: str, trials_cache: dict) -> list[dict]:
    print(f"\n{'='*60}")
    print(f"VARIANT: {variant_name}")
    print('='*60)
    results = []
    for tc in TEST_CASES:
        trial = trials_cache[tc["nct_id"]]
        t0 = time.time()
        try:
            response = call_claude(system_prompt, tc["patient"], trial)
            elapsed = time.time() - t0
            verdict = response.get("verdict", "ERROR")
            confidence = response.get("confidence", 0)
            passed = "PASS" if verdict == tc["expect"] else "FAIL"
            print(f"  {passed}  {tc['label']}")
            print(f"        verdict={verdict} (conf={confidence:.2f}) expected={tc['expect']} [{elapsed:.1f}s]")
            if verdict != tc["expect"]:
                print(f"        explanation: {response.get('explanation', '')[:200]}")
            results.append({
                "variant": variant_name,
                "label": tc["label"],
                "nct_id": tc["nct_id"],
                "patient_id": tc["patient"].patient_id,
                "expected": tc["expect"],
                "verdict": verdict,
                "confidence": confidence,
                "passed": verdict == tc["expect"],
                "explanation": response.get("explanation", ""),
                "exclusion_flags": response.get("exclusion_flags", []),
                "uncertain_items": response.get("uncertain_items", []),
                "matched_criteria": response.get("matched_criteria", []),
                "elapsed_s": round(elapsed, 1),
            })
        except Exception as e:
            print(f"  ERROR {tc['label']}: {e}")
            results.append({"variant": variant_name, "label": tc["label"],
                            "error": str(e), "passed": False})
        time.sleep(0.5)
    return results


def main():
    print("Fetching trial data from ClinicalTrials.gov...")
    trials_cache = {}
    for nct_id in set(tc["nct_id"] for tc in TEST_CASES):
        print(f"  {nct_id}...", end=" ", flush=True)
        trials_cache[nct_id] = fetch_trial(nct_id)
        print("ok")

    all_results = []

    # Run baseline first so we have a reference
    for variant_name in ["baseline", "fix1", "fix2"]:
        results = run_variant(variant_name, SYSTEM_PROMPTS[variant_name], trials_cache)
        all_results.extend(results)

        # Save after each variant in case of interrupt
        out_dir = OUT_BASE / variant_name
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "results.json").write_text(json.dumps(results, indent=2))

    # Summary table
    print(f"\n{'='*60}")
    print("SUMMARY")
    print('='*60)
    header = f"{'Label':<35} {'baseline':^12} {'fix1':^8} {'fix2':^8}"
    print(header)
    print("-" * len(header))
    for tc in TEST_CASES:
        row = f"{tc['label']:<35}"
        for v in ["baseline", "fix1", "fix2"]:
            match = next((r for r in all_results if r.get("variant") == v
                          and r.get("label") == tc["label"]), None)
            if match and "verdict" in match:
                verdict = match["verdict"]
                flag = "✓" if match["passed"] else "✗"
                row += f" {flag}{verdict:<11}"
            else:
                row += f" {'ERR':<12}"
        print(row)

    # Save combined
    (OUT_BASE / "all_results.json").write_text(json.dumps(all_results, indent=2))
    print(f"\nResults saved to {OUT_BASE}/")
    print("Fix 3 (two-stage extraction) requires a separate script — run test_prompt_fix3.py next.")


if __name__ == "__main__":
    main()
