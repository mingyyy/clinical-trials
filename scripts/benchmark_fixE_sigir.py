"""
Benchmark fixE against SIGIR cohort from TrialGPT dataset.

Architecture (same as fixE):
  Step 1 (LLM, once per patient) — extract typed patient record from clinical note
  Step 2 (LLM, per trial)        — evaluate criteria against typed record
  Step 3 (code)                  — compute verdict from per-criterion results

Compares against SIGIR qrels: 0=not relevant, 1=excluded, 2=eligible.

Run with: .venv/bin/python scripts/benchmark_fixE_sigir.py [--patients N] [--start N]
Output:   outputs/benchmark/sigir/
"""

import asyncio
import json
import os
import sys
import time
import argparse
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.test_prompt_fixD import (
    EXTRACT_SYSTEM,
    add_derived_fields,
)

load_dotenv(PROJECT_ROOT / ".env")

MODEL = "claude-sonnet-4-6"
BENCHMARK_DIR = PROJECT_ROOT / "benchmark" / "trialgpt"
OUT_DIR = PROJECT_ROOT / "outputs" / "benchmark" / "sigir"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CONCURRENCY = 8
MAX_CRITERIA_CHARS = 6000

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# Step 1: adapted for free-text clinical notes (richer than our structured profiles)
EXTRACT_CLINICAL_NOTE = """\
You are a medical data extractor. Convert the free-text clinical note into a
typed JSON record using the schema below.

CRITICAL RULES:
- Use null for ANY field not explicitly stated in the note.
- Do NOT infer, assume, or use clinical knowledge to fill gaps.
- "disease.is_metastatic" = true only if the note mentions metastatic disease,
  distant metastases, or stage IV. null if not mentioned.
- "disease.is_locally_advanced" = true only if the note says "locally advanced"
  or "unresectable." null otherwise.
- For prior_treatments, extract each drug/therapy mentioned with whatever context
  is given (setting, completion status). Use null for unknown fields.
- Do NOT set a field to false unless the note explicitly contradicts it.
  Missing information is null, not false.

Return ONLY valid JSON, no markdown. Schema:

{
  "age": integer or null,
  "sex": "M" | "F" | null,
  "ecog_ps": integer 0-4 or null,
  "disease": {
    "primary_condition": string or null,
    "histology_subtype": string or null,
    "stage_numeric": "I" | "II" | "III" | "IV" | null,
    "is_metastatic": true | false | null,
    "is_locally_advanced": true | false | null,
    "is_unresectable": true | false | null,
    "is_recurrent": true | false | null,
    "is_advanced_measurable": true | false | null,
    "metastatic_sites": [string] or null,
    "brain_metastases_present": true | false | null
  },
  "biomarkers": {
    "her2": "positive" | "negative" | "equivocal" | null,
    "her2_ihc_score": 0 | 1 | 2 | 3 | null,
    "er": "positive" | "negative" | null,
    "pr": "positive" | "negative" | null,
    "braf_status": "mutant" | "wildtype" | null,
    "braf_variant": "V600E" | "V600K" | "other" | null,
    "brca1": "pathogenic" | "wildtype" | null,
    "brca2": "pathogenic" | "wildtype" | null,
    "msi": "MSI-H" | "MSS" | null,
    "pdl1_expression": string or null,
    "tp53_status": "wildtype" | "mutant" | "Y220C" | null,
    "pik3ca_status": "wildtype" | "mutant" | null
  },
  "comorbidities": [string],
  "medications": [string],
  "lab_values": {},
  "vital_signs": {},
  "prior_treatments": [
    {
      "drug": string,
      "drug_class": string or null,
      "setting": "adjuvant" | "neoadjuvant" | "metastatic" | "palliative" | null,
      "line_of_therapy": integer or null,
      "completed": true | false | null
    }
  ]
}
"""

# Step 2: same EVAL_SYSTEM as fixE
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
# Helpers
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


def verdict_to_qrel(verdict: str) -> int:
    """Map fixE verdict to SIGIR qrel label."""
    if verdict == "ELIGIBLE":
        return 2
    elif verdict == "INELIGIBLE":
        return 1  # excluded
    else:  # UNCERTAIN or ERROR
        return 1  # conservative: treat as excluded (can't confirm eligible)


# ---------------------------------------------------------------------------
# Per-trial assessment
# ---------------------------------------------------------------------------

async def assess_trial(client, trial: dict, patient_record: dict, semaphore) -> dict:
    async with semaphore:
        inc = trial.get("inclusion_criteria", "")
        exc = trial.get("exclusion_criteria", "")
        criteria = ""
        if inc:
            criteria += f"Inclusion Criteria:\n{inc}\n\n"
        if exc:
            criteria += f"Exclusion Criteria:\n{exc}\n"
        if len(criteria) > MAX_CRITERIA_CHARS:
            criteria = criteria[:MAX_CRITERIA_CHARS] + "\n[truncated]"

        user_msg = (
            f"Patient record:\n{json.dumps(patient_record, indent=2)}\n\n"
            f"Eligibility criteria for {trial['NCTID']} — {trial['brief_title'][:60]}:\n"
            f"{criteria}\n\n"
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
                "nct_id": trial["NCTID"],
                "title": trial["brief_title"],
                "verdict": verdict,
                "qrel_pred": verdict_to_qrel(verdict),
                "num_criteria": len(evaluations),
                "num_failures": len(failures),
                "num_unknowns": len(unknowns),
                "elapsed_s": round(time.time() - t0, 1),
                "tokens": tokens,
            }
        except Exception as e:
            return {
                "nct_id": trial["NCTID"],
                "title": trial["brief_title"],
                "verdict": "ERROR",
                "qrel_pred": 1,
                "error": str(e),
                "elapsed_s": round(time.time() - t0, 1),
                "tokens": 0,
            }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--patients", type=int, default=5, help="Number of patients to run (default 5)")
    parser.add_argument("--start", type=int, default=0, help="Start index (default 0)")
    args = parser.parse_args()

    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    print(f"fixE Benchmark — SIGIR cohort (patients {args.start} to {args.start + args.patients - 1})")
    print("=" * 70)

    # Load data
    dataset = json.load(open(BENCHMARK_DIR / "dataset" / "sigir" / "retrieved_trials.json"))

    # Load qrels
    import csv
    qrels = {}
    with open(BENCHMARK_DIR / "dataset" / "sigir" / "qrels" / "test.tsv") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            qrels[(row["query-id"], row["corpus-id"])] = int(row["score"])

    # Select patients
    patients_to_run = dataset[args.start : args.start + args.patients]

    all_results = []
    total_tokens = 0
    total_correct = 0
    total_assessed = 0

    for pidx, entry in enumerate(patients_to_run):
        patient_id = entry["patient_id"]
        patient_text = entry["patient"]

        # Collect all trials for this patient (labeled 0, 1, 2)
        trials = []
        for label in ["0", "1", "2"]:
            for trial in entry.get(label, []):
                trial["_gt_label"] = int(label)
                trials.append(trial)

        print(f"\n[{patient_id}] {len(trials)} trials, extracting record...", end=" ", flush=True)

        # Step 1: extract typed record from clinical note
        t0 = time.time()
        try:
            record, extract_tokens = call_claude_sync(
                client, EXTRACT_CLINICAL_NOTE,
                f"Extract structured record from this clinical note:\n\n{patient_text}"
            )
            record = add_derived_fields(record)
            total_tokens += extract_tokens
            print(f"done ({time.time()-t0:.1f}s)")
        except Exception as e:
            print(f"EXTRACTION ERROR: {e}")
            continue

        # Step 2+3: assess each trial
        print(f"  Assessing {len(trials)} trials...", end=" ", flush=True)
        semaphore = asyncio.Semaphore(CONCURRENCY)
        tasks = [assess_trial(client, t, record, semaphore) for t in trials]
        matches = await asyncio.gather(*tasks)

        # Score against qrels
        patient_correct = 0
        patient_total = 0
        for match, trial in zip(matches, trials):
            gt_label = trial["_gt_label"]
            pred_label = match["qrel_pred"]
            total_tokens += match.get("tokens", 0)

            # For scoring: eligible(2) vs not-eligible(0,1)
            # Binary: did we correctly identify eligible trials?
            gt_eligible = gt_label == 2
            pred_eligible = match["verdict"] == "ELIGIBLE"

            # Three-way accuracy: exact match on 0/1/2
            if pred_label == gt_label:
                patient_correct += 1
                total_correct += 1
            # Also count: excluded(1) matches both our INELIGIBLE and UNCERTAIN
            elif gt_label in [0, 1] and pred_label == 1:
                # We said excluded, GT says not-relevant or excluded — close enough
                # Actually let's be strict: only count exact matches
                pass

            patient_total += 1
            total_assessed += 1

        patient_acc = patient_correct / patient_total * 100 if patient_total else 0

        eligible_count = sum(1 for m in matches if m["verdict"] == "ELIGIBLE")
        uncertain_count = sum(1 for m in matches if m["verdict"] == "UNCERTAIN")
        ineligible_count = sum(1 for m in matches if m["verdict"] == "INELIGIBLE")
        error_count = sum(1 for m in matches if m["verdict"] == "ERROR")

        elapsed = time.time() - t0
        print(f"E={eligible_count} U={uncertain_count} I={ineligible_count} err={error_count} | "
              f"acc={patient_acc:.1f}% ({patient_correct}/{patient_total}) | {elapsed:.0f}s")

        result = {
            "patient_id": patient_id,
            "trials_assessed": len(trials),
            "eligible": eligible_count,
            "uncertain": uncertain_count,
            "ineligible": ineligible_count,
            "errors": error_count,
            "accuracy": round(patient_acc, 1),
            "correct": patient_correct,
            "total": patient_total,
            "elapsed_s": round(elapsed, 1),
            "matches": matches,
        }
        (OUT_DIR / f"{patient_id}.json").write_text(json.dumps(result, indent=2))
        all_results.append(result)

    # Summary
    overall_acc = total_correct / total_assessed * 100 if total_assessed else 0
    cost = total_tokens * 3 / 1_000_000

    print(f"\n{'='*70}")
    print(f"SIGIR Benchmark Results ({len(all_results)} patients)")
    print(f"{'='*70}")
    print(f"Overall accuracy (3-way): {total_correct}/{total_assessed} = {overall_acc:.1f}%")
    print(f"Total tokens: {total_tokens:,} | Cost: ${cost:.2f}")
    print()
    print(f"{'Patient':<15} {'Trials':>7} {'E/U/I':>12} {'Acc':>8}")
    print("-" * 45)
    for r in all_results:
        eui = f"{r['eligible']}/{r['uncertain']}/{r['ineligible']}"
        print(f"{r['patient_id']:<15} {r['trials_assessed']:>7} {eui:>12} {r['accuracy']:>7.1f}%")

    # Breakdown by GT label
    print(f"\n{'='*70}")
    print("Accuracy by GT label:")
    by_gt = {0: [0, 0], 1: [0, 0], 2: [0, 0]}
    for r in all_results:
        patient_entry = next(e for e in dataset if e["patient_id"] == r["patient_id"])
        for match in r["matches"]:
            nct = match["nct_id"]
            gt = qrels.get((r["patient_id"], nct))
            if gt is None:
                # Find from trial data
                for label in ["0", "1", "2"]:
                    for t in patient_entry.get(label, []):
                        if t["NCTID"] == nct:
                            gt = int(label)
                            break
            if gt is not None:
                pred = match["qrel_pred"]
                by_gt[gt][1] += 1
                if pred == gt:
                    by_gt[gt][0] += 1

    for label, (correct, total) in by_gt.items():
        label_name = {0: "Not relevant", 1: "Excluded", 2: "Eligible"}[label]
        acc = correct / total * 100 if total else 0
        print(f"  {label_name} ({label}): {correct}/{total} = {acc:.1f}%")

    summary = {
        "cohort": "sigir",
        "patients_run": len(all_results),
        "overall_accuracy": round(overall_acc, 1),
        "total_assessed": total_assessed,
        "total_correct": total_correct,
        "total_tokens": total_tokens,
        "cost": round(cost, 2),
        "per_patient": [{k: v for k, v in r.items() if k != "matches"} for r in all_results],
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nResults saved to {OUT_DIR}/")


if __name__ == "__main__":
    asyncio.run(main())
