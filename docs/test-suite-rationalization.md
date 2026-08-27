# Continue the test suite rationalization

Use this guide when reviewing or changing SmartRoute tests. The purpose is to
keep one clear owner for each behavior without losing coverage of production
branches, public contracts, failure recovery, or untrusted provider data.

## Current result

The rationalization started from the Batch 0 certification count of 1,994
backend tests, 21 skipped tests, and 619 subtests. The first deletion pass
removed 154 collected tests and added two focused branch tests (1,842 passed).
A second overlap pass then removed leftover conversation twins and same-branch
provider or surface copies. The fresh quality gate now reports 1,813 passed,
21 skipped, and 444 subtests, with 0 new, 0 worsened, and 0 stale baseline
entries.

The frontend remains at 314 tests. Its current suites are proportionate to the
rendering and stream contracts they own. No frontend test was removed in this
pass.

## Removal rule

Remove a test only when at least one condition is proven:

- another test exercises the same production branch and asserts the same
  passenger or public contract
- a longer transcript fully contains the shorter transcript's state changes
- Auto and Quick reach the same server-owned branch, and another surviving
  test already proves Quick uses the shared contract
- the test checks private file shape, deleted architecture, wrapper identity,
  or prompt wording that no public behavior requires
- several malformed inputs reach the same normalization or recovery branch,
  and one representative untrusted input remains

Keep a test when it is the only owner of a production branch, security
boundary, cancellation path, concurrency invariant, identity check, provider
normalization branch, canonical itinerary fact, or passenger-visible fallback.

Do not use test deletion to satisfy Ruff, ESLint, Oxlint, coverage, CC, or CRAP.
After each deletion cluster, run the complete affected subsystem. After each
subsystem, run a fresh quality gate. If the ratchet worsens, restore the
smallest representative behavior test or add a focused test at the true owner.
Do not change `quality/baseline.json` to accept lost coverage.

## Removed tests

| Area | Before | After | Decision |
|---|---:|---:|---|
| Batch 1 tooling | 83 | 71 | Removed duplicate CLI, wrapper, malformed-input, and deployment-string cases. |
| Conversation matrix | 233 | 134 | Removed audit-only matrices, repeated Auto and Quick scenarios, and one shorter long-session transcript. |
| Conversation leftover twins | 134 | 126 | Removed leftover Auto copies of surviving Quick contracts and one contained refusal subset. |
| Agent prompt | 46 | 26 | Kept high-risk grounding, route, discovery, queue, response-mode, and context contracts. Removed wording catalog checks. |
| Web research policy | 27 | 15 | Kept evidence gates, provider-content redaction, streamed results, and pause behavior. Removed first-round surface copies owned by `test_public_tool_surface.py`. |
| Stop patterns | 32 | 28 | Kept synthetic contracts, real route resolution, and explicit transfer identity. Removed real-artifact duplicates of synthetic API tests. |
| Agent tool layers | 61 | 54 | Kept provider parsing, cache, fixture replay, facts, and accessibility behavior. Removed duplicate registry and prompt guards. |
| Session lease | 19 | 17 | Kept exclusivity, ownership, disconnect, cancellation, save failure, and cleanup ordering. Removed a no-op input and one repeated cleanup permutation. |

The conversation deletion includes these six isolated historical audit files:

- `backend/tests/conversation/test_conversation_pairwise_invariants.py`
- `backend/tests/conversation/conversation_pairwise_fixtures.py`
- `backend/tests/conversation/conversation_pairwise_support.py`
- `backend/tests/conversation/test_conversation_failure_matrix.py`
- `backend/tests/conversation/conversation_failure_matrix_fixtures.py`
- `backend/tests/conversation/conversation_failure_matrix_support.py`

Those files identified themselves as audit-only. Their fixtures and support
modules had no external importers. The surviving conversation suite still
drives the production loop, registries, stores, evidence transitions,
presenters, and SSE path.

The leftover conversation cut dropped eight tests total. Most were Auto twins
whose Quick owner already proved the shared contract. It also removed
`test_rejects_answer_while_place_presentation_pending` because
`test_rejects_all_outcomes_while_places_must_be_presented` already covers
answer and unavailable.

The second overlap pass also removed:

- `test_valid_empty_snapshot_does_not_refetch` in `test_damn_lines.py`, same
  fresh-cache skip as the surviving partial-snapshot test
- `test_non_transient_http_errors_are_not_retried` and unset-environment
  fixture rejection in `test_ny511.py`, same HTTP and environment branches as
  the surviving 401 and production-fixture tests
- `test_lm01_intent_and_tool_surface.py` and `test_agent_loop_tool_surface.py`,
  whose initial-surface Auto/Quick loops duplicated
  `test_public_tool_surface.py` and `test_strict_tool_schema.py`
- first-round web-search-absent copies in `test_web_research_policy.py`
- extra malformed coverage statuses, extra
  non-retryable HTTP statuses, extra non-finite 511NY config values, extra
  refresh exit-zero statuses, extra area-condition passthrough statuses,
  `test_partial_and_lock_held_return_their_bounded_result`, and
  `test_clarification_may_end_without_presentation`

## Coverage repairs

The first fresh quality run after deletion reported one new CRAP item and
three worsened items. Do not restore the deleted matrices. The missing branches
now have compact coverage at their actual owners:

- `backend/tests/test_transfer_semantics.py` covers accessible and unknown
  string values from provider accessibility fields
- `backend/tests/test_route_evidence_coverage.py` covers an empty candidate
  list returning `insufficient_coverage`
- `backend/tests/test_goal_aware_tool_round.py` covers a raised executor error
  becoming the bounded `tool failed` result
- `backend/tests/test_route_option_projection_grounding.py` covers the
  single-destination opaque identity fallback when candidate destinations are
  empty

The focused repair suite passes 40 tests and 11 subtests. The final fresh
quality run reports 0 new, 0 worsened, and 0 stale baseline entries. It also
reports 1,004 remaining baseline entries, 736 CC violations, 986 CRAP
violations, 1,813 passed backend tests, 21 skipped backend tests, 444 backend
subtests, and 314 passed frontend tests.

## Verification commands

Run the conversation suite after changing its scenarios or shared support:

```powershell
py -m pytest backend/tests/conversation -q --basetemp .pytest-conversation
```

The current result is 126 passed and 66 subtests.

Run the four branch owners after changing the coverage repairs:

```powershell
py -m pytest backend/tests/test_transfer_semantics.py backend/tests/test_route_evidence_coverage.py backend/tests/test_goal_aware_tool_round.py backend/tests/test_route_option_projection_grounding.py -q --basetemp .pytest-coverage-owners
```

Run Ruff with the repository configuration. Do not use Ruff defaults:

```powershell
py -m ruff check --config pyproject.toml backend
```

Run the final fresh gate without `--skip-tests`:

```powershell
py scripts/check_quality.py
```

The gate must report 0 new, 0 worsened, and 0 stale baseline entries. The
backend skip count must remain 21. The frontend count must remain 314 unless a
separate reviewed frontend rationalization changes it.

## What remains

Do not continue deleting tests by file size or test count. Live feed ownership,
check_transit, present_places, present_transit, cache_atomic, discover_places,
and remaining unique Quick budget or model tests still own distinct branches.

Review further files only with branch and contract evidence. Keep remaining
unique owners of security, cancellation, concurrency, identity, provider
normalization, canonical facts, and passenger fallbacks.

The remaining conversation Ruff backlog is not a reason to delete more tests.
Convert assertions and simplify helpers in the owning lint batch. Keep the
current 126 conversation scenarios unless a reviewer proves exact overlap with
a surviving owner.
