"""
Fix C — Annotation-first, verdict-derived.

The model never produces a verdict. It annotates each criterion as:
  CONFIRMED_MET    — profile explicitly confirms criterion is satisfied
  CONFIRMED_FAILED — profile explicitly contradicts criterion (requires literal citation)
  DATA_MISSING     — profile does not contain information needed to evaluate this criterion

The verdict is computed by code from the annotations:
  any CONFIRMED_FAILED with a valid citation  →  INELIGIBLE
  any material DATA_MISSING on an exclusion   →  UNCERTAIN
  otherwise                                   →  ELIGIBLE

Key constraint: profile_citation for CONFIRMED_FAILED must be a direct quote
from the profile that on its own — without inference — confirms the criterion
was triggered. The code validates this by checking the citation is a
substring of the patient profile text.

Run with: .venv/bin/python test_prompt_fixC.py
Output:   outputs/05_experiments/prompt_fixes/fixC/results.json
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
OUT_DIR = Path(__file__).parent / "outputs" / "05_experiments" / "prompt_fixes" / "fixC"
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

SYSTEM_PROMPT = """\
You are a clinical trial eligibility annotator. Do NOT produce a verdict.

Your job: for each criterion in the eligibility text, produce a structured annotation.

For each criterion, set status to exactly one of:
  CONFIRMED_MET    — the patient profile explicitly states something that satisfies this criterion
  CONFIRMED_FAILED — the patient profile explicitly states something that directly triggers this
                     exclusion or directly fails this inclusion requirement
  DATA_MISSING     — the profile does not contain the specific information needed to evaluate
                     this criterion

Rules for CONFIRMED_FAILED:
  - profile_citation MUST be an exact, word-for-word quote taken directly from the patient profile
  - The citation must, on its own and without any clinical inference, directly confirm the
    criterion was triggered
  - If confirming the criterion requires combining the citation with clinical knowledge
    (e.g. "this drug is typically used for X disease stage"), the status is DATA_MISSING
  - Example of a VALID citation: criterion says "ECOG must be 0-1", profile says "ECOG PS: 2"
    → citation "ECOG PS: 2" directly fails the criterion without inference
  - Example of an INVALID citation: criterion says "no prior systemic therapy for metastatic
    disease", profile says "prior treatments: ipilimumab, nivolumab" → this citation does NOT
    directly state the treatment was given for metastatic disease; the setting requires inference
    → status must be DATA_MISSING

For DATA_MISSING, also set material: true if this criterion, if evaluated, could plausibly
disqualify this specific patient given what IS known about them; false if it is a routine
administrative check unlikely to apply (e.g. standard lab value ranges for a non-critically ill
patient, pregnancy exclusion for a male patient).

Focus on the most important criteria (key inclusions and exclusions). You do not need to list
every single sub-criterion — prioritise criteria that could actually affect eligibility.

Respond ONLY with valid JSON (no markdown):
{
  "criteria": [
    {
      "criterion": "brief description of the criterion",
      "type": "inclusion" or "exclusion",
      "status": "CONFIRMED_MET" or "CONFIRMED_FAILED" or "DATA_MISSING",
      "profile_citation": "exact quote from profile, or null if DATA_MISSING",
      "material": true or false
    }
  ],
  "summary": "1-2 sentence overall assessment"
}
"""


def patient_profile_text(patient) -> str:
    return (
        f"Age: {patient.age}, Sex: {patient.sex}\n"
        f"Diagnosis: {patient.diagnosis}\n"
        f"Biomarkers: {', '.join(patient.biomarkers)}\n"
        f"Prior treatments: {', '.join(patient.prior_treatments)}\n"
        f"ECOG PS: {patient.ecog_ps}\n"
        f"Location: {patient.location}\n"
        f"Notes: {patient.notes}"
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


def call_claude(patient, trial: dict) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    profile_text = patient_profile_text(patient)
    user_content = (
        f"Patient profile:\n{profile_text}\n\n"
        f"Trial: {trial['nct_id']} — {trial['title']}\n\n"
        f"Eligibility criteria:\n{trial['eligibility']}\n\n"
        f"Annotate each material criterion."
    )
    msg = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw), profile_text


def is_valid_citation(citation: str | None, profile_text: str) -> bool:
    """Citation is valid only if it is a literal substring of the profile text."""
    if not citation:
        return False
    # Normalise whitespace for comparison
    citation_norm = " ".join(citation.lower().split())
    profile_norm = " ".join(profile_text.lower().split())
    return citation_norm in profile_norm


def compute_verdict(criteria: list[dict], profile_text: str) -> tuple[str, list[dict], list[dict]]:
    """
    Derive verdict from criterion annotations.

    Returns: (verdict, confirmed_failures, material_unknowns)
    """
    confirmed_failures = []
    material_unknowns = []

    for c in criteria:
        if c["status"] == "CONFIRMED_FAILED":
            citation = c.get("profile_citation")
            if is_valid_citation(citation, profile_text):
                confirmed_failures.append(c)
            else:
                # Citation not a literal profile quote — treat as DATA_MISSING
                c["_citation_rejected"] = True
                if c.get("material", True):
                    material_unknowns.append(c)

        elif c["status"] == "DATA_MISSING" and c.get("material", False):
            material_unknowns.append(c)

    if confirmed_failures:
        return "INELIGIBLE", confirmed_failures, material_unknowns
    if material_unknowns:
        return "UNCERTAIN", confirmed_failures, material_unknowns
    return "ELIGIBLE", [], []


def main():
    print("Fix C — Annotation-first, verdict-derived")
    print("=" * 60)

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
            annotations, profile_text = call_claude(tc["patient"], trial)
            elapsed = time.time() - t0

            criteria = annotations.get("criteria", [])
            verdict, failures, unknowns = compute_verdict(criteria, profile_text)
            passed = verdict == tc["expect"]
            flag = "PASS" if passed else "FAIL"

            print(f"  {flag}  verdict={verdict}  expected={tc['expect']}  [{elapsed:.1f}s]")
            print(f"  summary: {annotations.get('summary', '')[:160]}")

            # Show what drove the verdict
            if failures:
                print(f"  confirmed_failures ({len(failures)}):")
                for f in failures:
                    print(f"    [{f['type']}] {f['criterion']}")
                    print(f"      citation: \"{f.get('profile_citation', '')}\"")
            if unknowns:
                print(f"  material_unknowns ({len(unknowns)}):")
                for u in unknowns[:4]:
                    rejected = " [citation rejected]" if u.get("_citation_rejected") else ""
                    print(f"    [{u['type']}] {u['criterion']}{rejected}")

            results.append({
                "variant": "fixC",
                "label": tc["label"],
                "nct_id": tc["nct_id"],
                "patient_id": tc["patient"].patient_id,
                "expected": tc["expect"],
                "verdict": verdict,
                "passed": passed,
                "summary": annotations.get("summary", ""),
                "confirmed_failures": failures,
                "material_unknowns": unknowns,
                "all_criteria": criteria,
                "elapsed_s": round(elapsed, 1),
            })
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()
            results.append({"variant": "fixC", "label": tc["label"],
                            "error": str(e), "passed": False})
        time.sleep(0.5)

    passed_count = sum(1 for r in results if r.get("passed"))
    print(f"\n{'='*60}")
    print(f"Fix C results: {passed_count}/{len(TEST_CASES)} passed")

    # Compare with previous fixes
    prev_path = Path(__file__).parent / "outputs" / "05_experiments" / "prompt_fixes" / "all_results.json"
    if prev_path.exists():
        prev = json.load(open(prev_path))
        print(f"\n{'Label':<35} {'baseline':^12} {'fix1':^8} {'fix2':^8} {'fixC':^8}")
        print("-" * 75)
        for tc in TEST_CASES:
            row = f"{tc['label']:<35}"
            for v in ["baseline", "fix1", "fix2"]:
                m = next((r for r in prev if r.get("variant") == v
                          and r.get("label") == tc["label"]), None)
                if m and "verdict" in m:
                    flag = "✓" if m["passed"] else "✗"
                    row += f" {flag}{m['verdict'][:10]:<11}"
                else:
                    row += f" {'ERR':<12}"
            m = next((r for r in results if r.get("label") == tc["label"]), None)
            if m and "verdict" in m:
                flag = "✓" if m["passed"] else "✗"
                row += f" {flag}{m['verdict'][:7]}"
            print(row)

    (OUT_DIR / "results.json").write_text(json.dumps(results, indent=2))
    print(f"\nResults saved to {OUT_DIR}/results.json")


if __name__ == "__main__":
    main()
