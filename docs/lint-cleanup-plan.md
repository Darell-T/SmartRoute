# Complete the lint cleanup

Use this plan to finish the SmartRoute lint cleanup without repeating the
failed whole-backend rewrite. Work in large subsystem batches, but checkpoint
and review each subsystem before another worker starts.

The worker implements. A separate reviewer follows
`docs/lint-cleanup-review-spec.md`. The worker never approves its own work.

## Trusted starting point

The current worktree was reset to commit `427fbc8` on 2026-08-27. That commit
contains the approved Damn Lines integration, route-intelligence cleanup,
deterministic tooling cleanup, and test rationalization. The discarded
whole-backend rewrite is not part of this starting point.

The quality-policy update after that reset changes tooling and documentation
only. It does not change application behavior.

## Starting inventory (2026-08-27)

These counts were measured on 2026-08-27 with the checked-in policy update.
They are the starting snapshot, not the current backlog. After Batches 2
through 6, backend Ruff is 0. Regenerated complexity counts live in
`docs/lint-cleanup-handoff.md` and
`py scripts/report_backend_debt.py --max-existing 12`.

| Tool | Findings | Files | Notes |
|---|---:|---:|---|
| Ruff | 1,047 | 173 | 266 production, 781 tests |
| Ruff C901 | 66 | | McCabe ceiling 10 |
| Ruff PLR0912 | 33 | | Branch ceiling 12 |
| Ruff PLR0915 | 14 | | Statement ceiling 50 |
| complexipy | 276 | 106 | Legacy functions above cognitive 10 |
| Oxlint | 1,162 | 192 | 719 in the generator |
| ESLint | 193 | 76 | 185 complexity, 8 max-depth |
| Quality baseline | 353 | | Combined Python and TypeScript CC debt |

The largest Ruff rule is `PT009` with 614 findings. That is test assertion
style debt, not production complexity. `TRY003` is intentionally ignored. It
encouraged exception boilerplate without improving passenger behavior or
debuggability.

The fresh policy certification passed:

- frontend: 314 passed
- backend: 1,813 passed, 21 skipped, 444 subtests
- cyclomatic ratchet: 0 new, 0 worsened, 0 stale
- cognitive delta against `427fbc8`: 0 new or worsened

## Structural quality policy

Use four complementary signals. Do not substitute one for another.

1. Ruff C901 limits McCabe complexity to 10.
2. complexipy limits cognitive complexity to 10 for new functions and blocks
   regressions above 10 in existing functions.
3. Ruff PLR0912 limits branches to 12.
4. Ruff PLR0915 limits statements to 50.

CRAP remains in `scripts/check_quality.py` as a coverage-aware regression
signal for baseline functions. CRAP has no absolute ceiling. A hard CRAP value
of 6 would silently restore the discarded complexity ceiling of 6 and reward
unnecessary tests.

Function length above 100 lines and file length above 500 lines are review
signals. They do not automatically fail a batch. A cohesive 500 to 800 line
module is healthy when C901 stays at 10. A 1000 line file is a sign of leftover
complexity, not a split trigger by itself. Do not peel a 100-200 line fragment
that must always be read with its parent.

### Required refactoring style

Prefer:

- guard clauses that remove nesting
- early returns that clarify the successful path
- lookup tables for genuine data-to-result mappings
- named policy stages with distinct inputs and outputs
- deletion of dead branches and duplicate behavior
- existing helpers and standard-library behavior

Do not:

- extract a helper only to silence C901
- turn an explicit policy branch into a cryptic expression
- move branches into several one-use wrappers
- add a service, factory, strategy, interface, or configuration layer without
  an existing need
- split a file only because a length signal fired
- add tests only to lower CRAP

A one-use helper is acceptable when it owns a real policy, lifecycle,
side-effect boundary, parsing boundary, or recovery boundary. Its name must
describe that responsibility. Call count alone does not prove or disprove its
value.

## Fixed-point and baseline rules

Every batch starts from a commit. Record it before editing:

```powershell
$fixedPoint = git rev-parse HEAD
git status --short
```

Compare cognitive complexity to that exact point throughout the batch:

```powershell
py scripts/check_quality.py --cognitive-only --quality-ref $fixedPoint
py scripts/check_quality.py --quality-ref $fixedPoint
```

The worker must not run `--update-baseline`. The reviewer may shrink
`quality/baseline.json` only after the implementation is accepted, fresh tests
pass, and the review proves each stale entry represents a real improvement or
deletion. Never increase a baseline value.

## Batch execution loop

Use this loop for every batch:

1. Record the fixed point and clean status.
2. Regenerate scoped and global inventories.
3. Save the exact owned production and test files.
4. Read the owned behavior, direct callers, provider boundaries, and current
   tests.
5. Write a short invariant list before editing.
6. Repair one cohesive cluster.
7. Run its focused behavioral tests.
8. Run scoped Ruff, complexipy delta, and the relevant frontend checks.
9. Continue with the next cluster inside the same subsystem.
10. Run the full affected subsystem suite once the batch is green.
11. Run the full backend or frontend suite once at batch end.
12. Run `scripts/check_quality.py` against the fixed point.
13. Inspect the complete diff for behavior drift and metric gaming.
14. Send the isolated diff to the reviewer.
15. Stop after approval. Create the next checkpoint only with user approval.

Do not run the full repository suite after every mechanical edit. Focused tests
belong after a cohesive behavior cluster. The full suite belongs at the batch
boundary.

## Test policy

Preserve behavior with the smallest useful test set.

Add a characterization test before refactoring only when the behavior is not
already protected and a plausible regression would matter. Good targets are:

- provider fallback and redaction
- cancellation and resource cleanup
- cache and session ownership
- route identity and canonical itinerary contracts
- state-machine terminal outcomes
- parser rejection and boundary normalization
- generated artifact determinism

Do not add tests for:

- a private helper's exact internal call order when no contract depends on it
- impossible inputs rejected by an earlier typed or validated boundary
- Python syntax or a linter rule
- every branch of a trivial lookup table
- an outrageously rare state that cannot enter through a production boundary
- a refactor implementation detail that may change again

Do not delete a test merely to improve coverage or CRAP. Delete a test only
when it duplicates stronger evidence or tests a removed feature. Follow
`docs/test-suite-rationalization.md` and record the exact reason and count
change.

## Change-size guardrails

Large subsystem ownership does not authorize an uncontrolled rewrite. Stop the
current worker at the last green cohesive cluster when any review trigger is
met:

- total diff churn exceeds 2,500 changed lines
- production code grows by more than 500 net lines
- the patch adds 25 more production functions than it removes
- a new module boundary is required
- a public contract, retry policy, timeout, cache policy, or provider boundary
  must change
- the worker cannot explain a helper without referring to a lint rule
- focused behavior becomes harder to test than before

These are review triggers, not automatic rejection. The reviewer decides
whether the growth is justified or whether the remaining cluster should become
the next checkpoint.

## Non-negotiable product guardrails

- Preserve one backend-owned canonical itinerary.
- Preserve candidate identity, evidence association, and deterministic
  fallback selection.
- Keep capability choice model-led and state-scoped.
- Keep the destination decision with the user.
- Never invent live data or turn missing evidence into confirmed safety.
- Never expose prompts, reasoning, raw model output, provider payloads,
  credentials, or unnecessary coordinates.
- Keep queue information conversational and in PromptKit sources.
- Keep queue information off the map and route card.
- Preserve live-over-historical Damn Lines precedence.
- Never calculate an unprovided queue wait.
- Preserve provider timeout, retry, cache, and failure semantics.
- Preserve official MTA colors, route identity, station relationships, and
  deterministic generated artifacts.

## Remaining batch order

The old 20-batch plan was too granular. Batches 2 through 6 are complete.
Remaining backend work is five complexity batches, 6A through 6E, then
frontend Batches 7 through 9. Do not start Batch 7 until 6A through 6E are
independently approved and committed.

Measure debt with `scripts/report_backend_debt.py --max-existing 12`. Keep
the official ceilings in `pyproject.toml` at 10. The value 12 is a one-time
limit for existing functions. New functions must stay at 10 or lower.

| Batch | Subsystem | Why it is cohesive |
|---:|---|---|
| 2 | Realtime foundation and providers | Runtime, transport, MTA, and incident data lifecycle |
| 3 | Canonical trip planning | One itinerary and selection domain |
| 4 | Agent capability tools | One model-visible capability boundary |
| 5 | Agent orchestration and state | Model, session, loop, and turn lifecycle |
| 6 | Backend test style remainder | Test-only Ruff debt after production settles |
| 6A | Realtime, MTA, incidents, and routers | Live data, GTFS, alerts, and HTTP entry points |
| 6B | Canonical trips | One itinerary and incident-matching domain |
| 6C | Agent place, route, and shared tools | Place resolution and route preparation |
| 6D | Agent transit tools | Arrivals, evidence, and area conditions |
| 6E | Agent model, session, and turn | Prompt, stream, session, and turn lifecycle |
| 7 | Frontend contracts and I/O | Network, session, validation, and canonical adapters |
| 8 | Frontend presentation and map | Passenger rendering, interaction, and runtime map |
| 9 | Transit artifact generator | Large deterministic preprocessing subsystem, last |

## Batch 0: route intelligence and quality stabilization

Completed and included in `427fbc8`. Do not repeat it.

Preserve the schema version 2 route-intelligence boundary, removed live-shadow
behavior, deterministic replays, and existing quality-gate coverage fixes.

## Batch 1: deterministic and release tooling

Completed and included in `427fbc8`. Do not repeat it.

The owned release and deterministic tooling Ruff boundary is zero. Preserve
release evidence hashes, policy expiration, provider-fault seeds, CLI safety,
and the explicit opt-in requirement for live checks.

## Batch 2: realtime foundation and providers

Owned production paths:

- `backend/app/main.py`
- `backend/app/observability.py`
- `backend/app/runtime.py`
- `backend/app/routers/agent_chat.py`
- `backend/app/routers/trips.py`
- `backend/app/routers/live_feed/**`
- `backend/app/services/admission.py`
- `backend/app/services/cache.py`
- `backend/app/services/directions.py`
- `backend/app/services/evidence.py`
- `backend/app/services/geography.py`
- `backend/app/services/live_feed/**`
- `backend/app/services/incidents/**`
- `backend/app/services/mta/**`

Work in four clusters: runtime and admission, live-feed transport, incidents,
then MTA adapters and indexes. The cluster order follows the runtime data flow.

Preserve startup validation, readiness, backpressure, WebSocket close codes,
disconnect cleanup, cache fail-open behavior, provider provenance, incident
identity, refresh atomicity, GTFS semantics, BusTime behavior, and all timeout
and retry counts.

Run direct readiness, admission, live-feed, incident, NY511, MTA, GTFS,
scheduled-arrival, and stop-pattern tests after their cluster. Finish with all
owned tests, the full backend suite, scoped Ruff, complexipy delta, and fresh
quality.

## Batch 3: canonical trip planning

Owned production path:

- `backend/app/services/trips/**`

Work in four clusters: preparation, incident association, crowd evidence, then
itinerary, enrichment, scoring, and selection.

Preserve candidate identity, selection order, scoring semantics, merged walks,
transfers, waypoint and dwell provenance, multi-stop behavior, constraint
relaxation, and fallback selection. Do not change a response contract to make
lint easier.

Run tests matching `test_trip*`, `test_trips*`, `test_route*`,
`test_itinerary*`, `test_plan_trip*`, plus transfer and crowd tests. Finish
with the full backend suite and quality gates.

## Batch 4: agent capability tools

Owned production path:

- `backend/app/services/agent/tools/**`

Work in four clusters: shared tool boundary, places, transit, then route tools.

Preserve strict tool schemas, presented-entity references, evidence binding,
place pagination, route preparation ownership, and all Damn Lines invariants.
Do not add a parallel provider client or duplicate a canonical contract.

Run strict-schema, public-surface, discovery, Damn Lines, arrivals, transit
evidence, route preparation, route projection, and presentation tests. Finish
with capability reliability tests, the full backend suite, and quality gates.

## Batch 5: agent orchestration and state

Owned production paths:

- `backend/app/services/agent/model/**`
- root modules under `backend/app/services/agent/`
- `backend/app/services/agent/turn/**`

Exclude `tools/**`, which Batch 4 owns.

Work in four clusters: model request and stream boundaries, persistent stores
and sessions, candidate and reference state, then loop and turn lifecycle.

Preserve token budgets, overload behavior, cancellation, session leases,
transcript persistence, candidate lifecycle, one terminal outcome, ledger
ordering, continuation, tool-round limits, and passenger-output redaction.

Run model, stream, session, restore, lease, output, candidate, loop, turn,
continuation, and cleanup suites. Run order-sensitive files in both orders.
Finish with the full backend suite and quality gates.

## Batch 6: backend test style remainder

Own every remaining Ruff-reported file under `backend/tests/**`. Save the exact
manifest before editing. Do not edit production code in this batch.

Most debt is `PT009`. Convert assertions without changing operand order,
failure meaning, async sequencing, mock arguments, call counts, or fixture
identity. Keep protocol parameters that are required even when a local test
does not read them.

Do not create new test helper layers merely to remove repeated assertions. Do
not delete tests unless the rationalization guide proves exact duplicate
evidence. Run each changed test file, then the owned list in forward and reverse
order, then the full backend suite. Global Ruff must be zero at batch end.

Batch 6 is committed at `c058199`. That commit is the immutable fixed point
for Batches 6A through 6E.

## Backend complexity program

Record the fixed point before editing:

```powershell
$fixedPoint = git rev-parse HEAD
py scripts/report_backend_debt.py --self-test
py scripts/report_backend_debt.py --max-existing 12 --output .audit/backend-debt.json
```

Targets for 6A through 6E:

- Every existing production function has cyclomatic complexity of 12 or lower.
- Every existing production function has cognitive complexity of 12 or lower.
- Every new function stays at 10 or lower in both measurements.
- Functions that remain at 11 or 12 must not exceed the survivor count in
  `docs/lint-cleanup-handoff.md` at `c058199`. Record each survivor in the
  handoff.
- Resolve the CRAP scores above 30 listed in that handoff through useful
  tests, clearer code, or deletion.
- Keep branch-aware backend coverage at or above the handoff percentage.
- Do not change public REST, WebSocket, model-tool, itinerary, or event
  contracts.

A helper may be introduced only when it owns a named policy, parsing step,
aggregation, lifecycle, or side effect. Do not create a one-call helper only
to lower a score. Keep a function at 11 or 12 when that is clearer than
another layer.

After each of 6A through 6E, run the owned tests, scoped Ruff, the debt
report, complexipy against `c058199`, the full backend suite with branch
coverage, and `py scripts/check_quality.py --quality-ref c058199`. Leave
`quality/baseline.json` for the reviewer.

## Batch 6A: realtime, MTA, incidents, and routers

Own every production function under `backend/app` except `services/trips/**`
and `services/agent/**`. Regenerated above-12 and CRAP counts are in
`docs/lint-cleanup-handoff.md`.

Delete `GTFSStaticData.get_unique_routes_for_stops`. It has no production or
test caller.

Refactor `_build_subway_vehicle_positions` into named parsing, selection, and
diagnostic phases. Do not change vehicle identity, route filtering,
staleness, stop-only handling, colors, or debug output.

Simplify alert stop-name enrichment and GTFS query retry. Preserve failure
behavior, including stale-connection retry, statement timeout without retry,
and connection return.

Cover these cases through the cheapest public path before the structural
move:

- Vehicle route filtering, missing and zero coordinates, duplicate IDs,
  stale timestamps, stop-only vehicles, and diagnostics
- Alert stop-name lookup, directional child-stop fallback, duplicate names,
  missing GTFS, and lookup failure
- BusTime partial stop failures, expired arrivals, ordering, and empty-stop
  results
- Subway-stop endpoint readiness, cache reuse, coordinate omission, route
  colors, and GeoJSON output
- Database stale-connection retry, second failure, statement timeout without
  retry, and connection return
- Stable alert IDs and WebSocket change detection

Preserve startup validation, readiness, backpressure, WebSocket close codes,
cache fail-open behavior, provider provenance, incident identity, GTFS
semantics, BusTime behavior, and all timeout and retry counts.

## Batch 6B: canonical trips

Own `backend/app/services/trips/**`. At `c058199` that is 62 functions
above 12.

Prioritize `match_cached_incidents`, `_prefer`, and
`build_chained_itinerary`. Separate incident-to-stop matching from impact
classification. Separate official-source precedence from evidence merging.
Separate chained-segment construction from total calculation.

Preserve canonical itinerary arithmetic, selection order, dwell provenance,
transfers, merged walks, incident identity, route matching, crowd evidence,
constraints, and fallback behavior.

## Batch 6C: agent place, route, and shared tools

Own `backend/app/services/agent/tools/**` except `tools/transit/**`. At
`c058199` that is 40 functions above 12.

Remove the hidden `discovery_set_id` input from `place_reference.execute`.
The strict `get_place_details` schema cannot supply it, and no production
caller uses it. Keep presented-place lookup and active-set fallback as one
named internal policy.

Preserve strict schemas, session ownership, opaque identities, evidence
binding, route preparation ownership, candidate identity, and passenger
redaction.

## Batch 6D: agent transit tools

Own `backend/app/services/agent/tools/transit/**`. At `c058199` that is 35
functions above 12.

Prioritize `lookup_arrivals_bus.execute`, direction resolution, transit
evidence projection, accessibility binding, snapshot construction, and area
condition checks. Separate stop resolution, provider access, filtering,
grouping, and response construction when those are independent
responsibilities.

Preserve accepted-itinerary binding, route and direction matching, live
versus scheduled provenance, outage handling, provider timeouts, and
graceful unavailable results.

## Batch 6E: agent model, session, state, and turn lifecycle

Own remaining modules under `backend/app/services/agent/` except
`tools/**`. At `c058199` that is 53 functions above 12.

Prioritize `presented_entity_registry.resolve`, `build_turn_context`,
candidate persistence, public tool selection, model streaming, session
restoration, and turn completion. Split selector policies and independent
context summaries while keeping the public interface small.

Preserve token budgets, cancellation, overload handling, leases, session
persistence, tool ordering, continuation behavior, one terminal outcome, and
rider-output sanitization.

After Batch 6E, rerun `scripts/report_backend_debt.py --max-existing 12`
across all backend production code. No function may remain above 12. Then
run frontend lint, Oxlint, typecheck, unit tests, release checks, and
`scripts/check_quality.py` once against the final backend commit. Do not
start Batch 7 until those checks pass.

## Batch 7: frontend contracts and I/O

Owned paths:

- `frontend/lib/**`
- `frontend/app/**`
- frontend release tests
- PromptKit source and input primitives used by those boundaries

Before editing React or TypeScript, read the applicable repository skills.

Preserve stream framing, reconnect and abort behavior, session restoration,
route identity, proxy redaction, source validation, and canonical backend
contracts. Validate unknown data once at the I/O boundary. Do not calculate
trip facts in frontend adapters.

Run scoped ESLint and Oxlint, typecheck, unit tests, and release evidence tests.
Finish with all frontend gates and fresh quality.

## Batch 8: frontend presentation and map

Owned paths:

- `frontend/components/smart-route/**`
- `frontend/components/map/**`

Exclude generator code under `frontend/scripts/build/**`.

Preserve one recommended route in chat, canonical labels, collapsed transit
legs, explicit stop expansion, focus and keyboard behavior, reduced motion,
mobile navigation, route selection, camera behavior, official colors, shared
corridors, and station-to-line relationships.

Keep Damn Lines prose and `SourceTrigger` favicon sources after recommendation
ordering. Do not put queue data on the map or route card.

Run scoped linters, typecheck, all 314 unit tests, map checks, and transit
artifact verification. Generated artifacts must remain unchanged.

## Batch 9: transit artifact generator

Owned paths:

- `frontend/scripts/build/**`
- generator entry points and their direct tests
- station-anchor and artifact release evidence builders

Start this batch only after Batches 2 through 8 are approved. This is the
largest subsystem and remains last.

Work in four internal checkpoints: canonical inputs, topology and snapping,
bundle and authored geometry, then visual-network and station materialization.
Use the change-size guardrails after each checkpoint.

Preserve exact GTFS meanings, route and stop identity, lane ordering, shared
corridor separation, snap gates, authored repair preconditions, deterministic
ordering, official colors, artifact fingerprints, and validation failures.
Never loosen a geographic threshold to hide a bad transformation.

Run the matching generator tests after each checkpoint. Finish with scripts
typecheck, global Oxlint and ESLint, full regeneration, artifact verification,
map checks, and generated-diff inspection.

## Final completion gate

Cleanup is complete only when these commands exit 0:

```powershell
py -m ruff check --config pyproject.toml backend
py scripts/report_backend_debt.py --max-existing 12
py scripts/check_quality.py --cognitive-only --quality-ref HEAD
py scripts/check_quality.py --quality-ref HEAD

Set-Location frontend
npm run lint
npm run lint:oxlint
npm run typecheck
npm run typecheck:scripts
npm run test:unit
npm run verify:transit-artifacts
```

Also require:

- zero Ruff, Oxlint, and ESLint findings
- zero new or worsened cyclomatic, cognitive, or CRAP debt
- zero stale baseline entries
- no unexplained new suppression
- no baseline increase
- no test or skip reduction without approved feature deletion or duplicate
  evidence
- no unexpected generated artifact change
- no frontend ownership of canonical itinerary arithmetic
- a final handoff with exact counts, commands, and authorized exceptions
