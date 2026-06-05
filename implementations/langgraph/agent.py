"""
LangGraph implementation — clinical trial matching.

School of thought: state machine. Every step is a named node with explicit
edges. The graph is fully prescribed before any LLM call runs.

Run with: .venv/bin/python implementations/langgraph/agent.py
Output:   outputs/langgraph/P00X.json
"""

import json
import operator
import os
import re
import sys
import time
from pathlib import Path
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

BATCH_SIZE = 12   # max concurrent LLM calls; keeps rate limits comfortable

# Add project root to path so we can import pipeline modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pipeline.api_client import fetch_trials, hard_filter_trials
from pipeline.config import MAX_PAGES, MAX_TOKENS, MAX_TRIALS_FETCHED, MODEL, SEARCH_RADIUS_MILES
from pipeline.patient_schema import MatchingResult, PatientProfile, TrialMatch
from pipeline.test_profiles import TEST_PROFILES

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# ---------------------------------------------------------------------------
# State definition — the only thing that flows between nodes
# ---------------------------------------------------------------------------

class MatchingState(TypedDict):
    patient: PatientProfile
    raw_trials: list[dict]
    filtered_trials: list[dict]           # holds the batch slice inside analyze_batch branches
    filtered_trials_count: int            # full count set by filter_node; never modified by batches
    matches: Annotated[list[TrialMatch], operator.add]   # reducer: branches extend the list
    llm_calls: Annotated[int, operator.add]              # reducer: branches accumulate counts
    total_tokens: Annotated[int, operator.add]           # reducer: branches accumulate tokens
    _t0: float
    errors: list[str]


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

def fetch_node(state: MatchingState) -> MatchingState:
    """Node 1: Fetch trials from ClinicalTrials.gov API."""
    patient = state["patient"]
    trials = fetch_trials(
        condition=patient.search_condition or patient.diagnosis,
        lat=patient.lat,
        lon=patient.lon,
        radius_miles=SEARCH_RADIUS_MILES,
        max_results=MAX_TRIALS_FETCHED,
        max_pages=MAX_PAGES,
    )
    return {**state, "raw_trials": trials}


def filter_node(state: MatchingState) -> MatchingState:
    """Node 2: Hard filter — deterministic age/sex pre-filter, no LLM."""
    filtered = hard_filter_trials(
        state["raw_trials"],
        age=state["patient"].age,
        sex=state["patient"].sex,
    )
    return {**state, "filtered_trials": filtered, "filtered_trials_count": len(filtered)}


def route_to_batched_analysis(state: MatchingState) -> list[Send]:
    """Fan out filtered_trials to parallel batches of BATCH_SIZE each."""
    trials = state["filtered_trials"]
    batches = [trials[i:i + BATCH_SIZE] for i in range(0, len(trials), BATCH_SIZE)]
    return [Send("analyze_batch", {**state, "filtered_trials": batch}) for batch in batches]


def analyze_batch_node(state: MatchingState) -> dict:
    """Node 3 (parallel): LLM eligibility assessment for one batch of trials."""
    patient = state["patient"]
    model = ChatAnthropic(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
    )

    system = SystemMessage(content=(
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
    ))

    matches = []
    llm_calls = 0       # delta for this batch; reducer accumulates across branches
    total_tokens = 0    # delta for this batch

    for trial in state["filtered_trials"]:
        user_msg = HumanMessage(content=f"""
Patient:
- Age: {patient.age}, Sex: {patient.sex}
- Diagnosis: {patient.diagnosis}
- Biomarkers: {', '.join(patient.biomarkers)}
- Prior treatments: {', '.join(patient.prior_treatments)}
- ECOG PS: {patient.ecog_ps}
- Notes: {patient.notes}

Trial: {trial['nct_id']} — {trial['title']}
Phases: {', '.join(trial['phases']) if trial['phases'] else 'N/A'}
Sponsor: {trial['sponsor']}

Eligibility criteria:
{trial['eligibility']}

Respond ONLY with valid JSON. No markdown, no explanation outside the JSON.
""")

        try:
            response = model.invoke([system, user_msg])
            llm_calls += 1
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                total_tokens += response.usage_metadata.get("total_tokens", 0)

            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.content.strip())
            result = json.loads(raw)
            verdict = result.get("verdict", "INELIGIBLE").upper()
            if verdict not in ("ELIGIBLE", "INELIGIBLE", "UNCERTAIN"):
                verdict = "INELIGIBLE"
            matches.append(TrialMatch(
                nct_id=trial["nct_id"],
                title=trial["title"],
                verdict=verdict,
                eligible=(verdict == "ELIGIBLE"),
                confidence=float(result.get("confidence", 0.0)),
                matched_criteria=result.get("matched_criteria", []),
                exclusion_flags=result.get("exclusion_flags", []),
                uncertain_items=result.get("uncertain_items", []),
                explanation=result.get("explanation", ""),
            ))
        except Exception as e:
            # Parsing failure is a finding, not a crash
            matches.append(TrialMatch(
                nct_id=trial["nct_id"],
                title=trial["title"],
                verdict="UNCERTAIN",
                eligible=False,
                confidence=0.0,
                uncertain_items=["parse error — could not assess"],
                explanation=f"[parse error: {e}]",
            ))

    return {"matches": matches, "llm_calls": llm_calls, "total_tokens": total_tokens}


def output_node(state: MatchingState) -> MatchingState:
    """Node 4: Serialize MatchingResult to outputs/langgraph/P00X.json."""
    patient = state["patient"]
    result = MatchingResult(
        patient_id=patient.patient_id,
        framework="langgraph",
        matches=state["matches"],
        total_trials_fetched=len(state["raw_trials"]),
        trials_after_hard_filter=state["filtered_trials_count"],
        llm_calls=state["llm_calls"],
        total_tokens=state["total_tokens"],
        wall_time_seconds=round(time.time() - state["_t0"], 1),
    )

    out_dir = Path(__file__).parent.parent.parent / "outputs" / "02_rerun" / "langgraph"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{patient.patient_id}.json"
    out_path.write_text(result.model_dump_json(indent=2))
    eligible = sum(1 for m in state["matches"] if m.verdict == "ELIGIBLE")
    uncertain = sum(1 for m in state["matches"] if m.verdict == "UNCERTAIN")
    ineligible = sum(1 for m in state["matches"] if m.verdict == "INELIGIBLE")
    print(f"  [{patient.patient_id}] ELIGIBLE={eligible} UNCERTAIN={uncertain} INELIGIBLE={ineligible} "
          f"| {state['llm_calls']} LLM calls | written to {out_path.name}")
    return state


# ---------------------------------------------------------------------------
# Build graph
# ---------------------------------------------------------------------------

def build_graph():
    g = StateGraph(MatchingState)
    g.add_node("fetch", fetch_node)
    g.add_node("filter", filter_node)
    g.add_node("analyze_batch", analyze_batch_node)
    g.add_node("output", output_node)

    g.add_edge(START, "fetch")
    g.add_edge("fetch", "filter")
    g.add_conditional_edges("filter", route_to_batched_analysis)  # fan-out: N batches run in parallel
    g.add_edge("analyze_batch", "output")                          # fan-in: all branches converge at output
    g.add_edge("output", END)

    return g.compile()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    graph = build_graph()
    print("=== LangGraph: Clinical Trial Matching ===")

    for patient in TEST_PROFILES:
        print(f"\nRunning {patient.patient_id}: {patient.diagnosis[:50]}...")
        t0 = time.time()
        graph.invoke({
            "patient": patient,
            "raw_trials": [],
            "filtered_trials": [],
            "filtered_trials_count": 0,
            "matches": [],
            "llm_calls": 0,
            "total_tokens": 0,
            "_t0": time.time(),
            "errors": [],
        })
        elapsed = time.time() - t0
        print(f"  wall time: {elapsed:.1f}s")

    print("\n=== LangGraph done ===")
