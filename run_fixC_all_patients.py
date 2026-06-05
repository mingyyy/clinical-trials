"""
Fix C full run — all 5 patients.

Uses the same trial fetching pipeline as the main frameworks (same radius,
same page count, same hard filter). Runs Fix C annotation-first assessment
on every filtered trial, then compares verdict distributions against the
LangGraph rerun baseline.

Run with: .venv/bin/python run_fixC_all_patients.py
Output:   outputs/05_experiments/prompt_fixes/fixC_full/{P001..P005}.json
          outputs/05_experiments/prompt_fixes/fixC_full/summary.json
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.api_client import fetch_trials, hard_filter_trials
from pipeline.config import MAX_PAGES, MAX_TOKENS, MODEL, SEARCH_RADIUS_MILES
from pipeline.test_profiles import TEST_PROFILES

load_dotenv(Path(__file__).parent / ".env")

OUT_DIR = Path(__file__).parent / "outputs" / "05_experiments" / "prompt_fixes" / "fixC_full"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASELINE_DIR = Path(__file__).parent / "outputs" / "02_rerun" / "langgraph"

CONCURRENCY = 10   # parallel LLM calls per patient

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

Focus on the most important criteria. Prioritise criteria that could actually affect eligibility.

Respond ONLY with valid JSON (no markdown):
{
  "criteria": [
    {
      "criterion": "brief description",
      "type": "inclusion" or "exclusion",
      "status": "CONFIRMED_MET" or "CONFIRMED_FAILED" or "DATA_MISSING",
      "profile_citation": "exact quote from profile, or null",
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


def is_valid_citation(citation, profile_text: str) -> bool:
    if not citation:
        return False
    citation_norm = " ".join(citation.lower().split())
    profile_norm = " ".join(profile_text.lower().split())
    return citation_norm in profile_norm


def compute_verdict(criteria: list[dict], profile_text: str) -> tuple[str, list, list]:
    confirmed_failures, material_unknowns = [], []
    for c in criteria:
        if c["status"] == "CONFIRMED_FAILED":
            if is_valid_citation(c.get("profile_citation"), profile_text):
                confirmed_failures.append(c)
            else:
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


async def assess_trial(client, patient, trial: dict, profile_text: str, semaphore) -> dict:
    import anthropic
    async with semaphore:
        user_content = (
            f"Patient profile:\n{profile_text}\n\n"
            f"Trial: {trial['nct_id']} — {trial['title']}\n\n"
            f"Eligibility criteria:\n{trial['eligibility']}\n\n"
            f"Annotate each material criterion."
        )
        t0 = time.time()
        try:
            msg = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.messages.create(
                    model=MODEL,
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_content}],
                )
            )
            raw = msg.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            annotations = json.loads(raw)
            criteria = annotations.get("criteria", [])
            verdict, failures, unknowns = compute_verdict(criteria, profile_text)
            return {
                "nct_id": trial["nct_id"],
                "title": trial["title"],
                "verdict": verdict,
                "confirmed_failures": failures,
                "material_unknowns": unknowns,
                "all_criteria": criteria,
                "summary": annotations.get("summary", ""),
                "elapsed_s": round(time.time() - t0, 1),
                "tokens": msg.usage.input_tokens + msg.usage.output_tokens,
            }
        except Exception as e:
            return {
                "nct_id": trial["nct_id"],
                "title": trial["title"],
                "verdict": "ERROR",
                "error": str(e),
                "elapsed_s": round(time.time() - t0, 1),
                "tokens": 0,
            }


async def run_patient(client, patient) -> dict:
    print(f"\n[{patient.patient_id}] Fetching trials...", end=" ", flush=True)
    t0 = time.time()

    trials = fetch_trials(
        condition=getattr(patient, "search_condition", patient.diagnosis),
        lat=patient.lat,
        lon=patient.lon,
        radius_miles=SEARCH_RADIUS_MILES,
        max_pages=MAX_PAGES,
    )
    filtered = hard_filter_trials(trials, patient.age, patient.sex)
    print(f"{len(trials)} fetched, {len(filtered)} after hard filter")

    profile_text = patient_profile_text(patient)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    tasks = [assess_trial(client, patient, t, profile_text, semaphore) for t in filtered]
    matches = await asyncio.gather(*tasks)

    elapsed = time.time() - t0
    eligible = sum(1 for m in matches if m["verdict"] == "ELIGIBLE")
    uncertain = sum(1 for m in matches if m["verdict"] == "UNCERTAIN")
    ineligible = sum(1 for m in matches if m["verdict"] == "INELIGIBLE")
    errors = sum(1 for m in matches if m["verdict"] == "ERROR")
    total_tokens = sum(m.get("tokens", 0) for m in matches)

    print(f"  [{patient.patient_id}] E={eligible} U={uncertain} I={ineligible} err={errors} "
          f"| {len(matches)} assessed | {total_tokens:,} tokens | {elapsed:.0f}s")

    result = {
        "patient_id": patient.patient_id,
        "trials_fetched": len(trials),
        "trials_assessed": len(filtered),
        "eligible": eligible,
        "uncertain": uncertain,
        "ineligible": ineligible,
        "errors": errors,
        "total_tokens": total_tokens,
        "elapsed_s": round(elapsed, 1),
        "matches": matches,
    }
    (OUT_DIR / f"{patient.patient_id}.json").write_text(json.dumps(result, indent=2))
    return result


async def main():
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    print("Fix C — Full run, all 5 patients")
    print("=" * 60)

    all_results = []
    for patient in TEST_PROFILES:
        result = await run_patient(client, patient)
        all_results.append(result)

    # Load LangGraph baseline for comparison
    baseline = {}
    for patient in TEST_PROFILES:
        path = BASELINE_DIR / f"{patient.patient_id}.json"
        if path.exists():
            data = json.load(open(path))
            baseline[patient.patient_id] = {
                "eligible": sum(1 for m in data["matches"] if m["verdict"] == "ELIGIBLE"),
                "uncertain": sum(1 for m in data["matches"] if m["verdict"] == "UNCERTAIN"),
                "ineligible": sum(1 for m in data["matches"] if m["verdict"] == "INELIGIBLE"),
            }

    # Summary table
    print(f"\n{'='*75}")
    print("VERDICT DISTRIBUTION — Fix C vs LangGraph baseline")
    print(f"{'='*75}")
    print(f"{'Patient':<8} {'Assessed':>8} | {'LangGraph (E/U/I)':^20} | {'Fix C (E/U/I)':^20} | {'Delta U'}")
    print("-" * 75)

    total_tokens = 0
    for r in all_results:
        pid = r["patient_id"]
        b = baseline.get(pid, {})
        b_str = f"{b.get('eligible',0)}/{b.get('uncertain',0)}/{b.get('ineligible',0)}"
        c_str = f"{r['eligible']}/{r['uncertain']}/{r['ineligible']}"
        delta_u = r["uncertain"] - b.get("uncertain", 0)
        delta_str = f"+{delta_u}" if delta_u > 0 else str(delta_u)
        total_tokens += r["total_tokens"]
        print(f"{pid:<8} {r['trials_assessed']:>8} | {b_str:^20} | {c_str:^20} | {delta_str}")

    cost = total_tokens * 3 / 1_000_000
    print(f"\nTotal tokens: {total_tokens:,}  |  Estimated cost: ${cost:.2f}")

    # Special check: NCT04511013 for P004
    print(f"\n{'='*75}")
    print("NCT04511013 × P004 — target case check")
    p4 = next(r for r in all_results if r["patient_id"] == "P004")
    target = next((m for m in p4["matches"] if m["nct_id"] == "NCT04511013"), None)
    if target:
        print(f"  verdict: {target['verdict']}")
        print(f"  summary: {target.get('summary','')[:200]}")
        print(f"  material_unknowns: {[u['criterion'][:60] for u in target.get('material_unknowns',[])[:3]]}")
    else:
        print("  NCT04511013 not found in P004 results (may not have been fetched this run)")

    (OUT_DIR / "summary.json").write_text(json.dumps({
        "results": all_results,
        "baseline": baseline,
        "total_tokens": total_tokens,
        "estimated_cost": round(cost, 2),
    }, indent=2))
    print(f"\nFull results saved to {OUT_DIR}/")


if __name__ == "__main__":
    asyncio.run(main())
