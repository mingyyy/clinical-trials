# Day 1 Review — Monday June 2, 2026

Written end of day. Raw observations while fresh.

---

## Surprises

1. **Elicit is more capable than expected — but fails where it matters.**
   When given a direct patient query, Elicit searched ClinicalTrials.gov and ranked trials by biological fit. It didn't just redirect to literature. But it couldn't verify site locations and ranked by biology only, not full eligibility criteria. The gap is specific and measurable: it does 60% of the job and stops at the hard part (exclusion criteria, geographic verification). This is a better prior question answer than "Elicit can't do this" — it can do a version of it, just not well enough.

2. **The hard filter eliminated zero trials for P001.**
   All 50 fetched trials passed the age/sex pre-filter. This makes sense for P001 (most HER2+ breast cancer trials enroll females, and 52yo is within typical ranges), but it means the LLM has to do all the work for this profile. P004 (male, melanoma) and P002 (TNBC) will likely show more hard-filter action. Worth tracking the filter rate per profile.

3. **PydanticAI renamed `result_type` to `output_type` in v1.104.**
   The plan and all documentation used `result_type`. If you had followed the plan verbatim, the import would have failed silently at the Agent constructor. This is a real maintenance cost of type-safe frameworks — API changes break things the compiler can't catch.

4. **smolagents requires `litellm` as a separate install.**
   `pip install smolagents` does not include `litellm` despite `LiteLLMModel` being a first-class class. Discovered at import time. This is a packaging decision that will bite anyone following the README without reading the extras carefully.

5. **Docker on BCG network blocks `docker.all-hands.dev` entirely.**
   The official OpenHands registry is unreachable even without Zscaler active. Had to switch to `ghcr.io`. The multi-line `-e KEY=VALUE` docker command also breaks in zsh when the value (API key) is long — required a shell script workaround. Infrastructure friction consumed ~45 min that wasn't in the plan.

6. **`ChatAnthropic` (LangChain) does not pick up API key from `load_dotenv()`.**
   The raw `anthropic` SDK reads `ANTHROPIC_API_KEY` from the environment correctly after `load_dotenv()`. LangChain's `ChatAnthropic` does not — every call resulted in a "Connection error." even with the `.env` loaded. Fix: pass `anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY")` explicitly to the constructor. This is a LangChain-specific behaviour, not an Anthropic SDK issue. Important implication: LangGraph's observability advantage over Claude Direct comes with an extra integration surface that can fail silently.

7. **All frameworks standardised on `claude-sonnet-4-6` via `pipeline/config.py`.**
   Initial skeletons used Haiku for speed. Changed to Sonnet 4.6 for all frameworks — same model is required for a fair comparison. `pipeline/config.py` now holds MODEL, MAX_TOKENS, BATCH_SIZE, SEARCH_RADIUS_MILES, MAX_TRIALS_FETCHED, and COST_PER_M_TOKENS. Single edit propagates to all four agents.

---

## Rubric Gaps

1. **Ground truth NCT IDs not yet verified.**
   The test profiles were written with notes about expected behavior, but no actual ground-truth NCT IDs have been verified on ClinicalTrials.gov. Before scoring any framework output, need to manually run each profile on the live API and label a ground-truth set. Do this in Day 2 Block 1 (30 min), before building scoring.py.

2. **"Criteria accuracy" dimension needs a specific scoring guide.**
   The rubric says 0/1/2 per failure-mode test case. But what counts as "partially correct"? Need at least one worked example per test case before scoring begins. Add to rubric on Day 2 after seeing first real outputs.

3. **Cost dimension — resolved.** All frameworks now use `claude-sonnet-4-6` via `pipeline/config.py`. Cost formula in `findings/rubric.md` ($3/M tokens) applies correctly. No further action needed.

---

## What ml-intern and OpenHands Did (initial check)

- **ml-intern**: Launched. Still running or completed — check `outputs/ml_intern/` for output.
- **OpenHands**: Browser confirmed at localhost:3000. Task submitted. Check execution trace before Day 2 begins.

Both observations will be written up on Day 3 Block 1 per plan.

---

## Tomorrow's First Task

Day 2 opens with two quick confirmations, then scoring.py.

**Step 0 (5 min) — confirm the ChatAnthropic fix works end-to-end:**
```bash
cd trial_matching
.venv/bin/python implementations/langgraph/agent.py
```
Should show: `[P001] N eligible / 23 assessed | 23 LLM calls`. If LLM calls > 0, the fix is confirmed. If still 0, debug before anything else.

**Day 2 Block 1 — build `pipeline/scoring.py`:**
Before writing any scoring code:
1. Look at the real `outputs/01_original_runs/langgraph/P001.json` — confirm `MatchingResult` structure matches what a scorer needs
2. Manually label 5–10 P001 trials (eligible/ineligible) as ground truth using ClinicalTrials.gov
3. Then write scoring.py against real data, not hypothetical structure

Do not write scoring.py blind. Write it against actual output.
