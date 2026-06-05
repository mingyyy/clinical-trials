#!/bin/bash
# Run all four framework implementations in parallel.
# Each framework writes its results to outputs/<framework>/P00X.json.
# Logs go to outputs/<framework>/run.log.
#
# Usage: bash run_all_parallel.sh
#        tail -f outputs/langgraph/run.log   # to watch a specific framework

set -e
cd "$(dirname "$0")"

mkdir -p outputs/langgraph outputs/pydantic_ai outputs/smolagents outputs/claude_direct

echo "Starting all four frameworks in parallel..."
echo ""

.venv/bin/python implementations/langgraph/agent.py > outputs/langgraph/run.log 2>&1 &
PID_LG=$!
echo "LangGraph    PID $PID_LG  →  outputs/langgraph/run.log"

.venv_pydantic/bin/python implementations/pydantic_ai/agent.py > outputs/pydantic_ai/run.log 2>&1 &
PID_PA=$!
echo "PydanticAI   PID $PID_PA  →  outputs/pydantic_ai/run.log"

.venv_smolagents/bin/python implementations/smolagents/agent.py > outputs/smolagents/run.log 2>&1 &
PID_SM=$!
echo "smolagents   PID $PID_SM  →  outputs/smolagents/run.log"

.venv/bin/python implementations/claude_direct/agent.py > outputs/claude_direct/run.log 2>&1 &
PID_CD=$!
echo "Claude Direct  PID $PID_CD  →  outputs/claude_direct/run.log"

echo ""
echo "Waiting for all frameworks to complete..."
echo ""

STATUS=0
wait $PID_LG;  RC=$?; [ $RC -eq 0 ] && echo "LangGraph:    done"  || { echo "LangGraph:    FAILED (exit $RC)"; STATUS=1; }
wait $PID_PA;  RC=$?; [ $RC -eq 0 ] && echo "PydanticAI:   done"  || { echo "PydanticAI:   FAILED (exit $RC)"; STATUS=1; }
wait $PID_SM;  RC=$?; [ $RC -eq 0 ] && echo "smolagents:   done"  || { echo "smolagents:   FAILED (exit $RC)"; STATUS=1; }
wait $PID_CD;  RC=$?; [ $RC -eq 0 ] && echo "Claude Direct: done" || { echo "Claude Direct: FAILED (exit $RC)"; STATUS=1; }

echo ""
if [ $STATUS -eq 0 ]; then
    echo "All runs complete. Run scoring with:"
    echo "  .venv/bin/python pipeline/scoring.py --all"
else
    echo "One or more runs failed. Check log files in outputs/*/run.log"
fi

exit $STATUS
