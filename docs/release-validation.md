# Release validation evidence

`backend/scripts/release/validate.py` emits one sanitized JSON report for an
immutable candidate SHA. It does not deploy, migrate, restore, or roll back
SmartRoute. Those platform actions require external, reviewable evidence.

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

## Browser and accessibility evidence

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

### Approved development exception

`backend/release_advisory_exceptions.json` contains one temporary exception for
`GHSA-mh99-v99m-4gvg` in `brace-expansion@1.1.16` at
`node_modules/brace-expansion`. It is development-only: the finding is present
in the full frontend audit but absent from `npm audit --omit=dev`. It is the
`eslint-config-next@16.2.9` / ESLint 9.39.4 chain through
`@eslint/config-array@0.21.2`, `@eslint/eslintrc@3.3.5`,
`eslint-plugin-import@2.32.0`,
`eslint-plugin-jsx-a11y@6.10.2`, `eslint-plugin-react@7.37.5`, and callable
`minimatch@3.1.5`. Forcing Minimatch 10 is not safe because the current ESLint
plugins still require the callable 3.x API and no compatible upstream patched
chain is available.

The exception's first invalid UTC day is **2026-08-27**. It is bound to the exact
`frontend/package-lock.json` SHA-256
`67d5dcfdb3b2c68883b162db8c4c08d107f77b7d1a3272b363a2d4fa301e3bc6` and to
the exact package paths and versions above. A lock digest, ESLint/plugin,
Minimatch, or Brace Expansion change invalidates it immediately. The generated
evidence retains the accepted finding separately from all scanner findings; the
release parser reloads the policy from the candidate checkout and requires a
one-to-one match. Runtime scopes can never use an exception.

This exception definition was approved and introduced in commit
`4550d0d38b1bd6ff3ab539a95fadc1535fe529ed`.

Candidate identity is carried by the CI job's immutable `${{ github.sha }}` and
is compared to the release command's `--commit-sha`. The policy is read from
that same candidate checkout, while its lock digest prevents a policy from
being replayed against a different dependency tree. Re-audit before expiry or
after any dependency change:

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

The model chat smoke is off by default. `--model-chat-smoke` is one explicit,
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
