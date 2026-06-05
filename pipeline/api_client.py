"""
ClinicalTrials.gov v2 API client.

Public functions:
    fetch_trials(condition, lat, lon, radius_miles, max_results) -> list[dict]
    hard_filter_trials(trials, age, sex) -> list[dict]

The hard filter is purely deterministic (regex on eligibility text).
It eliminates trials where the patient's age or sex is an explicit mismatch.
Ambiguous cases are passed through — the LLM decides.
"""

import re
import requests

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DEFAULT_TIMEOUT = 20  # seconds


def fetch_trials(
    condition: str,
    lat: float,
    lon: float,
    radius_miles: int = 250,
    max_results: int = 50,
    max_pages: int = 1,
) -> list[dict]:
    """
    Fetch recruiting trials from ClinicalTrials.gov v2 API.

    Fetches up to `max_pages` pages of `max_results` trials each, following
    `nextPageToken` until exhausted or the page limit is reached.

    Returns a list of trial dicts, each with keys:
        nct_id, title, status, eligibility, locations, phases, sponsor
    Eligibility text is NEVER truncated.
    """
    params = {
        "query.cond": condition,
        "query.term": condition,   # also match keywords/title; catches trials registered with broad condition names
        "filter.overallStatus": "RECRUITING",
        "filter.geo": f"distance({lat},{lon},{radius_miles}mi)",
        "pageSize": min(max_results, 1000),
        "countTotal": "true",
    }

    trials = []
    for _ in range(max_pages):
        r = requests.get(BASE_URL, params=params, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        data = r.json()

        for study in data.get("studies", []):
            proto = study.get("protocolSection", {})

            id_mod = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            eligibility_mod = proto.get("eligibilityModule", {})
            contacts_mod = proto.get("contactsLocationsModule", {})
            design_mod = proto.get("designModule", {})
            sponsor_mod = proto.get("sponsorCollaboratorsModule", {})

            locations = contacts_mod.get("locations", [])
            location_list = [
                {
                    "facility": loc.get("facility", ""),
                    "city": loc.get("city", ""),
                    "state": loc.get("state", ""),
                    "country": loc.get("country", ""),
                    "status": loc.get("status", ""),
                }
                for loc in locations
            ]

            trials.append({
                "nct_id": id_mod.get("nctId", ""),
                "title": id_mod.get("briefTitle", ""),
                "status": status_mod.get("overallStatus", ""),
                "eligibility": eligibility_mod.get("eligibilityCriteria", ""),
                "min_age": eligibility_mod.get("minimumAge", ""),
                "max_age": eligibility_mod.get("maximumAge", ""),
                "sex": eligibility_mod.get("sex", ""),
                "locations": location_list,
                "phases": design_mod.get("phases", []),
                "sponsor": sponsor_mod.get("leadSponsor", {}).get("name", ""),
            })

        next_token = data.get("nextPageToken")
        if not next_token:
            break
        params["pageToken"] = next_token

    return trials


# ---------------------------------------------------------------------------
# Hard filter — deterministic, no LLM
# ---------------------------------------------------------------------------

_AGE_UNIT = {"year": 1, "years": 1, "month": 1/12, "months": 1/12}


def _parse_age_years(age_str: str) -> float | None:
    """Parse '18 Years' -> 18.0, '6 Months' -> 0.5, etc. Returns None if unparseable."""
    if not age_str:
        return None
    m = re.match(r"(\d+)\s*(year|years|month|months)", age_str, re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1)) * _AGE_UNIT[m.group(2).lower()]


def hard_filter_trials(trials: list[dict], age: int, sex: str) -> list[dict]:
    """
    Deterministic pre-filter. Eliminates trials where patient is:
      - Outside the API-reported age bounds (min_age / max_age fields)
      - Wrong sex (API sex field is ALL, MALE, or FEMALE)

    Ambiguous or missing fields -> trial is kept (LLM decides).
    Returns filtered list.
    """
    sex_norm = sex.strip().upper()  # expect "MALE" or "FEMALE"
    kept = []

    for trial in trials:
        # --- sex filter ---
        trial_sex = trial.get("sex", "ALL").strip().upper()
        if trial_sex not in ("ALL", "", sex_norm):
            continue  # explicit mismatch

        # --- age filter (API-provided bounds) ---
        min_age = _parse_age_years(trial.get("min_age", ""))
        max_age = _parse_age_years(trial.get("max_age", ""))

        if min_age is not None and age < min_age:
            continue
        if max_age is not None and age > max_age:
            continue

        kept.append(trial)

    return kept
