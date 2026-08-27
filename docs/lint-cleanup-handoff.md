# Lint cleanup handoff

Use this guide to continue the lint cleanup in the `damn-lines-integration`
worktree. The inventory is from 2026-08-26 after Batch 1. It uses the
checked-in Ruff, ESLint, and Oxlint configurations. Regenerate the reports
before editing.

## Current result

| Tool | Starting | Ending | Files | Rules | Status |
|---|---:|---:|---:|---:|---|
| Ruff | 2,283 | 1,385 | 213 | 40 | Backend backlog remains. Route-intelligence and Batch 1 tooling are clear. |
| Oxlint | 1,162 | 1,162 | 192 | 14 | Recovered frontend baseline after transit-artifact type restoration. |
| ESLint | 0 (prior table) | 193 | 76 | 2 | Current worktree config reports `complexity` (185) and `max-depth` (8). Batch 1 did not change frontend lint. |
| CC violations | not in prior table | 736 | | | Runtime ratchet only. |
| CRAP violations | not in prior table | 986 | | | Runtime ratchet only. |

Ruff reports 909 test findings and 476 production findings. Batch 1 reduced
the Batch 0 snapshot from 1,484 to 1,385 findings. Rule count fell from 44
to 40. Affected files fell from 228 to 213. No rule was disabled, ignored,
or lowered.

Oxlint stayed at 1,162 diagnostics against the recovered TypeScript-green
baseline. `eslint(complexity)` (372) is part of that current-configuration
inventory. Event-validator findings are no longer the top hotspot.

`quality/baseline.json` is untracked relative to the available fixed point, so
a pre-batch checksum cannot be proven from git. Batch 0 did not edit it. The
file still has 1,004 entries. `_alert_semantics` remains because its cyclomatic
complexity is 18. Two consecutive fresh runs of `py scripts/check_quality.py`
after the supporting advisory-fixture repair both exited 0 with 0 new, 0
worsened, 0 stale, and 1,004 remaining baseline entries. Do not use
`--skip-tests` to certify a later repair.

## Batch 0

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

Ruff on that test file: zero findings. Regenerated backend Ruff JSON: 1,484
findings, 228 files, 44 rules. Unchanged.

Two fresh `py scripts/check_quality.py` runs, no `PYTHONUTF8`, no
`--skip-tests`:

| Run | Exit | New | Worsened | Stale | Remaining | Backend |
|---:|---:|---:|---:|---:|---:|---|
| 1 | 0 | 0 | 0 | 0 | 1,004 | 1,994 passed, 21 skipped, 619 subtests |
| 2 | 0 | 0 | 0 | 0 | 1,004 | 1,994 passed, 21 skipped, 619 subtests |

A later independent review on UTC 2026-08-27 rejected those first two runs
because `backend/tests/test_dependency_advisory_evidence.py` still used
`expires_on: "2026-08-27"`. Production policy treats `today >= expires_on` as
expired, so five exception tests failed before the ratchet ran. That file is
outside the Batch 0 owned set. It was edited only as a supporting quality
recertification after the rejection review specified the repair.

Supporting change in `_exception_policy`: derive
`(datetime.now(UTC).date() + timedelta(days=1)).isoformat()` instead of a
near-term absolute date. UTC expiry semantics are unchanged.
`test_exception_expires_on_its_first_invalid_utc_day` still proves
`today >= expires_on` by injecting `today` equal to the derived expiry day.

Five advisory tests after that change: 5 passed in 0.30s.

Two consecutive recertification runs of `py scripts/check_quality.py`, no
`PYTHONUTF8`, no `--skip-tests`:

| Run | Exit | New | Worsened | Stale | Remaining | Backend |
|---:|---:|---:|---:|---:|---:|---|
| 1 | 0 | 0 | 0 | 0 | 1,004 | 1,994 passed, 21 skipped, 619 subtests |
| 2 | 0 | 0 | 0 | 0 | 1,004 | 1,994 passed, 21 skipped, 619 subtests |

`_alert_semantics` remains in the baseline at complexity 18 among 1,004
entries. `quality/baseline.json` was not edited. `_alert_semantics` was not
rewritten. Batch 1 is recorded below.

Changed files for this recertification:

- `backend/tests/test_dependency_advisory_evidence.py`
- `docs/lint-cleanup-handoff.md`

Batch 0 stopped here. Batch 1 is recorded below.

## Batch 1

Owned paths:

- `backend/scripts/release/**`
- `backend/scripts/build_scheduled_arrival_artifact.py`
- `backend/scripts/build_stop_patterns.py`
- `backend/scripts/live_checks/anthropic_agent.py`
- `backend/scripts/live_checks/crowd_search.py`
- `backend/scripts/live_checks/ticketmaster.py`
- `backend/scripts/phase2_quality_report.py`
- `backend/scripts/run_incident_refresh.py`

Starting owned inventory, regenerated before edits: 98 findings, 15 files,
12 rules. Per-rule counts: `TRY003` 46, `C901` 24, `TRY004` 10, `BLE001` 8,
`S311` 2, `TID251` 2, and one each of `SIM105`, `PERF401`, `S603`, `SIM102`,
`SIM117`, and `E402`.

Repairs kept ValueError and Fault* messages as named locals or `_invalid`.
UTC expiry remains `today >= expires_on`. Provider-fault seeds stay
`(37, 73, 109)` with `random.Random(seed)`. Live checks still require
`--live`. Git argv remains the three-tuple allowlist after `shutil.which`.
`provider_http` still cannot replace release SSE or sync HTTP.

Owned Ruff after repair: zero.

Focused command: 83 passed, 9 subtests in 9.07s.

Two consecutive `py scripts/check_quality.py` runs, no `PYTHONUTF8`, no
`--skip-tests`:

| Run | Exit | New | Worsened | Stale | Remaining | Backend | Frontend |
|---:|---:|---:|---:|---:|---:|---|---:|
| 1 | 0 | 0 | 0 | 0 | 1,004 | 1,994 passed, 21 skipped, 619 subtests | 314 |
| 2 | 0 | 0 | 0 | 0 | 1,004 | 1,994 passed, 21 skipped, 619 subtests | 314 |

`quality/baseline.json` was not edited. Regenerated backend Ruff JSON:
1,385 findings, 213 files, 40 rules. Production 476, tests 909.

Narrow line noqas that remain, each with a local reason:

- `TID251` on release `httpx.Client` and `httpx.AsyncClient` because
  `provider_http` only fetches JSON
- `S311` on seeded offline jitter
- `S603` on allowlisted `git` after `which()`
- `BLE001` on live-check and incident-refresh Exception boundaries that
  reduce to a sanitized error class or payload-free message
- `E402` on the two incident-refresh imports after `sys.path` insert

Oxlint and ESLint were not regenerated. This batch did not edit frontend
files. `frontend/eslint.config.mjs` still has a pre-existing worktree
diff. Do not treat that as a Batch 1 change.

Stop here. Do not start Batch 2 in this session.

## Route-intelligence batch

The route-intelligence implementation repairs are complete. Batch 0 is
complete. Do not select the next subsystem automatically.

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

- `replay.py` patches `httpx.AsyncClient.request` with `# noqa: TID251` because
  deterministic replay disables outbound HTTP
- `replay.py` patches `httpx.Client.request` with `# noqa: TID251` for the same
  offline sandbox
- `scripts/live_checks/advisor.py` catches `Exception` with `# noqa: BLE001`
  because unexpected provider failures must reduce to an error class rather
  than print details

### Commands and results

Focused Ruff on the owned boundary: zero findings.

Focused deterministic, advisor, replay, reporting, metrics, live-check,
import-boundary, and adjacent request-shape tests: 122 passed, 62 subtests
passed. The same files passed in reverse order.

Full backend suite with the inert test environment: 1,994 passed, 21 skipped,
619 subtests passed. The net drop from the prior 2,004 passed count is the
deleted live-shadow tests minus the review-repair and Batch 0 tests added
afterward. Skip count is unchanged.

Frontend: `npm run typecheck:scripts`, `npm run typecheck`, `npm run test:unit`
(314 passed), and `npm run verify:transit-artifacts` (15 station-anchor tests
plus overlay, palette, and renderer checks) all passed. Generated transit
artifacts were unchanged.

Live advisor check (`py -m scripts.live_checks.advisor --live`), one bounded
request (`overload_attempts=1`), telemetry.dev left uninitialized. Safe result
only:

- status=failed
- advisor_provider=anthropic
- advisor_model=claude-haiku-4-5-20251001
- error_type=AuthenticationError

No selected route index and no response-presence flag because certification
failed at the provider error class. The command did not print the system
prompt, raw model response, provider payload, API key, or chain of thought.

The post-batch review required these implementation repairs:

- `_english_text` coverage tests restored CRAP to the hard ceiling. The
  function body was not rewritten. Only that stale baseline entry was removed.
- `_alert_semantics` coverage is restored by a table-driven direct test in
  Batch 0. The function body was not rewritten. Its baseline entry remains
  because CC is still 18.
- Live certification calls `collect_recommendation(..., overload_attempts=1)`.
  Production REST still retries overload three times. A 529 test asserts the
  provider stream is entered once.
- `PlanningMode.SHADOW` and the positive shadow-mode test are gone.
- Schema v2 `SourceEffect` no longer includes unused score or confidence
  members.

## Review result

The Cursor PT009 continuation is safe to retain.

- The reviewed backend test diff had 1,370 test functions, 6,375 assertions,
  66 exception checks, and no new skips.
- The full backend suite passed after the review repairs.
- No edited file crossed 1,000 lines.
- The assertion conversions did not remove test functions.

The review found and repaired three integration problems:

1. Place presentation now supplies a real `turn_id`, so the required Google
   Maps source event does not invalidate older registry tests.
2. `backend/scripts/phase2_quality_report.py` now has one allowlisted Git
   subprocess call instead of three duplicated branches.
3. Nearby arrivals use the canonical `LiveArrival` contract instead of a local
   shadow interface.

The review also restored intentional fail-open behavior in telemetry and
official incident parsing. Those provider boundaries may catch ordinary
`Exception` values because instrumentation and injected parser failures must
not abort the passenger request. Keep those `BLE001` findings until the owner
contracts can express a narrower shared failure type.

Google Maps and Damn Lines attribution render through the PromptKit source
row. Normal sources use `SourceTrigger` with favicon display. Google attribution
remains visible as `Place data by Google Maps` after the recommendation prose.

## Verification completed

The reviewed work plus this route-intelligence batch, Batch 0, and Batch 1
pass these checks:

- Backend: 1,994 passed, 21 skipped, and 619 subtests passed.
- Route-intelligence focused tests: 122 passed and 62 subtests, two orderings.
- Focused MTA alert tests: 17 passed and 13 subtests.
- Focused Ruff on the route-intelligence boundary: zero findings.
- Focused Ruff on Batch 1 owned scripts: zero findings.
- Focused Batch 1 suites: 83 passed and 9 subtests.
- `py scripts/check_quality.py` twice from fresh coverage after Batch 1:
  both exited 0 with 0 new, 0 worsened, 0 stale, and 1,004 remaining
  baseline entries. `_alert_semantics` remains at CC 18.
- Frontend TypeScript scripts and app: passed.
- Frontend unit tests: 314 passed.
- Station anchor tests: 15 passed.
- Station overlay, subway palette, and subway renderer checks: passed.

Use these environment values for the backend suite. They are inert test
values, not production credentials.

```powershell
$env:APP_KEY='dummy'
$env:ANTHROPIC_API_KEY='dummy'
$env:SMARTROUTE_ENV='test'
$env:AGENT_ALLOW_MEMORY_SESSIONS='1'
py -m pytest backend/tests -q --basetemp .pytest-full-worker
```

Use a unique `--basetemp` path inside the worktree. Verify its resolved path
before deleting it.

## Reproduce the inventories

Run Ruff from the repository root:

```powershell
py -m ruff check --config pyproject.toml backend
py -m ruff check --config pyproject.toml --output-format json backend
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

Regenerated after Batch 1. The Fixable column is Ruff's reported fix count.
It does not authorize a broad unsafe rewrite. Apply fixes to a small owned
file set, inspect the diff, and run its tests.

| Rule | Total | Production | Tests | Files | Fixable |
|---|---:|---:|---:|---:|---:|
| `PT009` | 665 | 0 | 665 | 59 | 665 |
| `C901` | 234 | 208 | 26 | 120 | 0 |
| `TRY003` | 126 | 99 | 27 | 37 | 0 |
| `BLE001` | 72 | 72 | 0 | 34 | 0 |
| `I001` | 62 | 5 | 57 | 60 | 62 |
| `PT027` | 26 | 0 | 26 | 9 | 26 |
| `PERF401` | 18 | 9 | 9 | 14 | 0 |
| `ARG001` | 18 | 16 | 2 | 11 | 0 |
| `RUF059` | 14 | 0 | 14 | 3 | 14 |
| `ARG002` | 13 | 6 | 7 | 7 | 0 |
| `RUF022` | 12 | 0 | 12 | 12 | 12 |
| `RUF005` | 11 | 0 | 11 | 5 | 11 |
| `SIM117` | 11 | 0 | 11 | 4 | 5 |
| `PT018` | 11 | 0 | 11 | 2 | 11 |
| `TRY300` | 10 | 10 | 0 | 9 | 0 |
| `TID251` | 10 | 10 | 0 | 8 | 0 |
| `UP017` | 9 | 0 | 9 | 3 | 9 |
| `RUF001` | 8 | 8 | 0 | 7 | 0 |
| `PT017` | 7 | 0 | 7 | 1 | 0 |
| `TRY004` | 6 | 6 | 0 | 2 | 0 |
| `N803` | 5 | 5 | 0 | 3 | 0 |
| `B904` | 4 | 4 | 0 | 3 | 0 |
| `TRY301` | 4 | 4 | 0 | 3 | 0 |
| `UP041` | 4 | 0 | 4 | 3 | 4 |
| `UP042` | 4 | 4 | 0 | 3 | 4 |
| `N818` | 3 | 3 | 0 | 3 | 0 |
| `RUF012` | 3 | 0 | 3 | 2 | 0 |
| `SIM102` | 2 | 2 | 0 | 2 | 0 |
| `UP047` | 2 | 2 | 0 | 1 | 2 |
| `ARG005` | 1 | 1 | 0 | 1 | 0 |
| `B010` | 1 | 0 | 1 | 1 | 1 |
| `C408` | 1 | 0 | 1 | 1 | 1 |
| `C420` | 1 | 0 | 1 | 1 | 1 |
| `PERF403` | 1 | 1 | 0 | 1 | 0 |
| `PT011` | 1 | 0 | 1 | 1 | 0 |
| `RUF100` | 1 | 0 | 1 | 1 | 1 |
| `S105` | 1 | 0 | 1 | 1 | 0 |
| `UP035` | 1 | 0 | 1 | 1 | 1 |
| `UP037` | 1 | 0 | 1 | 1 | 1 |
| `UP046` | 1 | 1 | 0 | 1 | 1 |

## Ruff repair guidance

### Test assertion backlog

Convert `PT009` and `PT027` one file at a time. Preserve argument order for
membership assertions. Use `pytest.raises` with an exact `match` only when the
message is part of the contract. Flatten nested context managers when Ruff
reports `SIM117`. Remove a `unittest.TestCase` base only after the file no
longer uses its setup or helpers.

The next test files by total Ruff count are:

| File | Findings | Notes |
|---|---:|---|
| `backend/tests/conversation/conversation_cancellation_support.py` | 29 | Shared conversation helpers. Keep cancellation semantics. |
| `backend/tests/conversation/conversation_unoffered_tool_support.py` | 25 | Preserve unoffered-tool contract. |
| `backend/tests/conversation/test_conversation_no_good_nonfatal_followup.py` | 24 | Preserve follow-up lifecycle. |
| `backend/tests/conversation/test_conversation_presentation_race.py` | 24 | Preserve presentation race coverage. |
| `backend/tests/conversation/test_conversation_what_if_lifecycle.py` | 23 | Preserve what-if lifecycle. |
| `backend/tests/test_agent_evidence_binding_reliability.py` | 22 | Preserve evidence-binding assertions. |
| `backend/tests/test_crowd_evidence.py` | 22 | Preserve crowd evidence semantics. |
| `backend/tests/test_turn_resolution.py` | 22 | Preserve turn-resolution ordering. |

For `test_trips_enrichment.py`, rename unused local fake arguments only when
the callable is not implementing a named external protocol. Keep required
protocol signatures and use the argument meaningfully when the test should
verify it. Rename `_FakeHTTPException` to an `Error` suffix and move the long
raised message to a local variable.

### Mechanical production rules

Apply `I001`, `UP017`, `UP035`, `UP037`, `UP041`, `UP042`, `RUF022`,
`RUF046`, `B905`, `C420`, and the `FURB` fixes in small package batches. Run
the owning package tests and Ruff formatting. Do not apply unsafe fixes across
the whole backend.

### Exceptions and error ownership

Repair `TRY003`, `TRY004`, `TRY300`, `TRY301`, `B904`, `BLE001`, `S110`, and
`S112` together at each owner boundary.

- Put stable long messages in a local variable or a reusable exception class.
- Preserve causes with `raise ... from exc`.
- Catch only errors that the boundary can recover from or translate.
- Keep the reviewed fail-open telemetry and injected parser catches broad until
  their provider protocols expose a narrower shared failure type.
- Do not add an exception wrapper only to satisfy Ruff.

The largest remaining production files are:

| File | Findings |
|---|---:|
| `backend/app/routers/live_feed/socket.py` | 22 |
| `backend/app/services/agent/turn/contract.py` | 22 |
| `backend/app/services/agent/session.py` | 21 |
| `backend/app/services/incidents/ny511.py` | 14 |
| `backend/app/services/mta/alerts.py` | 11 |
| `backend/app/services/agent/model/stream.py` | 10 |
| `backend/app/services/agent/tools/__init__.py` | 10 |
| `backend/app/main.py` | 9 |

### Provider and security rules

`TID251` has 10 remaining findings. Production provider traffic must use the
existing `backend/app/services/agent/tools/provider_http.py` boundary. Do not
add another client wrapper. SDK transport tests may use the SDK's supported
mock transport instead of routing the SDK through the application provider.
Release validation keeps two line `TID251` noqas because it needs sync HTTP
and SSE. `provider_http` only fetches JSON.

The former `S603` Git allowlist finding now has a line noqa on
`phase2_quality_report.git_value` after `shutil.which` and the three-tuple
argv check. Do not widen that suppression.

Treat `S105` in
`backend/tests/conversation/conversation_external_content_fixtures.py` as a
fixture review. Rename the sentinel if it resembles a credential. Never place
a real credential in the fixture.

## Oxlint rule inventory

| Rule | Findings | Repair |
|---|---:|---|
| `eslint/complexity` | 372 | Split statement-level branches. Do not lower the ceiling. |
| `anti-slop/no-shape-in-symbol-names` | 242 | Rename generic structural placeholders. Keep official GTFS vocabulary exact. Add rule tests before correcting a demonstrated GTFS false positive. |
| `anti-slop/require-safety-comment-for-type-assertion` | 140 | Prefer parsing or inference. Use a `SAFETY:` comment only for a checked invariant that TypeScript cannot express. |
| `anti-slop/no-runtime-typeof` | 123 | Parse external data once at the I/O boundary and branch on the parsed domain contract. |
| `anti-slop/no-unsafe-dictionary-type` | 123 | Replace unknown dictionaries with owner or schema-derived contracts. |
| `anti-slop/no-known-value-widening` | 53 | Preserve inference, use `satisfies`, or return the owner contract. |
| `eslint/no-unused-vars` | 41 | Delete dead values. Check framework and callback signatures before renaming parameters. |
| `anti-slop/no-unknown-parameters` | 30 | Keep untrusted input at the I/O boundary and parse it before calling domain code. |
| `anti-slop/no-conditional-empty-object-spread` | 12 | Create the typed object, then assign an optional property in an explicit branch. |
| `unicorn/no-new-array` | 10 | Use an array literal or `Array.from` with an explicit length. |
| `anti-slop/no-chained-type-assertions` | 6 | Parse once from the original value instead of asserting through an intermediate type. |
| `unicorn/prefer-string-starts-ends-with` | 5 | Replace anchored regular expressions with `startsWith` or `endsWith`. |
| `unicorn/no-useless-fallback-in-spread` | 3 | Remove the fallback when the parsed contract already guarantees an iterable value. |
| `eslint/no-useless-escape` | 2 | Remove the escape and rerun the owning parser tests. |

The current hotspots are:

| File | Findings |
|---|---:|
| `frontend/scripts/regenerate-canonical-from-gtfs.ts` | 67 |
| `frontend/scripts/build/snap-off-revenue-to-shape.ts` | 43 |
| `frontend/scripts/regenerate-canonical-from-gtfs.test.ts` | 43 |
| `frontend/components/map/subway-network.ts` | 38 |
| `frontend/scripts/build/visual-network/repairs/route-continuity-repair-stage.ts` | 30 |
| `frontend/scripts/build/station-anchors/index.ts` | 22 |
| `frontend/components/smart-route/map/smart-route-map.tsx` | 21 |
| `frontend/scripts/build/snap-off-revenue-to-shape.test.ts` | 21 |
| `frontend/scripts/build/snap-dangling-same-color.ts` | 19 |
| `frontend/scripts/build/physical-bundle-materialization.ts` | 17 |

The transit-artifact generator remains postponed. Its TypeScript contracts are
green again. Do not start a generator cleanup until a later dedicated batch.

## Next batch risk ladder

Batch 1 is complete. Stop here. When a later session continues, use this
order:

1. Runtime, admission, routers, and live-feed transport. This is Batch 2 in
   `docs/lint-cleanup-plan.md`.
2. Presentation boundaries (live-feed socket, chat and rail adapters).
3. Providers (NY511, MTA alerts, BusTime).
4. Central agent orchestration (session, turn contract, tools).
5. Transit-artifact generator, only after the green type contracts stay green.

## Completion checklist

For every batch:

1. Record the owned files and exact rules before editing.
2. Apply the smallest behavior-preserving repair.
3. Run focused tests for the owned behavior.
4. Run the configured linter and type checker when applicable.
5. Inspect the diff for disabled rules, broad casts, wrappers, dead code, and
   unrelated formatting.
6. Update this document with fresh JSON counts.

The cleanup is complete only when these commands pass with the current
configuration. The route-intelligence batch, Batch 0, and Batch 1 are
already done. Global Ruff, ESLint, and Oxlint still fail on unrelated
backlog. Do not start Batch 2 automatically.

```powershell
py -m ruff check --config pyproject.toml backend
Set-Location frontend
npm run lint
npm run lint:oxlint
npm run typecheck
```
