# Lint cleanup handoff

Use this reference for the current lint inventory in the
`damn-lines-integration` worktree. Execute remaining batches with
`docs/lint-cleanup-plan.md`. Review a finished batch with
`docs/lint-cleanup-review-spec.md`.

The trusted application checkpoint is `427fbc8`. The quality-policy update
after that reset changes tooling and documentation only. It does not change
application behavior. Regenerate the reports below before editing a batch.

## Current result

The accepted 6F checkpoint is `16390f3`. It contains Batches 2 through 6F, F0,
and the backend test-assurance work. Batch F1 is Codex-approved on the tree
from that checkpoint at `d2ecdfe3bf43397aee2051c84ac0bf4544d8cfc0`. Codex
approved Batch 7 on the tree from that fixed point. Batch 8 is implemented
and uncommitted for Codex. Start Batch 9 only from the accepted Batch 8
commit. Workers must not shrink stale baseline entries.

| Tool | Findings | Files | Notes |
|---|---:|---:|---|
| Ruff | 0 | 0 | Backend production and tests |
| Ruff C901 | 0 | 0 | McCabe ceiling 10 |
| Ruff PLR0912 | 0 | 0 | Branch ceiling 12 |
| Ruff PLR0915 | 0 | 0 | Statement ceiling 50 |
| Backend combined complexity | 91 | | Radon or cognitive 11 through 12, none above 12 |
| Backend combined coverage | 89.34% | | Full `backend/app` statement and branch denominator |
| Backend CRAP above 30 | 0 | 0 | Public behavior tests closed the prior evidence gap |
| Oxlint complexity above 12 | 69 | | Batch 7: 0, Batch 8: 0, Batch 9: 69 |
| Quality baseline | 101 | | Batch 7 reviewer removed exactly 10 proven-stale TypeScript IDs |

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
| Batch 6C worker 2026-08-29 | 1 | 0 | 0 | 38 | 198 | 1,894 passed, 21 skipped, 444 subtests | 314 |
| Batch 6C reviewer final 2026-08-29 | 0 | 0 | 0 | 0 | 198 | 1,895 passed, 21 skipped, 444 subtests | 314 |
| Batch 6D worker 2026-08-30 | 1 | 0 | 0 | 31 | 198 | 1,895 passed, 21 skipped, 444 subtests | 314 |
| Batch 6D reviewer final 2026-08-30 | 0 | 0 | 0 | 0 | 168 | 1,896 passed, 21 skipped, 444 subtests | 314 |
| Batch 6E worker 2026-08-30 | 1 | 0 | 0 | 43 | 168 | 1,899 passed, 21 skipped, 444 subtests | 314 |
| Batch 6E reviewer final 2026-08-30 | 0 | 0 | 0 | 0 | 125 | 1,899 passed, 21 skipped, 444 subtests | 314 |
| F0 worker 2026-08-30 | 1 | 68 | 0 | 12 | 125 | 1,899 passed, 21 skipped, 444 subtests | 557 |
| F0 Codex repair 2026-08-30 | 1 | 0 | 0 | 12 | 125 | 1,899 passed, 21 skipped, 444 subtests | 557 |
| F0 reviewer final 2026-08-30 | 0 | 0 | 0 | 0 | 113 | 1,899 passed, 21 skipped, 444 subtests | 557 |
| Backend test assurance 2026-08-31 | 0 | 0 | 0 | 0 | 113 | 1,917 passed, 21 skipped, 446 subtests | 557 |
| Batch 6F reviewer final 2026-08-31 | 0 | 0 | 0 | 0 | 113 | 1,915 passed, 21 skipped, 446 subtests | 557 |
| F1 worker 2026-08-31 | 1 | 0 | 0 | 2 | 111 | 1,915 passed, 21 skipped, 446 subtests | 551 |
| F1 reviewer final 2026-08-31 | 0 | 0 | 0 | 0 | 111 | 1,915 passed, 21 skipped, 446 subtests | 551 |
| Batch 7 worker 2026-08-31 | 1 | 0 | 0 | 10 | 111 | 1,915 passed, 21 skipped, 446 subtests | 770 |
| Batch 7 reviewer final 2026-08-31 | 0 | 0 | 0 | 0 | 101 | 1,915 passed, 21 skipped, 446 subtests | 770 |
| Batch 8 worker 2026-09-01 | 1 | 0 | 0 | 42 | 101 | 1,915 passed, 21 skipped, 446 subtests | 1,084 unit plus 14 Playwright |

Cognitive delta against `427fbc8`: 0 new or worsened. CRAP has no absolute
ceiling. Baseline entries may not worsen.

The route-intelligence batch, Batch 0, and Batch 1 are complete inside
`427fbc8`. Batch 2 is independently APPROVED at `368c00d`. Batch 3 production
and the handoff repair are Codex-approved. Batch 4 owned tools Ruff is zero.
Batch 5 is committed at `22f6f0d` and reviewer-approved. Batch 6 is committed
at `c058199`. That commit is the fixed point for Batches 6A through 6E. Batch
6A is committed at `140495a`. Batch 6B is committed at `c8a0381`. Batch 6C
review is complete on the tree based on `c8a0381`. Batch 6D is committed at
`2298e32`. Batch 6E is Codex-approved on the tree based on that checkpoint.
Batch 6E is committed at `ae27211`. F0 is committed at `9b4327d`. The backend
test-assurance checkpoint is `ac96d12`. Batch 6F is Codex-approved on the tree
based on that checkpoint. Batch 6F is committed at `16390f3`. F1 is
Codex-approved on the tree from that checkpoint at
`d2ecdfe3bf43397aee2051c84ac0bf4544d8cfc0`. Codex approved Batch 7 on the
tree from that fixed point. Start Batch 8 only from the resulting Batch 7
commit.

## Structural policy

- Ruff C901 maximum 10
- Frontend JavaScript and TypeScript cyclomatic maximum 12
- complexipy cognitive maximum 10 for new Python functions, no worsening above 10
- Ruff PLR0912 maximum 12 branches
- Ruff PLR0915 maximum 50 statements
- CRAP is a coverage-aware delta for existing baseline debt
- Function length above 100 lines and file length above 500 lines are review
  signals, not split requirements
- Workers must not run `--update-baseline`
- Codex alone may shrink `quality/baseline.json` after review

## Remaining batches

The old 20-batch plan is retired. Batches 2 through 6F, F0, and F1 are
complete. Batch 7 is Codex-approved. Batch 8 is implemented and uncommitted for Codex. Batch 9 is next.

| Batch | Subsystem |
|---:|---|
| 2 | Realtime foundation and providers |
| 3 | Canonical trip planning |
| 4 | Agent capability tools |
| 5 | Agent orchestration and state |
| 6 | Backend test style remainder, committed at `c058199` |
| 6A | Realtime, MTA, incidents, and routers |
| 6B | Canonical trips |
| 6C | Agent place, route, and shared tools |
| 6D | Agent transit tools |
| 6E | Agent model, session, and turn |
| 6F | Proven backend dead-code deletion, Codex-approved from `ac96d12` |
| F0 | Frontend quality foundation, committed at `9b4327d` |
| F1 | Correct aggregate coverage semantics and delete proven dead frontend code. Codex-approved from `16390f3` |
| 7 | `frontend/app/**` and `frontend/lib/**` |
| 8 | `frontend/components/**` and `frontend/tests/release/**` |
| 9 | `frontend/scripts/**` |

## F0 worker result

Fixed point `ae27211fa9ba`. F0 was reviewed and committed at `9b4327d`.
Batch 7 production files stayed unchanged. The artifact-manifest generator
now hashes LF-normalized GeoJSON bytes so Windows CRLF checkouts do not
rewrite hashes.

F0 sets the frontend measurement contract used by Batches 7 through 9.

- Frontend cyclomatic ceiling 12. Oxlint and
  `scripts/js_function_metrics.mjs --authored` are exhaustive for every
  authored JS/TS function, including tests and `frontend/tools/run-unit-tests.mjs`.
  ESLint remains application-focused and ignores `scripts/**`, `tools/**`,
  and `*.test.mjs`. Lifecycle exclusions: generated, vendored
  `tools/oxlint/**`, declarations, dependencies, and build output.
- Python cyclomatic ceiling remains 10. `MAX_COMPLEXITY` was not replaced
  with 12.
- Production coverage denominator is authored production under
  `frontend/app/**`, `frontend/lib/**`, `frontend/components/**`, and
  `frontend/scripts/**`. Authored complexity includes tests and tools.
- Exclusions live in `scripts/frontend_quality_scope.json` by lifecycle.
- `frontend/tools/run-unit-tests.mjs` owns unit selection. Coverage wraps
  that same process.
- Native Node 23 V8 coverage cannot remap TypeScript or include missing
  files. `c8@12.0.0` is the coverage-specific dependency that does both.
- Playwright does not contribute source-mapped `.ts` and `.tsx` coverage.
  That remains a Batch 8 prerequisite.
- `npm run verify:transit-artifacts` owns `scripts/build-artifact-manifest.test.ts`.

Honest production baseline from
`py scripts/report_frontend_debt.py --quality-ref ae27211fa9ba`:

- production files 226
- line coverage 52.27% (23449 / 44859)
- exact branch coverage unresolved while 114 production files are unexecuted
- branch proxy upper bound 56.06% (4742 / 8459), mixed c8 and McCabe units
- historical authored-mapper ratio 34.40% (847 / 2462), superseded as an
  aggregate gate by the audit below
- mapped-only authored ratio 47.75% (diagnostic only)
- c8 source-mapped function coverage 82.27% (1601 / 1946), not yet
  gate-eligible while production files remain unexecuted
- unexecuted files 114
- measured functions 847
- uncovered functions 927
- unresolved functions 688
- authored functions above 12: 126 (Batch 7: 10, Batch 8: 46, Batch 9: 70)
- production functions above 12: 122
- unassigned production files 0
- production line growth +15 / -8, net +7, including untracked production files

Per-batch production coverage, none at the 95% gate yet. Branch percentages
below are proxy upper bounds. The exact branch gate stays unresolved.

| Batch | Lines | Branch proxy | Confirmed functions | Above 12 |
|---:|---|---|---|---:|
| 7 | 55.85% (3387 / 6064) | 68.81% (728 / 1058) | 25.13% (99 / 394) | 10 |
| 8 | 39.72% (6864 / 17283) | 48.52% (1493 / 3077) | 25.82% (235 / 910) | 46 |
| 9 | 61.35% (13198 / 21512) | 58.30% (2521 / 4324) | 44.30% (513 / 1158) | 70 |

Unit tests: 557 passed. Release: 14 passed, 4 skipped.
`scripts/build-artifact-manifest.test.ts` is on `verify:transit-artifacts`
and passes. GeoJSON was not hand-edited. The generator hashes LF-normalized
bytes so the committed manifest hashes stay `ea2e0dc306ac`, `7dcef6a621f9`,
and `0775d322d828`.

The 2026-08-30 audit supersedes F0's aggregate `confirmed function` gate.
Standard source-mapped c8 function coverage becomes exact only after all
owned production files execute. The authored mapping remains diagnostic.
Frontend target after Batch 9 is at least 95% line, exact c8 branch, and exact
c8 function coverage, subject only to a reviewer-recorded non-behavioral
exception. Codex alone may shrink `quality/baseline.json`.

The worker quality run against `ae27211fa9ba` exited 1 with
`approval_eligible: false`.
Tests ran. Backend 1,899 passed, 21 skipped, 444 subtests. Cognitive new or
worsened is 0. New cyclomatic 0. Worsened cyclomatic 0. Stale 12 TypeScript
baseline entries now at or below 12. Pre-existing TypeScript scope debt is
72 functions already over 12 at `ae27211` (68 generators plus 4 authored
tests). Scope-debt adoption requires an explicit `--quality-ref`. The plain
`py scripts/check_quality.py` command treats unbaselined violations as new
debt. Shrinking the baseline requires
`py scripts/check_quality.py --quality-ref ae27211fa9ba --update-baseline`.
They are not new product code and were not written into
`quality/baseline.json`. The worker did not run `--update-baseline`.

Reviewer final, 2026-08-30. Codex accepted the isolated F0 diff and removed
exactly those 12 proven-stale TypeScript entries. `quality/baseline.json`
decreased from 125 to 113 entries. No entry was added or changed. The full
quality command against `ae27211fa9ba` exits 0 with `approval_eligible: true`,
557 frontend tests, 1,899 backend tests, 21 skipped backend tests, and 444
backend subtests. New, worsened, cognitive, Ruff C901, Ruff structural, and
stale violations are all 0. The 72 TypeScript functions already above 12 at
the fixed point remain explicit scope debt and were not added to the baseline.

## Backend test assurance (2026-08-31)

The test-assurance checkpoint supersedes the backend coverage and CRAP counts
from the earlier audit below. The full branch-coverage run passed with 1,917
tests, 21 skips, and 446 subtests. Combined statement and branch coverage is
89.14%. Statement coverage is 91.77% (20,419 / 22,251). Branch coverage is
81.64% (6,360 / 7,790). The debt report has 2,437 authored functions, 50 with
zero coverage, and none with CRAP above 30.

The public area-condition projection tests now protect passenger-safe fields,
malformed-row filtering, both eight-item collection bounds, resolved-area
fallback, and evidence statuses. Batch 6F must not duplicate them. The audit
also found and fixed a dropped route id in the explicit crowd-request fallback.
See `docs/backend-test-quality.md` for the coverage report, the risk buckets,
the Cosmic Ray canary, and the 85% CI floor.

## Batch 6F reviewer final (2026-08-31)

Batch 6F started from `ac96d12`. The repository-wide call-site audit confirmed
that every owned candidate was dead. The accepted tree deletes all 13
zero-covered compatibility surfaces, the unreachable first-boarding timing
subtree, the two tests that only exercised that subtree, and imports, exports,
private data, and stale comments made obsolete by those deletions.

The change removes 329 net backend production lines and 22 production
functions. It adds no production code, module, dependency, or replacement
abstraction. Canonical itinerary projection still owns live route clocks.
Frontend production, `quality/baseline.json`, Cosmic Ray configuration, and the
85% CI floor are unchanged.

Fresh verification reports 1,915 backend tests passed, 21 skipped, and 446
subtests. Combined statement and branch coverage is 89.34%. Statement coverage
is 91.96% (20,336 / 22,113). Branch coverage is 81.86% (6,346 / 7,752). The
regenerated debt report has 2,415 authored functions, 37 with zero coverage,
none above 12, and none with CRAP above 30. Full quality against `ac96d12` exits
0 with `approval_eligible: true`. The only raw backend Ruff finding is the
fixed-point import-order issue in `backend/scripts/phase2_quality_report.py`;
Batch 6F did not edit that out-of-scope script.

## F1 worker result (2026-08-31)

Fixed point `16390f3`. F1 is uncommitted. Batch 7 was not started. The worker
did not update `quality/baseline.json`.

F1A reports source-mapped c8 branch and function totals as the aggregate
metrics. Exact branch and function status stays `unresolved` while any owned
production file is unexecuted. Authored measured, uncovered, anonymous
unresolved, and named unresolved counts remain diagnostic. Line coverage still
counts an included but unexecuted production file as zero. Reports no longer
use `confirmed_function` approval language.

F1B deleted the six proven-dead production files, the abandoned geometry helper
test, three test-only Near You exports, and CSS owned by the deleted chat top
bar, Near You row, and floating tab toggle. `buildHomeNearbyModel` remains.
`near-you.test.ts` uses one small local `LeftRailLiveData` fixture.

Current frontend measurement from
`py scripts/report_frontend_debt.py --quality-ref 16390f3ccaca108d3acbe9eac6cb35d4950d2f65`:

- production files 220
- line coverage 51.86% (22746 / 43860)
- exact branch coverage unresolved while 110 production files are unexecuted
- branch proxy upper bound 55.76% (4649 / 8337)
- source-mapped c8 function coverage 83.03% (1580 / 1903), unresolved while
  production files remain unexecuted
- unexecuted files 110
- measured functions 839
- uncovered functions 914
- anonymous unresolved 602
- named unresolved 69
- authored functions above 12: 123 (Batch 7: 10, Batch 8: 44, Batch 9: 69)
- production functions 2424
- production line growth +5 / -1201, net -1196

Unit tests: 551 passed. Release CI: 14 passed, 4 skipped. Transit artifacts
unchanged. Quality against `16390f3` exits 1 with `approval_eligible: false`
because two baseline entries are now stale. New 0. Worsened 0. Cognitive new
or worsened 0. The worker did not run `--update-baseline`.

## F1 reviewer result (2026-08-31)

Codex repeated the import, symbol, and stale-comment searches. The worker's
deletions are valid. The only missed cleanup was two comment banners in
`chat-panel.tsx` and `line-badge.tsx` that still described the deleted top bar
and Near You row. Codex removed those banners instead of adding replacement
documentation. The final production diff is +5 / -1222 lines, net -1217.

Desktop and mobile theme controls were exercised against the running Next.js
application. Both changed the shell theme and their accessible action labels.
Unit tests passed 551. The release suite passed 14 with 4 skipped. Typecheck,
script typecheck, transit artifact verification, reporter self-tests, and the
quality-runner self-test passed. Raw ESLint has 53 inherited complexity
findings plus 8 inherited max-depth findings. Oxlint has 123 inherited
complexity findings. F1 added none.

Full quality against `16390f3` passed 1,915 backend tests, with 21 skipped and
446 subtests, and 551 frontend tests. It exits 0 with
`approval_eligible: true`. New, worsened, stale, cognitive-delta, Ruff C901,
and Ruff structural counts are all 0. Codex removed exactly these two
proven-stale baseline entries and changed no surviving entry:

- `typescript:frontend/components/smart-route/chat/near-you.ts:buildArrivalsPayloadForRoute#0`
- `typescript:frontend/components/smart-route/left-rail/demo-data.ts:demoArrival#0`

`quality/baseline.json` decreased from 113 to 111 entries. Batch 7 was not
started. Its worker must use the accepted F1 commit as the immutable fixed
point.

## Batch 7 worker result (2026-08-31)

Worker implementation. Codex is the reviewer. The worker did not update
`quality/baseline.json`, did not approve this batch, did not commit, and did
not start Batch 8.

### 1. Fixed point

Immutable quality reference:
`d2ecdfe3bf43397aee2051c84ac0bf4544d8cfc0`.

`git rev-parse HEAD` equals that SHA before and after the work.

### 2. Preserved user-owned dirty files

Initial status, left untouched:

```text
 M frontend/next-env.d.ts
?? frontend/CLAUDE.md
```

Final status is identical. The worker did not edit, stage, delete, restore,
stash, or commit either file.

### 3. Changed files

Production:

- `frontend/lib/canonical-itinerary-schema.ts` (new)
- `frontend/lib/trip-response.ts` (new)
- `frontend/app/page-parts.tsx`
- `frontend/app/page.tsx`
- `frontend/lib/agent-chat-controller.ts`
- `frontend/lib/agent-chat-event-validator.ts`
- `frontend/lib/agent-chat-session.ts`
- `frontend/lib/agent-chat-state.ts`
- `frontend/lib/agent-chat-stream.ts`
- `frontend/lib/agent-route-selection.ts`
- `frontend/lib/api.ts`
- `frontend/lib/backend-proxy-core.ts`
- `frontend/lib/backend-proxy.ts`
- `frontend/lib/backend-stream-proxy.ts`
- `frontend/lib/initial-geolocation.ts`
- `frontend/lib/live-feed-connection.ts`
- `frontend/lib/mapbox-search.ts`
- `frontend/lib/route-planning.ts`
- `frontend/lib/use-live-feed.ts`
- `frontend/lib/ws-ticket.ts`
- `frontend/types/api.ts` (canonical itinerary import path only)

Tests:

- `frontend/lib/trip-response.test.mjs` (new)
- `frontend/lib/mapbox-search.test.mjs` (new)
- `frontend/lib/owned-lib-boundaries.test.mjs` (new)
- `frontend/lib/owned-hooks.test.mjs` (new)
- `frontend/app/page-parts.test.mjs` (new)
- `frontend/app/api/owned-routes.test.mjs` (new)
- `frontend/app/manifest.test.mjs` (new)
- `frontend/app/owned-react-surfaces.test.mjs` (new)
- `frontend/lib/agent-route-selection.test.mjs`
- `frontend/lib/initial-geolocation.test.mjs`
- `frontend/lib/use-agent-chat.test.mjs`
- `frontend/lib/ws-ticket.test.mjs`

Docs and inventory:

- `docs/lint-cleanup-plan.md`
- `docs/lint-cleanup-handoff.md`
- `.audit/frontend-debt.json`

### 4. Backend fields verified for the canonical itinerary

Inspected backend `build_canonical_itinerary` and the existing SSE Zod policy
before defining frontend requirements. Guaranteed on a successful canonical
itinerary:

- `itinerary_id`
- `total_duration_seconds`
- `transfer_count`
- `legs`

A successful REST trip also guarantees at least one route candidate with
`id`, `index`, `steps`, `is_recommended`, `total_minutes`, `itinerary`, and
`score_breakdown.transfers`, plus `selected_route_index` matching a candidate.

Legitimately nullable or optional, kept nullable or optional:

- `departure_at`, `arrival_at`
- `total_walk_seconds`, `total_wait_seconds`, `total_in_vehicle_seconds`,
  `total_dwell_seconds`
- origin, destination, waypoints, segments, dwell events
- structured recommendation reasons and `selection_decision`
- candidate `recommendation_reason`, `rejection_reason`, enrichment flags

No UI-required canonical fact was missing from the backend contract. The
worker did not invent duration, timing, transfers, ranking, or recommendation
choice in the frontend.

### 5. Raw and validated frontend types

Raw untrusted shapes stay at the existing public contract:

- `TripResponse` and `RouteCandidate` in `frontend/types/api.ts`
- `CanonicalItinerary` in `frontend/lib/agent-route-card-contract.ts`

Validated post-parse types:

- `ValidatedCanonicalItinerary` requires `itinerary_id`,
  `total_duration_seconds`, `transfer_count`, and `legs`
- `ValidatedRouteCandidate` requires `total_minutes`, `itinerary`, and
  `score_breakdown.transfers`
- `ValidatedTripResponse` requires `selected_route_index` and
  `route_candidates: ValidatedRouteCandidate[]`

JSON null on candidate clocks is stripped to absent so the validated type
remains a subtype of the public raw type. Itinerary clocks may still be JSON
null. `planTrip` parses `res.json()` as `unknown` and rejects through
`TRIP_PLAN_FAILED` (`Failed to plan trip`). It does not use `as TripResponse`,
`any`, a non-null assertion, or an empty successful plan.

### 6. Shared Zod schema owner

`frontend/lib/canonical-itinerary-schema.ts` owns `canonicalItinerarySchema`
and `parseCanonicalItinerary`. The SSE route-card validator and
`frontend/lib/trip-response.ts` reuse that module. The REST client does not
import the complete SSE event validator. `frontend/types/api.ts` imports
`CanonicalItinerary` from `agent-route-card-contract.ts`, not from
`agent-chat-stream.ts`.

### 7. Cluster invariants

7A. One canonical route boundary. Malformed REST JSON, a missing itinerary,
malformed duration totals, a malformed transfer count, and a selected index
with no matching candidate all throw `Failed to plan trip`. A valid response
returns `ValidatedTripResponse`. SSE `route_card` events parse itineraries
with the same schema. `normalizeTripCandidates` and agent route-card
selection return the validated candidate type.

7B. `applyAgentEvent` uses typed reducers for session and turn start,
streamed content, tool lifecycle, route and arrival cards, and terminal
outcomes. `ChatReducerAction` stays a discriminated union with a `never`
exhaustive check. Stale-turn events are ignored. Tool and card updates stay
unique by id. A turn receives at most one terminal result. Cancellation stays
cancellation. Clarification and failure remain distinct. Route cards and
arrival cards replace by identity.

7C. `runTurn` keeps retry, expired-session recovery, abort cleanup,
active-request cleanup, session replacement, dropped-stream classification,
meta-event timing, and one terminal callback. One network attempt returns
`TurnAttemptOutcome` (`ended`, `cancelled`, `transport_error`). Mapbox,
backend proxy, stream proxy, WebSocket ticket, session, and live-feed
boundaries use guard clauses and named parse or recovery policies. Timeouts,
abort signals, status codes, streaming headers, redaction, and retry counts
are unchanged. GET `/api/service-alerts` still does not pass `req` into
`proxyToBackend`. That pre-existing identity skip was not changed.

7D. `page-parts.tsx` gained independently testable ownership for rail status,
fullscreen toggle, destination coordinates from a route, route-card lookup,
and nearby-station selection. The page layout and interaction design did not
change. Remaining owned functions above complexity 12 are 0.

### 8. Focused tests and results

```powershell
npx tsx --test lib/trip-response.test.mjs
npx tsx --test lib/use-agent-chat.test.mjs
npx tsx --test lib/mapbox-search.test.mjs
npx tsx --test app/page-parts.test.mjs
npx tsx --test app/api/owned-routes.test.mjs lib/owned-lib-boundaries.test.mjs app/owned-react-surfaces.test.mjs lib/owned-hooks.test.mjs
```

All focused files passed. Invert proof for 7A: `total_duration_seconds` was
temporarily optional in the shared itinerary schema. The malformed-duration
test failed because it expected `Failed to plan trip` and did not throw. The
check was restored and the test passed again. The mutation is not in the tree.

### 9. Forward and reverse order

The stub-heavy files share `fetch`, `document`, and `Module._load`. Both
orders passed 117 tests:

- forward: hooks, react surfaces, lib boundaries, owned routes
- reverse: owned routes, lib boundaries, react surfaces, hooks

### 10. Unit, coverage, release, artifact, lint, and quality

From the repository root unless noted:

- `npm --prefix frontend run typecheck`: pass
- `npm --prefix frontend run typecheck:scripts`: pass
- `npm --prefix frontend run test:unit`: 770 passed
- `npm --prefix frontend run test:coverage`: used by the quality runner
- `npm --prefix frontend run test:release:ci`: 14 passed, 4 skipped
- `npm --prefix frontend run verify:transit-artifacts`: pass
- `py scripts/check_quality.py --self-test`: pass
- `py scripts/report_frontend_debt.py --self-test`: pass
- `node scripts/js_function_metrics.mjs --self-test`: pass
- `py scripts/report_frontend_debt.py --quality-ref d2ecdfe3bf43397aee2051c84ac0bf4544d8cfc0 --output .audit/frontend-debt.json`: wrote the Batch 7 inventory below
- `py scripts/check_quality.py --quality-ref d2ecdfe3bf43397aee2051c84ac0bf4544d8cfc0`: `tests_ran: true`, `approval_eligible: false` only because of the 10 stale baseline IDs listed in item 14. New 0. Worsened 0. Cognitive new or worsened 0. Ruff C901 0. Ruff structural 0.
- `git diff --check`: no whitespace errors
- `npm --prefix frontend run lint`: 43 complexity findings, 0 max-depth. Zero in `frontend/app/**` or `frontend/lib/**`. Remaining findings are Batch 8 and Batch 9.
- `npm --prefix frontend run lint:oxlint`: 113 complexity findings. Zero in `frontend/app/**` or `frontend/lib/**`.

Transit artifacts and manifest hashes did not change.

Starting inventory at the F1 commit matched the accepted F1 Batch 7 report:
50 production files, 394 production functions, 733 authored functions, 10
above complexity 12, 3 at 11 or 12, 21 unexecuted files, line coverage
55.85% (3387 / 6064). Regenerated ESLint was 53 complexity and 8 max-depth.
Regenerated Oxlint complexity was 123, with Batch 7 owning 10. After Batch 7,
those 10 Oxlint findings and the Batch 7 ESLint complexity and max-depth
findings are gone. Repository ESLint complexity is 43. Repository Oxlint
complexity is 113. The 8 inherited max-depth findings were in owned Batch 7
files.

### 11. Final Batch 7 coverage

Exact, because unexecuted owned production files are 0. Two consecutive
`npm --prefix frontend run test:coverage` runs produced identical totals:

- line 98.85% (6472 / 6547)
- branch 95.04% (1609 / 1693)
- function 98.31% (407 / 414)

The remaining 84 unhit branch arms are mostly c8 line-1 export-name
instrumentation, exhaustive `never` defaults, and a few unreachable guards
after validated state. They are not required to close the 95% gate.

### 12. Final Batch 7 complexity

- production files 52
- production functions 465
- authored functions 1674
- above 12: 0
- at 11 or 12: 7
- unexecuted files: 0
- high-CRAP diagnostic signals: 0

The original 3 functions at 11 or 12 were not refactored only to lower a
number. The new 11-or-12 count is from splitting former functions that were
above 12.

`frontend/lib/agent-chat-state.ts` is 528 lines. It remains one ChatState
reducer module: turn types, `ChatReducerAction`, and the five protocol
reducers must be read together. Splitting it would create pass-through files.

### 13. Production growth

Tracked plus untracked owned production vs `d2ecdfe`: +1369 / -886, net +483,
under the 500-line stop. Production functions 394 to 465, net +71, above the
25-function stop. The shared schema and REST parser remain the measured
reason. `canonical-itinerary-schema.ts` is 362 lines and `trip-response.ts`
is 107 lines.

The shared schema and REST parser remain the largest new files. Typed
reducers, `TurnAttemptOutcome`, and named parse or recovery policies account
for the function growth. The worker finished 7A through 7D because stopping
mid-cluster would leave the shared schema and reducers incomplete, then
stopped adding further production.

### 14. Stale baseline IDs for Codex

The worker did not run `--update-baseline`. Codex may shrink these 10
TypeScript entries after review:

- `typescript:frontend/app/page.tsx:SmartRoutePageContent#0`
- `typescript:frontend/lib/agent-chat-controller.ts:runTurn#0`
- `typescript:frontend/lib/agent-chat-event-validator.ts:parseAgentEvent#0`
- `typescript:frontend/lib/agent-chat-session.ts:parseSnapshot#0`
- `typescript:frontend/lib/agent-chat-state.ts:applyAgentEvent#0`
- `typescript:frontend/lib/agent-chat-stream.ts:parseSseFrame#0`
- `typescript:frontend/lib/backend-proxy-core.ts:readJsonBody#0`
- `typescript:frontend/lib/backend-proxy.ts:proxyToBackend#0`
- `typescript:frontend/lib/backend-stream-proxy.ts:streamProxyToBackend#0`
- `typescript:frontend/lib/mapbox-search.ts:retrieveMapboxSuggestion#0`

### 15. Review findings

1. Scope and behavior vs `d2ecdfe`. Applied. Diff stays in `frontend/app/**`,
   `frontend/lib/**`, the one `types/api.ts` import path, focused tests, the
   audit file, and these two docs. Passenger-visible layout did not change.
   Release chat, shell, map handoff, and accessibility tests passed.
2. Type-system and boundary. Applied. One shared itinerary schema. Raw types
   stay at the untrusted contract. Validated types are required after parse.
   REST does not import the SSE event validator.
3. Simplify and ponytail. Applied by deletion first. Reverted a
   `interpretServiceAlertMessage` extract from `use-service-alerts.ts` because
   it added production lines without an independently testable policy.
   `page-parts.tsx` keeps named policies, not a prop-bag move.
4. Testing on the Toilet. Applied. Tests call public parse, reducer, `runTurn`,
   proxy, and hook APIs. No source-text assertions. Node CSS and Next stubs
   exist so `layout.tsx`, `page.tsx`, and `manifest.ts` can execute. Those
   tests still assert metadata, shell render, and client mount.
5. Performance. No extra request-time work beyond one boundary parse that
   replaced the `as TripResponse` cast. Reducers are pure. Page composition
   did not add rerender subscriptions.
6. Comments. Kept the proxy note that browser-facing chat copy must not come
   from an upstream body. Deleted restating migration notes where touched.
7. Gaming. No new `any`, unsafe cast, suppression, or lint-config change.
   `applySocketMessage` remains private. Hook tests exercise it through
   `useLiveFeed`. Test `loadWithoutCss` was split because Oxlint counts tests
   in the Batch 7 authored inventory.

### 16. Skipped changes

- Did not pass `req` into GET `/api/service-alerts`. That would change
  deployed identity behavior and is not proven wrong by an existing test.
- Did not extract more page.tsx handlers. Remaining handlers are layout
  wiring, not independently testable domain ownership.
- Did not add coverage-only branches or wrappers to reach 95%.
- Did not lower the 7 functions at complexity 11 or 12.
- Did not copy the itinerary schema or add a second contract.
- Did not add a handler registry, runner class, or context bag.
- Did not modify `frontend/components/**`, backend, scripts, package files,
  generated artifacts, or `quality/baseline.json`.

### 17. Remaining risks

- Production function growth (+71) exceeds the 25-function review trigger.
  The shared schema and REST parser justify that growth.
- Node unit tests stub CSS, `next/font`, and `next/dynamic`. They do not
  replace Playwright for layout.
- Browser source-mapped coverage for React and MapLibre remains Batch 8A.

### 18. Batch 8

Batch 8 was not started.

### 19. Commit state

The Batch 7 tree is uncommitted. `quality/baseline.json` is unchanged.

## Batch 7 reviewer final (2026-08-31)

Codex reviewed the production diff and repaired the superseded worker text in
this handoff. Independent forward and reverse runs each passed 117 tests. Two
fresh coverage runs produced the same Batch 7 totals: 98.85% lines, 95.04%
branches, and 98.31% functions. Batch 7 has no production function above 12
and no unexecuted production file.

The reviewer removed exactly the 10 stale TypeScript entries listed above.
`quality/baseline.json` fell from 111 entries to 101. The full quality command
against `d2ecdfe3bf43397aee2051c84ac0bf4544d8cfc0` exits 0 with
`approval_eligible: true`. It runs 770 frontend tests and 1,915 backend tests,
with 21 backend skips and 446 subtests. New, worsened, stale, cognitive-delta,
Ruff C901, and Ruff structural counts are 0.

Batch 7 is Codex-approved. This reviewer result is part of the Batch 7 commit.
Start Batch 8 only from that commit.

## Batch 8 worker result (2026-09-01)

Worker implementation. Codex is the reviewer. The worker did not update
`quality/baseline.json`, did not approve this batch, did not commit, and did
not start Batch 9.

### 1. Fixed point

Immutable quality reference:
`6cd3238bc114e4a4176a77866b3b927aa57ed221`.

`git rev-parse HEAD` equals that SHA before and after the work.

### 2. Preserved user-owned dirty files

Expected initial status, left untouched:

```text
 M frontend/next-env.d.ts
?? frontend/CLAUDE.md
```

The finishing pass started from the in-progress Batch 8 tree. Those two files
were already dirty. The worker did not edit, stage, delete, restore, stash, or
commit either file.

Out of Batch 8 and left dirty:

```text
 M backend/app/services/trips/crowds/evidence.py
 M backend/tests/test_agent_model_stream.py
 M backend/tests/test_check_transit.py
 M backend/tests/test_crowd_evidence.py
 M backend/tests/test_directions.py
 M backend/tests/test_itinerary_canonical.py
 M backend/tests/test_transit_evidence.py
 M docs/README.md
```

### 3. Changed files

Production in Batch 8 owned paths:

- `frontend/components/map/buildings-layer.ts`
- `frontend/components/map/camera.ts`
- `frontend/components/map/route-stops-features.ts`
- `frontend/components/map/station-badges.ts`
- `frontend/components/map/subway-network.ts`
- `frontend/components/smart-route/chat/chat-arrivals-card.tsx`
- `frontend/components/smart-route/chat/chat-composer.tsx`
- `frontend/components/smart-route/chat/chat-message.tsx`
- `frontend/components/smart-route/chat/chat-sidebar.tsx`
- `frontend/components/smart-route/chat/chat-working-panel.tsx`
- `frontend/components/smart-route/chat/itinerary-card-legs.tsx`
- `frontend/components/smart-route/chat/itinerary-event-adapter.ts`
- `frontend/components/smart-route/chat/itinerary-view-model.ts`
- `frontend/components/smart-route/chat/near-you.ts`
- `frontend/components/smart-route/chat/recommended-itinerary-card.tsx`
- `frontend/components/smart-route/left-rail/alert-detail.tsx`
- `frontend/components/smart-route/left-rail/alert-feed-copy.ts`
- `frontend/components/smart-route/left-rail/alert-feed-normalizer.ts`
- `frontend/components/smart-route/left-rail/atoms.tsx`
- `frontend/components/smart-route/left-rail/live-data.ts`
- `frontend/components/smart-route/left-rail/live-data/alerts-feed.ts`
- `frontend/components/smart-route/left-rail/live-data/nearby-arrivals.ts`
- `frontend/components/smart-route/left-rail/live-data/route-candidates.ts`
- `frontend/components/smart-route/left-rail/live-data/route-plan.ts`
- `frontend/components/smart-route/left-rail/live-data/route-reason-copy.ts`
- `frontend/components/smart-route/left-rail/live-data/route-reasoning.ts`
- `frontend/components/smart-route/left-rail/live-data/route-steps.ts`
- `frontend/components/smart-route/left-rail/route-view-actions.tsx`
- `frontend/components/smart-route/left-rail/route-view-alternatives.tsx`
- `frontend/components/smart-route/left-rail/route-view-itinerary.tsx`
- `frontend/components/smart-route/left-rail/route-view.tsx`
- `frontend/components/smart-route/map/smart-route-map-helpers.ts` (new)
- `frontend/components/smart-route/map/smart-route-map.tsx`
- `frontend/components/smart-route/page/use-route-planning-controller.ts`

Supporting production and harness outside `components/**` and
`tests/release/**`:

- `frontend/lib/use-voice-input.ts` (DestinationInput reuses composer dictation)
- `frontend/playwright.config.ts` (NYC geolocation grant)
- `frontend/tools/run-unit-tests.mjs` (Playwright merge and 8A proof)
- `frontend/tools/browser-source-coverage.mjs` (new)
- `scripts/frontend_quality_scope.json`
- `scripts/report_frontend_debt.py` (`browser_tsx_source_mapped`)

Tests. New Node files plus edits to existing component and release tests,
including `frontend/tools/browser-source-coverage.test.mjs`.

Docs and inventory:

- `docs/lint-cleanup-plan.md`
- `docs/lint-cleanup-handoff.md`
- `.audit/frontend-debt.json`

### 4. Batch 8A proof

Exact, not hashed-chunk.

- Interaction. Playwright release tests with `SMARTROUTE_BROWSER_COVERAGE=1`.
  `openSmartRoute` loads `/?qa-map=1`. The page fixture calls
  `page.coverage.startJSCoverage`.
- Generated script URL. Owned webpack-internal modules such as
  `webpack-internal:///(app-pages-browser)/./components/...`. Hashed chunks
  are not the proof identity.
- Source-map owner. Next webpack. `withInlineSourceMap` fetches
  `http://127.0.0.1:3100/__nextjs_source-map?filename=...` and inlines
  `data:application/json;base64`. Existing inline `data:` maps are kept.
- Original path. `components/smart-route/chat/chat-sidebar.tsx`.
- Original line range. 1 through 271. 271 hit, including original line 143.
- Command. `npm --prefix frontend run test:coverage`.
- Proof. `istanbulFromBrowserRawDirectory` builds a browser-only Istanbul map.
  `assertOriginalLineHit` checks original line 143 on that map before merge.
  `coverage/browser-source-mapped.json` is deleted at the start of every
  coverage run and rewritten only after the browser-only assertion succeeds.
- Proof log. `browser source coverage: components/smart-route/chat/chat-sidebar.tsx original lines 1-271 (271 hit)`.
- Reporter flag. `detect_browser_source_mapped()` reads the marker written
  after the browser-only proof.

### 5. Tests

Frontend unit during the latest official coverage run. 1084 passed, 0 failed.
Playwright release. 14 passed, 4 skipped (`@visual`).
Shared-stub pair `live-data.test.mjs` then `hydration.test.mjs`, and the reverse
import order, each passed 33 tests in one process.

Deleted source-text assertions on touched surfaces. Replaced with SSR markup
or public API return values in chat sidebar, composer, working panel, and
related chrome tests.

### 6. Commands

- `npm --prefix frontend run typecheck`. Exit 0 after Batch 8 type fixes.
- `npm --prefix frontend run lint`. Exit 0. Quality parser ESLint complexity 0.
- `npm --prefix frontend run lint:oxlint`. Still red. Inherited component
  anti-slop plus Batch 9 `frontend/scripts/**` complexity. Quality parser
  Oxlint complexity 69, all Batch 9.
- Transit artifacts. Not regenerated. No builder or generated GeoJSON inputs
  changed.
- Two consecutive `npm --prefix frontend run test:coverage` runs, with
  `PLAYWRIGHT_BROWSERS_PATH=C:\Users\19293\AppData\Local\ms-playwright`.
  Both wrote the 8A proof line. Batch 8 `components/**` via
  `scripts/report_frontend_debt.py`:

  Both runs printed the browser-only 8A proof before merge.
  Reporter after run 2. line 96.86% (16374 / 16904), branch 95.18% (4422 / 4646),
  function 95.52% (832 / 871). Unexecuted files 0.
  `browser_tsx_source_mapped: True`. `above_12: 0`.

  Lines and functions stay at or above 95%. Branch counters still jitter
  across Istanbul merges. Both ratios are at or above 95%.

- `py scripts/check_quality.py --quality-ref 6cd3238bc114e4a4176a77866b3b927aa57ed221`.
  Exit 1. `tests_ran: true`. `approval_eligible: false`.
  new 0, worsened 0, new or worsened cognitive 0, Ruff C901 0, Ruff
  structural 0, ESLint complexity 0, Oxlint complexity 69.
  stale baseline entries 42. Backend tests 1915 passed, 21 skipped, 446
  subtests.

### 7. Starting versus final Batch 8 inventory

Start at `6cd3238`:

- production files 88
- production functions 880
- above 12: 44
- at 11 or 12: 10
- unexecuted files 21
- line 55.18% (9050 / 16402)
- branch unresolved
- function unresolved

Final:

- production files 88
- production functions 1000
- authored functions 1910
- above 12: 0
- at 11 or 12: 24
- unexecuted files 0
- high-CRAP diagnostic 24
- line 96.86% (16374 / 16904)
- branch 95.18% (4422 / 4646)
- function 95.52% (832 / 871)

### 8. ESLint and Oxlint

Start. ESLint complexity 43, all Batch 8. Oxlint complexity 113 (Batch 8: 44,
Batch 9: 69).

Final. Application ESLint complexity 0 (`npm run lint` exit 0). Quality Oxlint
complexity 69, Batch 9 only. Raw `lint:oxlint` still reports inherited
component anti-slop. That is not a complexity finding.

### 9. Production growth

Official `production_line_growth` versus `6cd3238`. Added 3130, removed 2631,
net 499. Production functions 880 to 1000, net +120, above the +25 trigger.

Continue justification. Named policy extracts (alert copy, destination
combobox keys, itinerary adapter, route-view, subway-network), the map helper
module for Node-testable paint and fit math, then deletion of dead camera
rotation and unused ETA aliases. No new architecture.

### 10. Stale baseline IDs for Codex

The worker did not run `--update-baseline`. Codex may shrink these 42
TypeScript entries after review:

- `typescript:frontend/components/map/route-stops-features.ts:buildRouteStopFeatures#0`
- `typescript:frontend/components/map/station-badges.ts:addIntermediateStopLabels#0`
- `typescript:frontend/components/map/subway-network.ts:anonymous#6`
- `typescript:frontend/components/map/subway-network.ts:buildSubwayLaneFeaturesFromVisual#0`
- `typescript:frontend/components/smart-route/chat/chat-arrivals-card.tsx:ChatArrivalsCard#0`
- `typescript:frontend/components/smart-route/chat/chat-composer.tsx:ChatComposer#0`
- `typescript:frontend/components/smart-route/chat/chat-message.tsx:AssistantMessage#0`
- `typescript:frontend/components/smart-route/chat/chat-sidebar.tsx:AnimatedSidebarIcon#0`
- `typescript:frontend/components/smart-route/chat/chat-working-panel.tsx:ChatWorkingPanel#0`
- `typescript:frontend/components/smart-route/chat/itinerary-card-legs.tsx:StopChain#0`
- `typescript:frontend/components/smart-route/chat/itinerary-event-adapter.ts:anonymous#2`
- `typescript:frontend/components/smart-route/chat/itinerary-event-adapter.ts:condensePreviewEvents#0`
- `typescript:frontend/components/smart-route/chat/itinerary-view-model.ts:buildItineraryViewModel#0`
- `typescript:frontend/components/smart-route/chat/itinerary-view-model.ts:formatStructuredRecommendationReason#0`
- `typescript:frontend/components/smart-route/chat/near-you.ts:buildHomeNearbyModel#0`
- `typescript:frontend/components/smart-route/chat/recommended-itinerary-card.tsx:ItineraryCardShell#0`
- `typescript:frontend/components/smart-route/left-rail/alert-detail.tsx:buildAlertDetailView#0`
- `typescript:frontend/components/smart-route/left-rail/alert-feed-copy.ts:compactAlertTitle#0`
- `typescript:frontend/components/smart-route/left-rail/alert-feed-copy.ts:compactFeedTitle#0`
- `typescript:frontend/components/smart-route/left-rail/alert-feed-normalizer.ts:anonymous#1`
- `typescript:frontend/components/smart-route/left-rail/alert-feed-normalizer.ts:normalizeServiceAlert#0`
- `typescript:frontend/components/smart-route/left-rail/atoms.tsx:StepIcon#0`
- `typescript:frontend/components/smart-route/left-rail/atoms.tsx:TransitText#0`
- `typescript:frontend/components/smart-route/left-rail/live-data/alerts-feed.ts:buildHealth#0`
- `typescript:frontend/components/smart-route/left-rail/live-data/nearby-arrivals.ts:buildArrivalRows#0`
- `typescript:frontend/components/smart-route/left-rail/live-data/route-candidates.ts:alternativeCardFields#0`
- `typescript:frontend/components/smart-route/left-rail/live-data/route-candidates.ts:buildAlternatives#0`
- `typescript:frontend/components/smart-route/left-rail/live-data/route-candidates.ts:normalizeAlternateReason#0`
- `typescript:frontend/components/smart-route/left-rail/live-data/route-plan.ts:buildPlan#0`
- `typescript:frontend/components/smart-route/left-rail/live-data/route-reason-copy.ts:buildWhyNotSentence#0`
- `typescript:frontend/components/smart-route/left-rail/live-data/route-reason-copy.ts:candidateDisplayLabel#0`
- `typescript:frontend/components/smart-route/left-rail/live-data/route-reasoning.ts:buildRouteReasoningInsights#0`
- `typescript:frontend/components/smart-route/left-rail/live-data/route-steps.ts:anonymous#5`
- `typescript:frontend/components/smart-route/left-rail/live-data/route-steps.ts:routeStepToRailStep#0`
- `typescript:frontend/components/smart-route/left-rail/route-view-actions.tsx:DestinationInput#0`
- `typescript:frontend/components/smart-route/left-rail/route-view-alternatives.tsx:AlternateRouteCard#0`
- `typescript:frontend/components/smart-route/left-rail/route-view-itinerary.tsx:RecommendedRouteCard#0`
- `typescript:frontend/components/smart-route/left-rail/route-view-itinerary.tsx:anonymous#14`
- `typescript:frontend/components/smart-route/left-rail/route-view.tsx:RouteView#0`
- `typescript:frontend/components/smart-route/map/smart-route-map.tsx:anonymous#15`
- `typescript:frontend/components/smart-route/map/smart-route-map.tsx:applyDarkMapTheme#0`
- `typescript:frontend/components/smart-route/page/use-route-planning-controller.ts:handleSubmit#0`

### 11. Review findings

1. Scope and behavior versus `6cd3238`. Applied. Canonical itinerary facts stay
   backend-owned. Chat, cards, steps, and map read the same contract.
2. Type-system and boundary. Applied. `STRUCTURED_REASON_COPY` is a string
   table. `isValidCard` is a type predicate. `LAYOUT_EASE` is a 4-tuple, not a
   const assertion against Motion.
3. Simplify and ponytail. Applied by deletion. Removed unused camera rotation,
   unused `selectedRouteIndex`, the `candidateEtaMinutes` alias, `buildPlan`'s
   unused clock argument, and restating comments on touched files.
4. Testing on the Toilet. Applied. New tests assert public API returns, SSR
   markup, or Playwright outcomes. The 8A proof asserts original line 143 on
   the browser-only Istanbul map before merge. Unit-only coverage cannot
   satisfy that proof. ChatMessage View alerts and source order are rendered
   markup tests. Redundant source-identifier regexes in `chat-route-card` and
   `hydration` tests were deleted.
5. Performance. No extra request-time work. Browser coverage collection is
   test-only behind `SMARTROUTE_BROWSER_COVERAGE=1`.
6. Comments. Deleted restating JSDoc on `flyToRoute`. Kept nearby-arrivals
   compass and walk-fold notes because they name passenger policy.
7. Gaming. No new `any`, suppression, or lint-config ceiling change. Coverage
   tests hit missed branches through public formatters and render, not wrappers
   added only to satisfy a meter.

### 12. Requested reviewer exceptions

- Official 95% gate is Batch 8 `components/**` via `report_frontend_debt.py`,
  not whole-frontend c8.
- Two consecutive official merges need not match Istanbul branch counters
  exactly when both ratios stay at or above 95%. Lines and functions matched.
- `frontend/lib/use-voice-input.ts` is outside owned paths. Reuse for
  DestinationInput dictation.
- Raw Oxlint may remain red for inherited component anti-slop. Complexity
  above 12 in owned Batch 8 files is 0. Batch 9 `scripts/**` stays red.
- Net production +499 and +120 functions. Documented continue justification
  above.

### 13. Skipped changes

- Did not rewrite untouched anti-slop across `subway-network.ts` and prompt-kit.
- Did not inline `transitLineId` or `locatedStopFeatures`.
- Did not swap left-rail `clockFromIso` onto `formatNycRouteClock`.
- Did not narrow `SubmitPrep` or delete `PREVIEW_EVENT_MAX`.
- Did not add Playwright for Stop route planning (control never visible) or
  Open in Live Feed (hides composer).
- Did not lower the 24 functions at complexity 11 or 12.
- Did not update `quality/baseline.json`.

### 14. Remaining risks

- Istanbul branch denominators jitter by a few counters across merges.
- Node unit tests stub MapLibre CSS. They do not replace Playwright for map
  layout.
- Files still below 95% lines individually. Totals still meet the gate.
- Full quality exits 1 until Codex removes the 42 stale entries.
- Cursor sandbox remaps Playwright unless `PLAYWRIGHT_BROWSERS_PATH` points at
  `C:\Users\19293\AppData\Local\ms-playwright`.

### 15. Batch 9

Batch 9 was not started.

### 16. Commit state

The Batch 8 tree is uncommitted. `quality/baseline.json` is unchanged.
HEAD remains `6cd3238bc114e4a4176a77866b3b927aa57ed221`.

### 17. Do not treat as Batch 8

- `frontend/next-env.d.ts`
- `frontend/CLAUDE.md`
- backend files and `docs/README.md`
- local levers under `.audit/` except `.audit/frontend-debt.json`

### 18. Reviewer final

Codex accepted the isolated Batch 8 diff after the worker repaired the two
review findings. The coverage command removes any prior browser marker, maps
Chromium records alone, and asserts original `chat-sidebar.tsx` line 143 before
merging unit coverage. Its unit test proves unit-only coverage cannot satisfy
the guard. Rendered `ChatMessage` tests protect the View alerts condition and
source order. The route-view SSR clock test has no wall-clock comparison.

The reviewer removed exactly 42 proven-stale TypeScript entries from
`quality/baseline.json`, which reduced the file from 101 to 59 entries. No
entry was added or widened. The generated update lowered the surviving
`check_transit.execute` CRAP ceiling from 12.083333 to 12.010417.

The final `py scripts/check_quality.py --quality-ref 6cd3238` command exits 0
with `approval_eligible: true`, `tests_ran: true`, new 0, worsened 0, stale 0,
cognitive new or worsened 0, Ruff C901 0, Ruff structural 0, and ESLint
complexity 0. It passed 1,084 frontend tests and 1,915 backend tests, with 21
backend skips and 446 backend subtests. The browser-only proof mapped original
sidebar lines 1 through 271. Batch 8 component coverage remains 96.86% lines
(16,374 / 16,904), 95.18% branches (4,422 / 4,646), and 95.52% functions
(832 / 871). Batch 9 was not started.

## Whole-repository audit and revised next work (2026-08-30)

This section records the evidence available before the backend test-assurance
checkpoint. Use the current result and test-assurance section above for active
counts and next steps.

Codex audited an isolated checkout of `9b4327d`. No subagent or worker changed
the result. The audit inspected completed backend diffs, current production
and tests, frontend F0 measurement, and the future Batch 7 through 9 scopes.
It changed only this plan and handoff.

### Backend evidence

The fresh branch-coverage suite passed with 1,899 tests, 21 skips, and 444
subtests. Backend coverage is 88.8%. The debt report contains 2,437 production
functions, 91 with a Radon or cognitive score of 11 or 12, none above 12, and
one CRAP value above 30. Ruff remains clean at its McCabe maximum of 10.

The one high CRAP signal is
`agent/tools/transit/evidence_projection.py:_area_condition_fields`: Radon 8,
cognitive 5, zero direct coverage, and CRAP 72. It is real rider-facing
behavior reached by `operation_facts` and transit evidence construction. A
single public dispatch test is justified. A helper-level coverage campaign is
not.

Batches 6A through 6E added 3,893 net backend production lines and no
production files. Static call counting found many one-call helpers, but manual
inspection of the largest clusters found named predicates, lifecycle stages,
and policy transforms. Do not launch a broad helper-inline pass. Future work
must be deletion first and neutral or negative growth unless new behavior
earns the addition.

Repository-wide reference searches found this bounded dead code:

- the 175-line `reconcile_first_boarding_timing` subtree in
  `agent/tools/route/route_projection.py`, reached only by its two tests
- `_expand_abbreviations` and `_TTS_ABBREVIATIONS` in `trips/text.py`
- `is_nyc_locality` in `tools/places/geography.py`
- `GTFSStaticData.get_stop_names`
- a stale `Later wiring` paragraph in the already-wired chained-itinerary
  implementation

Vulture at 80 percent confidence found no additional production result. The
audit did not find a high-confidence unused backend file. Test-only public
helpers in cache, incident batches, trip state, and turn contracts were not
added to the deletion list because their small size did not justify guessing
about intended interfaces.

Large backend files remain review signals. `turn/stream.py` and the largest
trip and tool modules are cohesive around one lifecycle or policy. The audit
found no independent boundary that justified a file-count refactor. Do not
split them by line count.

### Frontend measurement evidence

F0 measured 226 production files, 52.27 percent line coverage, 114 unexecuted
files, and 82.27 percent source-mapped c8 function coverage over the functions
c8 could discover. Exact branch coverage is unresolved while files remain
unexecuted.

The raw authored mapper reports 847 measured, 927 uncovered, and 688
unresolved functions. Of the unresolved records, 618 are anonymous callbacks
and 70 are named functions not present in raw V8 output. The mapper primarily
joins by file and function name. Treating all unresolved records as uncovered
would reward renaming callbacks or reshaping production code for the tool.
F1 keeps those records diagnostic and uses standard source-mapped c8 function
coverage as the exact aggregate metric only after every owned file executes.

### Proven dead frontend code

A conservative TypeScript import graph produced 13 root candidates. Manual
review retained Next's `app/manifest.ts`, transit `*.check.mjs` entry points,
`scripts/release/build-browser-evidence.ts`, and
`scripts/build/artifact-fingerprint.ts`. Framework, package, release, README,
or standalone CLI procedures identify those files as roots even when another
TypeScript module does not import them.

The remaining exact fixed-point references prove these production files dead:

- `chat-top-bar.tsx`, `tab-toggle.tsx`, and `near-you-row.tsx`
- `left-rail/demo-data.ts`, a 538-line production fixture imported only by
  `near-you.test.ts`
- `left-rail/incident-format.ts`
- `scripts/build/line-geometry-cleanup.ts`, imported only by its own test and
  never by the generator

Those complete files total 892 production lines. Dead selectors and three
test-only Near You exports add further removable production code. F1 must
replace the demo dependency with a small local typed test fixture. It must not
create another production fixture module.

### Frontend architecture evidence

Batch 7 must establish the canonical route boundary before Batch 8. The REST
client currently types `res.json()` as `TripResponse` without runtime
validation. The SSE path has a canonical itinerary Zod schema, but the REST
types leave required candidate facts optional. One shared schema and one
narrowed post-validation type prevent every component from repeating optional
checks.

Batch 8 has a real domain violation, not a style complaint.
`left-rail/live-data/route-plan.ts` invents arrival clocks, recounts transfers,
and derives leave-by time. `route-candidates.ts` falls back to step timing,
creates clocks from `Date.now()`, and parses passenger prose for minutes and
transfer counts. These calculations must be deleted after Batch 7 makes the
canonical type available. Missing backend facts remain unavailable.

Nine component test files rely substantially on source or CSS text. They do
not execute the interaction they claim to protect. Batch 8 must first prove
that existing Playwright execution can map to original TypeScript and TSX.
When a touched behavior has only a source assertion, replace it with rendered
or browser behavior. Keep true generated-output and rendered-markup contracts.

Batch 9's largest function mixes six real generator stages. Splitting those
stages is justified, but dozens of branch peelers are not. The fixed-point
pipeline also carries an `unbundledFeatures` array that is always empty. Remove
that state first. Type only touched stage boundaries with narrow property
interfaces. Do not replace more than 100 `any` uses with one giant optional
schema or unsafe casts.

### Work explicitly rejected

- Do not force all 91 accepted backend survivors from 11 or 12 down to 10.
- Do not split files or merge domains to improve a file count.
- Do not add a React test framework beside Playwright and the current unit
  runner.
- Do not write source-text tests to reach coverage.
- Do not rename production callbacks to satisfy the raw V8 mapper.
- Do not reopen completed backend architecture without a behavior or ownership
  defect.
- Do not pursue 100 percent by testing impossible typed states, framework
  internals, or decorative branches.

## Reproduce the inventories

Run Ruff from the repository root:

```powershell
py -m ruff check --config pyproject.toml backend
py -m ruff check --config pyproject.toml --output-format json backend
```

Run quality and cognitive delta from the repository root:

```powershell
$fixedPoint = git rev-parse HEAD
py scripts/check_quality.py --cognitive-only --quality-ref $fixedPoint
py scripts/check_quality.py --quality-ref $fixedPoint
```

Record `$fixedPoint` before editing. Do not recompute it after the worker
changes the tree.

Run the backend debt inventory from the repository root. Reuse
`backend/.coverage` from a branch-coverage pytest run.

```powershell
py scripts/report_backend_debt.py --self-test
py scripts/report_backend_debt.py --max-existing 12 --output .audit/backend-debt.json
py scripts/report_frontend_debt.py --self-test
py scripts/report_frontend_debt.py --quality-ref ae27211fa9ba --output .audit/frontend-debt.json
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
npm run typecheck:scripts
npm run test:unit
npm run test:coverage
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
`docs/lint-cleanup-plan.md` exit 0. Do not start Batch 7 until Batches 6A
through 6E are committed.

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
Batch 6 is committed at `c058199`. That commit is the fixed point for Batches
6A through 6E. Do not start Batch 7 until those batches are committed.

## Batches 6A through 6E

Fixed point `c058199`. Measured with
`py scripts/report_backend_debt.py --max-existing 12` against
`backend/.coverage` from the Batch 6 full suite. Official `pyproject.toml`
ceilings stay at 10.

| Metric | Count |
|---|---:|
| Production functions | 1879 |
| Branch-aware coverage | 87.2% |
| Above 10 in either measurement | 314 |
| Above 12 in either measurement | 237 |
| At 11 or 12 | 77 |
| CRAP above 30 | 21 |
| Zero coverage | 81 |

| Batch | Functions | Above 12 | At 11 or 12 | CRAP above 30 | Zero coverage |
|---|---:|---:|---:|---:|---:|
| 6A | 493 | 47 | 13 | 9 | 42 |
| 6B | 398 | 62 | 24 | 4 | 14 |
| 6C | 316 | 40 | 19 | 2 | 12 |
| 6D | 237 | 35 | 13 | 2 | 3 |
| 6E | 435 | 53 | 8 | 4 | 10 |

CRAP scores above 30 at the fixed point:

| Batch | Cyclomatic | Cognitive | CRAP | Coverage | Function |
|---|---:|---:|---:|---:|---|
| 6A | 34 | 45 | 1190.0 | 0.0 | `mta/subway.py:_build_subway_vehicle_positions` |
| 6A | 16 | 22 | 272.0 | 0.0 | `live_feed/router.py:_attach_alert_stop_names` |
| 6A | 9 | 8 | 90.0 | 0.0 | `mta/bus_updates.py:_fetch_nearby_bus_arrivals` |
| 6A | 9 | 10 | 90.0 | 0.0 | `GTFSStaticData.get_subway_stops_with_routes` |
| 6A | 8 | 0 | 72.0 | 0.0 | `routers/subway.py:subway_stops` |
| 6A | 8 | 16 | 72.0 | 0.0 | `GTFSStaticData._query` |
| 6A | 7 | 3 | 56.0 | 0.0 | `live_feed/router.py:_service_alert_id` |
| 6A | 6 | 8 | 42.0 | 0.0 | `GTFSStaticData.get_unique_routes_for_stops` |
| 6A | 11 | 10 | 35.375 | 0.414 | `vehicle_enrichment.py:_attach_trip_segment` |
| 6D | 39 | 26 | 58.814 | 0.765 | `lookup_arrivals_bus.py:execute` |
| 6E | 43 | 35 | 43.02 | 0.978 | `model/prompt.py:build_turn_context` |
| 6E | 36 | 41 | 41.438 | 0.839 | `presented_entity_registry.py:resolve` |
| 6B | 40 | 45 | 40.627 | 0.927 | `matching.py:match_cached_incidents` |
| 6E | 38 | 30 | 38.0 | 1.0 | `candidate_store.py:store_candidate_set` |
| 6B | 32 | 38 | 34.985 | 0.857 | `merge.py:_prefer` |
| 6D | 25 | 25 | 34.766 | 0.75 | `evidence_binding.py:bind_accessibility_target` |
| 6B | 33 | 36 | 33.01 | 0.979 | `itinerary.py:build_chained_itinerary` |
| 6C | 29 | 21 | 31.285 | 0.860 | `search_local_places.py:execute` |
| 6B | 31 | 26 | 31.049 | 0.963 | `constraints.py:route_constraints` |
| 6E | 23 | 13 | 30.14 | 0.762 | `discovery_store.py:_sanitized_search_scope` |
| 6C | 30 | 25 | 30.242 | 0.935 | `route_projection.py:reconcile_first_boarding_timing` |

Delete `GTFSStaticData.get_unique_routes_for_stops` in Batch 6A. Remove the
hidden `discovery_set_id` input from `place_reference.execute` in Batch 6C.
Do not change public contracts.

## Batch 6A (committed at 140495a)

Fixed point `c058199`. Batch 6A is committed as
`140495a9292bfc8831888d6f9332556c80cc4b81`. `quality/baseline.json` has 295
entries after the reviewer shrink. Frontend production, `services/trips/**`,
and `services/agent/**` were not opened for new work in Batch 6A.

### Debt after the final coverage run

`py scripts/report_backend_debt.py --max-existing 12 --output .audit/backend-debt.json`

| Metric | At `c058199` | After 6A worker |
|---|---:|---:|
| 6A functions | 493 | 579 |
| 6A above 12 | 47 | 0 |
| 6A at 11 or 12 | 13 | 13 |
| 6A CRAP above 30 | 9 | 0 |
| Branch-aware coverage | 87.2% | 88.4% |

`_bounded_point` dropped from 11/5 to 4/3. It now looks up the two coordinate
key spellings and reuses `_bounded_number` plus the same NYC box as
`_in_service_area`. That brings the 11/12 count back to the fixed-point 13.

### Survivors at 11 or 12

Keep each of these at 11 or 12. Another helper would only hide a short guard.

| Function | Cyclo | Cog | Coverage | CRAP | Why it stays |
|---|---:|---:|---:|---:|---|
| `reject_oversize_public_json` | 9 | 12 | 0.933 | 9.024 | ASGI public-body size and type admission |
| `observability.finish_turn` | 9 | 11 | 1.000 | 9.000 | Turn telemetry close |
| `verify_ticket` | 11 | 8 | 0.870 | 11.269 | HMAC ticket check. Parts, expiry, and signature already have named owners |
| `_enrichment_steps_are_bounded` | 11 | 12 | 0.900 | 11.121 | Per-field enrichment step admission |
| `admission._memory_acquire` | 9 | 11 | 0.960 | 9.005 | In-memory admission lock |
| `normalize_incident_record` | 11 | 9 | 1.000 | 11.000 | Incident record shape |
| `NY511Client.fetch_events` | 11 | 12 | 0.947 | 11.018 | NY511 fetch and fail-open |
| `_collect_alert_incidents` | 10 | 11 | 0.895 | 10.117 | Alert-to-incident projection |
| `_accepted_x_claim` | 12 | 4 | 1.000 | 12.000 | Tweet claim admission |
| `normalize_web_corroborations` | 10 | 12 | 0.933 | 10.030 | Web corroboration shape |
| `_derive_live_network_status` | 12 | 6 | 1.000 | 12.000 | Healthy / caution / disrupted table |
| `_attach_trip_segment` | 11 | 10 | 0.966 | 11.005 | Marker geometry. Public path is `place_vehicle_markers`. Incomplete coordinates fall back to a stop marker. IN_TRANSIT uses previous-to-target progress 0.55 |
| `StopPatternIndex.stops_for_routes` | 10 | 11 | 0.818 | 10.601 | Pattern-index route query |

### Owned and focused tests

From `backend` with `PYTHONPATH` set to that directory:

`py -m pytest tests/test_live_feed_snapshot.py tests/test_live_feed_stall_issues.py tests/test_mta_subway_vehicles.py tests/test_directions.py tests/test_live_feed_alert_enrichment.py tests/test_agent_chat_stream_cleanup.py tests/test_agent_chat_session_lease.py tests/test_live_feed_ownership.py tests/test_trips_enrichment.py tests/test_mta_feed_bus_stops.py tests/test_bus_routes.py -q`

Result: 143 passed, 17 subtests.

Hop-4 stall invert (`== []` to `!= []`) failed, then the assertion was restored.

`py -m ruff check backend/app backend/tests` result: all checks passed.

`py scripts/check_quality.py --quality-ref c058199 --cognitive-only` result: new or worsened 0.

### Quality

`py scripts/check_quality.py --quality-ref c058199` exit 1.
`tests_ran: true`. `approval_eligible: false`. New 0. Worsened 0. Cognitive
new or worsened 0. Frontend 314 passed. Backend 1890 passed, 21 skipped, 444
subtests. Ruff C901 0. Ruff structural 0. Resolved 32. Stale 34.

Stale baseline entries for the reviewer:

- `python:backend/app/routers/agent_chat.py:agent_chat#0`
- `python:backend/app/routers/live_feed/router.py:_attach_alert_stop_names#0`
- `python:backend/app/routers/live_feed/socket.py:receive_bounded_json#0`
- `python:backend/app/routers/trips.py:_bounded_point#0`
- `python:backend/app/routers/trips.py:_trip_payload_is_bounded#0`
- `python:backend/app/services/directions.py:_route_at_transfer#0`
- `python:backend/app/services/incidents/index.py:_coverage_status#0`
- `python:backend/app/services/incidents/index.py:lookup_incidents#0`
- `python:backend/app/services/incidents/normalization.py:sanitize_source_records#0`
- `python:backend/app/services/incidents/ny511.py:NY511Settings.from_env#0`
- `python:backend/app/services/incidents/ny511.py:_normalize_event#0`
- `python:backend/app/services/incidents/refresh.py:run_background_incident_refresh#0`
- `python:backend/app/services/incidents/scout.py:scout_incident_batch#0`
- `python:backend/app/services/incidents/scout_provider.py:_completed_sources#0`
- `python:backend/app/services/live_feed/network_snapshot.py:_normalize_network_data#0`
- `python:backend/app/services/live_feed/snapshot.py:_build_live_signals#0`
- `python:backend/app/services/live_feed/snapshot.py:_build_live_snapshot#0`
- `python:backend/app/services/live_feed/snapshot.py:_realtime_trip_stop_context#0`
- `python:backend/app/services/live_feed/snapshot.py:_route_stop_match#0`
- `python:backend/app/services/live_feed/snapshot.py:build_nearby_transit_issues#0`
- `python:backend/app/services/mta/alerts.py:_alert_semantics#0`
- `python:backend/app/services/mta/alerts.py:project_service_alert#0`
- `python:backend/app/services/mta/bus.py:parse_bus_stop_monitoring#0`
- `python:backend/app/services/mta/bus.py:parse_stalled_bus_positions#0`
- `python:backend/app/services/mta/bus.py:parse_stops_for_route#0`
- `python:backend/app/services/mta/bus.py:slice_route_stops#0`
- `python:backend/app/services/mta/feeds.py:fetch_feeds_with_metadata#0`
- `python:backend/app/services/mta/static_gtfs/scheduled_arrivals.py:ScheduledArrivalIndex.lookup#0`
- `python:backend/app/services/mta/static_gtfs/stop_patterns.py:StopPatternIndex.__init__#0`
- `python:backend/app/services/mta/static_gtfs/stop_patterns.py:StopPatternIndex.get_intermediate_stops_with_coords#0`
- `python:backend/app/services/mta/static_gtfs/stop_patterns.py:StopPatternIndex.suggest_one_transfer#0`
- `python:backend/app/services/mta/static_gtfs/store.py:BoundedIntermediateStopsCache._resolve#0`
- `python:backend/app/services/mta/subway.py:_build_subway_vehicle_positions#0`
- `python:backend/app/services/mta/subway.py:parse_vehicle_positions#0`

Do not run `--update-baseline` from this worker. `_bounded_point#0` is newly
stale because that function is now 4/3. `_route_at_transfer#0` and
`_build_subway_vehicle_positions#0` stay stale under the old names after the
public renames.

### Final review repairs

1. SSE lifecycle. Deleted `_one_sse_event` and the `SimpleNamespace` out-param.
   `_sse_stream` keeps `pending` and `succeeded` as locals (6/9). Ping frames
   still yield when the wait times out. Drain, save, admission release, then
   session lease order is unchanged.
2. Public test boundaries. `live_snapshot` stubs
   `network_snapshot_store.get_or_refresh` and calls `build_live_snapshot`.
   Parse-and-select is `build_subway_vehicle_positions`. Transfer selection is
   `route_at_transfer`. Alert identity and WS ticks are `service_alert_id`,
   `service_alert_signatures`, and `next_service_alert_message`.
3. Stall tests. `NearbyStallIssueTests` moved to
   `backend/tests/test_live_feed_stall_issues.py` (109 lines). That class uses
   `build_nearby_transit_issues` only. It does not use `NetworkSnapshot` or
   `canal_gtfs`.
4. `_lacks_map_coordinates` inlined. Coordinate admission stays in
   `_accepted_subway_vehicle_id` (6/6). Identity, route scope, and in-frame
   dedupe live in `_unique_requested_vehicle_id` (6/5). Folding coordinates
   into `_select_subway_vehicle_markers` made that new function cognitive 11.
5. Restored why-only notes: Google M15-SBS is BusTime/OBA M15+, and a
   direction group requires board before exit.

### Snapshot test file

`backend/tests/test_live_feed_snapshot.py` is 521 lines. Nearby stop fallback,
alert projection, live signals, and trip-stop context all go through
`live_snapshot` with a frozen `NetworkSnapshot`. 500 lines is a review signal,
not a split trigger. The leftover file is the smallest frozen-generation
cluster after the stall hop policy left.

### Production function growth

`git diff -U0 c058199 -- backend/app` counts 113 added `def` lines and 27
removed, net +86. That crosses the +25 review trigger. Remaining helpers own
named policies (parse, select, admit, identity, expire, fail-open, drain).
One-call reducers that did not (`_reduce_coverage_statuses`, `_lacks_map_coordinates`,
the SSE `SimpleNamespace`) were inlined or deleted. Do not rebuild the live
snapshot or NY511 assemblers to chase a smaller count.

### Unresolved risks

- `_service_alerts_payload` remains an underscored REST payload assembler.
  Stop-name tests drive that payload. They do not import `_attach_alert_stop_names`.
- Snapshot tests still construct a frozen `NetworkSnapshot`. The public path
  is `build_live_snapshot` with a stubbed store, not a live refresh.
- Public renames leave stale baseline keys under the old underscored names.
- Quality exit 1 is only the 34 stale baseline entries. Worker must not shrink
  `quality/baseline.json`.

### Reviewer final

The reviewer accepted the isolated Batch 6A diff and removed exactly the 34
proven-stale entries listed above. `quality/baseline.json` decreased from 329
to 295 entries. No entry was added or increased.

`py scripts/check_quality.py --quality-ref c058199 --update-baseline` exits 0.
`tests_ran: true`. `approval_eligible: true`. Frontend 314 passed. Backend
1890 passed, 21 skipped, 444 subtests. New 0. Worsened 0. Cognitive new or
worsened 0. Stale 0. Ruff C901 0. Ruff structural 0.

The reviewer regenerated `.audit/backend-debt.json` from that coverage run.
Batch 6A has 579 functions, 0 above 12, 13 at 11 or 12, 0 CRAP scores above
30, and 88.4% branch-aware coverage. Batch 6A is approved and committed as
`140495a`.

## Batch 6B worker completion (uncommitted)

Scope checkpoint `140495a9292bfc8831888d6f9332556c80cc4b81`. Quality
reference `c0581994e47ce1a5bbdb2d8e83fc0afd50ff1745`. HEAD is still
`140495a`. This tree is uncommitted for Codex review.
`quality/baseline.json` was not changed. Batch 6C, 6D, 6E, and Batch 7 were
not started. Frontend production and `services/agent/**` were not edited.

### Changed files

Production:

- `backend/app/services/trips/candidates.py`
- `backend/app/services/trips/crowds/event.py`
- `backend/app/services/trips/crowds/event_provider.py`
- `backend/app/services/trips/crowds/evidence.py`
- `backend/app/services/trips/crowds/hotspots.py`
- `backend/app/services/trips/crowds/search_normalization.py`
- `backend/app/services/trips/crowds/search_provider.py`
- `backend/app/services/trips/direct_plan.py`
- `backend/app/services/trips/enrichment.py`
- `backend/app/services/trips/itinerary.py`
- `backend/app/services/trips/preparation/combine.py`
- `backend/app/services/trips/preparation/constraints.py`
- `backend/app/services/trips/preparation/evidence.py`
- `backend/app/services/trips/preparation/finalize.py`
- `backend/app/services/trips/preparation/input.py`
- `backend/app/services/trips/preparation/multi_stop.py`
- `backend/app/services/trips/route_incidents/association.py`
- `backend/app/services/trips/route_incidents/context.py`
- `backend/app/services/trips/route_incidents/index_adapter.py`
- `backend/app/services/trips/route_incidents/matching.py`
- `backend/app/services/trips/route_incidents/merge.py`
- `backend/app/services/trips/route_incidents/scan.py`
- `backend/app/services/trips/scoring.py`
- `backend/app/services/trips/selection_decision.py`
- `backend/app/services/trips/transfer_semantics.py`

Tests and records:

- `backend/tests/test_incident_context_matching.py`
- `backend/tests/test_ticketmaster_event_lookup.py`
- `backend/tests/test_trip_candidate_reasons.py`
- `docs/lint-cleanup-handoff.md`
- `docs/lint-cleanup-plan.md`
- `.audit/backend-debt.json`

### Debt after the final coverage run

`py scripts/report_backend_debt.py --max-existing 12 --output .audit/backend-debt.json`

The command exits 1 because Batches 6C through 6E still have functions
above 12. Judge `by_batch: 6B`.

| Metric | At `140495a` | After 6B worker |
|---|---:|---:|
| 6B functions | 398 | 573 |
| 6B above 12 | 62 | 0 |
| 6B at 11 or 12 | 24 | 24 |
| 6B CRAP above 30 | 4 | 0 |
| Branch-aware coverage | 88.4% | 88.6% |

### Survivors at 11 or 12

Keep each of these at 11 or 12. Another helper would only hide a short guard
or push a sibling over the 24-function cap.

| Function | Cyclo | Cog | Coverage | CRAP | Why it stays |
|---|---:|---:|---:|---:|---|
| `_lookup_event_hubs` | 9 | 12 | 1.000 | 9.000 | Event-hub name and coordinate lookup |
| `_events_from_payload` | 11 | 9 | 0.867 | 11.287 | Ticketmaster payload rows |
| `_lookup_uncached` | 10 | 12 | 0.875 | 10.195 | Uncached event fetch and fail-open |
| `lookup_events` | 12 | 10 | 0.967 | 12.005 | Public event lookup |
| `find_hotspot_hits` | 7 | 12 | 0.909 | 7.037 | Hotspot association |
| `normalize_search_payload` | 9 | 11 | 0.875 | 9.158 | Crowd-search payload shape |
| `run_search` | 11 | 9 | 0.783 | 12.243 | Crowd-search provider |
| `_select_first_valid` | 7 | 11 | 0.818 | 7.295 | First valid candidate |
| `parse_coordinates` | 10 | 11 | 0.778 | 11.097 | Coordinate parse |
| `resolve_named_place` | 11 | 8 | 0.600 | 18.744 | Named place resolution |
| `_vehicle_signal_direction_matches` | 12 | 8 | 1.000 | 12.000 | Vehicle direction match |
| `_merge_envelope_rows` | 12 | 11 | 0.875 | 12.281 | Evidence envelope merge |
| `prepare_structural_candidates` | 11 | 8 | 0.900 | 11.121 | Structural candidate recovery |
| `validated_waypoints` | 8 | 11 | 0.688 | 9.953 | Waypoint admission |
| `_bounded_strings` | 8 | 11 | 0.917 | 8.037 | Association string admission |
| `_stop_association` | 12 | 10 | 0.950 | 12.018 | Stop-to-incident association |
| `_stop_records` | 8 | 11 | 1.000 | 8.000 | Endpoints plus opportunistic intermediates |
| `_source_records` | 9 | 12 | 0.857 | 9.236 | Index source projection |
| `_vehicle_signal_hits` | 11 | 10 | 1.000 | 11.000 | Vehicle hits on a scored route |
| `alert_penalty_from_score` | 11 | 4 | 0.727 | 13.455 | Alert penalty from score |
| `_fallback_scores` | 8 | 12 | 0.750 | 9.000 | Fallback selection scores |
| `_add_crowd_reason` | 12 | 4 | 0.833 | 12.667 | Crowd reason text |
| `_structured_reasons` | 10 | 11 | 0.933 | 10.030 | Structured reason list |
| `_dominates_for_preference` | 11 | 10 | 1.000 | 11.000 | Preference domination |

### Fixed-point CRAP targets

- `match_cached_incidents`: stop and geometry matching are separate from
  impact classification. Now 3/3, coverage 1.000, CRAP 3.000.
- `_prefer`: renamed `_combine_related_records`. Official fields win through
  `_official_fields_win`. Collection union stays. Now 1/0, coverage 1.000,
  CRAP 1.000. The stale baseline key is still `_prefer#0`.
- `build_chained_itinerary`: chained-segment construction is separate from
  total calculation. Now 4/3, coverage 1.000, CRAP 4.000.
- `route_constraints`: constraint kinds are named helpers. Now 10/9,
  coverage 0.938, CRAP 10.024.

A later extracted helper `_subway_pattern_intermediates` had CRAP 30.055 at
coverage 0.222. The public path `build_candidate_stop_context` now covers
pattern-index faults: intermediates are omitted and endpoints still match.
That helper is now 7/6, coverage 0.889, CRAP 7.067.

### Owned tests

From the repository root:

```
$env:PYTHONPATH = (Resolve-Path 'backend').Path
$env:APP_KEY = 'dummy'
$env:ANTHROPIC_API_KEY = 'dummy'
$env:SMARTROUTE_ENV = 'test'
$env:AGENT_ALLOW_MEMORY_SESSIONS = '1'
$files = @(
    'backend/tests/test_incident_context_matching.py',
    'backend/tests/test_trips_incidents.py',
    'backend/tests/test_itinerary_chain.py',
    'backend/tests/test_itinerary_canonical.py',
    'backend/tests/test_transfer_semantics.py',
    'backend/tests/test_event_crowd_scoring.py',
    'backend/tests/test_trips_plan_deterministic.py',
    'backend/tests/test_route_decision_evaluation.py',
    'backend/tests/test_route_evidence_coverage.py',
    'backend/tests/test_route_option_projection_grounding.py',
    'backend/tests/test_single_agent_route_availability.py',
    'backend/tests/test_trips_direct_plan.py',
    'backend/tests/test_trips_enrichment.py',
    'backend/tests/test_crowd_evidence.py',
    'backend/tests/test_crowd_hotspots.py',
    'backend/tests/test_crowd_search.py',
    'backend/tests/test_ticketmaster_event_lookup.py',
    'backend/tests/test_route_constraint_relaxation.py',
    'backend/tests/test_route_exclusion_constraints.py',
    'backend/tests/test_plan_trip_input_recovery.py',
    'backend/tests/test_trip_candidate_reasons.py',
    'backend/tests/test_intelligence_ablation.py',
    'backend/tests/test_route_option_assembly.py',
    'backend/tests/test_single_agent_route_multistop.py',
    'backend/tests/test_route_itinerary_contract.py',
    'backend/tests/test_plan_trip_projection.py'
)
py -m pytest @files -q --basetemp .pytest-batch6b-forward
$reverse = @($files)
[array]::Reverse($reverse)
py -m pytest @reverse -q --basetemp .pytest-batch6b-reverse
```

Forward: 280 passed, 1 skipped, 59 subtests.
Reverse: 280 passed, 1 skipped, 59 subtests.

### Red before green

1. `test_two_segments_default_25_min_dwell` inverted
   `wp["dwell_minutes"] == 25` to `== 24`. Failure: `assert 25 == 24`.
   Restored `== 25`.
2. `_subway_pattern_intermediates` except body changed from `return None`
   to `raise`. `test_pattern_index_fault_omits_intermediates_and_keeps_endpoints`
   failed with `RuntimeError: pattern index unavailable`. Restored
   `return None`.
3. Offsetless Ticketmaster `dateTime` plus valid local fields. Failure:
   `assert '2026-07-17T00:00:00Z' is None`. Local fallback removed for
   string `dateTime`.
4. `format_recommendation_reason({"code": []})`. Failure:
   `TypeError: unhashable type: 'list'`. Code must be a string before
   lookup.

### Codex review repairs

A Codex review of the first 6B tree found two behavior regressions.

P1. `_event_start_iso` used `or` between `_parse_aware_iso` and local
fields. A supplied `dateTime` string that lacked a timezone or failed
`fromisoformat` fell through to `localDate`/`localTime` and produced
`start_iso` plus `estimated_end_iso`. Checkpoint `140495a` returned
`None` for that string path. Local fields apply only when `dateTime` is
absent or not a string.

P2. `format_recommendation_reason` looked up `code` in dict tables.
`{"code": []}` and `{"code": {}}` raised `TypeError`. The checkpoint
returned `None`. Non-string codes return `None` before lookup.

### Full backend and quality

`py -m pytest backend/tests -q --basetemp .pytest-batch6b-full` is the
backend half of the quality command below.

`py -m ruff check backend scripts` result: all checks passed.

`py scripts/check_quality.py --quality-ref c058199 --cognitive-only`
result: new or worsened 0.

`py scripts/check_quality.py --quality-ref c058199` exit 1.
`tests_ran: true`. `approval_eligible: false`. New 0. Worsened 0. Cognitive
new or worsened 0. Frontend 314 passed. Backend 1894 passed, 21 skipped, 444
subtests. Ruff C901 0. Ruff structural 0. Resolved 57. Stale 59.

`git diff --check 140495a` is clean.

Stale baseline entries for the reviewer. All are
`python:backend/app/services/trips/...`:

- `candidates.py:route_family_signature#0`
- `crowds/event.py:_lookup_event_hubs#0`
- `crowds/event.py:search_hubs#0`
- `crowds/event_provider.py:_event_start_iso#0`
- `crowds/event_provider.py:_lookup_uncached#0`
- `crowds/event_provider.py:_parse_event#0`
- `crowds/evidence.py:_deduplicate_impacts#0`
- `crowds/evidence.py:collect#0`
- `crowds/hotspots.py:find_hotspot_hits#0`
- `crowds/search_normalization.py:normalize_search_payload#0`
- `crowds/search_provider.py:_citation_urls#0`
- `crowds/search_provider.py:_completed_sources#0`
- `direct_plan.py:_translate_prepare_error#0`
- `direct_plan.py:build_recommendation_reasons#0`
- `direct_plan.py:format_recommendation_reason#0`
- `direct_plan.py:project_route_candidates#0`
- `enrichment.py:_enrich_bus_legs#0`
- `enrichment.py:_enrich_subway_legs#0`
- `itinerary.py:_trip_clocks#0`
- `itinerary.py:build_canonical_itinerary#0`
- `itinerary.py:build_chained_itinerary#0`
- `itinerary.py:build_legs#0`
- `preparation/combine.py:combine_prepared_chains#0`
- `preparation/constraints.py:_candidate_timing#0`
- `preparation/constraints.py:_unconfirmed_claims#0`
- `preparation/constraints.py:candidate_digest#0`
- `preparation/constraints.py:route_constraints#0`
- `preparation/constraints.py:route_status#0`
- `preparation/evidence.py:candidate_evidence_for_route#0`
- `preparation/evidence.py:merge_candidate_evidence#0`
- `preparation/evidence.py:merge_incident_metadata_values#0`
- `preparation/evidence.py:vehicle_claims_for_route#0`
- `preparation/finalize.py:finalize_aggregate#0`
- `preparation/input.py:recover_structural_route#0`
- `preparation/multi_stop.py:prepare_multi_stop#0`
- `route_incidents/association.py:normalize_matcher_association#0`
- `route_incidents/context.py:extract_candidate_stop_context#0`
- `route_incidents/index_adapter.py:_matched_candidate_ids#0`
- `route_incidents/index_adapter.py:extract_lookup_context#0`
- `route_incidents/index_adapter.py:project_records#0`
- `route_incidents/matching.py:Cached511NYSearchTool.execute#0`
- `route_incidents/matching.py:_decode_polyline#0`
- `route_incidents/matching.py:_geometry_components#0`
- `route_incidents/matching.py:_nearest_distance_meters#0`
- `route_incidents/matching.py:match_cached_incidents#0`
- `route_incidents/merge.py:_current#0`
- `route_incidents/merge.py:_prefer#0`
- `route_incidents/merge.py:_same_incident#0`
- `route_incidents/scan.py:build_candidate_stop_context#0`
- `scoring.py:_route_alert_hits#0`
- `scoring.py:_route_alert_penalty#0`
- `scoring.py:_route_total_minutes#0`
- `scoring.py:finalized_route_score#0`
- `selection_decision.py:_add_preference_reasons#0`
- `transfer_semantics.py:_accessibility#0`
- `transfer_semantics.py:_classify_transfer#0`
- `transfer_semantics.py:endpoint_fields#0`
- `transfer_semantics.py:endpoint_identity#0`
- `transfer_semantics.py:route_accessibility#0`

Do not run `--update-baseline` from this worker. `_prefer#0` is stale
because the combiner is now `_combine_related_records`.

### Production line and function growth

`git diff --numstat 140495a -- backend/app/services/trips` is 3242 insertions
and 1972 deletions, net +1270 lines. `git diff -U0` counts 195 added `def`
lines and 22 removed, net +173. The debt inventory moved from 398 to 573
functions, net +175. That crosses the +25 function and +500 line review
triggers. Remaining helpers own named policies (match, merge, clock, total,
constraint, fail-open, admit, select). Peelers that only moved a condition
were inlined or restored when inlining created a 25th 11/12 survivor.

### Review findings

Read-only reviews: code quality and reader load, reuse and duplication,
performance and request-path behavior, Comment Sicko, simplify, ponytail.

Accepted:

- Delete restating helper docstrings. Keep provider, canonical, ordering,
  fallback, and ownership comments.
- Reuse `_CANDIDATE_ROUTE_ID`, `DEFAULT_DWELL_MINUTES`, scoring
  `_step_route_id` / `_normalized_mode`, and evidence
  `_vehicle_claim_is_layover`.
- Extract `_http_url` as https admission so `_citation_urls` does not sit
  at 11/12 over the cap.
- Inline one-call peelers that did not own a policy.

Rejected:

- Full inline of constraint-kind helpers (parked `route_status` above 10).
- Inline `_parse_route_seconds_minutes` and `_parse_route_minutes_field`
  (parked `_route_total_minutes` at 11/11 and broke the 24-survivor cap).
- Inline `_select_bus_steps` (parked `_enrich_bus_legs` at 12/12).
- Inline `_select_closer_impact` (parked `associate_events` at cognitive 13).
- Unify drifted walking overlay, digest `SUBWAY`/`BUS`/`RAIL` versus
  `TRANSIT_MODES`, TRAM matching, itinerary `duration_seconds`, and
  `to_event_point` for segments.
- Ticketmaster boolean out-param type.
- Private-to-public renames for tests.
- Metric-only helpers or file splits for length.

Performance review reported no request-path findings.

### Unresolved risks

- Pattern-index faults omit subway intermediates. Endpoints still match.
  Missing intermediates are not invented.
- A supplied Ticketmaster `dateTime` string that fails validation no longer
  falls through to local fields. Missing or non-string `dateTime` still
  uses `localDate`/`localTime`.
- Missing incident, crowd, vehicle, or route evidence still does not become
  certainty.
- Fourteen Batch 6B functions remain zero-covered. They were not in the
  four CRAP targets and were not given coverage-theater tests.
- Quality exit 1 is the 59 stale trips baseline entries plus later-batch
  debt. Worker must not shrink `quality/baseline.json`.
- Global debt exit 1 is Batches 6C through 6E. Batch 6B is
  `above_12=0` and `crap_above_30=0`.

### Codex reviewer final

Codex accepted both final repairs. A supplied invalid Ticketmaster `dateTime`
string no longer falls through to local fields. Non-string recommendation
reason codes return `None` instead of raising `TypeError`. The two focused
test files pass with 31 passed and 1 skipped.

The reviewer removed exactly 59 proven-stale entries from
`quality/baseline.json`, which reduced the file from 295 to 236 entries. No
entry was added. The generated update also lowered two surviving ceilings:
`lookup_events` moved from cyclomatic 13 to 12, and
`prepare_structural_candidates` moved from 12 to 11.

The final quality command against `c058199` exits 0 with
`approval_eligible: true` and `tests_ran: true`. Frontend tests have 314
passes. Backend tests have 1,894 passes, 21 skips, and 444 subtest passes.
New, worsened, and cognitive new or worsened violations are 0. Ruff C901 and
Ruff structural diagnostics are 0. Batch 6B is approved for commit. Batch 6C
was not started.

## Batch 6C completion

Scope checkpoint `$batchRef` =
`c8a0381420be7f341970c497b4ded7b988960be0`. Quality reference `$qualityRef`
= `c0581994e47ce1a5bbdb2d8e83fc0afd50ff1745`. The worker left HEAD at
`c8a0381` and the tree uncommitted for Codex review. Codex accepted the
implementation and removed exactly 38 stale Batch 6C entries from
`quality/baseline.json`, reducing it from 236 entries to 198. Batch 6D, 6E,
and Batch 7 were not started. Frontend production, `tools/transit/**`,
`services/trips/**`, and other `services/agent/**` modules were not edited.

After the first local commit, Codex reopened the review because net production
growth exceeded the agreed range. The correction removed unearned dispatch
and wrapper layers and reran the full quality gate before Batch 6D began.

Codex P2: `_admit_route_preparation` now returns a frozen
`RoutePreparationAdmission`. `execute` consumes named attributes. The
11-position tuple is gone.

### Changed files

Production:

- `backend/app/services/agent/tools/__init__.py`
- `backend/app/services/agent/tools/complete_turn.py`
- `backend/app/services/agent/tools/location_resolution.py`
- `backend/app/services/agent/tools/places/damn_lines.py`
- `backend/app/services/agent/tools/places/discover_places.py`
- `backend/app/services/agent/tools/places/place_reference.py`
- `backend/app/services/agent/tools/places/present_places.py`
- `backend/app/services/agent/tools/places/search_local_places.py`
- `backend/app/services/agent/tools/route/prepare_route_branches.py`
- `backend/app/services/agent/tools/route/prepare_route_options.py`
- `backend/app/services/agent/tools/route/prepare_route_persistence.py`
- `backend/app/services/agent/tools/route/present_route.py`
- `backend/app/services/agent/tools/route/present_route_commit.py`
- `backend/app/services/agent/tools/route/present_route_state.py`
- `backend/app/services/agent/tools/route/route_input.py`
- `backend/app/services/agent/tools/route/route_projection.py`

Tests and records:

- `backend/tests/test_local_discovery.py`
- `backend/tests/test_discovery_route_handoff.py`
- `backend/tests/test_single_agent_route_tools.py`
- `docs/lint-cleanup-handoff.md`
- `docs/lint-cleanup-plan.md`
- `.audit/backend-debt.json`

### Debt after the final coverage run

`py scripts/report_backend_debt.py --max-existing 12 --output .audit/backend-debt.json`

The command exits 1 because Batches 6D and 6E still have functions above
12. Judge `by_batch: 6C`.

| Metric | At `c8a0381` | After 6C review |
|---|---:|---:|
| 6C functions | 316 | 417 |
| 6C above 12 | 40 | 0 |
| 6C at 11 or 12 | 19 | 19 |
| 6C CRAP above 30 | 2 | 0 |
| 6C zero-covered | 12 | 12 |
| Branch-aware coverage | 88.6% | 88.7% |

Production growth versus `$batchRef`: 1,919 insertions and 1,141 deletions in
`backend/app/services/agent/tools` (net +778 lines). The Batch 6C function
inventory rose from 316 to 417, a net increase of 101. These totals cross the
project review triggers but stay inside the user-approved 500 to 800 net-line
range for replacement work. The refactor replaced 1,141 old production lines.

The reopened review reduced the first committed tree from 432 to 417 Batch 6C
functions. It removed operation-label projector functions, a projected-facts
function dispatcher with dummy parameters, five objective-reason check
functions, one missing-result wrapper, and two record-coercion wrappers. The
remaining additions own named provider, validation, persistence, evidence, or
lifecycle stages rather than one-call metric peelers.

### Survivors at 11 or 12

Keep each of these at 11 or 12. Another helper would only hide a short guard
or push a sibling over the 19-function cap.

| Function | Cyclo | Cog | Coverage | CRAP | Why it stays |
|---|---:|---:|---:|---:|---|
| `iter_unsupported_strict_keyword_paths` | 8 | 12 | 1.000 | 8.000 | Strict schema additionalProperties walk |
| `_parse_goal_keys` | 10 | 11 | 0.737 | 11.822 | Complete-turn goal-key admission |
| `resolve_discovery_place` | 12 | 7 | 0.867 | 12.341 | Opaque discovery place load |
| `_resolved_discovery_record` | 12 | 3 | 0.889 | 12.198 | Discovery record coordinate admission |
| `_read_current` | 12 | 12 | 0.812 | 12.949 | Damn Lines current-observation cache read |
| `_verify` | 10 | 11 | 0.929 | 10.036 | Named-place verification search |
| `_interleaved_sources` | 9 | 12 | 1.000 | 9.000 | Round-robin discovery sources |
| `_owned_discovery` | 12 | 8 | 0.926 | 12.059 | Session-owned discovery set load |
| `try_deterministic_fallback` | 12 | 10 | 0.938 | 12.035 | Place fallback text |
| `prepare_destination_branches` | 11 | 8 | 0.950 | 11.015 | Per-branch route preparation |
| `_validated_branch_ids` | 11 | 9 | 0.765 | 12.576 | Comparison destination-id admission |
| `_resolve_destination_state` | 11 | 7 | 0.933 | 11.036 | Destination options plus accepted label |
| `canonical_facts_with_fallback` | 11 | 11 | 0.944 | 11.021 | Invalid or dominated selection correction |
| `_route_reason_error` | 11 | 10 | 0.875 | 11.236 | Structured route-reason claims |
| `_candidate_discovery_place_id` | 11 | 7 | 0.812 | 11.798 | Selected candidate opaque place bind |
| `_load_canonical_candidate` | 12 | 11 | 0.929 | 12.052 | Stored itinerary and multi-stop snapshot |
| `_emit_recommended_card` | 11 | 1 | 1.000 | 11.000 | Passenger card projection |
| `_boarding_inputs` | 12 | 10 | 1.000 | 12.000 | First-boarding input assembly |
| `_validated_pattern_context` | 12 | 8 | 0.846 | 12.524 | Unique headsign-matching pattern only |

### Fixed-point CRAP targets

- `search_local_places.execute`: request body, NYC place admission, and page
  token are named helpers. Now 9/5, coverage 0.769, CRAP 9.995.
- `reconcile_first_boarding_timing`: catchable offset, first transit index,
  component totals, and clock stamping are named helpers. Catchable minutes
  still include access walk and are not double-counted. Now 10/9, coverage
  0.882, CRAP 10.163.

### Required contract repair

`place_reference.execute` no longer reads `discovery_set_id`. Presented-place
lookup and active-discovery-set fallback live in `_resolve_owned_place`.
Tests exercise the public `get_place_details` path.

### Owned tests

From the repository root:

```
$env:PYTHONPATH = (Resolve-Path 'backend').Path
$env:APP_KEY = 'dummy'
$env:ANTHROPIC_API_KEY = 'dummy'
$env:SMARTROUTE_ENV = 'test'
$env:AGENT_ALLOW_MEMORY_SESSIONS = '1'
$files = @(
    'backend/tests/test_local_discovery.py',
    'backend/tests/test_presented_entity_registry.py',
    'backend/tests/test_discovery_route_handoff.py',
    'backend/tests/test_present_places.py',
    'backend/tests/test_present_places_queue.py',
    'backend/tests/test_discover_places.py',
    'backend/tests/test_discover_places_queue_evidence.py',
    'backend/tests/test_damn_lines.py',
    'backend/tests/test_complete_turn.py',
    'backend/tests/test_route_itinerary_contract.py',
    'backend/tests/test_route_endpoint_resolution_policy.py',
    'backend/tests/test_present_route_correction.py',
    'backend/tests/test_present_route_framing.py',
    'backend/tests/test_present_route_reservation.py',
    'backend/tests/test_agent_tools.py',
    'backend/tests/test_agent_tools_p1.py',
    'backend/tests/test_agent_tools_p2.py',
    'backend/tests/test_single_agent_route_tools.py',
    'backend/tests/test_single_agent_route_what_if.py',
    'backend/tests/test_single_agent_route_availability.py',
    'backend/tests/test_single_agent_route_multistop.py',
    'backend/tests/test_route_option_assembly.py',
    'backend/tests/test_route_identity_gate.py',
    'backend/tests/test_discovery_route_branch_commit.py',
    'backend/tests/test_active_temporary_route_presenter.py',
    'backend/tests/test_agent_route_decision_reliability.py',
    'backend/tests/test_route_option_projection_grounding.py',
    'backend/tests/test_agent_loop_route_execution.py',
    'backend/tests/conversation/test_conversation_discovery_route.py',
    'backend/tests/test_discovery_route_provider_handoff.py'
)
py -m pytest @files -q --basetemp .pytest-batch6c-forward
$reverse = @($files)
[array]::Reverse($reverse)
py -m pytest @reverse -q --basetemp .pytest-batch6c-reverse
```

Forward: 313 passed, 52 subtests. Reverse: 313 passed, 52 subtests.

### Red-before-green for new tests

Restoring a shadow `tool_input["discovery_set_id"]` path made
`test_hidden_discovery_set_id_does_not_select_a_set` fail with
`discovery set is unknown, expired, or not owned`. Removing that path made
the test pass by resolving the presented or active-set place instead of the
invented set id. `test_presented_place_rebinds_its_source_set_for_followups`
and `test_unknown_or_expired_active_set_leaves_context_unchanged` cover the
presented-place and safe-failure paths through `place_reference.execute`.

`test_present_reads_evidence_from_an_active_legacy_candidate_set` exercises
the public route presenter with a candidate record that predates the current
evidence envelope. Candidate records can remain active for the 900-second
store lifetime across a deployment. The test protects that compatibility path
without pinning private record-coercion helpers.

### Full backend and frontend

`py scripts/check_quality.py --quality-ref c8a0381420be7f341970c497b4ded7b988960be0`

Exit 0. `approval_eligible: true`. `tests_ran: true`. New 0. Worsened 0.
Cognitive new or worsened 0. Ruff C901 0. Ruff structural 0. Stale 0.
Backend 1,895 passed, 21 skipped, 444 subtests. Frontend 314 passed.

Cognitive-only against `$batchRef`: new or worsened 0. The earlier
reviewer-owned baseline shrink against `$qualityRef` also exited 0.

### Reviewer-removed Batch 6C baseline IDs

Codex removed exactly these 38 entries after review. No baseline entry was
added or widened. The generated update also lowered the surviving ceilings
for `canonical_facts_with_fallback`, `_candidate_discovery_place_id`, and
`_validated_pattern_context`.

```
python:backend/app/services/agent/tools/__init__.py:_check_transit_label#0
python:backend/app/services/agent/tools/__init__.py:_discover_places_label#0
python:backend/app/services/agent/tools/complete_turn.py:_projected_facts#0
python:backend/app/services/agent/tools/complete_turn.py:execute#0
python:backend/app/services/agent/tools/location_resolution.py:_route_qualified_station#0
python:backend/app/services/agent/tools/location_resolution.py:resolve_named_place#0
python:backend/app/services/agent/tools/location_resolution.py:resolve_named_point#0
python:backend/app/services/agent/tools/places/damn_lines.py:_aggregate_history#0
python:backend/app/services/agent/tools/places/damn_lines.py:_fetch_history_rows#0
python:backend/app/services/agent/tools/places/damn_lines.py:_install_history#0
python:backend/app/services/agent/tools/places/discover_places.py:_queue_digest#0
python:backend/app/services/agent/tools/places/discover_places.py:_verify#0
python:backend/app/services/agent/tools/places/place_reference.py:execute#0
python:backend/app/services/agent/tools/places/present_places.py:_destination_selection_replay_allowed#0
python:backend/app/services/agent/tools/places/present_places.py:_emit_place_presentation#0
python:backend/app/services/agent/tools/places/present_places.py:_normalize_reasons#0
python:backend/app/services/agent/tools/places/present_places.py:_queue_presentation#0
python:backend/app/services/agent/tools/places/present_places.py:_rebind_researched_details#0
python:backend/app/services/agent/tools/places/present_places.py:_selected_places#0
python:backend/app/services/agent/tools/places/present_places.py:_validated_selections#0
python:backend/app/services/agent/tools/places/search_local_places.py:execute#0
python:backend/app/services/agent/tools/route/prepare_route_branches.py:resolve_destination_options#0
python:backend/app/services/agent/tools/route/prepare_route_options.py:_finalize_branch_candidates#0
python:backend/app/services/agent/tools/route/prepare_route_options.py:execute#0
python:backend/app/services/agent/tools/route/prepare_route_persistence.py:_candidate_set_payload#0
python:backend/app/services/agent/tools/route/prepare_route_persistence.py:_place_match_key#0
python:backend/app/services/agent/tools/route/prepare_route_persistence.py:_public_branch_coverage#0
python:backend/app/services/agent/tools/route/prepare_route_persistence.py:_update_trip_state#0
python:backend/app/services/agent/tools/route/present_route.py:_accepted_route_replay#0
python:backend/app/services/agent/tools/route/present_route.py:_requested_framing#0
python:backend/app/services/agent/tools/route/present_route_commit.py:activate_stored_discovery_context#0
python:backend/app/services/agent/tools/route/present_route_state.py:_candidate_binding#0
python:backend/app/services/agent/tools/route/present_route_state.py:_candidate_evidence#0
python:backend/app/services/agent/tools/route/present_route_state.py:_destination_identity_groups#0
python:backend/app/services/agent/tools/route/present_route_state.py:_load_candidate_entry#0
python:backend/app/services/agent/tools/route/present_route_state.py:canonical_facts#0
python:backend/app/services/agent/tools/route/route_input.py:merge_route_preparation_input#0
python:backend/app/services/agent/tools/route/route_projection.py:reconcile_first_boarding_timing#0
```

### Review suggestions

Primary-agent self-review after the last green cluster. No writer subagent
touched the tree.

Accepted:

- Drop the `isinstance(place, dict)` swallow in `_nyc_provider_place`. A
  non-dict Places row must still raise into `execute` and fail the payload
  as malformed.
- Keep `_public_text` as the public-digest blank-to-default parse after the
  FURB110 `or` form.
- Codex P2: return frozen `RoutePreparationAdmission` from
  `_admit_route_preparation` and consume named attributes in `execute`.
  Several adjacent values share compatible types, so a positional tuple
  could silently swap destination-set, label, or waypoint state.
- Replace four operation-label functions and their function dispatcher with
  one data table in `_check_transit_label`.
- Replace the projected-facts function dispatcher and dummy `outcome`
  parameters with direct branches to the three named projection policies.
- Inline `_record_list` and `_record_dict`. Protect the cross-deployment
  legacy candidate shape through the public route presenter.
- Remove five one-line objective-reason functions, `_missing_place_result`,
  and other one-call wrappers that did not own policy.

Rejected:

- Splitting `prepare_route_persistence.py` or `present_places.py` on line
  count. Each file still owns one lifecycle.
- Tests for private helpers. Public `place_reference.execute` and existing
  route/place tests already pin the behavior.
- Inlining `_leg_seconds`. It owns itinerary-second parsing at five repeated
  callsites.

### Preserved behavior

Strict tool schemas. Session ownership. Opaque discovery, place, route, and
candidate identities. Presented-place lookup. Active-discovery-set fallback.
Evidence binding. Route-preparation ownership. Candidate identity and
selected-candidate binding. Passenger redaction. Canonical itinerary
ownership in trips, not agent tools. Model-led capability choice. Tool
ordering. Timeout and unavailable behavior. Deterministic fallback. No
hidden `discovery_set_id` input. Numeric GTFS direction is not interpreted
in pattern validation.

### Unresolved risks

- Zero-covered 6C functions stayed at 12. The public route-presenter test
  covers the former zero-covered legacy candidate path. Overall branch-aware
  coverage rose to 88.7%.
- `quality/baseline.json` now has 198 entries. The reviewer-owned quality run
  exits 0 with `approval_eligible: true`.
- Global debt exit 1 is Batches 6D and 6E. Batch 6C is `above_12=0` and
  `crap_above_30=0`.

## Batch 6D completion

Scope checkpoint `$batchRef` =
`676ff134863a21a8f247c1b78b82edf1c477c844`. Quality reference `$qualityRef`
= `c0581994e47ce1a5bbdb2d8e83fc0afd50ff1745`. The worker left HEAD at
`676ff13` and the tree uncommitted for Codex review. Codex repaired one
production regression, corrected one stale provider test seam, removed 14
unearned helper functions, and accepted the resulting tree. The reviewer
removed exactly 30 stale Batch 6D entries from `quality/baseline.json`, which
reduced it from 198 to 168 entries. No entry was added or widened. Batch 6E
and Batch 7 were not started.

### Changed files

Production:

- `backend/app/services/agent/tools/transit/accessibility_status.py`
- `backend/app/services/agent/tools/transit/check_area_conditions.py`
- `backend/app/services/agent/tools/transit/check_transit.py`
- `backend/app/services/agent/tools/transit/direction.py`
- `backend/app/services/agent/tools/transit/evidence.py`
- `backend/app/services/agent/tools/transit/evidence_binding.py`
- `backend/app/services/agent/tools/transit/evidence_projection.py`
- `backend/app/services/agent/tools/transit/lookup_arrivals_bus.py`
- `backend/app/services/agent/tools/transit/lookup_arrivals_common.py`
- `backend/app/services/agent/tools/transit/lookup_arrivals_subway.py`
- `backend/app/services/agent/tools/transit/present_transit.py`
- `backend/app/services/agent/tools/transit/transit_snapshot.py`
- `backend/app/services/agent/tools/transit/venue_crowd_window.py`

Tests:

- `backend/tests/test_agent_tools.py`
- `backend/tests/test_check_transit.py`
- `backend/tests/conversation/test_conversation_multi_intent_tool_sequencing.py`

Records:

- `docs/lint-cleanup-handoff.md`
- `docs/lint-cleanup-plan.md`
- `.audit/backend-debt.json`
- `quality/baseline.json`

### Debt after the final coverage run

`py scripts/report_backend_debt.py --max-existing 12 --output .audit/backend-debt.json`

The command exits 1 because Batch 6E still has 53 functions above 12. Judge
`by_batch: 6D`.

| Metric | At `676ff13` | Worker handoff | After Codex review |
|---|---:|---:|---:|
| 6D functions | 237 | 335 | 321 |
| 6D above 12 | 35 | 0 | 0 |
| 6D at 11 or 12 | 13 | 16 | 17 |
| 6D CRAP above 30 | 2 | 1 | 1 |
| 6D zero-covered | 3 | 6 | 6 |
| Branch-aware coverage | 88.7% | 88.7% | 88.7% |

The worker tree had 1,687 insertions and 951 deletions in production, net
+736, with 98 additional functions. Codex reduced the final delta to 1,559
insertions and 993 deletions, net +566, with 84 additional functions. The
final totals cross the project review triggers but stay inside the accepted
500 to 800 net-line replacement range. The change replaces 993 old production
lines. The remaining helpers own provider access, admission, matching,
projection, aggregation, presentation, or recovery stages.

The remaining CRAP-above-30 row is
`evidence_projection._area_condition_fields` at cyclomatic 8 with zero
coverage in `backend/.coverage`. The worker did not add a private-helper
test. CRAP has no absolute ceiling. The two starting CRAP targets,
`lookup_arrivals_bus.execute` and
`evidence_binding.bind_accessibility_target`, are no longer above 30.

### Subtracted peelers

- Inlined `_build_events` after arrivals and status event builders owned the
  presentation policy.
- Inlined `_has_unscoped_scope` into `_with_direction_caveat`.
  `_has_unscoped_items` remains the shared unscoped-finding check.
- Collapsed two metadata-copy layers into `_copy_lookup_metadata`.
- Inlined `_arrivals_fields` and `_arrival_lookups` into the readable public
  arrivals flow.
- Removed the nine-argument `_append_status_match` wrapper and its one-call
  coverage wrapper.
- Replaced two operation function-dispatch tables with direct branches and
  removed the one-line `_fact_text` projector.
- Inlined trivial accessibility, direction-label, distance, affected-route,
  and result-shape adapters when their caller stayed within the ceiling.
- Removed `_pattern_index_maps`; GTFS map admission now stays with the one
  pattern-context owner.

Codex removed 14 functions from the worker tree. An attempted fifteenth
removal made `_admit_venue_window` a new cyclomatic-12 function. The quality
gate rejected that form, so `_event_timing_unconfirmed` remains as a named
admission policy.

### Comment Sicko

Codex accepted the comment-only deletions and restored concise reasons for
the five broad fail-open index recoveries. It also retained the EWR bounds,
opaque numeric GTFS direction, trips-owned crowd-table, MTA wrapper-key, and
accessibility capture-timestamp explanations. No restating docstring was
restored.

Accepted in-scope code fixes:

- Dropped the bytes fallback in `transit_snapshot._alert_provider_payload`.
  `fetch_service_alerts(..., with_metadata=True)` already returns a dict.
- Replaced the solo `except Exception` around area event lookup with
  `asyncio.gather(..., return_exceptions=True)`, matching the two-task path.
- Fixed `_collect_status_findings` so every matching candidate contributes
  alerts, incidents, signals, and coverage after the first observation
  timestamp. The previous short-circuit skipped later candidates.
- Updated the multi-intent conversation seam to return the documented
  `with_metadata=True` alert-provider shape. The production bytes fallback
  remains deleted.

Rejected as 6D behavior changes. Fail-open index recoveries stay, encoded
the same way as `location_resolution._stops_on_named_route`:

- Removing `_OUTSIDE_NYC_KNOWN_PLACES`. EWR is inside `NYC_BOUNDS` and
  `test_outside_areas_are_rejected_after_resolution_without_provider_calls`
  pins the carve-out.
- Narrowing `except Exception` on nearby stops, incident index, subway stop
  index, schedule lookup, and child-stop ids. Those recoveries stay empty or
  unavailable. A typed provider-fault type is out of this batch. Each
  `# noqa: BLE001` now carries the local fail-open reason.

GTFS and incident index methods that return empty or unavailable instead of
raising would let those noqas die. That work is out of Batch 6D.

### Survivors at 11 or 12

Keep each of these at 11 or 12. Another helper would hide a short guard or
restore a peeler removed during review.

| Function | Cyclo | Cog | Coverage | CRAP | Why it stays |
|---|---:|---:|---:|---:|---|
| `check_area_conditions._nearby_stop_context` | 10 | 12 | 0.650 | 14.287 | Optional GTFS index and bounded stop admission |
| `check_transit._candidate_leg_direction` | 8 | 11 | 1.000 | 8.000 | Candidate-leg semantic direction resolution |
| `check_transit.arrivals` | 11 | 6 | 0.833 | 11.560 | Multi-route lookup orchestration after two wrappers were inlined |
| `check_transit.execute` | 12 | 9 | 0.917 | 12.083 | Public operation admission and dispatch |
| `check_transit.grounding_succeeded` | 11 | 8 | 0.929 | 11.044 | Per-operation evidence success policy |
| `evidence_binding._official_alert_rows` | 10 | 11 | 0.833 | 10.463 | Official alert projection and route filtering |
| `evidence_binding._official_alert_ids` | 10 | 12 | 0.750 | 11.562 | Exact comparable alert-ID extraction |
| `evidence_binding._incident_projection` | 12 | 11 | 1.000 | 12.000 | Bounded route-scoped incident projection |
| `evidence_matching._typed_concern_values` | 6 | 12 | 1.000 | 6.000 | Typed concern normalization |
| `evidence_projection._event_lines` | 10 | 11 | 0.929 | 10.036 | Bounded passenger event rendering |
| `evidence_projection.safe_accessibility` | 11 | 7 | 1.000 | 11.000 | Passenger-safe accessibility binding projection |
| `evidence_projection.safe_unconfirmed_signal` | 11 | 10 | 1.000 | 11.000 | Bounded vehicle-signal projection |
| `lookup_arrivals.execute` | 12 | 6 | 0.667 | 17.333 | Mode dispatch and request validation |
| `lookup_arrivals_bus.execute` | 12 | 8 | 0.833 | 12.667 | Bus provider fetch, parse, and result lifecycle |
| `lookup_facts._find_section` | 12 | 12 | 1.000 | 12.000 | Bounded fact-section match policy |
| `transit_snapshot._signal_rows` | 11 | 9 | 0.857 | 11.353 | Provider-result and route admission |
| `transit_snapshot.collect_service_status` | 11 | 10 | 0.800 | 11.968 | Parallel alert, vehicle, and incident assembly |

### Verification

Public pin forward and reverse:

`backend/tests/test_lookup_arrivals.py`
`backend/tests/test_check_transit.py`
`backend/tests/test_transit_evidence.py`
`backend/tests/test_check_area_conditions.py`
`backend/tests/test_present_transit.py`
`backend/tests/test_agent_transit_direction_reliability.py`
`backend/tests/test_agent_evidence_binding_reliability.py`
`backend/tests/test_area_condition_incidents.py`
`backend/tests/test_agent_loop_transit_grounding.py`
`backend/tests/test_agent_tools.py`

Forward: 130 passed, 26 subtests. Reverse: 130 passed, 26 subtests.

The public regression
`test_service_status_reuses_findings_from_every_matching_candidate` failed
first with only the first official alert ID. It passes with both candidate
alert IDs after the short-circuit repair. The 15 multi-intent conversation
tests pass after the provider seam correction. The venue crowd-window file
passes with the 32-test `test_agent_tools_p1.py` pin.

Global Ruff: all checks passed.

Cognitive-only against `$batchRef`: new or worsened 0.

Full quality against `$qualityRef`:

```
py scripts/check_quality.py --update-baseline --quality-ref c0581994e47ce1a5bbdb2d8e83fc0afd50ff1745
```

Exit 0. `approval_eligible: true`. `tests_ran: true`. New 0. Worsened 0.
Cognitive new or worsened 0. Ruff C901 0. Ruff structural 0. Stale 0. Backend
1,896 passed, 21 skipped, 444 subtests. Frontend 314 passed.

The reviewer removed these 30 stale Batch 6D baseline IDs:

```
python:backend/app/services/agent/tools/transit/accessibility_status.py:_extract_outage_records#0
python:backend/app/services/agent/tools/transit/accessibility_status.py:execute#0
python:backend/app/services/agent/tools/transit/check_area_conditions.py:_incident_evidence#0
python:backend/app/services/agent/tools/transit/check_area_conditions.py:_safe_incidents#0
python:backend/app/services/agent/tools/transit/check_area_conditions.py:execute#0
python:backend/app/services/agent/tools/transit/check_transit.py:_candidate_direction#0
python:backend/app/services/agent/tools/transit/check_transit.py:accessibility#0
python:backend/app/services/agent/tools/transit/check_transit.py:prepare_direction#0
python:backend/app/services/agent/tools/transit/direction.py:_route_contexts#0
python:backend/app/services/agent/tools/transit/direction.py:resolve_direction#0
python:backend/app/services/agent/tools/transit/evidence.py:_arrival_row#0
python:backend/app/services/agent/tools/transit/evidence.py:build_evidence_set#0
python:backend/app/services/agent/tools/transit/evidence_binding.py:_decision_status_data#0
python:backend/app/services/agent/tools/transit/evidence_binding.py:bind_accessibility_target#0
python:backend/app/services/agent/tools/transit/evidence_binding.py:decision_evidence_for_status#0
python:backend/app/services/agent/tools/transit/evidence_projection.py:_safe_catchability#0
python:backend/app/services/agent/tools/transit/evidence_projection.py:accessibility_text#0
python:backend/app/services/agent/tools/transit/evidence_projection.py:arrivals_text#0
python:backend/app/services/agent/tools/transit/evidence_projection.py:operation_facts#0
python:backend/app/services/agent/tools/transit/evidence_projection.py:operation_facts_text#0
python:backend/app/services/agent/tools/transit/evidence_projection.py:safe_result#0
python:backend/app/services/agent/tools/transit/lookup_arrivals_common.py:_arrival_payload#0
python:backend/app/services/agent/tools/transit/lookup_arrivals_subway.py:_resolve_stop#0
python:backend/app/services/agent/tools/transit/lookup_arrivals_subway.py:execute#0
python:backend/app/services/agent/tools/transit/present_transit.py:StatusView.from_evidence#0
python:backend/app/services/agent/tools/transit/present_transit.py:_build_events#0
python:backend/app/services/agent/tools/transit/present_transit.py:_systemwide_alert_text#0
python:backend/app/services/agent/tools/transit/transit_snapshot.py:_incident_evidence#0
python:backend/app/services/agent/tools/transit/transit_snapshot.py:execute#0
python:backend/app/services/agent/tools/transit/venue_crowd_window.py:execute#0
```

The generated update also lowered four surviving ceilings without widening
any entry: `check_transit.arrivals` moved from 18 to 11,
`safe_accessibility` and `safe_unconfirmed_signal` moved from 15 to 11, and
`lookup_arrivals_bus.execute` moved from 39 to 12.

### Preserved behavior

Accepted-itinerary accessibility binding. Route and direction matching.
Live versus scheduled arrival provenance. Outage handling. Provider
timeouts. Graceful unavailable results. Missing live data is not confirmed
safety. Official alert IDs stay comparable for decision-evidence reuse.
Passenger text still comes from server-owned evidence projection.

### Unresolved risks

- One 6D function still shows CRAP above 30:
  `evidence_projection._area_condition_fields`. Do not add a helper test to
  hide that.
- Net production growth is +566 lines after replacing 993 old lines. Function
  inventory grew by 84 after Codex removed 14 worker helpers.
- Global debt exit 1 is Batch 6E. Batch 6D is `above_12=0`.
- `quality/baseline.json` has 168 entries. The reviewer-owned quality gate
  exits 0 with `approval_eligible: true`.

Batch 6D is committed at `2298e32`. Batch 7 was not started.

## Batch 6E completion (2026-08-30)

The worker left the implementation uncommitted for Codex review. Codex removed
dead and unearned helpers, restored useful boundary explanations, corrected a
private cross-module dependency, and accepted the resulting tree.

Starting refs: `$batchRef` / HEAD `2298e32`. `$qualityRef` `c058199`.
The worker left `quality/baseline.json` at 168 entries. The reviewer removed
exactly 43 proven-stale entries. The final file has 125 entries. No entry was
added or widened.

Scope: remaining production under `backend/app/services/agent/` excluding
`tools/**`. Six Batch 6C metadata-only files may still appear in
`git status --short`. They were not edited in this batch:
`backend/app/services/agent/tools/__init__.py`, `complete_turn.py`,
`location_resolution.py`, `places/place_reference.py`,
`places/present_places.py`, `route/present_route_state.py`.

### Starting inventory

From `scripts/report_backend_debt.py --max-existing 12 --scope backend/app/services/agent/`
`by_batch.6E` at HEAD: functions 435, `above_12` 53, `at_11_or_12` 8,
`crap_above_30` 4, `zero_covered` 10, overall backend branch coverage 88.7%.

Starting CRAP above 30: `store_candidate_set`, `_sanitized_search_scope`,
`presented_entity_registry.resolve`, `build_turn_context`.

### Ending inventory

From a fresh `coverage run --branch --source=app -m pytest` then
`scripts/report_backend_debt.py --max-existing 12`:

| Metric | Start | End |
|---|---:|---:|
| 6E functions | 435 | 547 |
| 6E `above_12` | 53 | 0 |
| 6E `at_11_or_12` | 8 | 18 |
| 6E `crap_above_30` | 4 | 0 |
| 6E `zero_covered` | 10 | 6 |
| Global production `above_12` | | 0 |
| Overall backend branch coverage | 88.7% | 88.8% |

Debt `--max-existing 12` exits 0. Cognitive vs `$batchRef` `2298e32` has
0 new and 0 worsened violations. Final quality vs `$qualityRef` `c058199`
has new 0, worsened 0, stale 0, remaining 125, and
`approval_eligible: true`. Ruff for the owned backend scope exits 0.

Net production `git diff --numstat HEAD -- backend/app/services/agent`:
2,037 insertions, 1,308 deletions, net +729. The change replaces 1,308 old
lines and stays under the accepted +800 boundary. Function count grew by 112.
Remaining helpers own named selector, parsing, persistence, retry, lifecycle,
telemetry, or aggregation policies. New functions stay cyclo and cognitive at
or below 10.

Backend pytest: 1,899 passed, 21 skipped, 444 subtests. Three new public-path
tests: `test_sources_event_casefolds_host_and_rejects_overlong_title`,
`test_open_states_keep_the_matching_next_action`,
`test_record_goal_keeps_unique_recovery_options_in_declaration_order`.

### Production files changed

21 files. None under `tools/**`.

- `backend/app/services/agent/candidate_store.py`
- `backend/app/services/agent/discovery_store.py`
- `backend/app/services/agent/events.py`
- `backend/app/services/agent/loop.py`
- `backend/app/services/agent/model/output_projection.py`
- `backend/app/services/agent/model/prompt.py`
- `backend/app/services/agent/model/request.py`
- `backend/app/services/agent/model/stream.py`
- `backend/app/services/agent/passenger_output.py`
- `backend/app/services/agent/presented_entity_registry.py`
- `backend/app/services/agent/profile.py`
- `backend/app/services/agent/public_surface.py`
- `backend/app/services/agent/session.py`
- `backend/app/services/agent/tool_input_policy.py`
- `backend/app/services/agent/transcript_store.py`
- `backend/app/services/agent/trip_state.py`
- `backend/app/services/agent/turn/completion.py`
- `backend/app/services/agent/turn/contract.py`
- `backend/app/services/agent/turn/evidence.py`
- `backend/app/services/agent/turn/stream.py`
- `backend/app/services/agent/turn/tool_round.py`

Tests: `backend/tests/test_agent_events.py`,
`backend/tests/test_completion_policy.py`,
`backend/tests/test_turn_evidence.py`. Audit: `.audit/backend-debt.json`.

### Cluster notes

Cluster 1 registries and stores: field-group extracts. `load_candidate_set`
reuses `_admit_watched_record`. `_timing_metrics` restored so new
`_comparison_option` stays at or below 10. `_borough_scope` kept because
inlining pushed `_sanitized_search_scope` to cyclo 13.

Cluster 2 model prompt, request, output, stream: Codex inlined the trivial
deadline check. Inlining the completion invariant pushed `stream_model_call`
to cognitive 14, so `_required_attempt_completion` owns that invariant.
`_projected_place_ids` stays because inlining pushed `_project_mapping` to
cognitive 13. `_accepted_route_comparison` uses an explicit empty-id guard.

Cluster 3 public policy, profiles, sessions, passenger output: sanitization
and slot extracts. Codex kept `_finite_price` local rather than reaching into
another module's private helper. `_registry_identity_fields` keeps new
`_admit_registry_entry` at or below 10. Codex inlined `_slot_place`.

Cluster 4 loop, events, turn: `_eval_bounded_binop`,
`_arithmetic_shortcut_events`, `_live_turn_events`. `_stream_turn`
passthrough inlined into `_live_turn_events`. Contract unique, known, and
acyclic checks merged into `_validate_goal_graph`. Stream token and error
peelers inlined. `_ModelPhase` and `_CapabilityPhase` store
`ModelDirective`.

### 11-or-12 survivors

18 remaining. Do not peel further. Inlining evidence is in the cluster notes.

| cyclo | cog | cov | CRAP | File:function |
|---:|---:|---:|---:|---|
| 12 | 12 | 1.000 | 12.000 | `model/stream.py:_stream_provider_events` |
| 7 | 12 | 0.875 | 7.096 | `model/stream.py:stream_model_call` |
| 12 | 11 | 0.833 | 12.667 | `presented_entity_registry.py:record` |
| 12 | 6 | 0.800 | 13.152 | `profile.py:normalize_place` |
| 12 | 11 | 0.920 | 12.074 | `tool_input_policy.py:goal_error` |
| 12 | 10 | 1.000 | 12.000 | `turn/completion.py:apply_completion` |
| 9 | 12 | 0.957 | 9.007 | `turn/completion.py:evaluate_completion` |
| 11 | 12 | 0.812 | 11.798 | `turn/contract.py:TurnContract._status_for` |
| 7 | 12 | 0.833 | 7.227 | `turn/stream.py:_capture_model_events` |
| 11 | 12 | 1.000 | 11.000 | `turn/tool_round.py:_ToolRoundExecution._validate_calls` |
| 11 | 10 | 0.824 | 11.665 | `discovery_store.py:_sanitized_search_scope` |
| 11 | 10 | 0.765 | 12.576 | `discovery_store.py:load_discovery_set` |
| 11 | 11 | 0.950 | 11.015 | `presented_entity_registry.py:resolve` |
| 11 | 9 | 1.000 | 11.000 | `profile.py:_validated_preferences` |
| 11 | 8 | 0.933 | 11.036 | `session.py:add_pending_continuation` |
| 11 | 5 | 0.944 | 11.021 | `turn/stream.py:_finish_model_iteration` |
| 7 | 11 | 1.000 | 7.000 | `turn/stream.py:_stream_react_loop` |
| 11 | 11 | 0.857 | 11.353 | `turn/stream.py:resolve_model_iteration` |

### Reviewer baseline shrink

The worker left these 43 stale IDs for Codex. The reviewer removed exactly
these entries with the fresh full-suite coverage evidence below. The generated
update also lowered eight surviving complexity or CRAP ceilings. No surviving
entry increased.

`candidate_store`: `_mark_presented_redis`, `accepted_route_comparison`,
`store_candidate_set`. `discovery_store`: `display_waypoint_labels`,
`resolve_place_reference`, `sanitized_discovery_context`,
`store_discovery_set`. `events`: `normalized_source`. `loop`:
`_eval_math_node`, `run_agent_turn`. `output_projection`:
`project_model_value`, `project_route_preparation`. `prompt`:
`build_turn_context`. `request`: `build_stream_kwargs`. `model/stream`:
`_paced_provider_iter`, `_stream_attempt`, `_web_sources`,
`stream_model_call`. `passenger_output`: `validated_activity_label`,
`validated_terminal_message`. `registry`: `_entries`, `_entry`,
`_entry_place`, `resolve_description`. `profile`: `resolve_profile_place`.
`public_surface`: `active_temporary_route_preview`, `offered_custom_tools`,
`state_valid_tool_names`. `session`: `extract_slots`, `save_session`.
`tool_input_policy`: `constrained_tool_input`,
`missing_verified_destination`. `transcript_store`:
`active_accepted_route_card`, `add_visible_events`,
`project_model_history`. `trip_state`: `_normalize`, `commit_scenario`.
`completion`: `_facts`, `_goal_progress`. `contract`:
`TurnContract.__post_init__`. `evidence`: `_record_discovery_result`,
`record_goal`. `turn/stream`: `stream_capability_iteration`.

### Frontend gate

Batch 6E did not change frontend production. From `frontend/`:

- `npm run typecheck` passed
- `npm run typecheck:scripts` passed
- `npm run test:unit` 314 passed
- transit artifact verify passed (15 station-anchor tests plus overlay,
  palette, and renderer checks)
- raw `npm run lint` still reports the 2026-08-27 ESLint backlog (193)
- `npm run lint:oxlint` still exits 1 with the 2026-08-27 Oxlint backlog
- `npm run test:release:ci` passed: 14 passed, 4 skipped, exit 0.
  First Chromium-backed run timed out 3 specs under 4 workers. A later
  `npm run test:release:ci` run of the same 18 specs passed. Browser-use
  CDP is not a substitute for this Playwright suite.

### Reviews

Quality, reuse, performance, Comment Sicko, Ponytail, Testing on the Toilet,
simplify, and blast-radius ran after the four clusters. Named policies stayed
in-file. Fail-open recoveries stayed at real boundaries. Comment Sicko
deleted restating docstrings. Anthropic grammar and tool-ordering comments
were kept. Blast-radius proof: owned live candidate sets load, foreign empty
missing expired and bad-expiry miss, registry price samples match including
nan and inf.

Codex then removed unused schema-count wrappers, constants, compatibility
entry points, session-key wrappers, and the temporary audit script. Codex
also inlined three trivial helpers, restored six boundary explanations that
state security or ownership constraints, and kept the remaining one-call
helpers only where they own a named policy or lifecycle responsibility.

The reviewer restored registry price parsing locally. The worker version
called `discovery_store._finite_price`, which coupled the registry to another
module's private implementation through an existing lazy import cycle.

### Final verification

- Focused agent regression set: 201 passed and 37 subtests passed.
- Full backend: 1,899 passed, 21 skipped, and 444 subtests passed.
- Frontend unit: 314 passed.
- Frontend typechecks and transit artifact verification passed.
- Release CI: 14 passed and 4 intentionally skipped.
- Raw ESLint still reports 193 inherited frontend findings. Raw Oxlint also
  reports the inherited frontend backlog. The quality ratchet reports no new
  or worsened frontend violations.
- Final quality exits 0 with `approval_eligible: true`, new 0, worsened 0,
  cognitive new or worsened 0, stale 0, Ruff C901 0, and Ruff structural 0.

### Remaining risk

The 11-or-12 band is 18, above the starting 8, but no function is above 12.
Batch 6E CRAP above 30 is 0. The only global CRAP item is the documented
Batch 6D `_area_condition_fields` signal. Raw frontend lint debt remains for
Batches 7 through 9. Batch 6E is Codex-approved for commit.

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
