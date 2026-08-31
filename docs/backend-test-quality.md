# Check backend test quality

Use coverage to find missing evidence. Do not treat a coverage percentage as proof
that the assertions are useful. Use mutation testing on small, important modules to
check whether the tests reject behavior changes.

## Current audited result

The 2026-08-31 audit used the full `backend/app` production denominator and branch
coverage.

| Measure | Result |
|---|---:|
| Combined statement and branch coverage | 89.34% |
| Statement coverage | 91.96% (20,336 of 22,113) |
| Branch coverage | 81.86% (6,346 of 7,752) |
| Missing statements | 1,777 |
| Missing branches | 1,406 |
| Zero-covered authored functions | 37 of 2,415 |
| Functions with CRAP above 30 | 0 |

The original audit found 66 zero-covered functions and one function with CRAP above
30. Public behavior tests reduced that list to 50 and removed the CRAP finding.
Batch 6F then deleted all 13 zero-covered functions whose repository-wide call-site
search stayed empty. It also deleted an unreachable first-boarding timing subtree
that had tests but no production caller. The repaired behavior includes evidence
freshness, passenger-safe area evidence, directions parsing, area-condition
dispatch, unconfirmed alerts, crowd fallback, BusTime vehicle requests,
stalled-bus failure isolation, API-key rejection, and an already-expired model
deadline.

The remaining 37 functions are not all boilerplate.

| Risk bucket | Count | Review result |
|---|---:|---|
| Low-risk request normalization, labels, empty-result projections, and defaults | 18 | These functions do not own route selection, itinerary arithmetic, or passenger-safety decisions. Cover them when their public behavior changes. |
| Provider, startup, shutdown, logging, and persistence lifecycle paths | 17 | These paths are operational risk, not harmless boilerplate. Prefer boundary fakes and deployment smoke checks over mocks of internal helpers. |
| Live orchestration adapters | 2 | `build_preparation_dependencies.derive_with_bound_provider` and `_progress_without_intermediate_complete.emit` still need public-path evidence when those flows change. |

The 17 operational paths include the three startup and refresh loops in
`app/main.py`, `NetworkSnapshotStore.close`, the BusTime client lifecycle and
cached route-stop parser, the three feed logging and summary callbacks, GTFS
download and scheduled-index loading, and five PostgreSQL pool or scheduled-arrival
methods. Do not raise coverage by replacing those boundaries with mocks that cannot
fail like the real dependency.

The 18 low-risk functions consist of two `AgentChatRequest` normalizers,
`_named_near_scope`, 11 tool-label formatters, two empty-result projections,
`parse_service_alerts_for_service_board`, and `_empty_evaluation`. The service-board
wrapper is one line, and its same-day inclusion policy is already covered at the
parser owner.

The zero-covered list no longer contains the 13 proven dead surfaces or the
identified route-evidence projection, API authorization, model-deadline, BusTime
request, directions-response, or unconfirmed-alert gaps. It also does not replace
line and branch review. A function can have coverage while an important branch
remains untested.

## Generate the coverage report

Run these commands from the repository root in PowerShell. The local temporary
directory avoids Windows permissions on pytest's shared system temporary folder.

```powershell
Set-Location backend
$backendTestTemp = Join-Path (Get-Location) "test-results\pytest-coverage-local"
python -m coverage erase
python -m coverage run --branch --source=app -m pytest -q --basetemp $backendTestTemp
python -m coverage report --precision=2 --show-missing --skip-covered
New-Item -ItemType Directory -Force test-results\coverage | Out-Null
python -m coverage json -o test-results\coverage\backend-coverage.json
Set-Location ..
python scripts\report_backend_debt.py `
  --coverage-file backend\.coverage `
  --output backend\test-results\coverage\backend-debt.json
```

Set the same test-only environment values that CI defines if local startup
validation requires them:

```powershell
$env:APP_KEY = "ci-test-key"
$env:ANTHROPIC_API_KEY = "ci-test-anthropic-key"
$env:SMARTROUTE_ENV = "test"
```

Never use a real provider secret for this run.

## Filter the report

Start with uncovered functions and high CRAP. Then inspect missing branches in the
public caller before writing a test.

```powershell
$debt = Get-Content backend\test-results\coverage\backend-debt.json -Raw |
  ConvertFrom-Json
$debt.crap_above_30 |
  Format-Table file,function,line,complexity,cognitive,coverage,crap -AutoSize
$debt.zero_covered |
  Sort-Object batch,file,line |
  Format-Table batch,file,function,line,complexity,cognitive -AutoSize
```

For each result, answer these questions in order:

1. Does a production caller reach it?
2. Does it own a passenger fact, authorization rule, fallback, provider boundary,
   persistence transition, or lifecycle guarantee?
3. Can a public API or module entry point reproduce the missing behavior?
4. Would the test fail if that behavior changed?

Delete proven dead code. Test important behavior through its public owner. Leave
simple generated accessors and impossible validated states alone. Do not assert a
private helper only to increase the percentage.

## Run the mutation canary

Cosmic Ray is pinned in `backend/requirements-dev.txt`. The checked-in
`backend/cosmic-ray.toml` mutates `app/services/evidence.py` and runs its focused
public tests. This is a fast canary for test strength, not a whole-backend score.

```powershell
Set-Location backend
cosmic-ray baseline cosmic-ray.toml
cosmic-ray init --force cosmic-ray.toml test-results\mutation\evidence.sqlite
cr-filter-operators test-results\mutation\evidence.sqlite cosmic-ray.toml
cosmic-ray exec cosmic-ray.toml test-results\mutation\evidence.sqlite
cr-report --surviving-only --no-show-output --show-diff `
  test-results\mutation\evidence.sqlite
```

The audited canary generated 185 jobs. The operator filter removed 131 type or
signature substitutions. Tests killed 48 of the 54 behavior-changing candidates,
which is 88.89%. Review found that the six survivors were equivalent for the
supported status values and timestamp inputs, or unreachable through the public
`evidence_envelope` factory.

Do not add artificial tests to kill an equivalent mutant. If a survivor changes a
supported public result, first demonstrate the wrong result, then add the cheapest
faithful public test. Change `module-path` and `test-command` together when moving
the canary to another critical module.

## Keep the CI floor at 85 percent

The backend CI job runs the full suite with branch coverage and fails below 85%.
The current 89.14% result leaves 4.14 percentage points of room for small coverage
mapping changes while still blocking meaningful regressions. Do not set the floor
to the exact current percentage. Raise it only after multiple accepted batches
show that the higher value is stable across clean CI runs.

Mutation score is not a pull-request gate yet. The current configuration covers one
canary module and requires human review of equivalent mutants.
