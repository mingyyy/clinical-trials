"""
Notes contamination test — P004 × NCT04511013.

Tests whether researcher framing in the patient profile's `notes` field
contaminates LLM eligibility verdicts, and whether position in the prompt matters.

Three fresh variants (+ existing Variant A result from LangGraph run as reference):

  A  Current     — researcher framing at END of patient context (matches framework runs)
  B  Clean       — researcher framing stripped; only clinical data passed to LLM
  C  Framing-first — researcher framing moved to TOP, before clinical data

Hypothesis:
  A → INELIGIBLE (confirmed: confidence 0.92 in existing LangGraph output)
  B → UNCERTAIN  (prompt rule applied correctly; prior treatment setting is missing data)
  C → INELIGIBLE if content dominates; lower confidence if position also matters

The prior treatment context (adjuvant vs metastatic) is the key ambiguity:
  - The prompt rule says: absent information = UNCERTAIN, not INELIGIBLE
  - The notes say: "Most trials should be excluded"
  - Existing run chose INELIGIBLE despite listing the ambiguity in uncertain_items

Run with:
  .venv/bin/python test_notes_contamination.py

Outputs:
  outputs/notes_contamination_test/trial_NCT04511013.json   raw trial data from API
  outputs/notes_contamination_test/results.json             all variants, full detail
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

OUT_DIR = Path(__file__).parent / "outputs" / "05_experiments" / "p004_notes"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MODEL = "claude-sonnet-4-6"

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
# P004 patient data — split into clinical facts vs researcher framing
# ---------------------------------------------------------------------------
CLINICAL_LINES = """\
- Age: 55, Sex: MALE
- Diagnosis: Metastatic melanoma with brain metastases
- Biomarkers: BRAF V600E mutant
- Prior treatments: ipilimumab, nivolumab
- ECOG PS: 2"""

RESEARCHER_NOTES = (
    "Hard exclusion test. Brain mets is a common exclusion criterion. "
    "High ECOG may also disqualify. Most trials should be excluded."
)

# ---------------------------------------------------------------------------
# Existing Variant A result from LangGraph rerun (for reference)
# ---------------------------------------------------------------------------
EXISTING_A = {
    "variant": "A_existing",
    "variant_description": "Existing LangGraph run (researcher framing at END) — reference only, not re-run here",
    "verdict": "INELIGIBLE",
    "confidence": 0.92,
    "explanation": (
        "The primary reason for ineligibility is the exclusion criterion prohibiting prior systemic "
        "therapy for metastatic disease: the patient received both ipilimumab and nivolumab, which "
        "based on the profile appear to have been administered for metastatic disease rather than in "
        "a neoadjuvant/adjuvant context. If these treatments were given only in the adjuvant setting "
        "with subsequent relapse, the patient could potentially be eligible, but the profile does not "
        "indicate this, making ineligibility the most likely classification. ECOG PS of 2 does meet "
        "the Zubrod <= 2 inclusion criterion."
    ),
    "exclusion_flags": [
        "Prior systemic therapy for metastatic disease: Patient received ipilimumab and nivolumab, "
        "which appear to have been given for metastatic disease (not stated as neoadjuvant/adjuvant)."
    ],
    "uncertain_items": [
        "Whether ipilimumab and nivolumab were given in the neoadjuvant/adjuvant setting vs. for metastatic disease",
        "Whether patient experienced grade 3 or 4 immune-related adverse events on ipilimumab or nivolumab",
        "MRI brain results confirming at least one measurable unirradiated brain metastasis >= 0.5 cm",
    ],
    "user_message_sent": None,
    "tokens_used": None,
    "source": "outputs/02_rerun/langgraph/P004.json",
}


# ---------------------------------------------------------------------------
# Fetch trial
# ---------------------------------------------------------------------------
def fetch_trial(nct_id: str) -> dict:
    """Fetch a single trial by NCT ID from ClinicalTrials.gov v2."""
    url = f"https://clinicaltrials.gov/api/v2/studies/{nct_id}"
    r = requests.get(url, params={"format": "json"}, timeout=20)
    r.raise_for_status()
    proto = r.json().get("protocolSection", {})
    id_mod = proto.get("identificationModule", {})
    elig = proto.get("eligibilityModule", {})
    design = proto.get("designModule", {})
    sponsor = proto.get("sponsorCollaboratorsModule", {})
    return {
        "nct_id": id_mod.get("nctId", ""),
        "title": id_mod.get("briefTitle", ""),
        "eligibility": elig.get("eligibilityCriteria", ""),
        "min_age": elig.get("minimumAge", ""),
        "max_age": elig.get("maximumAge", ""),
        "sex": elig.get("sex", ""),
        "phases": design.get("phases", []),
        "sponsor": sponsor.get("leadSponsor", {}).get("name", ""),
    }


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
        # Researcher framing at end — matches current framework behavior
        return (
            f"Patient:\n"
            f"{CLINICAL_LINES}\n"
            f"- Notes: {RESEARCHER_NOTES}\n\n"
            f"{trial_text}"
            f"{suffix}"
        )
    if variant == "B":
        # Researcher framing stripped — clinical data only
        return (
            f"Patient:\n"
            f"{CLINICAL_LINES}\n\n"
            f"{trial_text}"
            f"{suffix}"
        )
    if variant == "C":
        # Researcher framing at top — before all clinical data
        return (
            f"Patient notes: {RESEARCHER_NOTES}\n\n"
            f"Patient:\n"
            f"{CLINICAL_LINES}\n\n"
            f"{trial_text}"
            f"{suffix}"
        )
    raise ValueError(f"Unknown variant: {variant}")


# ---------------------------------------------------------------------------
# Run one variant
# ---------------------------------------------------------------------------
def run_variant(variant: str, trial: dict, client: anthropic.Anthropic) -> dict:
    descriptions = {
        "A": "Current — researcher framing at END of patient context",
        "B": "Clean   — researcher framing stripped entirely",
        "C": "Framing-first — researcher framing at TOP, before clinical data",
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
        "variant": variant,
        "variant_description": descriptions[variant],
        "user_message_sent": msg,
        "raw_llm_response": raw,
        "verdict": parsed.get("verdict", "PARSE_ERROR"),
        "confidence": parsed.get("confidence"),
        "explanation": parsed.get("explanation", ""),
        "matched_criteria": parsed.get("matched_criteria", []),
        "exclusion_flags": parsed.get("exclusion_flags", []),
        "uncertain_items": parsed.get("uncertain_items", []),
        "tokens_used": response.usage.input_tokens + response.usage.output_tokens,
        "source": "fresh API call",
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

    print("Fetching NCT04511013 from ClinicalTrials.gov...")
    trial = fetch_trial("NCT04511013")
    print(f"  Title: {trial['title']}")
    print(f"  Eligibility text length: {len(trial['eligibility'])} chars")
    (OUT_DIR / "trial_NCT04511013.json").write_text(json.dumps(trial, indent=2))
    print()

    results = [EXISTING_A]
    for variant in ["A", "B", "C"]:
        print(f"Running Variant {variant}: {['Current', 'Clean', 'Framing-first'][ord(variant)-65]}...")
        r = run_variant(variant, trial, client)
        results.append(r)
        print(f"  Verdict: {r['verdict']}  Confidence: {r['confidence']}")
        # Show whether prior treatment setting was listed as uncertain
        prior_uncertain = [u for u in r["uncertain_items"]
                           if any(w in u.lower() for w in ["prior", "adjuvant", "metastatic", "setting"])]
        if prior_uncertain:
            print(f"  Prior tx setting in uncertain_items: YES — {prior_uncertain[0][:80]}")
        else:
            print(f"  Prior tx setting in uncertain_items: NO")
        print()

    # Save full results
    output = {
        "experiment": "Notes contamination test",
        "patient_id": "P004",
        "nct_id": "NCT04511013",
        "model": MODEL,
        "system_prompt": SYSTEM_PROMPT,
        "researcher_notes_tested": RESEARCHER_NOTES,
        "clinical_lines": CLINICAL_LINES,
        "hypothesis": {
            "statement": "Researcher framing in patient profile notes contaminates LLM verdicts, overriding the explicit UNCERTAIN prompt rule",
            "predicted_A": "INELIGIBLE (confirmed from existing run)",
            "predicted_B": "UNCERTAIN — prompt rule applied correctly with no framing prior",
            "predicted_C": "INELIGIBLE if content dominates position; lower confidence if position matters",
        },
        "results": results,
    }
    out_path = OUT_DIR / "results.json"
    out_path.write_text(json.dumps(output, indent=2))

    # ---------------------------------------------------------------------------
    # Comparison table
    # ---------------------------------------------------------------------------
    print("=" * 72)
    print("RESULTS — P004 × NCT04511013 — Notes contamination test")
    print("=" * 72)
    header = f"{'Variant':<12} {'Notes position':<32} {'Verdict':<12} {'Conf':>6}"
    print(header)
    print("-" * 72)

    pos_labels = {
        "A_existing": "End / current (reference run)",
        "A":          "End / current (fresh run)",
        "B":          "Stripped",
        "C":          "Top / before clinical data",
    }
    for r in results:
        print(f"  {r['variant']:<10} {pos_labels[r['variant']]:<32} {r['verdict']:<12} {str(r['confidence']):>6}")

    print()
    print("Key question — did the LLM surface the prior treatment ambiguity as UNCERTAIN?")
    print("-" * 72)
    for r in results:
        prior = [u for u in r.get("uncertain_items", [])
                 if any(w in u.lower() for w in ["prior", "adjuvant", "metastatic", "setting"])]
        surfaced = "YES" if prior else "NO"
        print(f"  Variant {r['variant']:<10} {surfaced}  {prior[0][:65] + '...' if prior else '(not listed)'}")

    print()
    print("Explanations:")
    print("-" * 72)
    for r in results:
        print(f"  Variant {r['variant']}:")
        print(f"    {r['explanation'][:200]}")
        print()

    print(f"Full results saved to: {out_path}")


if __name__ == "__main__":
    main()
