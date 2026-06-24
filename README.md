# Clinical Trial Matching — Framework Comparison

A 4-day experiment (June 2-5, 2026) comparing four agent orchestration frameworks on the same clinical trial matching task. Same model (claude-sonnet-4-6), same 5 patient profiles, same ClinicalTrials.gov data source.

**The finding:** framework choice controls operational concerns (debuggability, schema safety, cost), not reasoning quality. The interesting variables turned out to be prompt calibration, assessment context (trials-per-call), notes contamination, and LLM confidence overriding explicit rules.

Full writeup: [findings/4_comparison_final.md](findings/4_comparison_final.md)
Article draft: [findings/medium_article_draft.md](findings/medium_article_draft.md)

## Results at a Glance

Rerun (100mi radius, 10 pages, 193 trials assessed):

| Framework | School of thought | Cost | Wall time | ELIGIBLE | UNCERTAIN |
|-----------|-------------------|------|-----------|----------|-----------|
| LangGraph | State machine | $1.23 | 646s | 1 | 54 |
| PydanticAI | Type safety | $1.79 | 395s | 1 | 45 |
| smolagents | Code generation | $1.69 | 1163s | 2 | 53 |
| Claude Direct | Zero framework | $0.93 | 307s | 7 | 46 |

Additional data points (different methodology): ml-intern (9 ELIGIBLE), OpenHands (14 ELIGIBLE), Elicit (correct answer in 4 minutes at $0).

## Project Structure

```
pipeline/                    Shared code used by all frameworks
  config.py                    Model, radius, batch size, cost constants
  patient_schema.py            PatientProfile, TrialMatch (three-state verdict), MatchingResult
  test_profiles.py             5 test patients (P001-P005)
  api_client.py                ClinicalTrials.gov API client
  scoring.py                   Rubric scorer

implementations/             One agent per framework
  langgraph/agent.py           fetch -> filter -> [analyze_batch x N parallel] -> output
  pydantic_ai/agent.py         Agent(output_type=EligibilityAssessment), asyncio.gather
  smolagents/agent.py          CodeAgent with tool definitions
  claude_direct/agent.py       Raw AsyncAnthropic, batch-10 assessment

findings/                    Analysis and writeups
  4_comparison_final.md        Main deliverable — full comparison with 7 findings
  5_reflections.md             Reflections document
  medium_article_draft.md      Polished article draft
  7_prompt_fix_experiments.md  Fixes 1-3, Annotation-First, Structured Extraction
  8_ontology_audit.md          Ontology coverage gaps and improvement roadmap
  ground_truth_verification.json  Hand-labeled + LLM-verified ground truth
  langgraph_findings.md        Per-framework findings (also pydantic_ai_, smolagents_, claude_direct_)

outputs/                     Raw JSON results (gitignored)
  01_original_runs/            250mi, 1 page, 206 trials
  02_rerun/                    100mi, 10 pages, 193 trials (matched pipeline)
  04_agents/                   ml-intern, OpenHands, Elicit observations
  05_experiments/              Prompt fix experiments (fixC, fixD)

scripts/                     One-off experiment and runner scripts
requirements/                Per-framework pip requirements
```

## Setup

Three separate venvs (frameworks have conflicting dependencies):

```bash
python -m venv .venv && .venv/bin/pip install -r requirements/langgraph.txt
python -m venv .venv_pydantic && .venv_pydantic/bin/pip install -r requirements/pydantic_ai.txt
python -m venv .venv_smolagents && .venv_smolagents/bin/pip install -r requirements/smolagents.txt
```

Set `ANTHROPIC_API_KEY` in `.env` at the project root.

## Run

```bash
# All four frameworks sequentially
bash run_all.sh

# Individual frameworks
.venv/bin/python implementations/langgraph/agent.py
.venv_pydantic/bin/python implementations/pydantic_ai/agent.py
.venv_smolagents/bin/python implementations/smolagents/agent.py
.venv/bin/python implementations/claude_direct/agent.py

# Score outputs
.venv/bin/python evaluate.py
```

## Key Findings

1. **Frameworks are statistically flat on reasoning quality** — all produced equivalent clinical output
2. **One prompt sentence changed everything** — "Absence of information is NOT evidence of ineligibility"
3. **Notes field is a contamination risk** — researcher framing changed 2/6 borderline verdicts, bidirectionally
4. **"Absence = UNCERTAIN" is a soft rule** — LLM confidence from clinical priors overrides explicit prompt rules
5. **Trials-per-call is an architectural decision** — per-trial (conservative) vs batch-10 (inclusive) vs batch-all (comparative)
6. **Every system got P004 wrong** — except Elicit, which declined to decide
7. **You can't prompt your way out of a confidence problem** — Structured Extraction (code-as-judge) was the only fix that worked
8. **The ontology didn't need expanding — three bugs did** — fixD v2: 75.8% to 84.1% from implementation fixes, not schema changes
9. **Simpler architecture, same accuracy, lower cost** — fixE eliminates the predicate vocabulary entirely: LLM evaluates criteria directly against the typed record, code computes verdict. 86.3% accuracy, $1.72, zero parse errors
