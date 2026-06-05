"""
Shared configuration for all framework implementations.
Change settings here; all agents pick them up automatically.
"""

# Model used for all LLM calls across all frameworks.
# Must be identical for a fair comparison — model differences confound results.
MODEL = "claude-sonnet-4-6"

# Token limits
MAX_TOKENS = 1024       # per LLM call in per-trial frameworks (LangGraph, PydanticAI)
MAX_TOKENS_BATCH = 4096 # for batch calls (Claude Direct)

# Fetching
SEARCH_RADIUS_MILES = 100
MAX_TRIALS_FETCHED = 50   # trials per API page (pageSize)
MAX_PAGES = 10            # pages to fetch per patient (500 trials total)
BATCH_SIZE = 10  # trials per LLM call in Claude Direct

# Cost estimate for rubric scoring ($/M tokens, Sonnet-class)
COST_PER_M_TOKENS = 3.0
