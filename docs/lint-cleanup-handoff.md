# Lint cleanup handoff

Use this reference for the current lint inventory in the
`damn-lines-integration` worktree. Execute remaining batches with
`docs/lint-cleanup-plan.md`. Review a finished batch with
`docs/lint-cleanup-review-spec.md`.

The trusted application checkpoint is `427fbc8`. The quality-policy update
after that reset changes tooling and documentation only. It does not change
application behavior. Regenerate the reports below before editing a batch.

## Current result

Measured on 2026-08-27 after the policy update, then confirmed by a second
fresh `py scripts/check_quality.py` run on 2026-08-28.

| Tool | Findings | Files | Notes |
|---|---:|---:|---|
| Ruff | 1,047 | 173 | 266 production, 781 tests, 40 rules |
| Ruff C901 | 66 | 50 | McCabe ceiling 10 |
| Ruff PLR0912 | 33 | 29 | Branch ceiling 12 |
| Ruff PLR0915 | 14 | 14 | Statement ceiling 50 |
| complexipy | 276 | 106 | Legacy functions above cognitive 10 |
| Oxlint | 1,162 | 192 | 719 in generator scripts |
| ESLint | 193 | 76 | 185 complexity, 8 max-depth |
| Quality baseline | 353 | | Combined Python and TypeScript CC debt |

The largest Ruff rule is `PT009` with 614 findings. That is test assertion
style debt, not production complexity. `TRY003` is ignored. It encouraged
exception boilerplate without improving passenger behavior or debuggability.

Ruff fell from the Batch 1 snapshot of 1,385 findings because C901 now uses
the default ceiling of 10 and `TRY003` is no longer enforced. No application
file changed for that drop.

Quality certification, two consecutive fresh runs, no `--skip-tests`:

| Run | Exit | New | Worsened | Stale | Remaining | Backend | Frontend |
|---|---:|---:|---:|---:|---:|---|---:|
| Policy baseline regeneration | 0 | 0 | 0 | 0 | 353 | 1,813 passed, 21 skipped, 444 subtests | 314 |
| Second pass 2026-08-28 | 0 | 0 | 0 | 0 | 353 | 1,813 passed, 21 skipped, 444 subtests | 314 |

Cognitive delta against `427fbc8`: 0 new or worsened. CRAP has no absolute
ceiling. Baseline entries may not worsen.

The route-intelligence batch, Batch 0, and Batch 1 are complete inside
`427fbc8`. Batch 2 is independently APPROVED in this worktree. Do not start
Batch 3 automatically.

## Structural policy

- Ruff C901 maximum 10
- complexipy cognitive maximum 10 for new functions, no worsening above 10
- Ruff PLR0912 maximum 12 branches
- Ruff PLR0915 maximum 50 statements
- CRAP is a coverage-aware delta for existing baseline debt
- Function length above 100 lines and file length above 500 lines are review
  signals, not split requirements
- Workers must not run `--update-baseline`

## Remaining batches

The old 20-batch plan is retired. Remaining work is eight subsystem batches
in `docs/lint-cleanup-plan.md`:

| Batch | Subsystem |
|---:|---|
| 2 | Realtime foundation and providers |
| 3 | Canonical trip planning |
| 4 | Agent capability tools |
| 5 | Agent orchestration and state |
| 6 | Backend test style remainder |
| 7 | Frontend contracts and I/O |
| 8 | Frontend presentation and map |
| 9 | Transit artifact generator, last |

## Reproduce the inventories

Run Ruff from the repository root:

```powershell
py -m ruff check --config pyproject.toml backend
py -m ruff check --config pyproject.toml --output-format json backend
```

Run quality and cognitive delta from the repository root:

```powershell
py scripts/check_quality.py --cognitive-only --quality-ref 427fbc8
py scripts/check_quality.py --quality-ref 427fbc8
```

Use these inert backend test values, not production credentials:

```powershell
$env:APP_KEY='dummy'
$env:ANTHROPIC_API_KEY='dummy'
$env:SMARTROUTE_ENV='test'
$env:AGENT_ALLOW_MEMORY_SESSIONS='1'
```

Run the frontend checks from `frontend/`:

```powershell
npm run lint
npm run lint:oxlint
node --import tsx node_modules/oxlint/bin/oxlint --format json .
npm run typecheck
```

The JSON reports are the source of truth for each file, line, rule, and
message. Do not add broad ignores or lower severity.

## Ruff rule inventory

Regenerated on 2026-08-27 with the policy update. The Fixable column is Ruff's
reported fix count. It does not authorize a broad unsafe rewrite.

| Rule | Total | Production | Tests | Files | Fixable |
|---|---:|---:|---:|---:|---:|
| `PT009` | 614 | 0 | 614 | 53 | 614 |
| `BLE001` | 72 | 72 | 0 | 34 | 0 |
| `C901` | 66 | 58 | 8 | 50 | 0 |
| `I001` | 56 | 5 | 51 | 54 | 56 |
| `PLR0912` | 33 | 30 | 3 | 29 | 0 |
| `PT027` | 26 | 0 | 26 | 9 | 26 |
| `ARG001` | 18 | 16 | 2 | 11 | 0 |
| `PERF401` | 18 | 9 | 9 | 14 | 0 |
| `PLR0915` | 14 | 9 | 5 | 14 | 0 |
| `ARG002` | 13 | 6 | 7 | 7 | 0 |
| `RUF022` | 11 | 0 | 11 | 11 | 11 |
| `RUF005` | 11 | 0 | 11 | 5 | 11 |
| `SIM117` | 11 | 0 | 11 | 4 | 5 |
| `TRY300` | 10 | 10 | 0 | 9 | 0 |
| `TID251` | 10 | 10 | 0 | 8 | 0 |
| `RUF001` | 8 | 8 | 0 | 7 | 0 |
| `TRY004` | 6 | 6 | 0 | 2 | 0 |
| `N803` | 5 | 5 | 0 | 3 | 0 |
| `TRY301` | 4 | 4 | 0 | 3 | 0 |
| `B904` | 4 | 4 | 0 | 3 | 0 |
| `UP042` | 4 | 4 | 0 | 3 | 4 |
| `RUF059` | 4 | 0 | 4 | 2 | 4 |
| `PT018` | 4 | 0 | 4 | 1 | 4 |
| `N818` | 3 | 3 | 0 | 3 | 0 |
| `RUF012` | 3 | 0 | 3 | 2 | 0 |
| `UP017` | 3 | 0 | 3 | 2 | 3 |
| `SIM102` | 2 | 2 | 0 | 2 | 0 |
| `UP047` | 2 | 2 | 0 | 1 | 2 |
| `ARG005` | 1 | 1 | 0 | 1 | 0 |
| `PERF403` | 1 | 1 | 0 | 1 | 0 |
| `UP046` | 1 | 1 | 0 | 1 | 1 |
| `UP035` | 1 | 0 | 1 | 1 | 1 |
| `RUF100` | 1 | 0 | 1 | 1 | 1 |
| `UP041` | 1 | 0 | 1 | 1 | 1 |
| `C408` | 1 | 0 | 1 | 1 | 1 |
| `UP037` | 1 | 0 | 1 | 1 | 1 |
| `S105` | 1 | 0 | 1 | 1 | 0 |
| `C420` | 1 | 0 | 1 | 1 | 1 |
| `PT011` | 1 | 0 | 1 | 1 | 0 |
| `B010` | 1 | 0 | 1 | 1 | 1 |

## Ruff repair guidance

Follow the refactoring style in `docs/lint-cleanup-plan.md`. Do not extract a
helper only to silence C901. Do not add tests only to lower CRAP.

### Test assertion backlog

Convert `PT009` and `PT027` in Batch 6 after production batches are stable.
Preserve argument order for membership assertions. Use `pytest.raises` with an
exact `match` only when the message is part of the contract.

The next test files by `PT009` count are:

| File | Findings |
|---|---:|
| `backend/tests/conversation/test_conversation_presentation_race.py` | 23 |
| `backend/tests/conversation/conversation_unoffered_tool_support.py` | 22 |
| `backend/tests/conversation/test_conversation_no_good_nonfatal_followup.py` | 21 |
| `backend/tests/test_agent_evidence_binding_reliability.py` | 21 |
| `backend/tests/test_crowd_evidence.py` | 21 |
| `backend/tests/test_turn_resolution.py` | 21 |
| `backend/tests/conversation/conversation_cancellation_support.py` | 20 |
| `backend/tests/conversation/test_conversation_what_if_lifecycle.py` | 20 |

### Mechanical production rules

Apply `I001`, `UP017`, `UP035`, `UP037`, `UP041`, `UP042`, `RUF022`, and the
`FURB` fixes in small package batches. Run the owning package tests. Do not
apply unsafe fixes across the whole backend.

### Exceptions and error ownership

Do not re-enable `TRY003`. Repair `TRY004`, `TRY300`, `TRY301`, `B904`, and
`BLE001` together at each owner boundary.

- Preserve causes with `raise ... from exc`.
- Catch only errors that the boundary can recover from or translate.
- Keep the reviewed fail-open telemetry and injected parser catches broad until
  their provider protocols expose a narrower shared failure type.
- Do not add an exception wrapper only to satisfy Ruff.

The largest remaining production files by Ruff count are:

| File | Findings |
|---|---:|
| `backend/app/routers/live_feed/socket.py` | 16 |
| `backend/app/services/mta/alerts.py` | 9 |
| `backend/app/services/agent/tools/__init__.py` | 8 |
| `backend/app/main.py` | 7 |
| `backend/app/observability.py` | 7 |
| `backend/app/routers/trips.py` | 7 |
| `backend/app/services/agent/tools/transit/evidence.py` | 7 |
| `backend/app/services/trips/crowds/event_provider.py` | 7 |

### Provider and security rules

`TID251` has 10 remaining findings. Production provider traffic must use
`backend/app/services/agent/tools/provider_http.py`. Do not add another
client wrapper. Release validation keeps line `TID251` noqas because it needs
sync HTTP and SSE. `provider_http` only fetches JSON.

Treat `S105` in
`backend/tests/conversation/conversation_external_content_fixtures.py` as a
fixture review. Rename the sentinel if it resembles a credential. Never place
a real credential in the fixture.

## Oxlint rule inventory

Regenerated on 2026-08-28. Totals match the 2026-08-27 policy-update count.

| Rule | Findings | Repair |
|---|---:|---|
| `eslint(complexity)` | 372 | Split statement-level branches only when it improves reading. Do not lower the ceiling. |
| `anti-slop(no-shape-in-symbol-names)` | 242 | Rename generic structural placeholders. Keep official GTFS vocabulary exact. |
| `anti-slop(require-safety-comment-for-type-assertion)` | 140 | Prefer parsing or inference. Use a `SAFETY:` comment only for a checked invariant that TypeScript cannot express. |
| `anti-slop(no-runtime-typeof)` | 123 | Parse external data once at the I/O boundary and branch on the parsed domain contract. |
| `anti-slop(no-unsafe-dictionary-type)` | 123 | Replace unknown dictionaries with owner or schema-derived contracts. |
| `anti-slop(no-known-value-widening)` | 53 | Preserve inference, use `satisfies`, or return the owner contract. |
| `eslint(no-unused-vars)` | 41 | Delete dead values. Check framework and callback signatures before renaming parameters. |
| `anti-slop(no-unknown-parameters)` | 30 | Keep untrusted input at the I/O boundary and parse it before calling domain code. |
| `anti-slop(no-conditional-empty-object-spread)` | 12 | Create the typed object, then assign an optional property in an explicit branch. |
| `unicorn(no-new-array)` | 10 | Use an array literal or `Array.from` with an explicit length. |
| `anti-slop(no-chained-type-assertions)` | 6 | Parse once from the original value instead of asserting through an intermediate type. |
| `unicorn(prefer-string-starts-ends-with)` | 5 | Replace anchored regular expressions with `startsWith` or `endsWith`. |
| `unicorn(no-useless-fallback-in-spread)` | 3 | Remove the fallback when the parsed contract already guarantees an iterable value. |
| `eslint(no-useless-escape)` | 2 | Remove the escape and rerun the owning parser tests. |

The current hotspots are:

| File | Findings |
|---|---:|
| `frontend/scripts/regenerate-canonical-from-gtfs.ts` | 67 |
| `frontend/scripts/regenerate-canonical-from-gtfs.test.ts` | 43 |
| `frontend/scripts/build/snap-off-revenue-to-shape.ts` | 43 |
| `frontend/components/map/subway-network.ts` | 38 |
| `frontend/scripts/build/visual-network/repairs/route-continuity-repair-stage.ts` | 30 |
| `frontend/scripts/build/station-anchors/index.ts` | 22 |
| `frontend/components/smart-route/map/smart-route-map.tsx` | 21 |
| `frontend/scripts/build/snap-off-revenue-to-shape.test.ts` | 21 |
| `frontend/scripts/build/snap-dangling-same-color.ts` | 19 |
| `frontend/scripts/build/physical-bundle-materialization.ts` | 17 |

The transit-artifact generator remains Batch 9. Do not start it until Batches
2 through 8 are approved.

## Completion checklist

For every batch:

1. Record the fixed point and owned files before editing.
2. Apply the smallest behavior-preserving repair.
3. Run focused tests for the owned behavior.
4. Run scoped Ruff or frontend linters and the complexipy delta against the
   fixed point.
5. Inspect the diff for disabled rules, metric-only helpers, dead code, and
   unrelated formatting.
6. Update this document with fresh JSON counts.

Cleanup is complete only when the commands in
`docs/lint-cleanup-plan.md` exit 0. Global Ruff, ESLint, and Oxlint still
fail on the backlog. Do not start Batch 3 automatically.

## Batch 2 worker result (2026-08-28)

Owned production paths from `docs/lint-cleanup-plan.md` Batch 2. Fixed point
`427fbc8`. `quality/baseline.json` was not updated. Do not start Batch 3
automatically.

First independent review REJECTED named fail-open tuples. Repair restored
`except Exception` with `# noqa: BLE001` and a one-line policy reason on
fail-open, log-and-continue, and telemetry-noop boundaries. Inner
`httpx.HTTPError` retry catches stay named. Readiness still names
`RedisError`, `TimeoutError`, `OSError`, and `RuntimeError`.

Repair also restored the 8.0s stops-for-route `get()` timeout on the shared
BusTime client, restored the undated scout web user string, collapsed the
pass-through `_best_at_transfer` loop into `_best_on_seed`, inlined
`_append_matching_stops` into `lookup`, inlined `_await_live_feed_inputs`
into `_drive_live_feed`, and named `_emit_same_tick_bus` outcomes
`disconnect`, `continue`, and `snapshot`.

Owned-path Ruff is zero. Cognitive delta vs `427fbc8`: 0 new or worsened
(1762 functions analyzed, 271 above 10). Quality certification after repair:
frontend 314 passed; backend 1820 passed, 21 skipped, 444 subtests; 0 new,
0 worsened, 9 stale baseline entries left for the reviewer
(`--update-baseline` is not a worker command).

Isolated owned diff vs `427fbc8`: 38 files, 1699 insertions, 1200 deletions
(33 production files plus focused tests). Net production growth is under 500
lines. Churn is above 2500. Extra production functions are 49 (review
trigger). New fail-open tests cover telemetry SDK faults, enrich-route
`OSError`, live-feed `DecodeError` HTTP and socket paths, warm-loop
`DecodeError`, and stops-for-route timeout 8.0.

Named `httpx.AsyncClient` constructors that remain are encoded with
`# noqa: TID251` until Batch 4 extends `provider_http` (protobuf feeds, 511
retry, BusTime lifecycle, alerts). `realtime.py` keeps `__all__` as the
re-export seam.

Focused command (474 passed, 115 subtests):

```powershell
$env:APP_KEY='dummy'; $env:ANTHROPIC_API_KEY='dummy'; $env:SMARTROUTE_ENV='test'; $env:AGENT_ALLOW_MEMORY_SESSIONS='1'
py -m pytest tests/test_readiness.py tests/test_runtime_safeguards.py tests/test_admission.py tests/test_agent_chat_admission.py tests/test_agent_chat_session_lease.py tests/test_agent_chat_stream_cleanup.py tests/test_cache_atomic.py tests/test_directions.py tests/test_live_feed_api.py tests/test_live_feed_ownership.py tests/test_live_feed_snapshot_trip_context.py tests/test_ny511.py tests/test_incident_scout_transport.py tests/test_incident_scout_normalization.py tests/test_incident_official_sources.py tests/test_incident_official_normalization.py tests/test_incident_job_router.py tests/test_incident_index.py tests/test_incident_batches.py tests/test_incident_monitor.py tests/test_incident_lifecycle.py tests/test_incident_index_batch_lookup.py tests/test_incident_index_and_background_job.py tests/test_incident_refresh_runner.py tests/test_incident_context_matching.py tests/test_background_incident_scout.py tests/test_mta_feed_service_alerts.py tests/test_mta_feed_bus_stops.py tests/test_lookup_arrivals.py tests/test_scheduled_arrivals.py tests/test_stop_patterns.py tests/test_build_stop_patterns.py tests/test_gtfs_static_index_delegation.py tests/test_gtfs_intermediate_stops.py tests/test_migrate_gtfs_transfers.py tests/test_transfer_semantics.py tests/test_observability.py tests/test_trips_enrichment.py -q
```

Quality:

```powershell
py -m ruff check --config pyproject.toml backend/app/main.py backend/app/observability.py backend/app/runtime.py backend/app/routers/agent_chat.py backend/app/routers/trips.py backend/app/routers/live_feed backend/app/services/admission.py backend/app/services/cache.py backend/app/services/directions.py backend/app/services/evidence.py backend/app/services/geography.py backend/app/services/live_feed backend/app/services/incidents backend/app/services/mta
py scripts/check_quality.py --cognitive-only --quality-ref 427fbc8
py scripts/check_quality.py --quality-ref 427fbc8
```

Independent review (third pass) APPROVED: scope, behavior, and gaming.
Do not start Batch 3 automatically. A reviewer may shrink the 9 stale
baseline ids. Remaining TID251 tokens wait for Batch 4 `provider_http`.

## Historical records through 427fbc8

The sections below are dated completion records. They used the previous C901
ceiling of 6, enforced `TRY003`, and a 1,004-entry quality baseline. Do not
use their Ruff totals as the current inventory.

### Batch 0

Owned files:

- `backend/tests/test_mta_feed_service_alerts.py`
- `docs/lint-cleanup-handoff.md`

`quality/baseline.json` was not edited. `_alert_semantics` was not rewritten.

Added a table-driven direct test for each existing `_alert_semantics` outcome:
planned, unplanned, and unknown source identifiers, planned local operation,
unplanned express-to-local, unplanned local without express, suspension,
severe delay, ordinary delay, planned service change, unknown change,
`service_operating` False, True, and `"unknown"`, and material versus
non-material results.

Focused command: `py -m pytest backend/tests/test_mta_feed_service_alerts.py -q --basetemp .pytest-quality-alerts`. Result: 17 passed, 13 subtests.

A later independent review on UTC 2026-08-27 rejected the first quality runs
because `backend/tests/test_dependency_advisory_evidence.py` still used
`expires_on: "2026-08-27"`. The supporting repair derives
`(datetime.now(UTC).date() + timedelta(days=1)).isoformat()`. UTC expiry
semantics stay `today >= expires_on`.

### Batch 1

Owned paths:

- `backend/scripts/release/**`
- `backend/scripts/build_scheduled_arrival_artifact.py`
- `backend/scripts/build_stop_patterns.py`
- `backend/scripts/live_checks/anthropic_agent.py`
- `backend/scripts/live_checks/crowd_search.py`
- `backend/scripts/live_checks/ticketmaster.py`
- `backend/scripts/phase2_quality_report.py`
- `backend/scripts/run_incident_refresh.py`

Starting owned inventory before edits: 98 findings, 15 files, 12 rules.
Owned Ruff after repair: zero. Focused command: 83 passed, 9 subtests.

Narrow line noqas that remain, each with a local reason:

- `TID251` on release `httpx.Client` and `httpx.AsyncClient` because
  `provider_http` only fetches JSON
- `S311` on seeded offline jitter
- `S603` on allowlisted `git` after `which()`
- `BLE001` on live-check and incident-refresh Exception boundaries that
  reduce to a sanitized error class or payload-free message
- `E402` on the two incident-refresh imports after `sys.path` insert

### Route-intelligence batch

Deleted live-shadow files:

- `backend/evaluation/route_intelligence/trip_shadow.py`
- `backend/evaluation/route_intelligence/shadow.py`
- `backend/scripts/review_shadow_decisions.py`
- `backend/tests/test_intelligence_shadow.py`
- `backend/tests/test_trip_shadow_integration.py`

Schema change (only authorized output-contract change):

- `aggregate_metrics` and fixture-validation reports now use `schema_version` 2
- records keep `evidence_kind=deterministic_fixture` and reject every other value
- `observation_id`, human classification, and `shadow_overhead` are removed
- `SourceContribution` now lives in `metrics.py` beside `SourceEffect`
- `SourceEffect` keeps only `changed_route`, `changed_explanation_only`, and
  `had_no_effect`. Deterministic evaluation never produced score or confidence
  effects, so those members are not part of schema v2.
- `PlanningMode.SHADOW` is removed. `"shadow"` is rejected like any other
  unknown planning mode.

Authorized lint exceptions (exactly three):

- `replay.py` patches `httpx.AsyncClient.request` with `# noqa: TID251`
- `replay.py` patches `httpx.Client.request` with `# noqa: TID251`
- `scripts/live_checks/advisor.py` catches `Exception` with `# noqa: BLE001`

Focused route-intelligence tests at completion: 122 passed, 62 subtests, two
orderings. Frontend unit tests: 314 passed. Generated transit artifacts were
unchanged.

Google Maps and Damn Lines attribution render through the PromptKit source
row. Normal sources use `SourceTrigger` with favicon display. Google
attribution remains visible as `Place data by Google Maps` after the
recommendation prose.
