# Agent eval harness (Layer 2)

Golden-query evals for the conversational transit agent
(`app/services/agent/`). Runs the **real** Anthropic model with every tool
call **replayed from handcrafted fixtures** (no live Google/Ticketmaster/MTA
calls), and asserts on the resulting `TurnTrace` -- tool call inputs and the
final assistant text.

This is Layer 2 of the plan doc's eval strategy (section 7). Layer 1
(`backend/tests/test_agent_*.py`, including `test_agent_evals.py`'s
assertion-engine and fake-model tests) runs in CI with no network and no
API key. **This harness is manual, on-demand, and spends real tokens -- it
never runs in CI.**

## Running it

From `backend/`, with `ANTHROPIC_API_KEY` set:

```sh
python evals/run_agent_evals.py                                  # every query
python evals/run_agent_evals.py --tier T2                        # one tier
python evals/run_agent_evals.py --only t2_no_bus_cart,t5_pizza_first
python evals/run_agent_evals.py --model claude-sonnet-5 --verbose
```

Prints one `PASS`/`FAIL` line per query, then a summary table of pass/fail
counts per tier and a rough token-spend estimate from the real `usage` in
each turn's `done` event. `--verbose` dumps the trace and final text for
every failing query. Exit code is `1` if any query failed, `0` otherwise.

### `--validate` -- no model, no network, no API key

```sh
python evals/run_agent_evals.py --validate
```

Static sanity check only: every query in `golden_queries.yaml` parses,
every tool/op named in an assertion is real, every fixture file an
explicit `fixtures:` mapping points at actually exists, and **every**
fixture file on disk for a query (not just explicitly-named ones, since the
single-file fallback can serve any file sitting in a tool's directory) is
valid JSON with the right envelope and per-tool shape. Run this after
editing `golden_queries.yaml` or adding a fixture, before spending tokens
on a real run. It's part of the CI-adjacent Layer-1 checklist even though
the harness itself is not (see `backend/tests/test_agent_evals.py`'s
`ValidateModeTests`, which run this exact function against the real query
bank in CI).

## Expected cost per full run

Rough estimate at Sonnet list pricing with the system+tools prompt cache
warm (see `loop.py`'s `cache_control: ephemeral` on the system block): on
the order of **$0.50-$1.50** for all 35 queries (most queries are 1-2
model rounds; a few multi-turn/multi-stop queries run 3-4). The runner
prints the actual cost estimate (from real `usage`, using the same
$/token constants as `app/services/agent/budget.py`) at the end of every
run -- trust that number over this estimate. If you hit
`budget_exceeded` mid-run, bump `AGENT_DAILY_SPEND_LIMIT_USD` for the
session (the harness runs through the same budget guardrails as
production, deliberately -- it exercises the real gate).

## The fuzzy fixture problem, and how it's solved

The production fixture-replay hook
(`app/services/agent/tools/__init__.py`, `AGENT_TOOL_FIXTURES=<dir>`) keys
a fixture by an exact sha256 hash of the canonical JSON tool input. That's
the right behavior for production replay (fail loud on drift), but useless
here: the real model chooses its own tool inputs freely ("Costco" vs.
"Costco Wholesale", exact ISO seconds, etc.), so pre-naming fixtures by
hash would make queries flaky based on model phrasing we never meant to
test.

`evals/fixture_router.py` solves this **without touching
`tools/__init__.py`** (that hook is untouched -- see the module's
docstring for the full design). It monkeypatches `TOOL_REGISTRY`'s
executors for the lifetime of one query, resolving each call in order:

1. **hash-exact** -- a file named `<canonical_hash(input)>.json` (same
   naming the production hook uses; the "graduation path" once you've
   recorded real fixtures).
2. **explicit call-order mapping** -- the query's `fixtures: {tool:
   [file_a.json, file_b.json]}` in `golden_queries.yaml`, one entry
   consumed per call to that tool. This is how a query with two different
   calls to the same tool (two `plan_trip` legs, an origin-change
   follow-up) disambiguates them.
3. **single-file fallback** -- if a tool's directory under
   `fixtures/<query_id>/` has exactly one file, use it for every call,
   regardless of the exact input. Covers the common one-call-per-tool case
   with zero YAML bookkeeping.

A miss at all three is a loud `ToolResult(ok=False, ...)`, matching the
production hook's philosophy. Set `AGENT_TOOL_FIXTURES_FUZZY=0` to disable
steps 2/3 and require hash-exact fixture names only -- useful once a
query's fixtures have been renamed to their real recorded hashes and you
want to confirm the model still produces byte-identical inputs (a
stricter regression check than the default).

Every resolved call feeds `evals/assertions.py`'s `derived` assertion
(the only one that reads a tool's *output*, for the multi-stop
"leg 2 departs after leg 1 arrives + dwell" check) via a `call_log`.

## Fixture layout

```
evals/fixtures/<query_id>/<tool_name>/<any_name>.json
```

Each file is `{"ok": bool, "data": <tool's model-facing digest>, "summary":
str, "error": str|null}` -- the exact shape `ToolResult` serializes to (see
`tools/__init__.py`'s `_write_fixture`). `data` shapes are pinned to what
each tool module actually returns, not guessed:

- `plan_trip`: `{"candidates": [{card_id, lines, eta_minutes, transfers,
  departs_iso, arrives_iso, walk_minutes, alert_headlines, reason}, ...]}`
  (see `tools/plan_trip.py`'s digest loop).
  **Fixture-authoring convention**: list the recommended candidate FIRST.
  Production's real digest carries no explicit "recommended" flag, but the
  `derived` multi-stop assertion needs a candidate to read `arrives_iso`
  from, and this is the convention it (and you, writing new multi-stop
  fixtures) rely on.
- `event_lookup`: `{"events": [{name, venue_name, venue_key, start_iso,
  estimated_end_iso, end_estimate_basis}, ...], "note"?}`.
- `poi_search`: `{"results": [{name, address, lat, lng, open_now}, ...]}`.
- `venue_crowd_window`: `{venue, stations, lines, surge_start_iso,
  surge_end_iso, alternates, note, is_heuristic}`.
- `transit_snapshot`: not currently exercised by any golden query.

`--validate` enforces these shapes on every fixture file present, whether
or not a query's `fixtures:` mapping names it explicitly.

`fixture_router.build_index()` writes an `index.json` manifest alongside
each query's fixtures on every run (tool -> sorted filenames) -- purely a
debugging aid, not read back by resolution.

## Recording new fixtures against live APIs (later)

The production hook already supports this; the eval harness doesn't need
its own recording path. With real API keys available:

```sh
AGENT_TOOL_FIXTURES=/tmp/recorded AGENT_TOOL_FIXTURES_RECORD=1 \
  python -c "..."   # exercise a tool call through the real TOOL_REGISTRY
```

writes `{tool}/{hash}.json` files shaped exactly like the ones in this
directory. Copy the ones you want, rename them descriptively, and either
drop them straight into a query's fixture directory (single-file fallback
picks them up automatically) or wire them into that query's `fixtures:`
mapping.

## Adding a query

1. Pick an id (`t<tier>_<short_description>`) and add an entry to
   `golden_queries.yaml`'s `queries:` list -- `tier`, `description`,
   `context.now` (a literal RFC3339 "now"; write every relative-time
   assertion in the query by hand against this, don't compute it live),
   optionally `context.origin_gps`, `messages` (usually one, occasionally
   two for a follow-up), and `assertions`.
2. Create `fixtures/<query_id>/<tool>/*.json` for every tool call the
   query is expected to trigger. One file per tool is enough unless the
   same tool is called more than once with different inputs (two
   `plan_trip` legs) -- in that case add a `fixtures:` mapping to the
   query entry listing the files in call order.
3. `python evals/run_agent_evals.py --validate` -- must pass before
   spending any tokens.
4. `python evals/run_agent_evals.py --only <query_id> --verbose` against
   the real model to check it actually passes, then leave it in the bank.

## Assertion vocabulary

Documented in full at the top of `golden_queries.yaml` (kept in sync with
the implementation in `assertions.py`): `tool_called`, `tool_not_called`,
`no_tools`, `tool_input` (ops: `contains`, `equals`, `absent`,
`matches_regex`, `iso_within`), `call_order`, `call_count`, `final_text`,
and the special-cased `derived: leg2_departs_after_leg1_arrival_plus` for
the multi-stop tier.
