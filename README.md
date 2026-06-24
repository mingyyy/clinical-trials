# Clinical Trial Matching — Framework Comparison

A study comparing agent orchestration frameworks and evaluation architectures on clinical trial matching. Started as a 4-day experiment (June 2-5, 2026) comparing four frameworks, then extended into post-study iteration on accuracy and benchmarking.

Same model throughout (claude-sonnet-4-6), same 5 patient profiles, same ClinicalTrials.gov data source. Benchmarked against the TrialGPT SIGIR cohort (58 patients, 3,141 pairs, expert-labeled).

**The finding:** framework choice is the least interesting variable. The variables that matter: one sentence in a prompt, how many trials share a context window, whether the LLM's confidence overrides explicit rules, and the clinical ambiguity of the patient's presentation.

Full writeup (11 findings): [findings/medium_article_draft.md](findings/medium_article_draft.md)
Comparison analysis: [findings/4_comparison_final.md](findings/4_comparison_final.md)

## Results at a Glance

### Framework comparison (100mi radius, 10 pages, 193 trials)

| Framework | School of thought | Cost | Wall time | ELIGIBLE | UNCERTAIN |
|-----------|-------------------|------|-----------|----------|-----------|
| LangGraph | State machine | $1.23 | 646s | 1 | 54 |
| PydanticAI | Type safety | $1.79 | 395s | 1 | 45 |
| smolagents | Code generation | $1.69 | 1163s | 2 | 53 |
| Claude Direct | Zero framework | $0.93 | 307s | 7 | 46 |

Additional: ml-intern (9 ELIGIBLE), OpenHands (14 ELIGIBLE), Elicit (correct answer in 4 minutes at $0).

### Architecture evolution (post-study)

| Version | Architecture | Accuracy | Cost |
|---------|-------------|----------|------|
| fixD v1 | Extract + parse predicates + code eval | 75.8% | $2.16 |
| fixD v2 | + 3 bug fixes | 84.1% | $2.33 |
| fixE | Extract + LLM eval against record + code verdict | **86.3%** | **$1.72** |

### SIGIR benchmark (expert-labeled, general medicine)

| Approach | Binary accuracy | Eligible recall |
|----------|----------------|----------------|
| fixE (conservative absence rule) | 83.3% | 11.5% |
| Direct (conservative) | 87.4% | 7.7% |
| TrialGPT-style (permissive absence rule) | 83.7% | **42.3%** |

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
  medium_article_draft.md      Article draft — 11 findings, full arc
  4_comparison_final.md        Main deliverable — framework comparison + post-study iteration
  5_reflections.md             Reflections document
  7_prompt_fix_experiments.md  Fixes 1-3, Annotation-First, Structured Extraction
  8_ontology_audit.md          Ontology audit + v2 bug fix results
  9_fixD_v2_error_analysis.md  Error analysis, fixE design, improvement options
  10_sigir_benchmark.md        SIGIR benchmark — 5 approaches, clinical ambiguity finding
  ground_truth_v2.json         Corrected ground truth (236 labels)

scripts/                     Experiment and runner scripts
  run_fixE_all_patients.py     fixE runner (best architecture for oncology)
  test_prompt_fixD.py          fixD runner (predicate-based)
  benchmark_fixE_sigir.py      SIGIR benchmark runner
  run_fixD_all_patients.py     fixD full-run script

benchmark/                   External benchmark data (gitignored)
  trialgpt/                    TrialGPT repo + SIGIR/TREC data

outputs/                     Raw JSON results (gitignored)
requirements/                Per-framework pip requirements
```

## Setup

```bash
python -m venv .venv && .venv/bin/pip install -r requirements/langgraph.txt
python -m venv .venv_pydantic && .venv_pydantic/bin/pip install -r requirements/pydantic_ai.txt
python -m venv .venv_smolagents && .venv_smolagents/bin/pip install -r requirements/smolagents.txt
```

Set `ANTHROPIC_API_KEY` in `.env` at the project root.

## Run

```bash
# All four frameworks
bash run_all.sh

# fixE (best accuracy)
.venv/bin/python scripts/run_fixE_all_patients.py

# SIGIR benchmark
.venv/bin/python scripts/benchmark_fixE_sigir.py --patients 5

# Score outputs
.venv/bin/python evaluate.py
```

## 11 Key Findings

1. **Frameworks are statistically flat on reasoning quality** — all produced equivalent clinical output
2. **One prompt sentence changed everything** — "Absence of information is NOT evidence of ineligibility"
3. **Notes field is a contamination risk** — researcher framing changed 2/6 borderline verdicts, bidirectionally
4. **"Absence = UNCERTAIN" is a soft rule** — LLM confidence from clinical priors overrides explicit prompt rules
5. **Trials-per-call is an architectural decision** — per-trial (conservative) vs batch-10 (inclusive) vs batch-all (comparative)
6. **Every system got P004 wrong** — except Elicit, which declined to decide
7. **You can't prompt your way out of a confidence problem** — Structured Extraction (code-as-judge) was the only fix
8. **The ontology didn't need expanding — three bugs did** — 75.8% to 84.1% from implementation fixes, not schema changes
9. **You can skip the predicate vocabulary entirely** — fixE: simpler, cheaper, same accuracy
10. **The absence rule matters more than the architecture** — one prompt sentence tripled eligible recall (11% to 42%)
11. **The ceiling is clinical ambiguity, not system design** — 5 approaches all hit ~33% on ambiguous patients, ~73% on clear ones
