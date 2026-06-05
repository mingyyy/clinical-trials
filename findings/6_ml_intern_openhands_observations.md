# ml-intern and OpenHands — Observations
# Day 5 addition, June 2026

---

## ml-intern

### What It Is (Corrected)

Secondary sources (marktechpost.com and others) described ml-intern as "built on smolagents." Reading the actual source code shows this is false.

**Actual architecture:**
- `agent/core/agent_loop.py`: LiteLLM's `acompletion` with standard JSON tool-calling
- `agent/core/tools.py`: Tool management via fastmcp (MCP protocol)
- No smolagents dependency anywhere in the codebase

ml-intern is a **tool-calling agent with MCP tools**, architecturally closer to Claude Direct (raw API + JSON tool calls) than to smolagents' code-generation paradigm. The smolagents logo in the README is HuggingFace branding, not the underlying framework.

**Tools available:**
`web_search`, `research`, `hf_papers`, `hf_inspect_dataset`, `github_find_examples`, `github_list_repos`, `github_read_file`, `hf_repo_files`, `hf_repo_git`, `hf_jobs`, `sandbox` (code execution), `plan`, `notify`

---

### Run Attempts and Results

**Goal:** Give ml-intern the P001 query (HER2+ breast cancer, NYC, 100-mile radius) and observe its behavior.

**Attempt 1 — `anthropic/claude-sonnet-4-6` (same model as frameworks):**
```
HF token loaded
Model: anthropic/claude-sonnet-4-6
Error: Authentication failed — your Hugging Face token is missing or invalid.
```
ml-intern validates the HF token against HF's API at startup regardless of which LLM is specified, and the HF Router does not carry any Anthropic/Claude models. The model ID `anthropic/claude-sonnet-4-6` is not recognized by the HF Router catalog (which has 119 open-source models). To use Claude, you'd need a provider suffix like `:fal-ai` — but Claude Sonnet 4.6 is not available on fal-ai either as of June 2026.

**Attempt 2 — `ollama/mistral:7b` (local model):**
```
litellm.InternalServerError: llama runner process has terminated: signal: killed
```
Mistral 7B OOM-killed. Not enough RAM on this machine.

**Attempt 3 — `ollama/phi3:mini`:**
```
litellm.BadRequestError: phi3:mini does not support tools
```
phi3:mini lacks JSON function calling support — incompatible with ml-intern's tool mechanism.

**Attempt 4 — `deepseek-ai/DeepSeek-V4-Flash` (HF Router model, supports tools):**
Run completed. Model: DeepSeek V4 Flash via HF inference. Max iterations: 20. History size: 47 messages.

Note: this is a different model than the four frameworks (which all used claude-sonnet-4-6). The observation captures behavioral patterns, not reasoning quality.

**Attempt 5 — `deepseek-ai/DeepSeek-V4-Flash` with options 1+2 (API context in prompt, max-iterations 50):**
```
HF token loaded
Model: deepseek-ai/DeepSeek-V4-Flash
Error: litellm.APIError: Error code: 402 — You have depleted your monthly
included credits. Purchase pre-paid credits to continue using Inference Providers.
```
HF Router inference credits exhausted. The monthly free allowance was consumed by the first DeepSeek run (Attempt 4) and other inference calls. Options 1+2 were correctly configured — the prompt had the API endpoint and `--max-iterations 50` was set — but the run never started due to billing. This forced a decision: buy HF credits to continue with DeepSeek, or patch ml-intern to use the Anthropic API key already available in `.env`.

**Decision: apply the Claude patch.** The 402 error made the patch necessary rather than optional. Using Claude Sonnet 4.6 also resolves the model comparability problem — the four frameworks all used the same model, so a Claude-powered ml-intern run is genuinely comparable.

**Attempt 6 — `anthropic/claude-sonnet-4-6` (patched, options 1+2):**
Run completed. 45 messages, 0 web searches, 5/5 plan steps done. Full output in `outputs/ml_intern/run_claude_sonnet.log`.

---

### What the Run Revealed

#### Step trace (Attempt 4 — DeepSeek V4 Flash, 20 iterations, history_size=47)

```
▸ web_search  "HER2-positive breast cancer clinical trials recruiting NYC 2025..."
▸ web_search  "clinicaltrials.gov HER2-positive breast cancer recruiting New York..."
▸ web_search  "site:clinicaltrials.gov HER2-positive breast cancer recruiting New..."
▸ web_search  "clinicaltrials.gov HER2+ breast cancer trastuzumab pertuzumab recruit..."
▸ web_search  "HER2-positive breast cancer clinical trials NYC Memorial Sloan Ketter..."
▸ web_search  "clinicaltrials.gov API search HER2-positive breast cancer recruiting..."
▸ web_search  "site:clinicaltrials.gov \"HER2-positive\" \"breast\" recruiting New..."
[agent decides to use API directly]
▸ bash  curl ClinicalTrials.gov API (wrong parameters — trial and error)
▸ bash  curl (retry with different params)
▸ bash  curl (retry)
▸ bash  curl with query.cond (still wrong)
▸ bash  curl (retry)
▸ bash  cat /tmp/ct_results.json  [checking error response]
▸ bash  curl (working call achieved)
▸ bash  curl with full fields request
▸ bash  curl with different field names
▸ web_search  "clinicaltrials.gov API v2 field names briefTitle locationCity eligibili..."
         [agent searches for API docs mid-run]
▸ bash  curl API docs endpoint
▸ bash  curl all results, save locally
▸ bash  curl page 2
▸ bash  curl page 3
▸ bash  python3 heredoc [process all pages, filter near NYC]
▸ bash  python3 heredoc [extract eligibility details for HER2+ trials]
▸ bash  python3 heredoc [get full criteria for most relevant trials]
▸ read  [read temp file with criteria output]
--- turn_complete (history_size=47) ---
```

**Key behavioral observations:**

**1. Web search first — 7 queries before trying the API.**
The agent did not know the ClinicalTrials.gov API existed. It searched the web seven times looking for trials before deciding to query the source directly. Our four frameworks used `api_client.py` which encoded the right endpoint and parameters upfront. ml-intern rediscovered this from scratch, spending 7 of its 20 iterations on fruitless web searches.

**2. API discovery through trial and error — ~8 failed curl calls.**
Once it decided to use the API, it had to figure out the correct v2 parameter format through repeated failed calls. It even searched for API documentation mid-run (one web_search for "clinicaltrials.gov API v2 field names"). Our `api_client.py` encoded this knowledge once; ml-intern re-derived it each run.

**3. Python inline — not tool calls.**
To process the JSON responses, the agent wrote Python code in bash heredocs (`python3 << 'PYEOF'`). It couldn't import libraries outside the bash shell, so it used only the standard library. This is analogous to smolagents writing Python — but without the tool abstraction layer.

**4. Hit max iterations before producing a final assessment.**
`history_size=47` with `Max iterations: 20` means the agent ran 20 complete LLM call cycles. The final eligibility assessment text was not captured in the log (the terminal output for the final reasoning step was lost to ANSI escape code truncation). The agent gathered data and processed it, but likely did not produce a structured verdict list comparable to the four frameworks.

**5. Ecosystem dependency as a constraint, not a feature.**
Even for a clinical trial task, ml-intern requires HF credentials at startup. It's not a general-purpose autonomous agent — it's an ML engineer agent with general-purpose tools. The HF-specific tools (hf_papers, hf_jobs, hf_inspect_dataset) were irrelevant to this task and not used. The useful tools (web_search, bash) are domain-agnostic.

#### Setup friction observations
- HF token required even for non-HF tasks (hard gate at startup)
- Inference Provider permission not obvious to users with read-only tokens
- Claude models not available via HF Router — must use DeepSeek, Qwen, Llama, etc.
- Local fallback (Ollama): memory-intensive models OOM; lightweight models lack tool support
- Net result: a team wanting to compare ml-intern to other frameworks would need HF credits and an inference-capable token before they can run a single evaluation

---

### What ml-intern Actually Did vs. The Four Frameworks

| Dimension | Four frameworks | ml-intern (DeepSeek V4 Flash) |
|-----------|----------------|-------------------------------|
| Step count | 4–19 per patient | 20 iterations (47 messages) for one patient |
| Retrieval | Pre-built API client (right parameters, first call) | 7 web searches + 8 failed curl calls before working API call |
| API discovery | Encoded in `api_client.py` (never re-derived) | Re-derived from scratch each run via web search |
| Data processing | Pre-built parsing in pipeline | Python heredocs written inline during run |
| Output format | Structured JSON (TrialMatch objects) | Free-form (assessment not captured due to log truncation) |
| Model | claude-sonnet-4-6 (all frameworks) | deepseek-ai/DeepSeek-V4-Flash (not comparable) |
| Hard filter (age/sex) | Yes — deterministic, before LLM | No — model decides what to filter |
| HF domain tools used | N/A | None (hf_papers, hf_jobs irrelevant to task) |

---

### Methodological Caveat: Tool vs. Implementation

The ml-intern run cannot fairly be compared to the four frameworks, for three reasons:

**1. Pre-built infrastructure advantage given to all other approaches.**
Every framework ran against `api_client.py` — a pre-built retrieval client with the correct ClinicalTrials.gov v2 endpoint, right parameters (`query.cond` + `query.term`), and pagination logic. This took two days to get right during Day 2. ml-intern received no such asset. It had to discover the API from scratch, costing 15 of its 20 iterations on infrastructure rather than the matching task itself.

A fairer comparison would either: (a) give ml-intern the API client as context in the prompt, (b) expose `api_client.py` as an MCP tool (ml-intern uses fastmcp — this is architecturally natural), or (c) increase max_iterations to 50+ so the agent can finish the actual reasoning task.

**2. Different model.**
The four frameworks all used `claude-sonnet-4-6`. ml-intern used `deepseek-ai/DeepSeek-V4-Flash` — the only available option on the HF Router. Any difference in reasoning quality or output format cannot be attributed to the framework.

**3. 20-iteration cap.**
ml-intern hit max iterations before producing a final assessment. OpenHands had no iteration limit. The frameworks had no iteration concept at all. The comparison is not on equal footing.

**What the run does tell us** (validly): a general-purpose agent given a domain-specific data retrieval task cold will spend its reasoning budget on infrastructure discovery, not on the task itself. This is a property of the cold-start condition, not specifically of ml-intern. OpenHands succeeded in 6 steps partly because it took a shortcut (string matching, no LLM per trial) and partly because its execution environment let `import requests` work immediately without any API discovery.

---

### Architectural Finding

ml-intern is the most autonomous application in this study, but its autonomy is domain-specific. It's an ML post-training engineer, not a general-purpose agent. Given the P001 clinical trial query:

- It would use its general tools (web_search, sandbox) to approach the problem
- It would not use its domain tools (hf_papers, hf_jobs) — those are irrelevant to the task
- The mismatch between its tool set and the task would be visible in its step trace

This is the central observation the original plan intended to capture: **what does a fully autonomous agent do when given a task outside its primary design domain?** The answer — based on architecture and tool set — is that it falls back on general-purpose tools (web search, code execution) and produces a less structured but potentially broader result than a purpose-built pipeline.

---

## OpenHands

### Run Details

**Container:** `ghcr.io/all-hands-ai/openhands:0.40`, running at `localhost:3000`
**Patients:** All 5, via GUI (headless runs blocked — see below)
**Trajectories:** `outputs/04_agents/openhands/trajectory-p1.json` through `trajectory-p5.json`

P001 details: Start 2026-06-03 04:16:49 UTC → first API call at 04:23:04 UTC (~6 minutes total). 28 events, 6 actual agent actions.

### 5-Patient Results

| Patient | Events | ELIGIBLE | UNCERTAIN | INELIGIBLE |
|---------|--------|----------|-----------|------------|
| P001 — HER2+ BC, NYC | 28 | 1 | 1 | ~13 |
| P002 — TNBC, LA | 45 | 7 | 13 | 0 |
| P003 — HR+/HER2-, Chicago | 57 | 4 | 10 | 5 |
| P004 — Melanoma+brain, Seattle | 69 | 0 | 9 | 9 |
| P005 — HER2+ metastatic, Boston | 81 | 2 | 3 | 15 |
| **Total** | | **14** | **36** | **~42** |

Event count grew monotonically (28→45→57→69→81), suggesting OpenHands accumulated context across patients rather than restarting clean — or later profiles with more complex clinical pictures required more reasoning steps.

Notable: P004 correctly returned 0 ELIGIBLE. NCT04511013 (the BRAF V600E + brain mets trial designed for exactly this patient's profile) was listed INELIGIBLE — but for the wrong reason. OpenHands stated the trial "excludes brain mets," which is factually incorrect (the trial specifically enrolls patients with brain metastases). The real exclusion was prior systemic therapy for metastatic disease. Same trial, wrong reasoning, correct verdict by accident. P002 returned 7 ELIGIBLE — the highest count of any patient — which is consistent with TNBC's limited approved-treatment landscape creating more trial enrollment opportunities.

### Step Trace (P001)

```
[16] call_tool_mcp: fetch ClinicalTrials.gov API
     → BLOCKED: robots.txt disallows autonomous fetching by MCP client

[18] run_ipython: import requests; GET clinicaltrials.gov/api/v2/studies
     → Status 200. 20 studies returned. Saved to /tmp/clinical_trials_response.json

[20] run_ipython: load JSON, print patient profile, iterate over 20 studies
     → First-pass listing of all 20 NCT IDs with titles

[22] run_ipython: filter to HER2+ studies, detailed eligibility assessment
     → Found 15 studies specifically mentioning HER2-positive criteria
     → Rule-based Python logic (string matching) to classify each trial

[24] think: "Most studies are for advanced/metastatic disease, Stage II patient
     ineligible. 1-2 trials appear to accept early-stage disease."

[26] finish: task_completed=true
     final_thought: "75% of trials require advanced/metastatic disease — unsuitable for
     Stage II patient. NCT07214532 most promising."
```

### Verdicts Produced (from event 23 output)

| NCT ID | Title | Verdict | Key reason |
|--------|-------|---------|------------|
| NCT07214532 | Signatera-Guided CDK4/6 Inhibitor Therapy | **ELIGIBLE** | Accepts early-stage; age/gender met |
| NCT07060807 | Patritumab Deruxtecan (MK-1022-016) | LIKELY INELIGIBLE | Requires metastatic/advanced disease |
| NCT05870579 | [177Lu]Lu-NeoB + Ribociclib + Fulvestrant | LIKELY INELIGIBLE | Requires advanced disease; HER2- required |
| NCT02945579 | Eliminating Surgery/RT trial | LIKELY INELIGIBLE | May require exceptional response to neoadjuvant |
| 11 others | — | LIKELY INELIGIBLE | Require metastatic/advanced or HER2-negative |

20 trials fetched. 15 specifically mentioning HER2+. 1 ELIGIBLE, ~14 LIKELY INELIGIBLE.

### Key Behavioral Observations

**1. MCP fetch blocked by robots.txt — immediate recovery.**
OpenHands first tried to use the MCP `fetch` tool to call the ClinicalTrials.gov API. The response was blocked: "The sites robots.txt specifies that autonomous fetching of this page is not allowed." OpenHands recovered in the very next step by writing Python `requests` code that hit the same URL and received 200 OK. One blocked attempt → immediate pivot. This is robust behavior.

**2. Python string matching, not LLM reasoning per trial.**
OpenHands did NOT call the Anthropic API for individual trial assessment. It used Python string matching (`if 'her2-positive' in criteria_text`) and structural checks (age/gender fields from the API response) to classify trials. The task prompt asked it to "use the Anthropic API for eligibility assessment" — it ignored this and used rule-based Python instead. This produced faster, cheaper output but less nuanced reasoning than our four frameworks.

**3. 6 steps — most efficient approach observed.**
- LangGraph: 4 named nodes × 5 patients = 206 LLM calls
- smolagents: ~19 steps × 5 patients
- ml-intern: 20 iterations for 1 patient (hit max)
- OpenHands: 6 steps for the equivalent of 1 patient (20 trials)

The efficiency comes from batching all analysis into Python code rather than making one LLM call per trial.

**4. Correct clinical reasoning on disease stage — without being prompted.**
OpenHands correctly identified that "75% of trials require advanced/metastatic disease, making them unsuitable for a Stage II patient." This is the right clinical conclusion and the agent reached it through Python text analysis, not a specialized prompt rule. The four frameworks reached the same conclusion via UNCERTAIN/INELIGIBLE verdicts; OpenHands stated it explicitly in narrative.

**5. 1 ELIGIBLE (P001) — agreement on clinical conclusion, not verdict label.**
NCT07214532 (Signatera-Guided CDK4/6 Inhibitor Therapy) was marked ELIGIBLE by OpenHands. LangGraph and smolagents returned UNCERTAIN for this trial — they agreed it was a potential match but flagged missing data. The agreement is on the clinical conclusion, not the verdict label.

**6. P004 correctly returned 0 ELIGIBLE — but wrong reasoning.**
NCT04511013 (BRAF V600E + brain mets Phase 2, Seattle) was listed INELIGIBLE. OpenHands stated the trial "excludes brain mets." This is factually wrong — the trial specifically enrolls patients with brain metastases. The real exclusion is prior systemic therapy for metastatic disease. Correct verdict, incorrect explanation. Unlike ml-intern, which hallucinated the trial accepted prior-treated patients and returned ELIGIBLE, OpenHands landed on the right verdict. But neither system identified the actual exclusion criterion correctly.

**6. Built nothing — ran everything inline.**
Despite the task framing ("build a Python script"), OpenHands ran all analysis inline in Jupyter cells. It did not create a reusable script, install packages, or produce a file. The output is ephemeral. This is appropriate for a one-off analysis but different from what the task asked for.

### Architecture Note

OpenHands v0.40 uses the **CodeAct paradigm**: the agent generates and executes code in a real shell + Jupyter environment. Unlike smolagents (sandboxed Python with restricted imports) or ml-intern (tool-calling with predefined tools), OpenHands can:
- Install packages (`pip install`)
- Write to the filesystem
- Call any external API
- Run multi-step programs

In this run, it used none of those capabilities — the task was simple enough to solve with standard library requests and string matching. The full power of CodeAct would show on tasks requiring multi-file programs, package installation, or iterative debugging.

---

### What OpenHands Is

OpenHands (formerly OpenDevin) v0.40 uses the **CodeAct paradigm**: the agent generates and executes code as its primary action mechanism. Unlike smolagents which generates Python that calls tool functions, OpenHands generates code that runs in a real shell environment — with access to the filesystem, pip install, curl, and the full Python ecosystem.

This is architecturally the most powerful approach: the agent can install any library, write multi-file programs, test its own code, and iterate. It is also the highest risk approach: bugs in generated code have real effects (files written, API calls made).

**Key architectural difference from ml-intern:**
- ml-intern: tool-calling agent (JSON function calls to predefined tools)
- OpenHands: code-generation agent (writes and executes arbitrary code in a shell)
- smolagents: code-generation agent (writes Python that calls predefined tool functions, sandboxed)

OpenHands' approach is closer to smolagents than to ml-intern in paradigm, but without the sandbox restrictions. It can `pip install anthropic`, write `agent.py`, run it, check the output, and iterate — all autonomously.

---

---

### OpenHands Headless — All 5 Patients Attempt (Blocked)

**Date:** June 2026
**Script:** `run_openhands_all.py`
**Outcome:** All 5 patients failed. Zero successful headless runs.

#### What was attempted

After the P001 GUI run, a headless script was written to run all 5 patients automatically using the same Docker image. The approach:

```bash
docker run --rm ghcr.io/all-hands-ai/openhands:0.40 \
  python -m openhands.core.main \
  --config-file /app/config.toml \
  -t "<patient prompt>" -i 50
```

(`python -m openhands.core.main` is headless mode in v0.40 — there is no `--headless` flag.)

#### Root cause: temperature+top_p incompatibility

OpenHands v0.40 hardcodes both `temperature` and `top_p` as non-None defaults in `LLMConfig`. Every LLM call sends both parameters to the Anthropic API. Claude 4.x models (all current Anthropic models as of June 2026) reject this:

```
litellm.BadRequestError: AnthropicException —
{"type":"invalid_request_error",
 "message": "`temperature` and `top_p` cannot both be specified for this model.
 Please use only one."}
```

All 5 patients hit this error within seconds of the agent starting (`AgentState.RUNNING → AgentState.ERROR`).

Workarounds attempted and why they failed:

| Workaround | Why it failed |
|---|---|
| `LITELLM_DROP_PARAMS=true` env var | Only drops unrecognized params; temperature+top_p are both valid params — LiteLLM sends both |
| `config.toml [llm] top_p = null` | TOML has no null type; empty value is a parse error |
| Switch to `claude-3-5-sonnet-20241022` (Claude 3.x accepts both) | Model deprecated: `not_found_error: model: claude-3-5-sonnet-20241022` |
| Switch to other Claude 3.x models | All claude-3-x-haiku/opus/sonnet-20241022 and earlier variants return not_found_error as of June 2026 |

The constraint cannot be patched without modifying the OpenHands source or using a proxy that strips `top_p` before forwarding to Anthropic.

#### Why the GUI run worked

The P001 GUI run succeeded because the browser UI controls LLM parameters independently. When a user configures the model via the settings panel, top_p is left unset (None) unless explicitly entered. The browser path does not hit the same `LLMConfig` defaults that the headless `main.py` path uses.

#### Script error detection note

The first headless run of all 5 patients appeared to succeed (printed "done" for each). OpenHands exits 0 even when the agent transitions to `AgentState.ERROR`. A second check was added to the script:

```python
content = log_path.read_text()
if "AgentState.ERROR" in content and "AgentState.FINISHED" not in content:
    return False  # agent error, not a real completion
```

With this check, all 5 patients correctly report FAILED.

#### Conclusion

OpenHands v0.40 headless mode is incompatible with the Anthropic API as of June 2026. All 5 patients were run via the GUI as a workaround — trajectories `outputs/04_agents/openhands/trajectory-p1.json` through `trajectory-p5.json`. The GUI path does not hit the same `LLMConfig` defaults and therefore does not trigger the temperature+top_p conflict.

The correct fix for future work: upgrade to OpenHands v0.41+ (which resolves this constraint per the OpenHands changelog), or patch `openhands/core/config/llm_config.py` to set `top_p = None` by default.

---

## ml-intern Follow-up Run — Claude Sonnet 4.6 + Options 1+2

**Date:** June 2026 (Day 5+)
**Model:** `anthropic/claude-sonnet-4-6` (patched — see below)
**Max iterations:** 50
**Log:** `outputs/ml_intern/run_claude_sonnet.log`

### What changed from the first run

**Option 1 — API context in prompt:** Added the ClinicalTrials.gov v2 endpoint, correct parameters, and "do not use web search" instruction directly to the prompt.

**Option 2 — Max iterations 50:** Increased from 20 to 50.

**Claude patch:** Two file edits to `agent/core/llm_params.py` and `agent/main.py` to bypass HF Router for `anthropic/` model IDs and route directly to the Anthropic API using `ANTHROPIC_API_KEY`. The patch was forced by HF inference credit depletion (402 error on the first options-1+2 attempt with DeepSeek V4 Flash), which made the Claude patch necessary rather than optional.

### Run outcome

**Completed: 45 messages, 0 web searches, 5/5 plan steps.** The prompt context hint worked exactly as expected — the agent went straight to the API on step 1 with no discovery overhead.

**Step trace:**
```
▸ plan_tool     [plan: 5 steps]
▸ bash          curl ClinicalTrials.gov page 1 (correct params, first call)
▸ bash          curl pages 2–4 (pagination)
▸ bash          ...pages 5–10
▸ plan_tool     [step 1 done]
▸ bash          python3 haversine filter (NYC coords, 100-mile radius → 68 trials)
▸ plan_tool     [steps 2-3 done]
▸ bash          python3 eligibility assessment (full criteria text per trial)
▸ bash          python3 [continued assessment]
▸ plan_tool     [step 4 done]
▸ [rich narrative output — see log]
▸ plan_tool     [5/5 done]
--- Agent turn_complete (history_size=45) ---
```

### Output summary

| Category | Count | Key examples |
|---|---|---|
| ✅ Likely Eligible (act now) | 2 | NCT07211178 (ctDNA MRD observational, NJ), NCT05232916 (GLSI-100 vaccine Phase 3, Columbia) |
| 🟡 Potentially Eligible | ~13 | Neoadjuvant trials, metastatic-at-progression bookmarks, each with specific caveats |
| ❌ Not Eligible | 7 | HER2-negative-only, Stage I-only, prior anti-HER2 exclusion |

**Retrieval:** 500 trials fetched (10 pages × 50) vs 50 in the four frameworks. Geographic filtering used haversine distance per site — every trial result includes distance to nearest NYC-area location. This is not a feature any of the four frameworks implemented.

**NCT05232916 (GLSI-100 vaccine, Columbia, 0.1 miles) marked Likely Eligible** — consistent with Elicit (Day 1 #1 pick) and LangGraph/smolagents. Three independent approaches converge on the same trial.

### Comparison to four frameworks and OpenHands

| Dimension | LangGraph | Claude Direct | OpenHands | ml-intern (this run) |
|---|---|---|---|---|
| Trials retrieved | 50 | 50 | 20 | **500** |
| Geographic filter | Age/sex hard filter | Age/sex hard filter | String matching | **Haversine distance per site** |
| Output format | Structured JSON | Structured JSON | Narrative prose | **Rich narrative with distances, priority tiers, caveats** |
| ELIGIBLE/act-now verdicts | 1 | 5 | 1 | **2 act-now + 13 conditional** |
| Distance to sites | Not computed | Not computed | Not computed | **Every trial** |
| "Absence of info" rule applied | Yes (prompt) | Yes (prompt) | Not explicitly | **Yes (explicitly stated in methodology)** |
| Reproducibility | High | High | Low | Low |
| Structured output | Yes | Yes | No | No |

### The finding this run adds

**Given the same model, same task, and domain context in the prompt, ml-intern produced the most comprehensive and clinically useful output of any approach in this study.** It fetched 10× more trials, computed geographic distances the other frameworks didn't, and produced priority tiers with clinical reasoning per trial.

The tradeoff is reproducibility: the approach (how many pages to fetch, how to filter, how to format the output) is determined by the model during the run. A different run may produce different code and different structure. The four frameworks guarantee reproducible structure; ml-intern guarantees a capable agent will do its best.

**The prior finding (cold-start burns iterations on infrastructure) is now confirmed as implementation-dependent, not tool-dependent.** With domain context provided, ml-intern used 0 iterations on discovery and all iterations on the actual task. The "reasoning budget consumed by infrastructure" finding was a property of the cold-start condition, not ml-intern.

### The Claude patch — what changed and why

**Why it was needed:** ml-intern's `_resolve_llm_params()` in `agent/core/llm_params.py` routes every non-local model through `https://router.huggingface.co/v1` using the HF token as the API key. Even if you specify `anthropic/claude-sonnet-4-6`, it becomes `openai/anthropic/claude-sonnet-4-6` sent to HF Router — which doesn't serve Claude. The HF token gate in `agent/main.py` also blocks startup if no HF token is present and the model isn't local.

**What was changed:**

`agent/core/llm_params.py` — added 8 lines before the HF Router path:
```python
# Anthropic models — bypass HF Router, call Anthropic API directly.
# LiteLLM routes "anthropic/..." and "claude-..." to Anthropic when
# ANTHROPIC_API_KEY is set, without needing an HF token.
if normalized_model.startswith(("anthropic/", "claude-")):
    return {
        "model": normalized_model,
        "api_key": os.environ.get("ANTHROPIC_API_KEY"),
    }
```

`agent/main.py` — relaxed HF token gate in both `main()` and `headless_main()`:
```python
# Before:
if not hf_token and (not is_local_model_id(config.model_name) or not local_mode):
# After:
_is_anthropic_model = config.model_name.startswith(("anthropic/", "claude-"))
if not hf_token and not _is_anthropic_model and (not is_local_model_id(config.model_name) or not local_mode):
```

Total: ~15 lines changed across 2 files. The patch is minimal and targeted — it unlocks the LiteLLM layer (which already supports Anthropic natively) without changing any other ml-intern behavior. Not a supported configuration; specific to this comparison experiment.

---

*OpenHands trajectories: `outputs/04_agents/openhands/trajectory-p1.json` through `trajectory-p5.json`*
*ml-intern DeepSeek run log: `outputs/04_agents/ml_intern/run_deepseek.log`*
*ml-intern Claude Sonnet run log: `outputs/04_agents/ml_intern/run_claude_sonnet.log`*
