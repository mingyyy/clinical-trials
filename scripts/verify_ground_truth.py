"""
verify_ground_truth.py — Independent LLM verification of hand-labeled ground truth.

For each patient × trial in findings/ground_truth.json:
  1. Fetch eligibility criteria fresh from ClinicalTrials.gov (by NCT ID)
  2. Ask an independent LLM agent to assess ELIGIBLE / INELIGIBLE / UNCERTAIN
     using only the clinical facts in the patient profile (no notes field)
  3. Compare to ground truth label; flag disagreements for human review

The verification agent uses a strict conservative prompt:
  - UNCERTAIN when profile data is absent — never infer from clinical context
  - INELIGIBLE only when the profile directly states a disqualifier
  - ELIGIBLE only when all key stated criteria are clearly met by the profile

Run with:  .venv/bin/python verify_ground_truth.py [--patient P001] [--dry-run]
Output:    findings/ground_truth_verification.json
           findings/ground_truth_verification_report.md
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BASE_DIR = Path(__file__).parent
GT_PATH = BASE_DIR / "findings" / "ground_truth.json"
OUT_JSON = BASE_DIR / "findings" / "ground_truth_verification.json"
OUT_MD   = BASE_DIR / "findings" / "ground_truth_verification_report.md"

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 512
CONCURRENCY = 8           # max parallel LLM calls
FETCH_TIMEOUT = 15        # seconds per ClinicalTrials.gov fetch

# ── Patient profiles (clinical facts only — no notes field) ─────────────────

PATIENT_PROFILES = {
    "P001": """Patient ID: P001
Age: 52 | Sex: Female
Diagnosis: HER2-positive breast cancer, stage II (non-metastatic)
Biomarkers: HER2+ ER+ PR-
Prior treatments: surgery, chemotherapy
ECOG PS: 0
Location: New York, NY""",

    "P002": """Patient ID: P002
Age: 34 | Sex: Female
Diagnosis: Triple-negative breast cancer (TNBC), stage III
Biomarkers: ER- PR- HER2- BRCA1 mutant
Prior treatments: neoadjuvant chemotherapy
ECOG PS: 1
Location: Los Angeles, CA""",

    "P003": """Patient ID: P003
Age: 61 | Sex: Female
Diagnosis: HR-positive HER2-negative breast cancer, post-mastectomy, NED (no evidence of disease)
Biomarkers: ER+ PR+ HER2-
Prior treatments: mastectomy, radiation, tamoxifen (5 years, completed)
ECOG PS: 1
Location: Chicago, IL""",

    "P004": """Patient ID: P004
Age: 55 | Sex: Male
Diagnosis: Metastatic melanoma with brain metastases
Biomarkers: BRAF V600E mutant
Prior treatments: ipilimumab, nivolumab (treatment setting — adjuvant vs metastatic — not stated in profile)
ECOG PS: 2
Location: Seattle, WA""",

    "P005": """Patient ID: P005
Age: 58 | Sex: Female
Diagnosis: HER2-positive metastatic breast cancer
Biomarkers: HER2+ ER-
Prior treatments: trastuzumab, pertuzumab, T-DM1 (3 prior HER2-targeted lines)
ECOG PS: 1
Location: Boston, MA""",
}

SYSTEM_PROMPT = """You are a clinical trial eligibility verification agent. Your job is to assess whether a patient meets the eligibility criteria for a clinical trial.

RULES — follow exactly:
1. Return exactly one verdict: ELIGIBLE, INELIGIBLE, or UNCERTAIN.
2. INELIGIBLE: only if the patient profile DIRECTLY STATES information that triggers an exclusion criterion or fails a mandatory inclusion criterion. Do not infer from diagnosis, disease context, or standard-of-care assumptions.
3. UNCERTAIN: if eligibility depends on data that is NOT explicitly stated in the patient profile. Absence of data is NEVER evidence of ineligibility — it is evidence of uncertainty.
4. ELIGIBLE: only if the patient's stated profile clearly satisfies the key inclusion criteria and no exclusion criteria are triggered.
5. When in doubt between INELIGIBLE and UNCERTAIN, choose UNCERTAIN.

OUTPUT FORMAT (JSON only, no other text):
{
  "verdict": "ELIGIBLE" | "INELIGIBLE" | "UNCERTAIN",
  "confidence": 0.0-1.0,
  "reason": "one sentence — cite the specific criterion and profile fact"
}"""

USER_TEMPLATE = """PATIENT PROFILE:
{profile}

TRIAL: {nct_id} — {title}

ELIGIBILITY CRITERIA:
{criteria}

Assess whether this patient is eligible for this trial. Return JSON only."""


# ── ClinicalTrials.gov fetch by NCT ID ──────────────────────────────────────

def fetch_trial_by_nct(nct_id: str) -> dict | None:
    """Fetch a single trial by NCT ID. Returns dict with nct_id, title, criteria or None on error."""
    url = f"https://clinicaltrials.gov/api/v2/studies/{nct_id}"
    try:
        r = requests.get(url, timeout=FETCH_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        proto = data.get("protocolSection", {})
        id_mod = proto.get("identificationModule", {})
        elig_mod = proto.get("eligibilityModule", {})
        return {
            "nct_id": nct_id,
            "title": id_mod.get("briefTitle", ""),
            "criteria": elig_mod.get("eligibilityCriteria", ""),
        }
    except Exception as e:
        print(f"  [FETCH ERROR] {nct_id}: {e}", file=sys.stderr)
        return None


def fetch_all_trials(nct_ids: list[str]) -> dict[str, dict]:
    """Fetch all unique NCT IDs. Returns {nct_id: trial_dict}."""
    print(f"Fetching {len(nct_ids)} unique trials from ClinicalTrials.gov...")
    cache = {}
    for i, nct_id in enumerate(nct_ids):
        if i % 20 == 0 and i > 0:
            print(f"  {i}/{len(nct_ids)} fetched...")
        cache[nct_id] = fetch_trial_by_nct(nct_id)
        time.sleep(0.1)  # polite rate limiting
    fetched = sum(1 for v in cache.values() if v is not None)
    print(f"  Fetched {fetched}/{len(nct_ids)} successfully.")
    return cache


# ── LLM verification ────────────────────────────────────────────────────────

async def verify_one(
    client: anthropic.AsyncAnthropic,
    semaphore: asyncio.Semaphore,
    patient_id: str,
    nct_id: str,
    trial: dict,
    gt_label: str,   # "eligible" | "ineligible" | "ambiguous"
) -> dict:
    """Run one verification LLM call. Returns result dict."""
    profile = PATIENT_PROFILES[patient_id]
    criteria = trial["criteria"] or "(no eligibility criteria text available)"
    user_msg = USER_TEMPLATE.format(
        profile=profile,
        nct_id=nct_id,
        title=trial["title"],
        criteria=criteria[:6000],  # cap to avoid token overflow on very long criteria
    )

    async with semaphore:
        try:
            resp = await client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = resp.content[0].text.strip()
            # strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw)
            verdict = parsed.get("verdict", "ERROR")
            confidence = parsed.get("confidence", 0.0)
            reason = parsed.get("reason", "")
        except Exception as e:
            verdict = "ERROR"
            confidence = 0.0
            reason = str(e)

    # Map ground truth label to verdict space
    gt_verdict_map = {"eligible": "ELIGIBLE", "ineligible": "INELIGIBLE", "ambiguous": "UNCERTAIN"}
    gt_verdict = gt_verdict_map.get(gt_label, "UNKNOWN")

    agrees = (verdict == gt_verdict)

    return {
        "patient_id": patient_id,
        "nct_id": nct_id,
        "trial_title": trial["title"],
        "gt_label": gt_label,
        "gt_verdict": gt_verdict,
        "agent_verdict": verdict,
        "agent_confidence": confidence,
        "agent_reason": reason,
        "agrees": agrees,
    }


async def run_verification(
    gt: dict,
    trial_cache: dict[str, dict],
    patients: list[str],
) -> list[dict]:
    """Run all LLM verification calls concurrently."""
    client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    semaphore = asyncio.Semaphore(CONCURRENCY)

    tasks = []
    for patient_id in patients:
        patient_gt = gt[patient_id]
        for label in ("eligible", "ineligible", "ambiguous"):
            for nct_id in patient_gt.get(label, []):
                trial = trial_cache.get(nct_id)
                if trial is None:
                    continue  # fetch failed
                tasks.append(verify_one(client, semaphore, patient_id, nct_id, trial, label))

    print(f"\nRunning {len(tasks)} LLM verification calls (concurrency={CONCURRENCY})...")
    t0 = time.time()
    results = await asyncio.gather(*tasks)
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s")
    return list(results)


# ── Report generation ────────────────────────────────────────────────────────

def build_report(results: list[dict], gt_issues: list[str]) -> str:
    """Build markdown disagreement report."""
    lines = ["# Ground Truth Verification Report\n"]
    lines.append(f"*Generated {time.strftime('%Y-%m-%d')} — independent LLM agent vs hand-labeled ground truth*\n")

    # Summary
    total = len(results)
    errors = [r for r in results if r["agent_verdict"] == "ERROR"]
    valid = [r for r in results if r["agent_verdict"] != "ERROR"]
    agrees = [r for r in valid if r["agrees"]]
    disagrees = [r for r in valid if not r["agrees"]]

    lines.append(f"## Summary\n")
    lines.append(f"| | Count |")
    lines.append(f"|--|--|")
    lines.append(f"| Total assessments | {total} |")
    lines.append(f"| Agent errors | {len(errors)} |")
    lines.append(f"| Agreement | {len(agrees)} / {len(valid)} = {len(agrees)/len(valid):.1%} |")
    lines.append(f"| Disagreements | {len(disagrees)} |")
    lines.append("")

    if gt_issues:
        lines.append("## Ground Truth Data Issues (duplicates)\n")
        for issue in gt_issues:
            lines.append(f"- {issue}")
        lines.append("")

    # Disagreements by patient
    lines.append("## Disagreements by Patient\n")
    patients = sorted(set(r["patient_id"] for r in disagrees))
    for pid in patients:
        pid_disagrees = [r for r in disagrees if r["patient_id"] == pid]
        lines.append(f"### {pid} ({len(pid_disagrees)} disagreements)\n")
        lines.append("| NCT ID | Trial | GT | Agent | Conf | Reason |")
        lines.append("|--------|-------|----|----|------|--------|")
        for r in sorted(pid_disagrees, key=lambda x: x["nct_id"]):
            title = r["trial_title"][:50] + "..." if len(r["trial_title"]) > 50 else r["trial_title"]
            reason = r["agent_reason"][:80] + "..." if len(r["agent_reason"]) > 80 else r["agent_reason"]
            lines.append(f"| {r['nct_id']} | {title} | {r['gt_verdict']} | {r['agent_verdict']} | {r['agent_confidence']:.2f} | {reason} |")
        lines.append("")

    # Error calls
    if errors:
        lines.append("## LLM Call Errors\n")
        for r in errors:
            lines.append(f"- {r['patient_id']} × {r['nct_id']}: {r['agent_reason']}")
        lines.append("")

    # Disagreement type breakdown
    lines.append("## Disagreement Type Breakdown\n")
    type_counts: dict[str, int] = {}
    for r in disagrees:
        key = f"GT={r['gt_verdict']} Agent={r['agent_verdict']}"
        type_counts[key] = type_counts.get(key, 0) + 1
    lines.append("| GT → Agent | Count |")
    lines.append("|-----------|-------|")
    for k, v in sorted(type_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")
    lines.append("")

    lines.append("## Interpretation\n")
    lines.append(
        "Disagreements do not automatically mean the ground truth is wrong — the LLM agent can also be wrong. "
        "Each disagreement is a flag for human review. High-priority flags: cases where the agent "
        "says INELIGIBLE with confidence ≥ 0.85 but GT says UNCERTAIN or ELIGIBLE."
    )
    lines.append("")

    # High-confidence disagreements
    high_conf = [r for r in disagrees if r["agent_confidence"] >= 0.85]
    if high_conf:
        lines.append("### High-confidence disagreements (agent confidence ≥ 0.85)\n")
        lines.append("| Patient | NCT ID | GT | Agent | Conf | Reason |")
        lines.append("|---------|--------|----|----|------|--------|")
        for r in sorted(high_conf, key=lambda x: -x["agent_confidence"]):
            reason = r["agent_reason"][:100] + "..." if len(r["agent_reason"]) > 100 else r["agent_reason"]
            lines.append(f"| {r['patient_id']} | {r['nct_id']} | {r['gt_verdict']} | {r['agent_verdict']} | {r['agent_confidence']:.2f} | {reason} |")

    return "\n".join(lines)


# ── Duplicate detection ──────────────────────────────────────────────────────

def find_gt_issues(gt: dict) -> list[str]:
    issues = []
    for pid, patient_gt in gt.items():
        if pid.startswith("_"):
            continue
        eligible = set(patient_gt.get("eligible", []))
        ineligible = set(patient_gt.get("ineligible", []))
        ambiguous = set(patient_gt.get("ambiguous", []))
        for nct in eligible & ineligible:
            issues.append(f"{pid}/{nct}: appears in both 'eligible' and 'ineligible'")
        for nct in eligible & ambiguous:
            issues.append(f"{pid}/{nct}: appears in both 'eligible' and 'ambiguous'")
        for nct in ineligible & ambiguous:
            issues.append(f"{pid}/{nct}: appears in both 'ineligible' and 'ambiguous' — will use 'ambiguous'")
    return issues


def deduplicate_gt(gt: dict) -> dict:
    """For any trial in both ineligible+ambiguous, keep ambiguous (more conservative)."""
    clean = {}
    for pid, patient_gt in gt.items():
        if pid.startswith("_"):
            clean[pid] = patient_gt
            continue
        ineligible = list(patient_gt.get("ineligible", []))
        ambiguous = list(patient_gt.get("ambiguous", []))
        ambiguous_set = set(ambiguous)
        ineligible_clean = [n for n in ineligible if n not in ambiguous_set]
        clean[pid] = {
            "_profile": patient_gt.get("_profile", ""),
            "eligible": patient_gt.get("eligible", []),
            "ineligible": ineligible_clean,
            "ambiguous": ambiguous,
        }
    return clean


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--patient", help="Run for a single patient (e.g. P001)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch trials but skip LLM calls")
    args = parser.parse_args()

    with open(GT_PATH) as f:
        gt_raw = json.load(f)

    # Remove metadata key
    gt = {k: v for k, v in gt_raw.items() if not k.startswith("_")}

    # Detect issues
    gt_issues = find_gt_issues(gt)
    if gt_issues:
        print("Ground truth data issues detected:")
        for issue in gt_issues:
            print(f"  {issue}")

    # Deduplicate
    gt = deduplicate_gt(gt)

    # Filter to requested patient(s)
    patients = [args.patient] if args.patient else list(gt.keys())
    patients = [p for p in patients if p in gt]

    # Collect all unique NCT IDs
    all_ncts = set()
    for pid in patients:
        for label in ("eligible", "ineligible", "ambiguous"):
            all_ncts.update(gt[pid].get(label, []))

    # Fetch trials
    trial_cache = fetch_all_trials(sorted(all_ncts))

    if args.dry_run:
        fetched = sum(1 for v in trial_cache.values() if v)
        print(f"Dry run complete. {fetched}/{len(all_ncts)} trials fetched successfully.")
        return

    # Run LLM verification
    results = asyncio.run(run_verification(gt, trial_cache, patients))

    # Save JSON
    output = {
        "meta": {
            "model": MODEL,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "patients": patients,
            "gt_issues": gt_issues,
        },
        "results": results,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Results saved to {OUT_JSON}")

    # Build and save report
    report = build_report(results, gt_issues)
    with open(OUT_MD, "w") as f:
        f.write(report)
    print(f"Report saved to {OUT_MD}")

    # Print summary to console
    valid = [r for r in results if r["agent_verdict"] != "ERROR"]
    agrees = [r for r in valid if r["agrees"]]
    disagrees = [r for r in valid if not r["agrees"]]
    print(f"\nAgreement: {len(agrees)}/{len(valid)} = {len(agrees)/len(valid):.1%}")
    print(f"Disagreements: {len(disagrees)}")
    high_conf = [r for r in disagrees if r["agent_confidence"] >= 0.85]
    print(f"High-confidence disagreements (≥0.85): {len(high_conf)}")
    if high_conf:
        print("\nHigh-confidence flags (potential GT errors):")
        for r in sorted(high_conf, key=lambda x: -x["agent_confidence"]):
            print(f"  {r['patient_id']} × {r['nct_id']}: GT={r['gt_verdict']} Agent={r['agent_verdict']} ({r['agent_confidence']:.2f}) — {r['agent_reason'][:80]}")


if __name__ == "__main__":
    main()
