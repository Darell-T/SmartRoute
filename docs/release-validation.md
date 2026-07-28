# Release validation evidence

`backend/scripts/release_validation.py` emits one sanitized JSON report for an
immutable candidate SHA. It does not deploy, migrate, restore, or roll back
SmartRoute. Those platform actions require external, reviewable evidence.

## Offline deterministic preflight

Choose exactly one mode. This command makes no network or subprocess calls:

```powershell
cd backend
python -m scripts.release_validation --commit-sha <immutable-git-sha> --self-test
```

All reports use `PASSED`, `FAILED`, `BLOCKED`, or `NOT_APPLICABLE`. Invalid
mode, SHA, request, time, concurrency, byte, soak-duration, or declared-cost
budgets fail before any scanner or network work. The report never includes
supplied URLs, header values, query secrets, tokens, SSE body content, or
scanner output. `provider_fault_jitter` remains `BLOCKED`: this repository has
no external provider-fault/jitter harness. That is an intentional release gap,
not a claim of coverage.

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
python -m scripts.release_validation `
  --commit-sha <immutable-git-sha> `
  --staging --staging-url <staging-base-url> `
  --advisory-command "npm audit --omit=dev" `
  --advisory-command "pip-audit -r requirements.txt" `
  --deployment-evidence <deployment-evidence.json> `
  --rollback-evidence <rollback-evidence.json> `
  --model-chat-smoke --chat-header "X-App-Key: <secret-injected-value>" `
  --max-requests 16 --timeout-seconds 5 --concurrency 2 `
  --max-chat-bytes 65536 --max-estimated-cost-usd 0.01 `
  --estimated-cost-per-request-usd 0.001 `
  --load-requests 2 --spike-requests 4 --soak-requests 3 `
  --soak-interval-seconds 2 --max-soak-seconds 15
```

Run the command only in a secure runner. Inject `X-App-Key` from its secret
store; do not put a real value in shell history. The app actually authenticates
this header, so arbitrary headers cannot satisfy the chat prerequisite.

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

Browser and accessibility certification is a separate gate. Wave 5C1 remains
blocked because the approval system rejected pinned Playwright and axe
installation until August 4. This command does not substitute HTTP/source
checks for that browser evidence. Transit-line certification remains outside
this release-validation scope.
