"""
Rubric scorer for clinical trial matching framework comparison.

Loads outputs/<framework>/P00X.json, scores each against findings/ground_truth.json,
and writes rows to findings/comparison_draft.md.

Usage:
    .venv/bin/python pipeline/scoring.py --framework langgraph
    .venv/bin/python pipeline/scoring.py --all
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "outputs"
FINDINGS_DIR = ROOT / "findings"
FRAMEWORKS = ["langgraph", "pydantic_ai", "smolagents", "claude_direct"]


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------

def load_ground_truth() -> dict:
    path = FINDINGS_DIR / "ground_truth.json"
    if not path.exists():
        print(f"ERROR: ground_truth.json not found at {path}")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def load_output(framework: str, patient_id: str) -> dict | None:
    path = OUTPUT_DIR / framework / f"{patient_id}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Per-dimension scorers
# ---------------------------------------------------------------------------

def score_recall_precision(matches: list[dict], gt: dict) -> tuple[float | None, float | None]:
    """
    Returns (recall, precision) as floats 0–1, or None if no ground truth to score against.

    Only NCT IDs in gt['eligible'] or gt['ineligible'] are scored. gt['ambiguous'] is skipped.
    """
    eligible_gt = set(gt.get("eligible", []))
    ineligible_gt = set(gt.get("ineligible", []))

    if not eligible_gt and not ineligible_gt:
        return None, None

    # From the matches list, build sets
    predicted_eligible = {m["nct_id"] for m in matches if m.get("eligible", False)}
    predicted_ineligible = {m["nct_id"] for m in matches if not m.get("eligible", False)}

    # Only score on NCT IDs in ground truth (exclude ambiguous + any not-fetched)
    scoreable_ncts = eligible_gt | ineligible_gt

    tp = len(eligible_gt & predicted_eligible)
    fn = len(eligible_gt & predicted_ineligible)  # eligible but marked ineligible
    fp = len(ineligible_gt & predicted_eligible)   # ineligible but marked eligible

    # Also count eligible trials not returned at all (not in matches) as false negatives
    returned_ncts = {m["nct_id"] for m in matches}
    fn_not_returned = len(eligible_gt - returned_ncts)
    fn += fn_not_returned

    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    precision = tp / (tp + fp) if (tp + fp) > 0 else None

    return recall, precision


def score_explanation_quality(matches: list[dict]) -> float:
    """
    Mean explanation quality across all matches.
    0 = no explanation / parse error
    1 = references criteria vaguely
    2 = cites specific criterion text

    Returns mean score (0–2) and count of scored matches.
    """
    if not matches:
        return 0.0

    scores = []
    for m in matches:
        explanation = m.get("explanation", "")
        if not explanation or explanation.startswith("[parse error"):
            scores.append(0)
        elif len(explanation) < 50 or "may" in explanation.lower() and len(m.get("matched_criteria", [])) == 0:
            scores.append(1)
        else:
            # Has specific criteria cited (matched_criteria or exclusion_flags non-empty, or explanation mentions specific criterion)
            has_criteria = bool(m.get("matched_criteria") or m.get("exclusion_flags"))
            cites_specific = any(kw in explanation.lower() for kw in [
                "inclusion criterion", "exclusion criterion", "requires", "explicitly",
                "per protocol", "per asco", "per cap", "icd", "ecog", "nct", "stage",
                "her2", "er+", "er-", "brca", "braf", "prior", "metastatic"
            ])
            scores.append(2 if (has_criteria or cites_specific) else 1)

    return sum(scores) / len(scores) if scores else 0.0


def score_cost(total_tokens: int, llm_calls: int) -> dict:
    """Returns raw cost metrics. Relative scoring done in compare_all()."""
    cost_usd = total_tokens * 0.000003  # $3/M tokens
    return {
        "total_tokens": total_tokens,
        "llm_calls": llm_calls,
        "cost_usd": round(cost_usd, 4),
    }


# ---------------------------------------------------------------------------
# Per-patient scorer
# ---------------------------------------------------------------------------

def score_patient(framework: str, patient_id: str, gt_data: dict) -> dict:
    """Score one framework × patient combination. Returns a result dict."""
    output = load_output(framework, patient_id)
    gt = gt_data.get(patient_id, {})

    if output is None:
        return {
            "framework": framework,
            "patient_id": patient_id,
            "error": "output file not found",
            "recall": None,
            "precision": None,
            "explanation_quality": None,
            "cost": None,
            "parse_errors": None,
            "total_trials_fetched": 0,
            "trials_after_hard_filter": 0,
        }

    matches = output.get("matches", [])
    parse_errors = sum(1 for m in matches if m.get("explanation", "").startswith("[parse error"))

    recall, precision = score_recall_precision(matches, gt)
    explanation_quality = score_explanation_quality(matches)
    cost = score_cost(output.get("total_tokens", 0), output.get("llm_calls", 0))

    return {
        "framework": framework,
        "patient_id": patient_id,
        "recall": recall,
        "precision": precision,
        "explanation_quality": round(explanation_quality, 2),
        "cost": cost,
        "parse_errors": parse_errors,
        "total_matches": len(matches),
        "eligible_count": sum(1 for m in matches if m.get("eligible", False)),
        "total_trials_fetched": output.get("total_trials_fetched", 0),
        "trials_after_hard_filter": output.get("trials_after_hard_filter", 0),
        "wall_time_seconds": output.get("wall_time_seconds", 0.0),
    }


# ---------------------------------------------------------------------------
# Framework-level summary
# ---------------------------------------------------------------------------

def score_framework(framework: str, gt_data: dict) -> dict:
    """Score one framework across all 5 patients."""
    patient_ids = ["P001", "P002", "P003", "P004", "P005"]
    results = [score_patient(framework, pid, gt_data) for pid in patient_ids]

    # Aggregate
    recalls = [r["recall"] for r in results if r.get("recall") is not None]
    precisions = [r["precision"] for r in results if r.get("precision") is not None]
    eq_scores = [r["explanation_quality"] for r in results if r.get("explanation_quality") is not None]

    total_tokens = sum(r["cost"]["total_tokens"] for r in results if r.get("cost"))
    total_llm_calls = sum(r["cost"]["llm_calls"] for r in results if r.get("cost"))
    total_cost_usd = sum(r["cost"]["cost_usd"] for r in results if r.get("cost"))
    total_parse_errors = sum(r.get("parse_errors") or 0 for r in results)
    total_wall_time = sum(r.get("wall_time_seconds") or 0.0 for r in results)

    return {
        "framework": framework,
        "per_patient": results,
        "avg_recall": round(sum(recalls) / len(recalls), 3) if recalls else None,
        "avg_precision": round(sum(precisions) / len(precisions), 3) if precisions else None,
        "avg_explanation_quality": round(sum(eq_scores) / len(eq_scores), 2) if eq_scores else None,
        "total_tokens": total_tokens,
        "total_llm_calls": total_llm_calls,
        "total_cost_usd": round(total_cost_usd, 4),
        "total_parse_errors": total_parse_errors,
        "total_wall_time_seconds": round(total_wall_time, 1),
    }


# ---------------------------------------------------------------------------
# Comparison table writer
# ---------------------------------------------------------------------------

def write_comparison_draft(summaries: list[dict]) -> None:
    """Append or overwrite findings/comparison_draft.md with current scores."""
    FINDINGS_DIR.mkdir(exist_ok=True)
    path = FINDINGS_DIR / "comparison_draft.md"

    # Compute relative cost scores
    max_cost = max((s["total_cost_usd"] for s in summaries), default=1) or 1

    lines = [
        "# Comparison Draft — Clinical Trial Matching",
        "",
        "_Auto-generated by pipeline/scoring.py. Run again after each framework completes._",
        "",
        "## Rubric Scores",
        "",
        "| Framework | Recall (30%) | Precision (25%) | Explanation (15%) | Cost USD | Parse Errors | Total Tokens | LLM Calls | Wall Time (s) |",
        "|-----------|-------------|-----------------|-------------------|----------|--------------|--------------|-----------|---------------|",
    ]

    for s in summaries:
        recall = f"{s['avg_recall']:.2f}" if s["avg_recall"] is not None else "N/A (no GT)"
        prec = f"{s['avg_precision']:.2f}" if s["avg_precision"] is not None else "N/A (no GT)"
        eq = f"{s['avg_explanation_quality']:.2f}" if s["avg_explanation_quality"] is not None else "—"
        cost = f"${s['total_cost_usd']:.4f}"
        lines.append(
            f"| {s['framework']} | {recall} | {prec} | {eq}/2.0 | {cost} | "
            f"{s['total_parse_errors']} | {s['total_tokens']:,} | {s['total_llm_calls']} | {s['total_wall_time_seconds']} |"
        )

    lines += [
        "",
        "## Per-Patient Breakdown",
        "",
    ]

    for s in summaries:
        lines.append(f"### {s['framework']}")
        lines.append("")
        lines.append("| Patient | Recall | Precision | Explanation | Trials Fetched | After Filter | LLM Calls | Parse Errors |")
        lines.append("|---------|--------|-----------|-------------|----------------|--------------|-----------|--------------|")
        for p in s["per_patient"]:
            if p.get("error"):
                lines.append(f"| {p['patient_id']} | — | — | — | — | — | — | {p['error']} |")
            else:
                recall = f"{p['recall']:.2f}" if p["recall"] is not None else "N/A"
                prec = f"{p['precision']:.2f}" if p["precision"] is not None else "N/A"
                lines.append(
                    f"| {p['patient_id']} | {recall} | {prec} | "
                    f"{p['explanation_quality']:.2f} | {p['total_trials_fetched']} | "
                    f"{p['trials_after_hard_filter']} | {p['cost']['llm_calls']} | {p['parse_errors']} |"
                )
        lines.append("")

    path.write_text("\n".join(lines))
    print(f"Written: {path}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Score framework outputs against rubric")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--framework", choices=FRAMEWORKS, help="Score one framework")
    group.add_argument("--all", action="store_true", help="Score all frameworks and write comparison table")
    args = parser.parse_args()

    gt_data = load_ground_truth()

    if args.all:
        summaries = [score_framework(fw, gt_data) for fw in FRAMEWORKS]
        write_comparison_draft(summaries)
        print("\nSummary:")
        for s in summaries:
            print(f"  {s['framework']:15s} recall={s['avg_recall']}  precision={s['avg_precision']}  "
                  f"cost=${s['total_cost_usd']:.4f}  parse_errors={s['total_parse_errors']}")
    else:
        gt_data = load_ground_truth()
        summary = score_framework(args.framework, gt_data)
        print(f"\n=== {args.framework} ===")
        for p in summary["per_patient"]:
            if p.get("error"):
                print(f"  {p['patient_id']}: {p['error']}")
            else:
                print(f"  {p['patient_id']}: recall={p['recall']}  precision={p['precision']}  "
                      f"eq={p['explanation_quality']}  trials={p['total_trials_fetched']}  "
                      f"llm_calls={p['cost']['llm_calls']}  parse_errors={p['parse_errors']}")
        print(f"\nFramework totals: tokens={summary['total_tokens']:,}  "
              f"cost=${summary['total_cost_usd']:.4f}  parse_errors={summary['total_parse_errors']}")


if __name__ == "__main__":
    main()
