"""
Fix D full run — all 5 patients.

Same pipeline as run_fixC_all_patients.py but uses Fix D:
  Step 1 (LLM, once per patient) — typed patient record extraction
  Step 2 (LLM, per trial)        — criterion → structured predicates
  Step 3 (code, per trial)       — deterministic predicate evaluation

Run with: .venv/bin/python run_fixD_all_patients.py
Output:   outputs/05_experiments/prompt_fixes/fixD_full/
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
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for test_prompt_fixD import
from pipeline.api_client import fetch_trials, hard_filter_trials
from pipeline.config import MAX_PAGES, MODEL, SEARCH_RADIUS_MILES
from pipeline.test_profiles import TEST_PROFILES

# Import evaluator components from fixD test script
from test_prompt_fixD import (
    EXTRACT_SYSTEM,
    PARSE_SYSTEM,
    MAX_CRITERIA_CHARS,
    evaluate_single,
    compute_verdict,
    patient_text,
    add_derived_fields,
)

load_dotenv(PROJECT_ROOT / ".env")

OUT_DIR = PROJECT_ROOT / "outputs" / "05_experiments" / "prompt_fixes" / "fixD_v2"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASELINE_DIR = PROJECT_ROOT / "outputs" / "02_rerun" / "langgraph"
FIXC_DIR = PROJECT_ROOT / "outputs" / "05_experiments" / "prompt_fixes" / "fixC_full"

CONCURRENCY = 8


def call_claude_sync(client, system: str, user: str, max_tokens: int = 2048):
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


async def assess_trial(client, trial: dict, patient_record: dict, semaphore) -> dict:
    async with semaphore:
        eligibility = trial["eligibility"]
        if len(eligibility) > MAX_CRITERIA_CHARS:
            eligibility = eligibility[:MAX_CRITERIA_CHARS] + "\n[truncated]"

        t0 = time.time()
        try:
            predicates, tokens = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: call_claude_sync(
                    client, PARSE_SYSTEM,
                    f"Parse these eligibility criteria into structured predicates:\n\n{eligibility}",
                    max_tokens=4096,
                )
            )
            if isinstance(predicates, dict):
                predicates = predicates.get("predicates", [predicates])

            evaluated = []
            for pred in predicates:
                result = evaluate_single(pred, patient_record)
                evaluated.append({**pred, "result": result})

            verdict, failures, unknowns = compute_verdict(evaluated)
            return {
                "nct_id": trial["nct_id"],
                "title": trial["title"],
                "verdict": verdict,
                "failures": [{"criterion_text": f["criterion_text"],
                               "variable": f.get("variable"),
                               "operator": f.get("operator"),
                               "required_value": f.get("required_value")} for f in failures],
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


async def main():
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    print("Fix D — Full run, all 5 patients")
    print("=" * 60)

    # Step 1: extract typed records for all patients (once each)
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

    # Step 2+3: assess trials per patient
    all_results = []
    for patient in TEST_PROFILES:
        result = await run_patient(client, patient, patient_records[patient.patient_id])
        all_results.append(result)

    # Load baselines for comparison
    def load_dist(path):
        if not path.exists():
            return {}
        data = json.load(open(path))
        return {
            "eligible": sum(1 for m in data["matches"] if m["verdict"] == "ELIGIBLE"),
            "uncertain": sum(1 for m in data["matches"] if m["verdict"] == "UNCERTAIN"),
            "ineligible": sum(1 for m in data["matches"] if m["verdict"] == "INELIGIBLE"),
        }

    baseline = {p.patient_id: load_dist(BASELINE_DIR / f"{p.patient_id}.json") for p in TEST_PROFILES}
    fixC     = {p.patient_id: load_dist(FIXC_DIR / f"{p.patient_id}.json") for p in TEST_PROFILES}

    total_tokens = record_tokens + sum(r["total_tokens"] for r in all_results)
    cost = total_tokens * 3 / 1_000_000

    print(f"\n{'='*85}")
    print("VERDICT DISTRIBUTION — Fix D vs Fix C vs LangGraph baseline (P001-P004 comparable)")
    print(f"{'='*85}")
    print(f"{'Patient':<8} {'N':>5} | {'LangGraph (E/U/I)':^20} | {'Fix C (E/U/I)':^20} | {'Fix D (E/U/I)':^20}")
    print("-" * 85)

    for r in all_results:
        pid = r["patient_id"]
        b  = baseline.get(pid, {})
        c  = fixC.get(pid, {})
        b_str = f"{b.get('eligible',0)}/{b.get('uncertain',0)}/{b.get('ineligible',0)}"
        c_str = f"{c.get('eligible',0)}/{c.get('uncertain',0)}/{c.get('ineligible',0)}"
        d_str = f"{r['eligible']}/{r['uncertain']}/{r['ineligible']}"
        note = " *" if pid == "P005" else ""
        print(f"{pid:<8} {r['trials_assessed']:>5} | {b_str:^20} | {c_str:^20} | {d_str:^20}{note}")

    print("\n* P005: no search_condition → broad query, data currency difference vs baseline")

    # NCT04511013 check
    print(f"\n{'='*85}")
    print("NCT04511013 × P004 — target case")
    p4 = next(r for r in all_results if r["patient_id"] == "P004")
    target = next((m for m in p4["matches"] if m["nct_id"] == "NCT04511013"), None)
    if target:
        print(f"  verdict: {target['verdict']}")
        if target.get("failures"):
            print(f"  confirmed_failures: {[f['criterion_text'][:60] for f in target['failures']]}")
        print(f"  data_missing_exclusions: {target.get('unknowns_count', 0)}")
    else:
        print("  NCT04511013 not in P004 results this run")

    # P002 check — the Fix C over-INELIGIBLE problem
    print(f"\nP002 distribution detail:")
    p2 = next(r for r in all_results if r["patient_id"] == "P002")
    p2_failures_driven_by = {}
    for m in p2["matches"]:
        if m["verdict"] == "INELIGIBLE":
            for f in m.get("failures", []):
                var = f.get("variable", "unparsed")
                p2_failures_driven_by[var] = p2_failures_driven_by.get(var, 0) + 1
    print(f"  INELIGIBLE verdicts by driving variable:")
    for var, count in sorted(p2_failures_driven_by.items(), key=lambda x: -x[1])[:8]:
        print(f"    {count:>3}x  {var}")

    print(f"\nTotal tokens: {total_tokens:,}  |  Estimated cost: ${cost:.2f}")

    (OUT_DIR / "summary.json").write_text(json.dumps({
        "results": all_results,
        "baseline": baseline,
        "fixC": fixC,
        "total_tokens": total_tokens,
        "estimated_cost": round(cost, 2),
    }, indent=2))
    print(f"\nFull results saved to {OUT_DIR}/")


if __name__ == "__main__":
    asyncio.run(main())
