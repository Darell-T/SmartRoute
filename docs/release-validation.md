# Release validation evidence

`backend/scripts/release/validate.py` emits one sanitized JSON report for an
immutable candidate SHA. It does not deploy, migrate, restore, or roll back
SmartRoute. Those platform actions require external, reviewable evidence.

## Repository quality checks

Install `backend/requirements.txt`, `backend/requirements-dev.txt`, and the
locked frontend dependencies before running these commands from the repository
root:

```powershell
python -m ruff check --config pyproject.toml backend
python scripts/check_quality.py --quality-ref <base-commit>
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run lint:oxlint
npm --prefix frontend run verify:transit-artifacts
npm --prefix frontend run build
```

The quality command runs frontend coverage and backend tests with branch
coverage. It checks complexity against the saved baseline and the supplied
Git commit. Existing complexity entries may remain, but new or worsened debt
fails the check. Do not raise the baseline to accept a change.

CI uses the pull request's base commit for comparison. A push to `main` uses
the first parent of the pushed commit. The quality job retains its JSON report.
The frontend job runs Oxlint with warnings denied. The backend job runs Ruff
and enforces the 85% branch-coverage floor.

## Deterministic backend suite

These checks do not call Anthropic, Google Routes, Ticketmaster, or another
live provider. Run them from `backend/` after setting:

```powershell
$env:ANTHROPIC_API_KEY=''
$env:RUN_ANTHROPIC_TOOL_CONTRACT='0'
$env:TICKETMASTER_LIVE_SMOKE_TEST='0'
$env:TELEMETRY_DEV_API_KEY=''
$env:SMARTROUTE_RUN_LIVE_TESTS='0'
$env:RUN_LIVE_TESTS='0'
```

Use a workspace-local temporary directory. The Windows system temporary
directory can reject pytest cleanup.

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp=..\.pytest-backend
.\.venv\Scripts\python.exe -m compileall -q app evaluation scripts tests
.\.venv\Scripts\python.exe -m pip check
```

Architecture and import-boundary tests live in the pytest suite, including
`tests/test_route_tool_import_boundary.py`. Route-intelligence replay runs
through `python -m scripts.replay_route_intelligence`.

The production request path must not import evaluation modules. The Agent
registry must offer exactly eight public capabilities.

## Offline deterministic preflight

Choose exactly one mode. This command makes no network or subprocess calls:

```powershell
cd backend
python -m scripts.release.validate --commit-sha <immutable-git-sha> --self-test
```

All reports use `PASSED`, `FAILED`, `BLOCKED`, or `NOT_APPLICABLE`. Invalid
mode, SHA, request, time, concurrency, byte, soak-duration, or declared-cost
budgets fail before any scanner or network work. The report never includes
supplied URLs, header values, query secrets, tokens, SSE body content, or
scanner output. `provider_fault_jitter` is a fixed-seed, offline replay and
fake-provider gate; it fails closed if the covered provider classifications,
deadline, cancellation, or jitter contracts regress.

## Transit artifact regeneration

When verifying a builder change, use the same GTFS archive as the checked-in
artifacts. The GTFS network artifact in `frontend/public/` records its
`metadata.gtfs_zip_sha256`. Set `SMARTROUTE_GTFS_CACHE_DIR` to a directory
containing that `google_transit.zip`, then run:

```powershell
cd frontend
$env:SMARTROUTE_GTFS_CACHE_DIR='<directory-with-matching-archive>'
npm run build:transit-artifacts
npm run verify:transit-artifacts
```

Without this setting, the builders use `frontend/.gtfs-cache`. The network
builder downloads the current MTA feed if the archive is absent. A newer feed
is an input change and needs its own geometry review.

## Browser and accessibility evidence

Maps require WebGL2. `frontend/next.config.mjs` copies MapLibre's worker and
shared module from the locked package to a versioned public directory. Both
files must be served together. The shell browser test checks that the worker
loads subway features, in addition to checking that the canvas exists.

The committed Linux browser job runs the deterministic non-visual Playwright
suite and then emits `frontend/test-results/release/browser-evidence.json`.
It is bound to GitHub's exact candidate SHA and is retained with the raw JSON
report and failure traces. The evidence contains only required coverage IDs,
desktop/mobile project counts, candidate SHA, and visual-certification status.

To reproduce the CI evidence locally:

```powershell
cd frontend
npm run test:release:ci
npx tsx scripts/release/build-browser-evidence.ts `
  test-results/release/results.json `
  test-results/release/browser-evidence.json `
  <immutable-git-sha>

cd ..\backend
python -m scripts.release.validate `
  --commit-sha <immutable-git-sha> `
  --self-test `
  --browser-evidence ..\frontend\test-results\release\browser-evidence.json
```

The parser fails closed on malformed reports, retries/flakes, failures, missing
desktop/mobile coverage, missing chat/Quick/map/accessibility/shell/zoom cases,
or a mismatched SHA. A status-only JSON cannot satisfy the gate. Linux CI
deliberately excludes `@visual` snapshots: visual comparison remains
platform-local (Windows baseline) and is explicitly **not certified** by this
browser evidence.

## Dependency advisory evidence

The `dependency-advisories` CI job scans all supported dependency sets: the
full frontend lock, frontend runtime lock (`npm audit --omit=dev`), backend
runtime requirements, and backend development requirements. It publishes only
`dependency-advisories.json`; raw scanner JSON and stderr stay in the ephemeral
runner workspace.

The generator binds each scanner result to the exact Git SHA and SHA-256 digest
of its lockfile or requirements file. It records scanner name/version/format,
scope, scanner exit code, count-by-severity, and normalized advisory IDs,
packages, directness, dependency paths when the scanner provides them, and
fixed versions. A scan may exit `0` only with no findings and `1` only with
findings. Unknown scanner output, omitted scope, a changed input digest, a
mismatched SHA, or any unaccepted finding fails closed. This gate deliberately
does not accept arbitrary commands or status-only JSON.

### Run dependency scans

`backend/release_advisory_exceptions.json` contains no accepted exceptions.
The CI job binds evidence to `${{ github.sha }}` and the candidate dependency
files. Re-run all scans after changing dependencies:

```powershell
cd frontend
npm audit --json
npm audit --omit=dev --json

cd ..\backend
pip-audit -r requirements.txt --format json
pip-audit -r requirements-dev.txt --format json
```

Use the retained CI artifact in the staging gate:

```powershell
cd backend
python -m scripts.release.validate `
  --commit-sha <immutable-git-sha> `
  --staging --staging-url <staging-base-url> `
  --advisory-evidence <dependency-advisories.json> `
  --deployment-evidence <deployment-evidence.json> `
  --rollback-evidence <rollback-evidence.json>
```

## Opt-in staging validation

The production behavior contract is documented in
[`production-topology-contract.md`](production-topology-contract.md). It does
not define platform deployment, migration, restore, worker-count, or rollback
automation. Supply external deployment and rollback evidence before staging
network checks can run. Deployment evidence must have the candidate
`commit_sha` and nonempty `instance_ids`. Rollback evidence must have a distinct
valid `previous_commit_sha`, matching `restored_commit_sha`, and
`"result": "passed"`.

```powershell
cd backend
python -m scripts.release.validate `
  --commit-sha <immutable-git-sha> `
  --staging --staging-url <staging-base-url> `
  --advisory-evidence <dependency-advisories.json> `
  --deployment-evidence <deployment-evidence.json> `
  --rollback-evidence <rollback-evidence.json> `
  --model-chat-smoke --chat-header "X-App-Key: <secret-injected-value>" `
  --max-requests 16 --timeout-seconds 5 --concurrency 2 `
  --max-chat-bytes 65536 --max-estimated-cost-usd 0.01 `
  --estimated-cost-per-request-usd 0.001 `
  --load-requests 2 --spike-requests 4 --soak-requests 2 `
  --soak-interval-seconds 2 --max-soak-seconds 15
```

Run the command only in a secure runner. Inject `X-App-Key` from its secret
store; do not put a real value in shell history. The app actually authenticates
this header, so arbitrary headers cannot satisfy the chat prerequisite. The
validator supplies its own fixed, non-secret `X-SmartRoute-Principal` admission
identity; do not provide or log a principal value in the command.

The live Agent chat check is off by default. `--model-chat-smoke` is one explicit,
costed model-backed request and requires both a positive maximum cost budget
and a positive declared cost. It requires HTTP success **and** a bounded SSE
stream terminating in the backend’s successful `done` event (`end_turn` or
`clarification_required`). `error`, `stream_error`, malformed, oversized,
timed-out, or terminal-less streams fail without exposing body content.

`load_readiness_sample` is sequential, `spike_readiness_sample` is concurrent
up to the declared cap, and `soak_readiness_sample` is sequential with the
declared interval and maximum total duration. They are readiness samples only;
they do not prove provider jitter/fault behavior, global multi-instance limits,
or production capacity. `migration_restore` is `NOT_APPLICABLE` until platform
automation exists and is exercised externally.

Browser and accessibility certification is a separate evidence gate. Supply
the CI artifact with `--browser-evidence`; without it the gate remains
`BLOCKED`. This command does not substitute HTTP/source checks for browser
evidence. Transit-line certification remains outside this release-validation
scope.
