# Clinical Trial Matching — Framework Comparison

## Venvs
- `.venv/`            — LangGraph, Claude Direct, evaluate.py
- `.venv_pydantic/`   — PydanticAI
- `.venv_smolagents/` — smolagents

## Run all frameworks
bash run_all.sh

## Run one framework
.venv/bin/python implementations/langgraph/agent.py
.venv_pydantic/bin/python implementations/pydantic_ai/agent.py
.venv_smolagents/bin/python implementations/smolagents/agent.py
.venv/bin/python implementations/claude_direct/agent.py

## Score all outputs → comparison table
.venv/bin/python evaluate.py

## Outputs
outputs/<framework>/P00X.json    — raw run results
findings/<framework>_findings.md — scored analysis
findings/comparative_analysis.md — MAIN DELIVERABLE
