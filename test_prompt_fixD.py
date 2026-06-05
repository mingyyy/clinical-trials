"""
Fix D — Structured extraction + deterministic evaluation.

Architecture:
  Step 1 (LLM)  — extract a typed patient record from free-text profile.
                   null means "not stated" — an explicit absence marker.
  Step 2 (LLM)  — parse eligibility criteria into structured predicates
                   (variable + operator + required_value). Patient-agnostic.
  Step 3 (code) — evaluate each predicate against the typed record.
                   null in record → DATA_MISSING. No LLM judgment involved.

Verdict (code):
  any CONFIRMED_FAILED exclusion predicate → INELIGIBLE
  any DATA_MISSING on a material criterion  → UNCERTAIN
  otherwise                                 → ELIGIBLE

The LLM never produces a verdict. The LLM never sees both the criterion
and the patient together in a judgment context. Inference has nowhere to land.

Run with: .venv/bin/python test_prompt_fixD.py
Output:   outputs/05_experiments/prompt_fixes/fixD/results.json
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
OUT_DIR = Path(__file__).parent / "outputs" / "05_experiments" / "prompt_fixes" / "fixD"
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

# ─────────────────────────────────────────────────────────────────────────────
# Step 1 prompt — patient record extraction
# ─────────────────────────────────────────────────────────────────────────────

EXTRACT_SYSTEM = """\
You are a medical data extractor. Convert the free-text patient profile into a
typed JSON record using the schema below.

CRITICAL RULES:
- Use null for ANY field not explicitly stated in the profile text.
- Do NOT infer, assume, or use clinical knowledge to fill gaps.
- "prior_treatment.setting" must be null unless the profile explicitly names
  the disease stage or context when the drug was given (e.g. "adjuvant",
  "for metastatic disease", "after surgery").
- "disease.is_metastatic" = true only if the profile uses the word "metastatic"
  or "stage IV". false only if the profile explicitly says a non-metastatic
  stage (e.g. "stage II", "stage III"). null if not stated.
- "disease.is_locally_advanced" = true only if the profile explicitly says
  "locally advanced" or "unresectable". null otherwise.
- "disease.is_unresectable" = true only if the profile explicitly says
  "unresectable". null otherwise.
- Do NOT set a field to false unless the profile explicitly contradicts it.
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
    "metastatic_sites": [string] or null,
    "brain_metastases_present": true | false | null,
    "brain_metastases_irradiated": true | false | null,
    "brain_metastases_measurable_unirradiated": true | false | null
  },
  "biomarkers": {
    "her2": "positive" | "negative" | "equivocal" | null,
    "er": "positive" | "negative" | null,
    "pr": "positive" | "negative" | null,
    "braf_status": "mutant" | "wildtype" | null,
    "braf_variant": "V600E" | "V600K" | "other" | null,
    "brca1": "pathogenic" | "wildtype" | null,
    "brca2": "pathogenic" | "wildtype" | null,
    "msi": "MSI-H" | "MSS" | null,
    "pdl1_expression": string or null
  },
  "prior_treatments": [
    {
      "drug": string,
      "drug_class": string or null,
      "setting": "adjuvant" | "neoadjuvant" | "metastatic" | "palliative" | null,
      "line_of_therapy": integer or null,
      "completed": true | false | null,
      "irae_grade_3_4": true | false | null
    }
  ]
}
"""

# ─────────────────────────────────────────────────────────────────────────────
# Step 2 prompt — criterion parser (patient-agnostic)
# ─────────────────────────────────────────────────────────────────────────────

PARSE_SYSTEM = """\
You are a clinical trial criteria parser. Convert eligibility criteria text
into structured predicates. You do NOT have a patient profile — this is purely
a text-to-structure conversion.

For each material criterion, produce one predicate object.

Variable paths use dot-notation into this patient record schema:
  ecog_ps                                    (integer)
  disease.is_metastatic                      (bool)
  disease.is_locally_advanced                (bool)
  disease.is_unresectable                    (bool)
  disease.stage_numeric                      ("I","II","III","IV")
  disease.brain_metastases_present           (bool)
  disease.brain_metastases_irradiated        (bool)
  disease.brain_metastases_measurable_unirradiated (bool)
  disease.primary_condition                  (string)
  disease.histology_subtype                  (string)
  biomarkers.her2                            ("positive","negative","equivocal")
  biomarkers.er                              ("positive","negative")
  biomarkers.pr                              ("positive","negative")
  biomarkers.braf_status                     ("mutant","wildtype")
  biomarkers.braf_variant                    ("V600E","V600K","other")
  biomarkers.brca1                           ("pathogenic","wildtype")
  biomarkers.brca2                           ("pathogenic","wildtype")
  biomarkers.msi                             ("MSI-H","MSS")
  prior_treatments[*].setting                (list field: "adjuvant","neoadjuvant","metastatic",null)
  prior_treatments[*].drug                   (list field: string)
  prior_treatments[*].drug_class             (list field: string)
  prior_treatments[*].irae_grade_3_4         (list field: bool)

Operators:
  eq / neq / lt / lte / gt / gte             — scalar comparison
  in_set / not_in_set                        — value in/not in a set of values
  is_true / is_false                         — boolean check
  list_any_eq / list_none_eq                 — any/no item in list field equals value
  list_any_null                              — any item in list has null for this field

For OR criteria (e.g. "metastatic OR locally advanced"), produce an object
with "or_predicates": [...] instead of a single variable/operator.

Set parseable=false for criteria that cannot be expressed as a predicate
(e.g. "adequate organ function", "able to swallow pills"). These will be
treated as DATA_MISSING automatically.

Return ONLY valid JSON — an array of predicate objects, no markdown:

[
  {
    "criterion_text": string (brief description),
    "criterion_type": "inclusion" | "exclusion",
    "parseable": true | false,
    "variable": string or null,
    "operator": string or null,
    "required_value": any or null,
    "list_field": string or null,          (for list_any_eq / list_none_eq)
    "or_predicates": [ {...} ] or null     (for OR criteria)
  }
]
"""

# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — deterministic predicate evaluator
# ─────────────────────────────────────────────────────────────────────────────

def get_path(record: dict, path: str):
    """Resolve a dot-notation path into the record. Returns None if any key missing."""
    parts = path.split(".")
    node = record
    for p in parts:
        if not isinstance(node, dict):
            return None
        node = node.get(p)
        if node is None:
            return None
    return node


def evaluate_single(pred: dict, record: dict) -> str:
    """
    Evaluate one predicate against the typed record.
    Returns: CONFIRMED_MET | CONFIRMED_FAILED | DATA_MISSING
    """
    if not pred.get("parseable", True):
        return "DATA_MISSING"

    # OR predicate
    if pred.get("or_predicates"):
        sub_results = [evaluate_single(p, record) for p in pred["or_predicates"]]
        if any(r == "CONFIRMED_MET" for r in sub_results):
            return "CONFIRMED_MET"
        if all(r == "CONFIRMED_FAILED" for r in sub_results):
            return "CONFIRMED_FAILED"
        return "DATA_MISSING"  # any branch uncertain → conservative

    variable = pred.get("variable")
    operator = pred.get("operator")
    required = pred.get("required_value")

    if not variable or not operator:
        return "DATA_MISSING"

    # List operators (prior_treatments[*].field)
    if operator in ("list_any_eq", "list_none_eq", "list_any_null"):
        list_path = variable.split("[*]")[0]
        field = pred.get("list_field")
        items = get_path(record, list_path)
        if items is None:
            return "DATA_MISSING"
        if not isinstance(items, list):
            return "DATA_MISSING"

        if operator == "list_any_null":
            return "CONFIRMED_MET" if any(
                item.get(field) is None for item in items
            ) else "CONFIRMED_FAILED"

        values = [item.get(field) for item in items]

        if operator == "list_any_eq":
            if any(v == required for v in values):
                return "CONFIRMED_MET"
            if any(v is None for v in values):
                return "DATA_MISSING"   # unknown items — could match
            return "CONFIRMED_FAILED"

        if operator == "list_none_eq":
            if any(v == required for v in values):
                return "CONFIRMED_FAILED"
            if any(v is None for v in values):
                return "DATA_MISSING"   # unknown — might match
            return "CONFIRMED_MET"

    # Scalar operators
    value = get_path(record, variable)
    if value is None:
        return "DATA_MISSING"

    if operator == "eq":
        return "CONFIRMED_MET" if value == required else "CONFIRMED_FAILED"
    if operator == "neq":
        return "CONFIRMED_MET" if value != required else "CONFIRMED_FAILED"
    if operator == "lt":
        return "CONFIRMED_MET" if value < required else "CONFIRMED_FAILED"
    if operator == "lte":
        return "CONFIRMED_MET" if value <= required else "CONFIRMED_FAILED"
    if operator == "gt":
        return "CONFIRMED_MET" if value > required else "CONFIRMED_FAILED"
    if operator == "gte":
        return "CONFIRMED_MET" if value >= required else "CONFIRMED_FAILED"
    if operator == "is_true":
        return "CONFIRMED_MET" if value is True else "CONFIRMED_FAILED"
    if operator == "is_false":
        return "CONFIRMED_MET" if value is False else "CONFIRMED_FAILED"
    if operator == "in_set":
        return "CONFIRMED_MET" if value in required else "CONFIRMED_FAILED"
    if operator == "not_in_set":
        return "CONFIRMED_MET" if value not in required else "CONFIRMED_FAILED"

    return "DATA_MISSING"


def compute_verdict(evaluated: list[dict]) -> tuple[str, list, list]:
    """
    Derive verdict from evaluated predicates.
    Returns (verdict, failures, unknowns)
    """
    failures, unknowns = [], []
    for e in evaluated:
        result = e["result"]
        ctype = e["criterion_type"]
        if result == "CONFIRMED_FAILED":
            failures.append(e)
        elif result == "DATA_MISSING" and ctype == "exclusion":
            unknowns.append(e)

    if failures:
        return "INELIGIBLE", failures, unknowns
    if unknowns:
        return "UNCERTAIN", failures, unknowns
    return "ELIGIBLE", [], []


# ─────────────────────────────────────────────────────────────────────────────
# API helpers
# ─────────────────────────────────────────────────────────────────────────────

def fetch_trial(nct_id: str) -> dict:
    url = f"https://clinicaltrials.gov/api/v2/studies/{nct_id}"
    params = {"fields": "protocolSection.identificationModule,protocolSection.eligibilityModule"}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    ps = r.json()["protocolSection"]
    return {
        "nct_id": nct_id,
        "title": ps["identificationModule"].get("briefTitle", ""),
        "eligibility": ps["eligibilityModule"].get("eligibilityCriteria", ""),
    }


def call_claude(system: str, user: str, max_tokens: int = 2048) -> dict | list:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


def patient_text(patient) -> str:
    return (
        f"Age: {patient.age}, Sex: {patient.sex}\n"
        f"Diagnosis: {patient.diagnosis}\n"
        f"Biomarkers: {', '.join(patient.biomarkers)}\n"
        f"Prior treatments: {', '.join(patient.prior_treatments)}\n"
        f"ECOG PS: {patient.ecog_ps}\n"
        f"Location: {patient.location}\n"
        f"Notes: {patient.notes}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main assessment pipeline
# ─────────────────────────────────────────────────────────────────────────────

MAX_CRITERIA_CHARS = 6000   # truncate very long criteria before parsing

def assess(patient, trial: dict, patient_record: dict) -> dict:
    """Run Steps 2+3 for one trial against a pre-extracted patient record."""
    # Truncate eligibility text to avoid overflowing parser output budget
    eligibility = trial["eligibility"]
    if len(eligibility) > MAX_CRITERIA_CHARS:
        eligibility = eligibility[:MAX_CRITERIA_CHARS] + "\n[truncated — focus on criteria above]"

    # Step 2 — parse criteria (higher token budget; criteria can produce large JSON)
    predicates = call_claude(
        PARSE_SYSTEM,
        f"Parse these eligibility criteria into structured predicates:\n\n{eligibility}",
        max_tokens=4096,
    )
    if isinstance(predicates, dict):
        predicates = predicates.get("predicates", [predicates])

    # Step 3 — evaluate each predicate
    evaluated = []
    for pred in predicates:
        result = evaluate_single(pred, patient_record)
        evaluated.append({**pred, "result": result})

    verdict, failures, unknowns = compute_verdict(evaluated)
    return {
        "nct_id": trial["nct_id"],
        "title": trial["title"],
        "verdict": verdict,
        "failures": failures,
        "unknowns": unknowns,
        "all_evaluated": evaluated,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Test runner
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("Fix D — Structured extraction + deterministic evaluation")
    print("=" * 60)

    # Cache patient records (Step 1 — once per patient)
    patient_records = {}
    for patient in [PATIENT_P004, PATIENT_P001]:
        if patient.patient_id not in patient_records:
            print(f"\nExtracting typed record for {patient.patient_id}...")
            record = call_claude(EXTRACT_SYSTEM, f"Extract structured record from this patient profile:\n\n{patient_text(patient)}")
            patient_records[patient.patient_id] = record
            print(f"  prior_treatments: {[{'drug': t['drug'], 'setting': t.get('setting')} for t in record.get('prior_treatments', [])]}")
            print(f"  disease.is_metastatic: {record.get('disease', {}).get('is_metastatic')}")
            print(f"  disease.is_locally_advanced: {record.get('disease', {}).get('is_locally_advanced')}")
            print(f"  biomarkers.her2: {record.get('biomarkers', {}).get('her2')}")
            print(f"  ecog_ps: {record.get('ecog_ps')}")

    # Fetch trials
    print("\nFetching trial data...")
    trials_cache = {}
    for nct_id in set(tc["nct_id"] for tc in TEST_CASES):
        print(f"  {nct_id}...", end=" ", flush=True)
        trials_cache[nct_id] = fetch_trial(nct_id)
        print("ok")

    # Run test cases
    results = []
    print()
    for tc in TEST_CASES:
        print(f"{tc['label']}")
        t0 = time.time()
        try:
            record = patient_records[tc["patient"].patient_id]
            trial = trials_cache[tc["nct_id"]]
            result = assess(tc["patient"], trial, record)
            elapsed = time.time() - t0

            verdict = result["verdict"]
            passed = verdict == tc["expect"]
            flag = "PASS" if passed else "FAIL"
            print(f"  {flag}  verdict={verdict}  expected={tc['expect']}  [{elapsed:.1f}s]")

            if result["failures"]:
                print(f"  CONFIRMED_FAILED ({len(result['failures'])}):")
                for f in result["failures"][:3]:
                    print(f"    [{f['criterion_type']}] {f['criterion_text'][:70]}")
                    print(f"      var={f.get('variable')} op={f.get('operator')} req={f.get('required_value')}")

            if result["unknowns"]:
                print(f"  DATA_MISSING exclusions ({len(result['unknowns'])}):")
                for u in result["unknowns"][:3]:
                    print(f"    {u['criterion_text'][:70]}")
                    print(f"      var={u.get('variable')} → record_value=null")

            if not passed:
                # Show what the predicate evaluator saw for key criteria
                print(f"  [debug] all evaluated ({len(result['all_evaluated'])}):")
                for e in result["all_evaluated"][:5]:
                    print(f"    {e['result']:18} [{e['criterion_type']}] {e['criterion_text'][:55]}")

            results.append({
                "label": tc["label"],
                "nct_id": tc["nct_id"],
                "patient_id": tc["patient"].patient_id,
                "expected": tc["expect"],
                "verdict": verdict,
                "passed": passed,
                "failures": result["failures"],
                "unknowns": result["unknowns"],
                "all_evaluated": result["all_evaluated"],
                "elapsed_s": round(elapsed, 1),
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  ERROR: {e}")
            results.append({"label": tc["label"], "error": str(e), "passed": False})
        time.sleep(0.5)

    # Compare all fixes
    passed_count = sum(1 for r in results if r.get("passed"))
    print(f"\n{'='*60}")
    print(f"Fix D results: {passed_count}/{len(TEST_CASES)} passed")

    prev_path = Path(__file__).parent / "outputs" / "05_experiments" / "prompt_fixes" / "all_results.json"
    fixC_path = Path(__file__).parent / "outputs" / "05_experiments" / "prompt_fixes" / "fixC" / "results.json"

    if prev_path.exists() and fixC_path.exists():
        prev = json.load(open(prev_path))
        fixC = json.load(open(fixC_path))
        print(f"\n{'Label':<35} {'baseline':>11} {'fix1':>8} {'fix2':>8} {'fixC':>8} {'fixD':>8}")
        print("-" * 82)
        for tc in TEST_CASES:
            row = f"{tc['label']:<35}"
            for vname, src in [("baseline", prev), ("fix1", prev), ("fix2", prev), ("fixC", fixC)]:
                m = next((r for r in src if r.get("variant", vname) == vname
                          and r.get("label") == tc["label"]), None)
                if m and "verdict" in m:
                    sym = "✓" if m["passed"] else "✗"
                    row += f" {sym}{m['verdict'][:7]:>7}"
                else:
                    row += f" {'ERR':>8}"
            m = next((r for r in results if r.get("label") == tc["label"]), None)
            if m and "verdict" in m:
                sym = "✓" if m["passed"] else "✗"
                row += f" {sym}{m['verdict'][:7]:>7}"
            print(row)

    (OUT_DIR / "results.json").write_text(json.dumps(results, indent=2))
    (OUT_DIR / "patient_records.json").write_text(json.dumps(patient_records, indent=2))
    print(f"\nResults saved to {OUT_DIR}/")


if __name__ == "__main__":
    main()
