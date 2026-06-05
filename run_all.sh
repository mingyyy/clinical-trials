#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "=== LangGraph ==="
.venv/bin/python implementations/langgraph/agent.py

echo "=== PydanticAI ==="
.venv_pydantic/bin/python implementations/pydantic_ai/agent.py

echo "=== smolagents ==="
.venv_smolagents/bin/python implementations/smolagents/agent.py

echo "=== Claude Direct ==="
.venv/bin/python implementations/claude_direct/agent.py

echo "=== All runs complete. Run: .venv/bin/python evaluate.py ==="
