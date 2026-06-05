"""
Notes contamination test — P001 (all 6 UNCERTAIN trials from LangGraph run).

Tests whether researcher expectation framing in P001's notes inflates verdicts.

P001 notes: "Baseline case. Should match several HER2+ trials in NYC area."

This is expectation-setting framing: it tells the LLM what the researcher
expects to find. Hypothesis: this framing pulls UNCERTAIN verdicts toward
ELIGIBLE by anchoring the LLM's prior before it reads the eligibility criteria.

Six trials from the LangGraph P001 rerun that returned UNCERTAIN:
  NCT07211178  conf=0.65
  NCT07192432  conf=0.55
  NCT02945579  conf=0.45
  NCT06253871  conf=0.45
  NCT06220214  conf=0.45
  NCT05232916  conf=0.30

Three variants tested for each trial:
  A  Current        — researcher framing at END of patient context
  B  Clean          — notes stripped; clinical data only
  C  Framing-first  — notes moved to TOP, before all clinical data

Hypothesis:
  A vs B: framing at end may inflate confidence for borderline UNCERTAIN cases
  A vs C: moving framing to top may further inflate (primacy effect)
  B: the cleanest baseline — LLM without expectation framing

Run with:
  .venv/bin/python test_p001_notes.py

Outputs:
  outputs/p001_notes_test/trials/<nct_id>.json   raw trial data
  outputs/p001_notes_test/results.json            all variants, full detail
  stdout: comparison table
"""

import json
import os
import re
import sys
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

OUT_DIR = Path(__file__).parent / "outputs" / "p001_notes_test"
OUT_DIR.mkdir(parents=True, exist_ok=True)
(OUT_DIR / "trials").mkdir(exist_ok=True)

MODEL = "claude-sonnet-4-6"

# NCT IDs from LangGraph P001 rerun — all returned UNCERTAIN
UNCERTAIN_TRIALS = [
    ("NCT07211178", 0.65),
    ("NCT07192432", 0.55),
    ("NCT02945579", 0.45),
    ("NCT06253871", 0.45),
    ("NCT06220214", 0.45),
    ("NCT05232916", 0.30),
]

# ---------------------------------------------------------------------------
# Exact system prompt from LangGraph's analyze_batch_node
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
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
    "   or an inclusion criterion is clearly not met.\n"
    "3. For UNCERTAIN: list specifically what additional data would confirm or deny eligibility.\n\n"
    "Respond ONLY with valid JSON (no markdown) with keys:\n"
    "  verdict (string: ELIGIBLE|INELIGIBLE|UNCERTAIN)\n"
    "  confidence (float 0.0-1.0)\n"
    "  matched_criteria (list of strings — criteria clearly met)\n"
    "  exclusion_flags (list of strings — criteria clearly failed, with evidence from profile)\n"
    "  uncertain_items (list of strings — criteria that cannot be evaluated due to missing data)\n"
    "  explanation (string, 1-3 sentences citing specific criteria and evidence)"
)

# ---------------------------------------------------------------------------
# P001 patient data — split into clinical facts vs researcher framing
# ---------------------------------------------------------------------------
CLINICAL_LINES = """\
- Age: 52, Sex: FEMALE
- Diagnosis: HER2-positive breast cancer, stage II
- Biomarkers: HER2+, ER+, PR-
- Prior treatments: surgery, chemotherapy
- ECOG PS: 0
- Location: New York, NY"""

RESEARCHER_NOTES = "Baseline case. Should match several HER2+ trials in NYC area."


# ---------------------------------------------------------------------------
# Fetch trial
# ---------------------------------------------------------------------------
def fetch_trial(nct_id: str) -> dict:
    cache_path = OUT_DIR / "trials" / f"{nct_id}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    url = f"https://clinicaltrials.gov/api/v2/studies/{nct_id}"
    r = requests.get(url, params={"format": "json"}, timeout=20)
    r.raise_for_status()
    proto = r.json().get("protocolSection", {})
    id_mod = proto.get("identificationModule", {})
    elig = proto.get("eligibilityModule", {})
    design = proto.get("designModule", {})
    sponsor = proto.get("sponsorCollaboratorsModule", {})
    trial = {
        "nct_id": id_mod.get("nctId", ""),
        "title": id_mod.get("briefTitle", ""),
        "eligibility": elig.get("eligibilityCriteria", ""),
        "min_age": elig.get("minimumAge", ""),
        "max_age": elig.get("maximumAge", ""),
        "sex": elig.get("sex", ""),
        "phases": design.get("phases", []),
        "sponsor": sponsor.get("leadSponsor", {}).get("name", ""),
    }
    cache_path.write_text(json.dumps(trial, indent=2))
    return trial


def trial_block(trial: dict) -> str:
    return (
        f"Trial: {trial['nct_id']} — {trial['title']}\n"
        f"Phases: {', '.join(trial['phases']) if trial['phases'] else 'N/A'} | "
        f"Sponsor: {trial['sponsor']}\n"
        f"Age: {trial['min_age']} to {trial['max_age']} | "
        f"Sex: {trial.get('sex') or 'ALL'}\n\n"
        f"Eligibility criteria:\n{trial['eligibility']}"
    )


# ---------------------------------------------------------------------------
# Build user messages — one per variant
# ---------------------------------------------------------------------------
def user_message(variant: str, trial_text: str) -> str:
    suffix = "\n\nRespond ONLY with valid JSON. No markdown, no explanation outside the JSON."

    if variant == "A":
        return (
            f"Patient:\n"
            f"{CLINICAL_LINES}\n"
            f"- Notes: {RESEARCHER_NOTES}\n\n"
            f"{trial_text}"
            f"{suffix}"
        )
    if variant == "B":
        return (
            f"Patient:\n"
            f"{CLINICAL_LINES}\n\n"
            f"{trial_text}"
            f"{suffix}"
        )
    if variant == "C":
        return (
            f"Patient notes: {RESEARCHER_NOTES}\n\n"
            f"Patient:\n"
            f"{CLINICAL_LINES}\n\n"
            f"{trial_text}"
            f"{suffix}"
        )
    raise ValueError(f"Unknown variant: {variant}")


# ---------------------------------------------------------------------------
# Run one variant for one trial
# ---------------------------------------------------------------------------
def run_variant(variant: str, trial: dict, client: anthropic.Anthropic) -> dict:
    descriptions = {
        "A": "Current — researcher framing at END",
        "B": "Clean   — researcher framing stripped",
        "C": "Framing-first — researcher framing at TOP",
    }
    msg = user_message(variant, trial_block(trial))
    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": msg}],
    )
    raw = response.content[0].text.strip()
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        parsed = {}

    return {
        "nct_id": trial["nct_id"],
        "variant": variant,
        "variant_description": descriptions[variant],
        "verdict": parsed.get("verdict", "PARSE_ERROR"),
        "confidence": parsed.get("confidence"),
        "explanation": parsed.get("explanation", ""),
        "matched_criteria": parsed.get("matched_criteria", []),
        "exclusion_flags": parsed.get("exclusion_flags", []),
        "uncertain_items": parsed.get("uncertain_items", []),
        "tokens_used": response.usage.input_tokens + response.usage.output_tokens,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set (check .env)")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print(f"P001 notes contamination test — {len(UNCERTAIN_TRIALS)} trials × 3 variants")
    print(f"Notes: \"{RESEARCHER_NOTES}\"")
    print()

    all_results = []
    for nct_id, langgraph_conf in UNCERTAIN_TRIALS:
        print(f"Fetching {nct_id}...")
        trial = fetch_trial(nct_id)
        print(f"  Title: {trial['title'][:70]}")
        print(f"  LangGraph baseline: UNCERTAIN conf={langgraph_conf}")

        trial_results = {"nct_id": nct_id, "langgraph_baseline": {"verdict": "UNCERTAIN", "confidence": langgraph_conf}, "variants": []}
        for variant in ["A", "B", "C"]:
            r = run_variant(variant, trial, client)
            trial_results["variants"].append(r)
            print(f"  Variant {variant}: {r['verdict']:<12} conf={r['confidence']}")

        all_results.append(trial_results)
        print()

    # Save full results
    output = {
        "experiment": "P001 notes contamination test",
        "patient_id": "P001",
        "model": MODEL,
        "system_prompt": SYSTEM_PROMPT,
        "researcher_notes_tested": RESEARCHER_NOTES,
        "clinical_lines": CLINICAL_LINES,
        "hypothesis": {
            "statement": "Expectation framing ('Should match several HER2+ trials') inflates UNCERTAIN verdicts toward ELIGIBLE",
            "predicted_A": "UNCERTAIN or ELIGIBLE (framing may inflate confidence)",
            "predicted_B": "UNCERTAIN (baseline without framing)",
            "predicted_C": "ELIGIBLE or UNCERTAIN (framing first may anchor toward ELIGIBLE)",
        },
        "trials": all_results,
    }
    out_path = OUT_DIR / "results.json"
    out_path.write_text(json.dumps(output, indent=2))

    # ---------------------------------------------------------------------------
    # Comparison table
    # ---------------------------------------------------------------------------
    print("=" * 80)
    print("RESULTS — P001 notes contamination test")
    print("=" * 80)
    header = f"{'NCT ID':<14} {'LG baseline':>12}  {'A (notes end)':>14}  {'B (clean)':>11}  {'C (notes top)':>14}"
    print(header)
    print("-" * 80)

    for tr in all_results:
        lg = f"UNCERTAIN {tr['langgraph_baseline']['confidence']:.2f}"
        variants = {v["variant"]: v for v in tr["variants"]}
        a = f"{variants['A']['verdict']} {variants['A']['confidence']:.2f}"
        b = f"{variants['B']['verdict']} {variants['B']['confidence']:.2f}"
        c = f"{variants['C']['verdict']} {variants['C']['confidence']:.2f}"
        print(f"  {tr['nct_id']:<12} {lg:>12}  {a:>14}  {b:>11}  {c:>14}")

    print()
    print("Verdict shifts (A vs B — effect of notes at end):")
    print("-" * 80)
    shifts = 0
    for tr in all_results:
        variants = {v["variant"]: v for v in tr["variants"]}
        va, vb = variants["A"]["verdict"], variants["B"]["verdict"]
        conf_a, conf_b = variants["A"]["confidence"], variants["B"]["confidence"]
        delta = conf_a - conf_b if conf_a and conf_b else 0
        changed = va != vb
        if changed:
            shifts += 1
        marker = "*** VERDICT CHANGED ***" if changed else f"delta={delta:+.2f}"
        print(f"  {tr['nct_id']}: {vb} → {va}  {marker}")

    print()
    print(f"Verdict changes A vs B: {shifts}/{len(all_results)}")

    print()
    print("Verdict shifts (C vs B — effect of notes at top):")
    print("-" * 80)
    shifts_c = 0
    for tr in all_results:
        variants = {v["variant"]: v for v in tr["variants"]}
        vc, vb = variants["C"]["verdict"], variants["B"]["verdict"]
        conf_c, conf_b = variants["C"]["confidence"], variants["B"]["confidence"]
        delta = conf_c - conf_b if conf_c and conf_b else 0
        changed = vc != vb
        if changed:
            shifts_c += 1
        marker = "*** VERDICT CHANGED ***" if changed else f"delta={delta:+.2f}"
        print(f"  {tr['nct_id']}: {vb} → {vc}  {marker}")

    print()
    print(f"Verdict changes C vs B: {shifts_c}/{len(all_results)}")
    print()
    print(f"Full results saved to: {out_path}")


if __name__ == "__main__":
    main()
