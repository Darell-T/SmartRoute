"""Layer-2 golden-query eval harness for the conversational transit agent.

Runs the real Anthropic model against replayed tool fixtures and asserts on
the resulting `TurnTrace` (tool call inputs + final text). Manual/on-demand
only -- spends real tokens, never runs in CI (see plan doc section 7,
Layer 2; Layer 1 is `backend/tests/test_agent_*.py`).

Modules:
    assertions.py       pure assertion-evaluation engine, no model/network
    fixture_router.py   eval-only fuzzy fixture resolution (see its docstring)
    run_agent_evals.py  CLI runner + `--validate` static check
    golden_queries.yaml the query bank (~35 queries across 6 tiers)
    fixtures/           per-query handcrafted tool-result fixtures
"""
