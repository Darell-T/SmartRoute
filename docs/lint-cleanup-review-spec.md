# Review a lint cleanup batch

Use this procedure to review one completed batch from
`docs/lint-cleanup-plan.md`. The reviewer verifies the worker's actual files,
diff, tests, and tool output. The worker's summary is a claim to test, not
evidence to trust.

## Reviewer role

The reviewer is read-only unless the user explicitly asks it to implement a
repair. It must not edit the source, change a configuration, update a baseline,
or start the next batch while reviewing.

The reviewer owns:

- interpreting the batch requirements
- identifying the canonical behavior owner
- confirming the changed-file scope
- reproducing relevant tests and quality gates
- reviewing behavior, maintainability, and simplicity
- rejecting metric gaming and hidden scope changes
- giving the worker precise, bounded repair instructions
- issuing the final verdict

The worker owns implementation and test repair. The user decides whether to
start the next batch.

## Required inputs

Do not begin a review without these inputs:

1. Batch number and batch title.
2. The matching section of `docs/lint-cleanup-plan.md`.
3. The repository `AGENTS.md` instructions.
4. A fixed point from before the batch when one exists.
5. The worker's owned-file manifest.
6. Before and after lint counts by tool, file, and rule.
7. The worker's changed-file list.
8. Exact test and quality commands with exit codes and summary counts.
9. Any baseline, configuration, generated artifact, or suppression diff.
10. Assumptions, unresolved risks, and known out-of-scope debt.
11. Cognitive-complexity output compared with the same fixed point.
12. Function, file, and production-code growth signals for the isolated diff.

If any item is missing, recover it from the repository when possible. Do not
ask the worker for information that a read-only command can establish.

## Fixed-point rule

A batch should start from a Git commit or another user-approved immutable
checkpoint. Resolve it before reviewing:

```powershell
git rev-parse <fixed-point>
git status --short
git diff --name-status <fixed-point> -- <owned paths>
```

If the worktree had pre-existing uncommitted changes and no batch checkpoint
exists, perform a final-state audit but label it clearly. Do not claim that the
review proved the batch introduced no scope creep. Use the verdict
`PROVISIONAL FINAL-STATE PASS` rather than `APPROVED` when the implementation
passes but the batch diff cannot be isolated.

Do not create a commit without the user's authorization. Do not use the index,
a stash, or a destructive reset as an improvised checkpoint.

## Review principles

Apply these principles in order:

1. Correct behavior.
2. Existing architecture and canonical ownership.
3. Passenger clarity and data integrity.
4. Security and provider-boundary safety.
5. Tests and reproducible evidence.
6. Human readability and maintainability.
7. Simplicity and minimal code.
8. Lint and metric reduction.

Lint reduction never outranks correctness or readability.

## Structural quality contract

Review complexity as a delta, not as a demand to rewrite every legacy
function. The checked-in policy is:

- Ruff C901 maximum 10
- complexipy cognitive maximum 10
- Ruff PLR0912 maximum 12 branches
- Ruff PLR0915 maximum 50 statements
- CRAP has no absolute ceiling and may not worsen for a baseline entry
- function length above 100 lines is a review signal
- file length above 500 lines is a review signal, not a split requirement

New functions must stay within the cyclomatic and cognitive ceilings. An
existing function above a ceiling may remain unchanged or improve. It may not
worsen. Sub-threshold increases are review context, not automatic defects.

Do not approve a helper solely because it lowers one function's score. Read the
caller and helper together. The helper must own a named policy, lifecycle,
side-effect, parsing, or recovery responsibility. Guard clauses, early returns,
and lookup tables are preferred only when they make the behavior easier to
follow.

## Simplicity standard

For every non-mechanical change, ask these questions:

1. Could the changed code be deleted instead?
2. Does the repository already contain the needed helper, contract, or parser?
3. Does the Python or JavaScript standard library already solve it?
4. Does the current platform or installed dependency already solve it?
5. Can direct control flow express the behavior more clearly?
6. Is an abstraction backed by a real repeated concept?
7. Does each new file have an independent responsibility and lifecycle?
8. Would a human engineer understand the change without reading several
   pass-through wrappers?

Prefer deletion over addition, existing code over new code, direct control
flow over indirection, and boring code over clever code.

Reject:

- an interface with one implementation and no external boundary
- a factory for one product
- configuration for a value that never varies
- a service that only forwards to another function
- a helper created only to lower complexity
- a boolean or mutable list used as an out-parameter
- a copied API contract instead of the canonical type
- a new dependency for behavior already available in the repository or
  standard library
- a file split justified only by line count
- comments that restate code rather than explain a constraint
- speculative hooks, options, or future-proofing

Do not demand an abstraction merely because two code fragments look similar.
They must represent the same concept and be expected to change together.

## Review sequence

### 1. Pin the batch contract

Read the complete batch section in `docs/lint-cleanup-plan.md`. Convert its
requirements into a checklist containing:

- owned files and explicit exclusions
- behavior that must remain unchanged
- allowed contract changes
- focused tests
- full affected suite
- lint and quality gates
- expected stop condition

Do not let the worker's implementation redefine the requirement.

### 2. Establish scope integrity

Compare the worker's manifest with the actual changed files. Report every
changed file that is outside the batch. Determine whether it is:

- required by a real caller or contract change
- a test needed to protect changed behavior
- an unrelated cleanup
- a configuration, baseline, artifact, or documentation change

Required supporting files may be accepted only when the reason is concrete.
Unrelated cleanup must be reverted or moved to its assigned batch.

Check:

```powershell
git status --short
git diff --stat <fixed-point> -- <owned and supporting paths>
git diff --check <fixed-point> -- <owned and supporting paths>
git diff <fixed-point> -- pyproject.toml frontend/eslint.config.mjs frontend/.oxlintrc.json quality/baseline.json
```

When no fixed point exists, inspect final file state and disclose that scope
isolation is unproven.

### 3. Understand behavior before judging structure

Read each changed production function, its callers, and its focused tests.
Trace the real path from input boundary to canonical owner to output. Search
for existing abstractions before approving a new one.

For a bug or behavior repair, confirm that the fix is at the shared root cause
rather than copied into one caller. A small patch in the wrong layer is not a
simple solution.

### 4. Review specification correctness

Look for:

- missing or partial requirements
- behavior that contradicts the batch invariants
- behavior added without authorization
- deleted or weakened fallback behavior
- changed output contracts
- changed retry, timeout, cache, or error semantics
- stale flags, modes, docs, tests, or schemas left after deletion
- new external calls or expanded authorization
- hidden changes to user choice or recommendation behavior

Verify the implementation, not merely the test name.

### 5. Review architecture and data ownership

Reject any change that:

- calculates canonical itinerary facts in the frontend
- duplicates a backend response or domain contract
- moves normalization into a React component
- invents live data or converts missing evidence into certainty
- bypasses an existing provider HTTP boundary
- creates a parallel API, store, cache, model, or temporary architecture
- logs secrets, raw provider payloads, prompts, coordinates, or unnecessary
  conversation data
- exposes internal scoring, prompts, reasoning, or provider responses
- changes generated transit artifacts by hand

Confirm that the backend still owns timing, duration, transfers, dwell,
ranking, selection, confidence, and recommendation facts.

### 6. Review error handling

For every changed catch or raise:

- identify the errors the boundary can recover from
- verify the fallback is intentional
- verify the cause is retained when translating an error
- verify the error is logged at most once at the layer with useful context
- verify secrets and payloads are not included
- reject logging followed by undefined continuation
- reject a wrapper exception created only to satisfy Ruff

Broad `Exception` catches require a real fail-open or redaction boundary and a
documented reason. Do not narrow a reviewed provider or telemetry catch if that
would make a passenger request fail.

### 7. Review tests

Compare test counts and skip counts before and after. Read changed assertions
instead of trusting a passing count.

Reject:

- deleted tests without an explicitly deleted feature
- new skips, xfails, or conditional bypasses
- weaker assertions
- changed operand order that changes failure meaning
- `pytest.raises` around code that was not intended to fail
- a mock that no longer verifies call arguments or call count
- a test that only mirrors implementation details
- coverage created by an unrelated focused run and reused as final evidence
- a deterministic test replaced by a live call

Require failure and fallback paths for provider, stream, cache, parser, and
state-machine changes.

For order-sensitive Python batches, run the owned files in forward and reverse
order. Import pollution or module replacement must not depend on file order.

### 8. Review lint repairs for metric gaming

Search the changed paths:

```powershell
rg -n "noqa|eslint-disable|@ts-(ignore|nocheck)|as unknown as|SAFETY:|type: ignore" <changed paths>
```

Reject:

- new broad suppressions
- a disabled or weakened rule
- a broader per-file ignore
- a complexity threshold changed from the documented 10, 12, and 50 policy
- a baseline value increased to absorb a regression
- `# complexipy: ignore` or `# noqa: complexipy`
- `Any`, `unknown`, unsafe dictionaries, or assertions used to silence types
- repeated parsing moved to callers to reduce one function's complexity
- one branch split across meaningless helpers
- pass-through wrappers or one-use services
- conditions rewritten into opaque expressions to manipulate a metric
- test removal used to change coverage or CRAP

Also inspect total churn, net production lines, and production function count.
The thresholds in `docs/lint-cleanup-plan.md` are review triggers. They require
an explanation or a smaller checkpoint. They are not automatic reasons to
reject an otherwise cohesive change.

A `SAFETY:` comment is valid only when a runtime check proves the invariant and
TypeScript cannot express the narrowed result. The comment must name the check.

### 9. Reproduce the gates independently

Run the smallest focused test first, then the complete owned suite, then the
full affected suite. Run the configured linter on the exact owned paths.

At the end of every batch run fresh quality coverage:

```powershell
py scripts/check_quality.py --quality-ref <fixed-point>
```

Do not certify with `--skip-tests`. For a quality-gate repair or a suspected
coverage-order issue, require two consecutive fresh runs with identical
ratchet results.

For backend completion, require:

```powershell
py -m ruff check --config pyproject.toml <owned paths>
py scripts/check_quality.py --cognitive-only --quality-ref <fixed-point>
py -m pytest <owned tests> -q --basetemp <unique worktree path>
py -m pytest backend/tests -q --basetemp <unique worktree path>
py scripts/check_quality.py --quality-ref <fixed-point>
```

The worker must not run `--update-baseline`. If the accepted patch makes
entries stale, the reviewer may run a fresh baseline update after all other
gates pass. Inspect the baseline diff and rerun quality before approval.

For frontend completion, require the applicable subset and then the full
configured gates:

```powershell
Set-Location frontend
npm run lint
npm run lint:oxlint
npm run typecheck
npm run typecheck:scripts
npm run test:unit
npm run verify:transit-artifacts
```

Do not accept a worker's claim that a command passed when the reviewer cannot
reproduce it. Report invocation or environment failures separately from code
failures.

### 10. Inspect generated artifacts and inventories

If a transit builder changed, regenerate through the documented scripts.
Inspect the generated diff for route identity, geometry, station relationships,
official colors, stable ordering, and manifest changes. A lint-only refactor
should normally leave generated artifacts unchanged.

Regenerate Ruff, Oxlint, and ESLint inventories. Confirm:

- the owned count is zero
- no rule disappeared because configuration changed
- global counts did not increase outside the batch
- file and rule totals reconcile with the handoff
- resolved quality entries are genuinely stale before removal
- C901, PLR0912, and PLR0915 use ceilings 10, 12, and 50
- cognitive complexity has no new or worsened function above 10
- CRAP has no worsened baseline entry
- a long function or file was reviewed for cohesion rather than split by reflex

## Product invariants to check in every relevant batch

### Canonical routes

- One backend-owned canonical itinerary feeds chat, cards, route steps, and map.
- Frontend code renders facts and does not recalculate them.
- Candidate identity and selected-route identity remain stable.
- Walking, transfers, waypoints, dwell, and timing provenance remain intact.

### Agent behavior

- Capability choice remains model-led.
- Tool availability is state-scoped, not hidden by regex.
- The user owns the destination decision.
- One turn produces one valid terminal outcome.
- Cancellation, continuation, and session restoration remain reliable.
- Raw prompts, reasoning, model responses, and provider payloads remain private.

### Damn Lines

- Queue lookup follows registry matching or an explicit covered-venue request.
- Live information wins over historical trends.
- Historical trends are a fallback for an open monitored venue with no live
  coverage or a direct question about usual conditions.
- The agent never calculates or predicts an unprovided queue time.
- Queue information stays conversational and in sources.
- Queue information does not appear on the map or route card.
- The empty registry cache refreshes. The normal cache remains five minutes.
- PromptKit sources retain favicon behavior and Google attribution remains
  after recommendation prose.

### Providers and live data

- Missing evidence is not confirmed safety.
- Cached or historical data is labeled accurately.
- Provider failures use the documented fallback.
- Timeouts and retry counts do not expand without authorization.
- No live test runs unless the user explicitly authorizes it.

### Transit artifacts

- Official MTA colors remain exact.
- Shared corridors and station-to-line relationships remain correct.
- Generated artifacts are never hand-edited.
- Geographic thresholds are not loosened to hide a bad transformation.
- Output remains deterministic.

## Finding standard

Every finding must include:

- severity
- concise title
- exact file and tight line range
- the violated requirement or standard
- the concrete failure mode
- evidence from code, test, or tool output
- the smallest acceptable repair direction

Do not write vague findings such as "clean this up" or "consider refactoring."
Do not present a question or preference as a confirmed defect. Group repeated
symptoms under their shared root cause.

## Severity

### P0

Immediate security, secret exposure, destructive data loss, corrupted
canonical transit data, or an external action outside user authorization.

### P1

A failed required gate, behavior regression, incorrect passenger result,
broken canonical contract, false completion claim, unauthorized provider
request, or missing core requirement.

### P2

A partial requirement, unsafe fallback, stale public contract, type-boundary
failure, order-dependent test, substantial scope creep, or maintainability
defect likely to cause a future correctness problem.

### P3

Local slop, misleading documentation, unnecessary indirection, duplication,
or a readability defect. A P3 blocks approval when the batch introduced it or
the batch explicitly owns that lint or cleanup surface. Proven pre-existing
out-of-scope P3 debt is recorded but does not expand the batch.

## Verdicts

Use exactly one verdict:

- `APPROVED`: isolated diff is reviewable, every required gate passes, owned
  lint is zero, complexity deltas are green, and no blocking finding remains.
- `PROVISIONAL FINAL-STATE PASS`: final state passes, but no fixed point exists
  to prove the isolated batch diff or absence of scope creep.
- `REJECTED`: at least one P0, P1, P2, blocking P3, failed gate, or false claim
  remains.
- `BLOCKED`: required evidence or environment is unavailable and the reviewer
  cannot reach a defensible verdict.

Do not use "approved with caveats." A remaining blocking caveat is a rejection.
Non-blocking notes must be proven pre-existing and outside the owned scope.

## Reviewer response format

Use this exact order:

```text
Outcome
One sentence with the verdict and the most important reason.

Findings
Ordered P0 to P3. Put findings before summaries.

Specification review
State which batch requirements passed or failed.

Standards and simplicity review
State whether architecture, human readability, and the simplicity standard pass.

Independent gate evidence
List exact commands, exit codes, test counts, lint counts, and quality result.

Scope integrity
List out-of-scope files, config changes, baseline changes, suppressions, and artifacts.

Worker repair handoff
Give a bounded ordered repair list with acceptance criteria.

Verdict
APPROVED, PROVISIONAL FINAL-STATE PASS, REJECTED, or BLOCKED.
```

If there are no findings, write `No actionable findings.` Do not invent minor
feedback to make the review look thorough.

## Worker repair loop

When the verdict is `REJECTED`:

1. Send only the bounded findings and acceptance criteria to the worker.
2. Do not ask the worker to clean adjacent code.
3. Require the worker to report changed files and exact rerun results.
4. Reinspect the repair diff and rerun the failed focused gates.
5. Rerun the full batch gates after focused gates pass.
6. Issue a new complete verdict.
7. Stop after approval. Do not tell the worker to start the next batch.

If a repair would require a contract change, new dependency, broader scope,
baseline increase, configuration change, live external call, or destructive
operation, stop and ask the user. The reviewer and worker cannot authorize
those expansions themselves.

## Final rule

The goal is not code that merely silences tools. The goal is the smallest
correct code a human engineer can read, test, and safely change later. A batch
is done only when behavior is preserved, the owned lint is zero, the quality
and cognitive ratchets are green, and the diff is simpler than the code it
replaced.

