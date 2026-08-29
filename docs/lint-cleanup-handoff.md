# Lint cleanup handoff

Use this reference for the current lint inventory in the
`damn-lines-integration` worktree. Execute remaining batches with
`docs/lint-cleanup-plan.md`. Review a finished batch with
`docs/lint-cleanup-review-spec.md`.

The trusted application checkpoint is `427fbc8`. The quality-policy update
after that reset changes tooling and documentation only. It does not change
application behavior. Regenerate the reports below before editing a batch.

## Current result

Ruff rows were regenerated on 2026-08-28 after Batch 6 from
`py -m ruff check --config pyproject.toml backend` (exit 0, 0 findings).
Oxlint, ESLint, and complexipy rows remain the 2026-08-27 policy measurement.
Workers must not shrink stale baseline entries.

| Tool | Findings | Files | Notes |
|---|---:|---:|---|
| Ruff | 0 | 0 | Backend production and tests |
| Ruff C901 | 0 | 0 | McCabe ceiling 10 |
| Ruff PLR0912 | 0 | 0 | Branch ceiling 12 |
| Ruff PLR0915 | 0 | 0 | Statement ceiling 50 |
| complexipy | 276 | 106 | Legacy functions above cognitive 10 |
| Oxlint | 1,162 | 192 | 719 in generator scripts |
| ESLint | 193 | 76 | 185 complexity, 8 max-depth |
| Quality baseline | 329 | | Combined Python and TypeScript CC debt after reviewer-only shrink |

`TRY003` is ignored. It encouraged exception boilerplate without improving
passenger behavior or debuggability.

Ruff fell from the 2026-08-27 policy snapshot of 1,047 findings because
Batches 2 through 6 cleared owned production and then all remaining
`backend/tests` findings. The earlier drop from the Batch 1 snapshot of
1,385 was the C901 ceiling of 10 and the `TRY003` ignore.

Quality certification from fresh runs with no `--skip-tests`:

| Run | Exit | New | Worsened | Stale | Remaining | Backend | Frontend |
|---|---:|---:|---:|---:|---:|---|---:|
| Policy baseline regeneration | 0 | 0 | 0 | 0 | 353 | 1,813 passed, 21 skipped, 444 subtests | 314 |
| Second pass 2026-08-28 | 0 | 0 | 0 | 0 | 353 | 1,813 passed, 21 skipped, 444 subtests | 314 |
| Batch 3 reviewer final 2026-08-28 | 0 | 0 | 0 | 0 | 340 | 1,822 passed, 21 skipped, 444 subtests | 314 |
| Batch 4 worker 2026-08-28 | 1 | 0 | 0 | 6 | 334 | 1,822 passed, 21 skipped, 444 subtests | 314 |
| Batch 5 worker 2026-08-28 | 1 | 0 | 0 | 11 | 329 | 1,823 passed, 21 skipped, 444 subtests | 314 |
| Batch 6 worker 2026-08-28 | 1 | 0 | 0 | 11 | 329 | 1,823 passed, 21 skipped, 444 subtests | 314 |
| Batch 5 reviewer final 2026-08-28 | 0 | 0 | 0 | 0 | 329 | 1,823 passed, 21 skipped, 444 subtests | 314 |
| Batch 6 reviewer final 2026-08-28 | 0 | 0 | 0 | 0 | 329 | 1,823 passed, 21 skipped, 444 subtests | 314 |

Cognitive delta against `427fbc8`: 0 new or worsened. CRAP has no absolute
ceiling. Baseline entries may not worsen.

The route-intelligence batch, Batch 0, and Batch 1 are complete inside
`427fbc8`. Batch 2 is independently APPROVED at `368c00d`. Batch 3 production
and the handoff repair are Codex-approved. Batch 4 owned tools Ruff is zero.
Batch 5 is committed at `22f6f0d` and reviewer-approved. Batch 6 lands remaining
`backend/tests` Ruff cleanup and the reviewer-only baseline shrink. Do not start
Batch 7. Batches 6A through 6E own remaining backend complexity debt.

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

Regenerated on 2026-08-28 after Batch 3 from configured JSON. The Fixable
column is Ruff's reported fix count. It does not authorize a broad unsafe
rewrite.

| Rule | Total | Production | Tests | Files | Fixable |
|---|---:|---:|---:|---:|---:|
| `PT009` | 616 | 0 | 616 | 53 | 616 |
| `I001` | 52 | 1 | 51 | 50 | 52 |
| `C901` | 38 | 30 | 8 | 29 | 0 |
| `PT027` | 27 | 0 | 27 | 9 | 27 |
| `PLR0912` | 18 | 15 | 3 | 16 | 0 |
| `PERF401` | 14 | 5 | 9 | 10 | 0 |
| `BLE001` | 13 | 13 | 0 | 9 | 0 |
| `ARG001` | 13 | 11 | 2 | 7 | 0 |
| `SIM117` | 12 | 0 | 12 | 5 | 5 |
| `ARG002` | 11 | 4 | 7 | 6 | 0 |
| `RUF022` | 11 | 0 | 11 | 11 | 11 |
| `RUF005` | 11 | 0 | 11 | 5 | 11 |
| `PLR0915` | 8 | 3 | 5 | 8 | 0 |
| `RUF001` | 7 | 7 | 0 | 6 | 0 |
| `N818` | 4 | 2 | 2 | 3 | 0 |
| `TRY004` | 4 | 4 | 0 | 1 | 0 |
| `UP042` | 4 | 4 | 0 | 3 | 4 |
| `RUF059` | 4 | 0 | 4 | 2 | 4 |
| `PT018` | 4 | 0 | 4 | 1 | 4 |
| `B904` | 3 | 3 | 0 | 2 | 0 |
| `RUF012` | 3 | 0 | 3 | 2 | 0 |
| `UP017` | 3 | 0 | 3 | 2 | 3 |
| `SIM102` | 2 | 2 | 0 | 2 | 0 |
| `ARG005` | 1 | 1 | 0 | 1 | 0 |
| `PERF403` | 1 | 1 | 0 | 1 | 0 |
| `TRY300` | 1 | 1 | 0 | 1 | 0 |
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

Batch 6 converted remaining `PT009` and `PT027` under `backend/tests`.
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
| `backend/app/services/agent/tools/__init__.py` | 8 |
| `backend/app/services/agent/tools/transit/evidence.py` | 7 |
| `backend/app/services/agent/model/stream.py` | 6 |
| `backend/app/services/agent/tools/transit/lookup_arrivals_subway.py` | 6 |
| `backend/app/services/agent/tools/location_resolution.py` | 5 |
| `backend/app/services/agent/session.py` | 4 |
| `backend/app/services/agent/tools/transit/evidence_binding.py` | 4 |
| `backend/app/services/agent/candidate_store.py` | 3 |

### Provider and security rules

Configured Ruff reports 0 `TID251` findings. Remaining named `httpx`
constructors keep line `TID251` noqas until Batch 4 extends
`provider_http` (protobuf feeds, 511 retry, BusTime lifecycle, alerts).
Production provider traffic must use
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
fail on the backlog. Do not start Batch 4 automatically.

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

## Batch 3 worker result (2026-08-28)

Owned production path from `docs/lint-cleanup-plan.md` Batch 3:
`backend/app/services/trips/**`. Fixed point `368c00d`.
The worker did not update `quality/baseline.json`. The Batch 3 reviewer later
performed the permitted update. Do not start Batch 4 automatically.

Owned-path Ruff before edits: 44 findings, 17 files (C901 12, BLE001 13,
PLR0912 5, ARG001 4, I001 1, PLR0915 2, ARG002 2, PERF401 2, TID251 1,
TRY300 1, RUF001 1). After repair: zero.

Clusters: preparation, incident association, crowd evidence, then itinerary,
enrichment, scoring, and selection. Scoring and selection modules had no
owned Ruff findings and were not edited.

`event_provider.fetch_json` is a lazy delegate to
`app.services.agent.tools.provider_http.fetch_json`. A top-level import
circular-imports through `agent.tools.__init__` and the venue crowd tables.
HTTP semantics stay JSON, timeout, never-raises, no retry. Fail-open
`except Exception` catches keep `# noqa: BLE001` with a one-line
`{source} faults {outcome}` reason.

Owned-path Ruff is zero. Cognitive delta vs `368c00d`: 0 new or worsened
(1791 functions analyzed, 267 above 10). Quality certification:
frontend 314 passed, backend 1822 passed, 21 skipped, 444 subtests,
0 new, and 0 worsened. The worker run exited 1 only because 13 stale baseline
entries remained. The reviewer removed those entries and lowered 19 remaining
measurements without increasing any entry. The final full gate exits 0 with
340 remaining entries and 0 stale entries.

Isolated owned diff vs `368c00d`: 17 files, 964 insertions, 529 deletions.
Net production +435 lines. Churn 1493. Extra production functions are 33
(review trigger). Independent scope, behavior, and gaming review judged
those as named pipeline, copy, parse, and recovery stages. HEAD
`prepare_single_leg` was radon CC 72.

Focused command, recorded 2026-08-28, exit 0, 243 passed, 58 subtests.
PowerShell does not expand pytest globs. The 27 files are listed explicitly.
`--basetemp` is unique and gitignored.

```powershell
$env:APP_KEY='ci-test-key'
$env:ANTHROPIC_API_KEY='ci-test-anthropic-key'
$env:SMARTROUTE_ENV='test'
$env:AGENT_ALLOW_MEMORY_SESSIONS='1'
$env:SMARTROUTE_RUN_LIVE_TESTS='0'
$env:RUN_LIVE_TESTS='0'
Set-Location backend
$files = @(
  'tests/test_crowd_evidence.py',
  'tests/test_crowd_hotspots.py',
  'tests/test_crowd_search.py',
  'tests/test_event_crowd_scoring.py',
  'tests/test_itinerary_canonical.py',
  'tests/test_itinerary_chain.py',
  'tests/test_plan_trip_input_recovery.py',
  'tests/test_plan_trip_prepare_cancellation.py',
  'tests/test_plan_trip_projection.py',
  'tests/test_route_constraint_relaxation.py',
  'tests/test_route_decision_evaluation.py',
  'tests/test_route_endpoint_resolution_policy.py',
  'tests/test_route_evidence_coverage.py',
  'tests/test_route_exclusion_constraints.py',
  'tests/test_route_identity_gate.py',
  'tests/test_route_itinerary_contract.py',
  'tests/test_route_option_assembly.py',
  'tests/test_route_option_assembly_integration.py',
  'tests/test_route_option_projection_grounding.py',
  'tests/test_route_tool_import_boundary.py',
  'tests/test_transfer_semantics.py',
  'tests/test_trip_admission.py',
  'tests/test_trip_candidate_reasons.py',
  'tests/test_trips_direct_plan.py',
  'tests/test_trips_enrichment.py',
  'tests/test_trips_incidents.py',
  'tests/test_trips_plan_deterministic.py'
)
py -m pytest @files -q --basetemp "$PWD/../.pytest-batch3-focused"
```

Quality:

```powershell
py -m ruff check --config pyproject.toml backend/app/services/trips
py scripts/check_quality.py --cognitive-only --quality-ref 368c00d
py scripts/check_quality.py --quality-ref 368c00d
```

Owned Ruff and cognitive-only exit 0. The worker's full
`check_quality.py --quality-ref 368c00d` run exited 1 only because 13 stale
baseline entries remained. The reviewer removed those entries, inspected the
change, and reran the full command. The final command exits 0 with 340
remaining entries and 0 new, worsened, or stale entries.

Independent production review APPROVED: scope, behavior, and gaming.
Codex rejected the first handoff on 2026-08-28 because the focused-test
command used unresolved globs and the current Ruff inventory was stale.
Codex rejected the second handoff because its fixed-point Ruff count was one
too high and it did not record the final baseline result. The final docs
repair corrects both records. The recorded 27-file command exits 0 with 243
passed and 58 subtests. The final quality command exits 0. Batch 3 is
Codex-approved.

## Batch 4

Owned production path:

- `backend/app/services/agent/tools/**`

Supporting edits:

- `docs/lint-cleanup-handoff.md`

Fixed point: `ae37295940b7f1218922ae153c50b0e32368228a`.
`quality/baseline.json` was not edited.

Owned-path Ruff after repair: zero. Cognitive delta vs `ae37295`: 0 new or
worsened (1864 functions analyzed, 259 above 10).

Clusters: shared tool boundary, places, transit, then route tools.

`ToolOutcome` is a `StrEnum`. Unused registry kwargs use `del arg`. Fail-open
`except Exception` catches keep `# noqa: BLE001` with a one-line
`{source} faults {outcome}` reason. Rider GPS origin uses
`location_resolution._origin_latlng`, not NYC-bounded `parse_coordinates`.

Isolated owned diff vs `ae37295`: 27 files, 1677 insertions, 837 deletions.
Net production +840 lines. Extra production functions are 73 including
async and nested defs. Those are named C901, cognitive, and radon stages.
File length above 500 lines is a review signal, not a split requirement.

Focused command, recorded 2026-08-28, exit 0, 282 passed, 90 subtests.
PowerShell does not expand pytest globs. The files are listed explicitly.
`--basetemp` is unique and gitignored.

```powershell
$env:APP_KEY='ci-test-app-key'
$env:ANTHROPIC_API_KEY='ci-test-anthropic'
$env:SMARTROUTE_ENV='test'
$env:AGENT_ALLOW_MEMORY_SESSIONS='1'
$env:PYTHONPATH='backend'
Set-Location backend
$files = @(
  'tests/test_strict_tool_schema.py',
  'tests/test_strict_tool_schema_new_tools.py',
  'tests/test_public_tool_surface.py',
  'tests/test_discover_places.py',
  'tests/test_place_discovery_pagination.py',
  'tests/test_discovery_references.py',
  'tests/test_damn_lines.py',
  'tests/test_lookup_arrivals.py',
  'tests/test_scheduled_arrivals.py',
  'tests/test_transit_evidence.py',
  'tests/test_check_transit.py',
  'tests/test_present_transit.py',
  'tests/test_present_route_correction.py',
  'tests/test_present_route_framing.py',
  'tests/test_present_route_reservation.py',
  'tests/test_plan_trip_projection.py',
  'tests/test_route_option_projection_grounding.py',
  'tests/test_route_tool_import_boundary.py',
  'tests/test_single_agent_route_tools.py',
  'tests/test_agent_capability_completion_reliability.py',
  'tests/test_agent_evidence_binding_reliability.py',
  'tests/test_agent_transit_direction_reliability.py',
  'tests/test_agent_route_decision_reliability.py',
  'tests/test_check_area_conditions.py',
  'tests/test_agent_loop_transit_grounding.py'
)
py -m pytest @files -q --basetemp "$PWD/../.pytest-batch4-end"
```

Full backend: 1822 passed, 21 skipped, 444 subtests. `git diff --check` on
the owned path is clean.

Quality:

```powershell
py -m ruff check --config pyproject.toml backend/app/services/agent/tools
py scripts/check_quality.py --cognitive-only --quality-ref ae37295940b7f1218922ae153c50b0e32368228a
py scripts/check_quality.py --quality-ref ae37295940b7f1218922ae153c50b0e32368228a
```

Owned Ruff and cognitive-only exit 0. The worker's full
`check_quality.py --quality-ref ae37295` run exits 1 only because 6 stale
baseline entries remain after 6 resolved measurements. New 0. Worsened 0.
Remaining 334. The worker did not run `--update-baseline`.

Independent production review APPROVED: scope, behavior, and gaming.
Do not start Batch 5 automatically. Batch 5 later started from `93473c3`.

pstack how pass, recorded 2026-08-28, after independent review. Four
explorers, one explainer, three critics. Named-stage extracts stayed in
existing files. File length above 500 lines remains a review signal, not
a split requirement. Production tools were not changed in this pass.

`ok` and `ToolOutcome` are two status axes. Discover and prepare goal
recording uses `ok` plus payload fields. Transit goal recording uses
`evidence_ready`. The turn ledger caches a result when `outcome` is not
`FAILED`. `complete_turn` writes a different `outcome` vocabulary into
`data`.

Rider GPS uses `location_resolution._origin_latlng`. That name is not in
`__all__`. Arrivals import it anyway. Model and tool coordinate strings
use `trips.location.parse_coordinates`, which applies `NYC_BOUNDS`.
`search_local_places._COORD_RE` parses `near` with no NYC bounds.
`geo._is_in_nyc` still drops out-of-city provider rows.

`_historical_pattern` is defined in both `discover_places.py` and
`present_places.py`. Both wrappers call `damn_lines.get_historical_pattern`
with `now=when` and return None on `TypeError` or `ValueError`. Discover
stamps model-facing `queue_evidence` dicts after persist. Present builds
passenger notes from a second lookup. `heads_up` is silent in discover and
live in present. Those policies cannot share one digest.

### Place gather shrink (after Batch 4)

Batch 5 stays frozen. `damn_lines.py` was not edited. Predicate: net lines
down, radon CC down, Ruff C901/PLR still 0, focused tests green, function
count must not rise.

Trail: `.audit/places-simplify.tsv`.

| File | Before | After |
|---|---:|---:|
| `discover_places.py` | 1000 | 839 |
| `search_local_places.py` | 432 | 354 |
| `damn_lines.py` | 514 | 514 (frozen) |
| Functions in the two gather files | 52 | 43 |

`_search` radon 16 to 9. `_verify` 19 to 13. `_persist` 17 to 10.
`_interleaved_sources` 11 to 9. `_matching_continuation_tokens` 12 to 6.
`search_local_places.execute` stays radon 29. Ruff C901/PLR on both files
is 0. Focused pytest: 172 passed, 34 subtests.

`DiscoveryRequest` is passed through `_search`, `_verify`, and `_persist`.
Empty-area coverage is extra labels into `_coverage`. Scores are stamped
while normalizing. Persist slices. Provider normalize reads execute payload
keys only. Verify walks `product(names, targets)` into named `pairs`,
`pending`, and `coverage_targets`. Presented identities come from
`presented_entity_registry.place_ids`. Matching continuation tokens reuse
`discovery_store.sanitized_continuation_tokens`.

A `queue_evidence.py` extract was tried and reverted. The digest has one
caller (`_persist`). Present cannot reuse it without changing `heads_up`,
clocks, or rider notes. 839 is 39 over 800. The remaining bulk is the
public schema plus queue evidence that must be read with persist. Do not
split that fragment.

### Batch 5: agent orchestration and state

Fixed point `93473c3`. Predicate: net lines down, function count must not
rise, no helper that exists only to silence C901. Exclude `tools/**`.
`quality/baseline.json` was not edited.

Trail: `.audit/batch5-simplify.tsv`.

Nine cluster files vs `93473c3`: 4185 to 4084 lines (net -101), 145 to 145
functions. Owned C901/PLR0912/PLR0915 is 0. Complete owned Ruff is 0.
Cognitive vs `93473c3`: 0 new or worsened (1856 functions, 256 above 10).

Cluster 1 is model request and stream. `output_projection.py` 259 to 208.
`prompt.py` 533 to 512. Context sections precompute rider-safe digests and
use two same-shape JSON tables, then `lines.extend` for present payloads.
`model/stream.py` dropped the `text_stream` fallback. Test fakes keep
`__aiter__` only.

Clusters 2-4 are stores, public surface, and turn lifecycle. Dead
`HARD_LIMIT` asserts after `BUDGET=0` are gone. Goal cycles use
`graphlib.TopologicalSorter`. `pause_turn` web evidence lives in
`_apply_server_web_progress`. Registry ordinal lookup walks newest
presentation first. Evidence capability state lives in
`_evidence_capability_error`. Presenter research and readiness stay two
returns. Discovery place-id remap lives in `_rewrite_source_place_id`.

Route-discovery reliability tests keep their assertions. Shared
`provider_search_result` in `agent_route_decision_test_support.py` is the
canonical `_provider_search` envelope (`data["results"]`, flattened
`lat`/`lng`). A discovery execute check fails if that envelope drifts:
empty `places` on a `places` key, missing stored coordinates without
`lat`/`lng`. It does not call `_provider_places`. Prove-it: `test_stage_a_excludes_absurd_current_location_route_before_model_choice`
failed with an empty place set on the stale `places` envelope, then passed
after the helper.

Provider stream recovery catches `anthropic.APIError` and related runtime
faults. Loop tests raise `anthropic.BadRequestError` and
`InternalServerError` through `httpx.Response`, not local `Exception`
subclasses. Nested continuation constraints raise `TypeError`. Atomic
candidate presentation fail-open includes `RuntimeError` so a broken
pipeline stays a bounded store error.

Approval uses `scripts/check_quality.py --quality-ref` as specified in
`docs/lint-cleanup-review-spec.md`. This batch did not add a second runner.

Recorded from the repository root with inert test credentials,
`SMARTROUTE_ENV=test`, `AGENT_ALLOW_MEMORY_SESSIONS=1`, and
`PYTHONPATH=backend`.

```powershell
$env:APP_KEY = 'dummy'
$env:ANTHROPIC_API_KEY = 'dummy'
$env:SMARTROUTE_ENV = 'test'
$env:AGENT_ALLOW_MEMORY_SESSIONS = '1'
$env:PYTHONPATH = 'backend'

python -m ruff check --config pyproject.toml backend/app/services/agent --exclude tools
python -m ruff check --config pyproject.toml --select C901,PLR0912,PLR0915 backend/app/services/agent --exclude tools
```

Both exit 0.

```powershell
$env:APP_KEY = 'dummy'
$env:ANTHROPIC_API_KEY = 'dummy'
$env:SMARTROUTE_ENV = 'test'
$env:AGENT_ALLOW_MEMORY_SESSIONS = '1'
$env:PYTHONPATH = 'backend'
python scripts/check_quality.py --cognitive-only --quality-ref 93473c3
```

Exit 0. Cognitive 0 new or worsened. Prints `approval_eligible: false`.

34-file manifest, forward then reverse, 329 passed, 58 subtests, both
exit 0:

```powershell
$env:APP_KEY = 'dummy'
$env:ANTHROPIC_API_KEY = 'dummy'
$env:SMARTROUTE_ENV = 'test'
$env:AGENT_ALLOW_MEMORY_SESSIONS = '1'
$env:PYTHONPATH = 'backend'
$files = @(
    'backend/tests/test_agent_model_request.py',
    'backend/tests/test_agent_model_stream.py',
    'backend/tests/test_agent_prompt.py',
    'backend/tests/test_model_output_projection.py',
    'backend/tests/test_agent_session.py',
    'backend/tests/test_agent_chat_session_lease.py',
    'backend/tests/test_agent_chat_session_restore.py',
    'backend/tests/test_agent_chat_stream_cleanup.py',
    'backend/tests/test_agent_loop.py',
    'backend/tests/test_agent_loop_output_integrity.py',
    'backend/tests/test_complete_turn.py',
    'backend/tests/test_turn_contract.py',
    'backend/tests/test_turn_terminal_contract.py',
    'backend/tests/test_pending_continuation.py',
    'backend/tests/test_session_pending_continuations.py',
    'backend/tests/test_public_tool_surface.py',
    'backend/tests/test_presented_entity_registry.py',
    'backend/tests/test_discovery_references.py',
    'backend/tests/test_web_research_policy.py',
    'backend/tests/test_active_discovery_presenter.py',
    'backend/tests/test_active_temporary_route_presenter.py',
    'backend/tests/test_agent_loop_round_cap_reliability.py',
    'backend/tests/test_model_led_goal_loop.py',
    'backend/tests/test_agent_context_projection.py',
    'backend/tests/test_turn_evidence.py',
    'backend/tests/test_agent_route_branch_reliability.py',
    'backend/tests/test_agent_route_decision_reliability.py',
    'backend/tests/test_agent_route_stage_a_reliability.py',
    'backend/tests/test_agent_chat_admission.py',
    'backend/tests/test_agent_events.py',
    'backend/tests/test_turn_outcomes.py',
    'backend/tests/test_turn_resolution.py',
    'backend/tests/test_turn_telemetry.py',
    'backend/tests/test_turn_latency_guards.py'
)
py -m pytest @files -q --basetemp "$PWD/.pytest-batch5-fwd"
$rev = @($files)
[array]::Reverse($rev)
py -m pytest @rev -q --basetemp "$PWD/.pytest-batch5-rev"
```

```powershell
$env:APP_KEY = 'dummy'
$env:ANTHROPIC_API_KEY = 'dummy'
$env:SMARTROUTE_ENV = 'test'
$env:AGENT_ALLOW_MEMORY_SESSIONS = '1'
$env:PYTHONPATH = 'backend'
py -m pytest backend/tests -q --basetemp "$PWD/.pytest-batch5-fullbackend2"
```

Exit 0. 1823 passed, 21 skipped, 444 subtests.

```powershell
$env:APP_KEY = 'dummy'
$env:ANTHROPIC_API_KEY = 'dummy'
$env:SMARTROUTE_ENV = 'test'
$env:AGENT_ALLOW_MEMORY_SESSIONS = '1'
$env:PYTHONPATH = 'backend'
python scripts/check_quality.py --quality-ref 93473c3
```

Worker run before the reviewer baseline shrink: exit 1. `tests_ran: true`.
`approval_eligible: false`. Frontend 314 passed.
Backend coverage pytest 1823 passed, 21 skipped, 444 subtests. New 0.
Worsened 0. Cognitive 0. Stale 11, all Batch 4 `agent/tools` entries after
the place-gather shrink. Remaining 329. The worker did not run
`--update-baseline`.

Reviewer final after the authorized 340-to-329 baseline shrink: exit 0.
`tests_ran: true`. `approval_eligible: true`. Frontend 314 passed. Backend
coverage pytest 1823 passed, 21 skipped, 444 subtests. New 0. Worsened 0.
Cognitive 0. Stale 0. Remaining 329.

### Batch 6: backend test style remainder

Fixed point `22f6f0d`. Tests only. Production code was not edited. The worker
did not edit `quality/baseline.json`. After accepting the rest of the batch,
the reviewer removed exactly 11 proven-stale Batch 4 entries. No entry was
added or increased.

Trail: `.audit/batch6-simplify.tsv`.

Pre-edit inventory on `backend/tests`: 787 findings across 82 files,
616 of them `PT009`. Post-edit `py -m ruff check --config pyproject.toml backend`
exits 0 with 0 findings.

`ruff --fix --unsafe-fixes` converted `PT009` and `PT027` while keeping
operand order (`assert a == b`, `a in b`). Remaining C901/PLR/ARG/PERF/N818
were owned by splitting named test adapters, not by new assertion helpers.
Protocol kwargs (`deadline_monotonic`, Redis `ex=`) kept their production
names. Prefixing them broke `consume_nonce` (`unavailable` vs `consumed`) and
`_Ledger.execute`. Unused protocol values are discarded with `del`, matching
`test_goal_aware_tool_round.py`.

Prove-it: `test_ticket_nonce_is_single_use` failed after renaming `ex` to
`_ex`, then passed after restoring the keyword. Cancellation collects events
with `anext` so a mid-turn cancel still keeps partial events. An `async for`
append loop would trip `PERF401`. An async listcomp would drop partials.

Simplify kept the `anext` collectors. It replaced `_declare_goals_round`
with already-imported `_turn_round`, and made `_rewrite_multi_call` always
return a dict. `_fill_public_schema` stayed in the F2 fixtures. Merging it
into E1 `_complete_public_inputs` would couple audit batches.

H03 phases are `_h03_through_barclays_destination` and
`_h03_through_bound_reload`. The first returns only `set_a`. Newly collapsed
asserts that exceeded 160 characters versus `22f6f0d` were reflowed. Operand
order and failure messages stayed.

Comment Sicko deleted 44 leftover sermon lines. None were restored.
`_model_led_rounds` now requires `evidence_id` from `run_multi_probe`. The
deleted `_load_trips_module` import-first lecture was stale. `app.routers.trips`
no longer imports `app.services.agent.tools`.

Recorded from the repository root with inert test credentials,
`SMARTROUTE_ENV=test`, `AGENT_ALLOW_MEMORY_SESSIONS=1`, and
`PYTHONPATH=backend`.

```powershell
$env:APP_KEY = 'dummy'
$env:ANTHROPIC_API_KEY = 'dummy'
$env:SMARTROUTE_ENV = 'test'
$env:AGENT_ALLOW_MEMORY_SESSIONS = '1'
$env:PYTHONPATH = 'backend'

python -m ruff check --config pyproject.toml backend
python -m ruff check --config pyproject.toml backend/tests
```

Both exit 0.

60-file impacted manifest, forward then reverse:

```powershell
$env:APP_KEY = 'dummy'
$env:ANTHROPIC_API_KEY = 'dummy'
$env:SMARTROUTE_ENV = 'test'
$env:AGENT_ALLOW_MEMORY_SESSIONS = '1'
$env:PYTHONPATH = 'backend'
$files = @(
    'backend/tests/conversation/test_conversation_candidate_lifecycle_safety.py',
    'backend/tests/conversation/test_conversation_discovery_reference.py',
    'backend/tests/conversation/test_conversation_discovery_route.py',
    'backend/tests/conversation/test_conversation_discovery_waypoint.py',
    'backend/tests/conversation/test_conversation_long_state_retention.py',
    'backend/tests/conversation/test_conversation_multi_intent_tool_sequencing.py',
    'backend/tests/conversation/test_conversation_no_good_aggregate.py',
    'backend/tests/conversation/test_conversation_no_good_nonfatal_followup.py',
    'backend/tests/conversation/test_conversation_presentation_race.py',
    'backend/tests/conversation/test_conversation_what_if_lifecycle.py',
    'backend/tests/test_active_temporary_route_presenter.py',
    'backend/tests/test_activity_copy.py',
    'backend/tests/test_admission.py',
    'backend/tests/test_agent_chat_admission.py',
    'backend/tests/test_agent_evidence_binding_reliability.py',
    'backend/tests/test_agent_loop_round_cap_reliability.py',
    'backend/tests/test_agent_progress.py',
    'backend/tests/test_anthropic_conversation_contract_live.py',
    'backend/tests/test_completion_policy.py',
    'backend/tests/test_conversational_geography.py',
    'backend/tests/test_crowd_evidence.py',
    'backend/tests/test_crowd_hotspots.py',
    'backend/tests/test_declare_goals.py',
    'backend/tests/test_evidence_freshness.py',
    'backend/tests/test_geo_nearest_stops.py',
    'backend/tests/test_geo_privacy.py',
    'backend/tests/test_gtfs_intermediate_stops.py',
    'backend/tests/test_incident_batches.py',
    'backend/tests/test_incident_index.py',
    'backend/tests/test_incident_index_and_background_job.py',
    'backend/tests/test_incident_index_batch_lookup.py',
    'backend/tests/test_incident_job_router.py',
    'backend/tests/test_incident_lifecycle.py',
    'backend/tests/test_incident_monitor.py',
    'backend/tests/test_intent_scoped_tool_policy.py',
    'backend/tests/test_live_feed_snapshot_trip_context.py',
    'backend/tests/test_model_led_goal_loop.py',
    'backend/tests/test_model_output_projection.py',
    'backend/tests/test_mta_feed_bus_stops.py',
    'backend/tests/test_mta_feed_service_alerts.py',
    'backend/tests/test_nearby_issues.py',
    'backend/tests/test_observability.py',
    'backend/tests/test_pending_continuation.py',
    'backend/tests/test_plan_trip_projection.py',
    'backend/tests/test_public_body_bounds.py',
    'backend/tests/test_route_constraint_relaxation.py',
    'backend/tests/test_route_decision_evaluation.py',
    'backend/tests/test_route_exclusion_constraints.py',
    'backend/tests/test_route_identity_gate.py',
    'backend/tests/test_route_itinerary_contract.py',
    'backend/tests/test_route_option_assembly.py',
    'backend/tests/test_strict_tool_schema_new_tools.py',
    'backend/tests/test_trip_admission.py',
    'backend/tests/test_trip_candidate_reasons.py',
    'backend/tests/test_trips_enrichment.py',
    'backend/tests/test_turn_contract.py',
    'backend/tests/test_turn_latency_guards.py',
    'backend/tests/test_turn_outcomes.py',
    'backend/tests/test_turn_resolution.py',
    'backend/tests/test_turn_telemetry.py'
)
py -m pytest @files -q --basetemp "$PWD/.pytest-batch6-fwd"
$rev = @($files)
[array]::Reverse($rev)
py -m pytest @rev -q --basetemp "$PWD/.pytest-batch6-rev"
```

```powershell
$env:APP_KEY = 'dummy'
$env:ANTHROPIC_API_KEY = 'dummy'
$env:SMARTROUTE_ENV = 'test'
$env:AGENT_ALLOW_MEMORY_SESSIONS = '1'
$env:PYTHONPATH = 'backend'
py -m pytest backend/tests -q --basetemp "$PWD/.pytest-batch6-full"
```

Exit 0. 1823 passed, 21 skipped, 444 subtests.

```powershell
$env:APP_KEY = 'dummy'
$env:ANTHROPIC_API_KEY = 'dummy'
$env:SMARTROUTE_ENV = 'test'
$env:AGENT_ALLOW_MEMORY_SESSIONS = '1'
$env:PYTHONPATH = 'backend'
python scripts/check_quality.py --quality-ref 22f6f0d
```

Worker run before the reviewer baseline shrink: exit 1. `tests_ran: true`.
`approval_eligible: false`. Frontend 314 passed.
Backend coverage pytest 1823 passed, 21 skipped, 444 subtests. New 0.
Worsened 0. Cognitive 0. Stale 11, all Batch 4 `agent/tools` entries.
Remaining 329. Ruff C901 and structural diagnostics 0. The worker did not
run `--update-baseline`.

Reviewer final after the authorized 340-to-329 baseline shrink: exit 0.
`tests_ran: true`. `approval_eligible: true`. Frontend 314 passed. Backend
coverage pytest 1823 passed, 21 skipped, 444 subtests. New 0. Worsened 0.
Cognitive 0. Stale 0. Remaining 329. Ruff C901 and structural diagnostics 0.
Do not start Batch 7.

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
