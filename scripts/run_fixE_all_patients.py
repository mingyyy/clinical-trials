"""
Fix E (Path 4) — Simplified structured extraction.

Architecture:
  Step 1 (LLM, once per patient) — extract typed patient record from profile.
                                    Same as fixD. null = "not stated."
  Step 2 (LLM, per trial)        — evaluate eligibility criteria against the typed
                                    record. Returns per-criterion results:
                                    CONFIRMED_MET / CONFIRMED_FAILED / DATA_MISSING.
                                    No intermediate predicates. No string matching.
  Step 3 (code)                  — compute verdict from per-criterion results.

Key difference from fixD: Step 2 sees both the patient record and the criteria.
This eliminates the two-LLM vocabulary agreement problem (string mismatch, wrong
cohort) while preserving inference isolation — the LLM checks a typed record with
explicit nulls, not a narrative profile.

Run with: .venv/bin/python scripts/run_fixE_all_patients.py
Output:   outputs/05_experiments/fixE/
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.api_client import fetch_trials, hard_filter_trials
from pipeline.config import MAX_PAGES, MODEL, SEARCH_RADIUS_MILES
from pipeline.test_profiles import TEST_PROFILES

# Reuse Step 1 from fixD
from scripts.test_prompt_fixD import (
    EXTRACT_SYSTEM,
    patient_text,
    add_derived_fields,
)

load_dotenv(PROJECT_ROOT / ".env")

OUT_DIR = PROJECT_ROOT / "outputs" / "05_experiments" / "fixE"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CONCURRENCY = 8
MAX_CRITERIA_CHARS = 6000

# ---------------------------------------------------------------------------
# Step 2': evaluate criteria against typed record (replaces fixD Steps 2+3)
# ---------------------------------------------------------------------------

EVAL_SYSTEM = """\
You are a clinical trial eligibility evaluator. You are given a typed patient record
(JSON) and eligibility criteria text.

For each material criterion, check the patient record and return one result:
- CONFIRMED_MET: the record has the relevant field AND it satisfies the criterion
- CONFIRMED_FAILED: the record has the relevant field AND it does NOT satisfy the criterion
- DATA_MISSING: the relevant field is null or absent in the record — cannot determine

CRITICAL RULES:
- You are checking the RECORD, not making clinical inferences.
- If a field is null, return DATA_MISSING. Do NOT infer what the value might be.
- null means "not stated." It does NOT mean false, zero, or absent.
- For list fields (e.g. prior_treatments), if ANY item has null for the checked field,
  return DATA_MISSING for criteria that depend on that field.
- For multi-cohort trials, identify which cohort the patient's disease type matches
  and evaluate criteria for THAT cohort only.

Return ONLY valid JSON — an array of evaluation objects:
[
  {
    "criterion_text": "brief description",
    "criterion_type": "inclusion" | "exclusion",
    "field_checked": "dot-path into record",
    "record_value": the actual value from the record (include null if null),
    "result": "CONFIRMED_MET" | "CONFIRMED_FAILED" | "DATA_MISSING"
  }
]
"""


# ---------------------------------------------------------------------------
# Verdict computation (same logic as fixD v2)
# ---------------------------------------------------------------------------

def compute_verdict(evaluations: list[dict]) -> tuple[str, list, list]:
    failures = []
    unknowns = []
    for e in evaluations:
        result = e.get("result", "")
        ctype = e.get("criterion_type", "inclusion")
        if result == "CONFIRMED_FAILED":
            failures.append(e)
        elif result == "CONFIRMED_MET" and ctype == "exclusion":
            failures.append(e)
        elif result == "DATA_MISSING" and ctype == "exclusion":
            unknowns.append(e)
    if failures:
        return "INELIGIBLE", failures, unknowns
    if unknowns:
        return "UNCERTAIN", failures, unknowns
    return "ELIGIBLE", [], []


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def call_claude_sync(client, system: str, user: str, max_tokens: int = 4096):
    msg = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw), msg.usage.input_tokens + msg.usage.output_tokens


def extract_patient_record(client, patient) -> tuple[dict, int]:
    record, tokens = call_claude_sync(
        client, EXTRACT_SYSTEM,
        f"Extract structured record from this patient profile:\n\n{patient_text(patient)}"
    )
    record = add_derived_fields(record)
    return record, tokens


# ---------------------------------------------------------------------------
# Per-trial assessment
# ---------------------------------------------------------------------------

async def assess_trial(client, trial: dict, patient_record: dict, semaphore) -> dict:
    async with semaphore:
        eligibility = trial["eligibility"]
        if len(eligibility) > MAX_CRITERIA_CHARS:
            eligibility = eligibility[:MAX_CRITERIA_CHARS] + "\n[truncated]"

        user_msg = (
            f"Patient record:\n{json.dumps(patient_record, indent=2)}\n\n"
            f"Eligibility criteria for {trial['nct_id']} — {trial['title'][:60]}:\n"
            f"{eligibility}\n\n"
            f"Evaluate each material criterion against the patient record. Return JSON only."
        )

        t0 = time.time()
        try:
            evaluations, tokens = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: call_claude_sync(client, EVAL_SYSTEM, user_msg, max_tokens=4096)
            )
            if isinstance(evaluations, dict):
                evaluations = evaluations.get("evaluations", [evaluations])

            verdict, failures, unknowns = compute_verdict(evaluations)
            return {
                "nct_id": trial["nct_id"],
                "title": trial["title"],
                "verdict": verdict,
                "evaluations": evaluations,
                "failures": [
                    {
                        "criterion_text": f.get("criterion_text", ""),
                        "field_checked": f.get("field_checked"),
                        "record_value": f.get("record_value"),
                        "result": f.get("result"),
                    }
                    for f in failures
                ],
                "unknowns_count": len(unknowns),
                "elapsed_s": round(time.time() - t0, 1),
                "tokens": tokens,
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


# ---------------------------------------------------------------------------
# Per-patient runner
# ---------------------------------------------------------------------------

async def run_patient(client, patient, patient_record: dict) -> dict:
    print(f"\n[{patient.patient_id}] Fetching trials...", end=" ", flush=True)
    t0 = time.time()

    trials = fetch_trials(
        condition=getattr(patient, "search_condition", None) or patient.diagnosis,
        lat=patient.lat,
        lon=patient.lon,
        radius_miles=SEARCH_RADIUS_MILES,
        max_pages=MAX_PAGES,
    )
    filtered = hard_filter_trials(trials, patient.age, patient.sex)
    print(f"{len(trials)} fetched, {len(filtered)} after hard filter")

    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [assess_trial(client, t, patient_record, semaphore) for t in filtered]
    matches = await asyncio.gather(*tasks)

    elapsed = time.time() - t0
    eligible   = sum(1 for m in matches if m["verdict"] == "ELIGIBLE")
    uncertain  = sum(1 for m in matches if m["verdict"] == "UNCERTAIN")
    ineligible = sum(1 for m in matches if m["verdict"] == "INELIGIBLE")
    errors     = sum(1 for m in matches if m["verdict"] == "ERROR")
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    print("Fix E (Path 4) — Simplified structured extraction")
    print("=" * 60)

    # Step 1: extract typed records
    print("\nExtracting typed patient records...")
    patient_records = {}
    record_tokens = 0
    for patient in TEST_PROFILES:
        record, tokens = extract_patient_record(client, patient)
        patient_records[patient.patient_id] = record
        record_tokens += tokens
        mt = record.get("prior_treatments", [])
        settings = [t.get("setting") for t in mt]
        print(f"  {patient.patient_id}: ecog={record.get('ecog_ps')} "
              f"metastatic={record.get('disease',{}).get('is_metastatic')} "
              f"treatment_settings={settings}")

    (OUT_DIR / "patient_records.json").write_text(json.dumps(patient_records, indent=2))

    # Step 2' + verdict: assess trials per patient
    all_results = []
    for patient in TEST_PROFILES:
        result = await run_patient(client, patient, patient_records[patient.patient_id])
        all_results.append(result)

    # Load baselines for comparison
    baseline_dir = PROJECT_ROOT / "outputs" / "02_rerun" / "langgraph"
    fixd_dir = PROJECT_ROOT / "outputs" / "05_experiments" / "prompt_fixes" / "fixD_v2"

    def load_dist(path):
        if not path.exists():
            return {}
        data = json.load(open(path))
        return {
            "eligible": sum(1 for m in data["matches"] if m["verdict"] == "ELIGIBLE"),
            "uncertain": sum(1 for m in data["matches"] if m["verdict"] == "UNCERTAIN"),
            "ineligible": sum(1 for m in data["matches"] if m["verdict"] == "INELIGIBLE"),
        }

    baseline = {p.patient_id: load_dist(baseline_dir / f"{p.patient_id}.json") for p in TEST_PROFILES}
    fixd     = {p.patient_id: load_dist(fixd_dir / f"{p.patient_id}.json") for p in TEST_PROFILES}

    total_tokens = record_tokens + sum(r["total_tokens"] for r in all_results)
    cost = total_tokens * 3 / 1_000_000

    print(f"\n{'='*90}")
    print("VERDICT DISTRIBUTION — Fix E vs Fix D v2 vs LangGraph baseline")
    print(f"{'='*90}")
    print(f"{'Patient':<8} {'N':>5} | {'LangGraph (E/U/I)':^20} | {'fixD v2 (E/U/I)':^20} | {'fixE (E/U/I)':^20}")
    print("-" * 90)

    for r in all_results:
        pid = r["patient_id"]
        b = baseline.get(pid, {})
        d = fixd.get(pid, {})
        b_str = f"{b.get('eligible',0)}/{b.get('uncertain',0)}/{b.get('ineligible',0)}"
        d_str = f"{d.get('eligible',0)}/{d.get('uncertain',0)}/{d.get('ineligible',0)}"
        e_str = f"{r['eligible']}/{r['uncertain']}/{r['ineligible']}"
        print(f"{pid:<8} {r['trials_assessed']:>5} | {b_str:^20} | {d_str:^20} | {e_str:^20}")

    # NCT04511013 check
    print(f"\n{'='*90}")
    print("NCT04511013 × P004 — inference isolation test")
    p4 = next(r for r in all_results if r["patient_id"] == "P004")
    target = next((m for m in p4["matches"] if m["nct_id"] == "NCT04511013"), None)
    if target:
        print(f"  verdict: {target['verdict']}  (expected: UNCERTAIN)")
    else:
        print("  NCT04511013 not in P004 results this run")

    print(f"\nTotal tokens: {total_tokens:,}  |  Estimated cost: ${cost:.2f}")

    # Summary
    summary = {
        "architecture": "fixE (Path 4): extract record → evaluate criteria against record → code verdict",
        "results": [{k: v for k, v in r.items() if k != "matches"} for r in all_results],
        "baseline_langgraph": baseline,
        "fixd_v2": fixd,
        "total_tokens": total_tokens,
        "estimated_cost": round(cost, 2),
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nFull results saved to {OUT_DIR}/")


if __name__ == "__main__":
    asyncio.run(main())
