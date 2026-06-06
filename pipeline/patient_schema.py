"""
Shared data contracts for the clinical trial matching comparison.

MatchingResult is the only schema that all framework implementations must produce.
PatientProfile and TrialMatch are helpers used within implementations.

Gaps in conformance (missing fields, wrong types) are findings, not bugs.
"""

from typing import Literal

from pydantic import BaseModel, Field


class PatientProfile(BaseModel):
    patient_id: str  # e.g. "P001"
    age: int
    sex: str  # "MALE" or "FEMALE"
    diagnosis: str  # free text, e.g. "HER2+ breast cancer stage II"
    biomarkers: list[str] = Field(default_factory=list)  # e.g. ["HER2+", "ER-"]
    prior_treatments: list[str] = Field(default_factory=list)
    ecog_ps: int | None = None  # 0-4, None if unknown
    location: str = ""  # city/state for geo filtering
    lat: float = 0.0
    lon: float = 0.0
    search_condition: str = ""  # simplified term for ClinicalTrials.gov query.cond; defaults to diagnosis if empty
    her2_ihc_score: int | None = None  # HER2 IHC score: 0, 1, 2, or 3. None if not tested/reported.
    notes: str = ""  # anything the framework should know but doesn't fit above


class TrialMatch(BaseModel):
    nct_id: str
    title: str
    verdict: Literal["ELIGIBLE", "INELIGIBLE", "UNCERTAIN"] = "INELIGIBLE"
    eligible: bool = False      # derived: verdict == "ELIGIBLE"; kept for backward compat with scoring.py
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    matched_criteria: list[str] = Field(default_factory=list)
    exclusion_flags: list[str] = Field(default_factory=list)
    uncertain_items: list[str] = Field(default_factory=list)   # data gaps that would confirm/deny eligibility
    explanation: str = ""


class MatchingResult(BaseModel):
    """
    The shared output contract.
    Every framework's agent.py should write one of these per patient as JSON.
    Output path: outputs/<framework>/P00X.json
    """
    patient_id: str
    framework: str  # "langgraph" | "pydantic_ai" | "smolagents" | "claude_direct"
    matches: list[TrialMatch]
    total_trials_fetched: int
    trials_after_hard_filter: int
    llm_calls: int = 0
    total_tokens: int = 0
    wall_time_seconds: float = 0.0
    notes: str = ""  # framework-specific observations, errors, quirks
