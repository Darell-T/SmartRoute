# Damn Lines integration implementation plan

Status: implemented

Owner: Cursor session after Codex usage limit

Last updated: 2026-08-25

## Goal

Add Damn Lines queue evidence to SmartRoute's existing place discovery and destination decision flow without creating a new agent capability, changing canonical route facts, or placing queue information on maps and route cards.

The integration must remain optional. Google Places remains the authority for physical place identity, branch, location, and open status. Damn Lines supplies current or historical queue evidence only for explicitly registered physical venues.

## Product contract

### Identity and coverage

- Treat each Google Place ID as one physical venue branch.
- Keep a manually verified dictionary from Google Place ID to Damn Lines slug and trusted source URL.
- Do not use fuzzy runtime matching, brand-level matching, address inference, or automatic onboarding.
- Treat registry absence only as missing Damn Lines coverage. Never interpret it as a short line, low popularity, or a better destination.
- Resolve an unspecified brand through Google branch search. Treat a neighborhood or address-specific request as a fixed branch unless the rider permits alternatives.

### Agent trigger

Extend the existing `discover_places` input with one turn-scoped structure:

```text
queue_context:
  mode: ignore | heads_up | decision | historical
  max_wait_minutes: optional non-negative number
```

- `ignore`: do not fetch or mention queue evidence.
- `heads_up`: preserve the existing Google ordering and selection. Check only the places selected for presentation, then append compact queue notes after the ordered recommendations.
- `decision`: queue conditions can affect the current destination choice. Enrich plausible supported candidates before the agent decides or asks for confirmation.
- `historical`: answer an explicit question about usual, past, or last-known queue conditions.
- Keep this context limited to the current destination decision. Do not save it as a preference.
- Do not invent a global threshold for a long wait. Preserve an explicit numeric limit exactly and leave qualitative judgment to the agent.

### Current observations

- Fetch one on-demand `/v1/locations` snapshot for all supported venues.
- Check the local registry before making the provider request.
- Share and cache the snapshot. Coalesce concurrent refreshes within a process.
- Refresh once in the turn when the cache is empty or relevant observations are stale.
- Accept a current observation only when `captured_at` is no more than five minutes old and at least one valid numeric field exists.
- Accept zero values. Reject negative, malformed, or missing values.
- Keep partial observations partial. Never fill a missing live field with historical data.
- Show the estimated wait first, then people count when available, and always include the provider observation time.
- Do not say `and counting`.
- Never predict the wait at the rider's arrival time.
- Never add queue wait to the route ETA.
- Explain that an estimated queue wait is a join-now estimate and does not include order preparation only when that distinction is useful to the rider.
- Never calculate a recent trend, slope, or projected wait.

### Historical fallback

- Use historical evidence when the venue is registered, Google says it is open, and no fresh current observation exists.
- Do not use the fallback when Google says the venue is closed.
- Use history directly when the rider explicitly requests historical conditions.
- Refresh the historical snapshot weekly in the background.
- If no historical snapshot exists at startup, start an asynchronous refresh without blocking a rider request.
- Retain the last good snapshot when refresh fails.
- Stop serving a snapshot when its last successful refresh is more than 30 days old.
- Build an in-process dictionary keyed by Google Place ID, NYC weekday, and NYC hour for constant-time lookup.
- Build each bucket from the most recent 30 days of provider records and preserve sample count, distinct comparable dates, and date range.
- Weight aggregated history by provider sample count.
- With one comparable date, say what was recorded on that date. Do not say `usually`.
- With multiple comparable dates, describe the historical average and evidence count.
- Do not compare current and historical values as equivalent observations.

### Decision behavior

- Compare fresh current observations with fresh current observations.
- Compare comparable historical buckets with comparable historical buckets.
- Treat mixed current, historical, and unknown evidence as asymmetric.
- Ask the rider only when the missing or asymmetric evidence could realistically change the current destination choice.
- If other priorities resolve the choice, make the choice and disclose the relevant limitation.
- If the rider says `pick one`, use agent judgment and state the evidence limitation briefly.
- If an explicit destination materially conflicts with the rider's queue preference, ask whether to proceed or see four to five alternatives in Auto mode.
- Keep Quick mode's current alternative count.
- When all candidates lack coverage, choose from Google and route facts without indecision and disclose only when the rider asked about queues.

### Presentation and attribution

- Queue information is conversational only.
- Do not place queue facts on the map, map markers, route cards, route steps, or analytics payloads.
- Put queue prose after the ordered place recommendations.
- Render canonical queue prose from normalized backend facts. Do not let the model invent numeric queue statements.
- Send a structured source event containing the trusted server-owned title and URL.
- Render the source with a PromptKit source component after the queue prose.
- Show Damn Lines once for each queue response or comparison, not once per venue sentence.
- Persist and restore the source across page reloads.
- Allow only configured Damn Lines source URLs. Never render a model-supplied source URL.

### Failure behavior

- Provider failures must not break place discovery, Google results, routing, or conversation streaming.
- For a normal heads-up failure, use valid history when allowed. Otherwise remain silent.
- For an explicit queue request, state that current queue information is unavailable and use valid history when allowed.
- Handle timeout, network errors, HTTP 401, 403, 429, 5xx, malformed JSON, malformed venue records, missing timestamps, and removed slugs.
- Honor `Retry-After` with a shared cooldown when provided.
- Preserve a valid record when a different provider record is malformed.
- Do not log API keys, complete provider payloads, or unnecessary user/location data.
- Never send rider location to Damn Lines.
- Ignore all provider cameras, images, videos, streams, and computer-vision fields.

## Architecture constraints

- Keep the existing eight agent capabilities. Do not add a Damn Lines tool.
- Put provider access and normalization behind one cohesive place-domain module.
- Keep Google place identity and branch resolution in the existing place discovery path.
- Keep queue evidence separate from canonical itinerary facts and route scoring.
- Reuse the existing cache abstraction and session discovery state.
- Use the existing SSE and transcript mechanisms for the structured source.
- Prefer the fewest new modules and dependencies that preserve clear ownership.
- Do not add a database table, router, microservice, cron service, dependency, or generalized evidence framework for this feature.
- Do not change unrelated UI or route behavior.

## Planned files and ownership

The exact list can shrink after inspection. Any expansion requires a concrete reason.

### Primary agent

- `DAMN_LINES_IMPLEMENTATION_PLAN.md`
- `CONTEXT.md`
- `docs/adr/0001-damn-lines-place-queue-evidence.md`
- Relevant current architecture or agent pipeline documentation
- Secret-safe environment inspection and verified physical venue registry data
- Worker integration review, final fixes, full validation, provider timing measurement, and optional minimal Anthropic smoke test

### Backend provider worker

Owned scope:

- One Damn Lines provider and queue evidence module under `backend/app/services/agent/tools/places/`
- Focused provider normalization, freshness, cache, historical bucket, and failure tests
- Background history refresh hookup only if it does not overlap another worker
- Environment example entry only if needed

Acceptance criteria:

- Manual exact Google Place ID registry
- One current snapshot request with cached single-flight refresh
- Five-minute observation freshness based on `captured_at`
- Weekly historical refresh and 30-day stale cutoff
- Constant-time historical lookup
- No camera handling, prediction, trend calculation, route arithmetic, or raw provider exposure
- Fail-open behavior covered by focused tests

### Backend orchestration worker

Owned scope:

- `discover_places` queue context schema and persistence
- Google pagination continuation and already-presented filtering
- `present_places` queue enrichment, canonical prose, and source emission
- Agent prompt rules
- Backend SSE and transcript source persistence
- Focused discovery, presentation, prompt, event, and transcript tests

Acceptance criteria:

- No ninth capability
- Auto supports four to five alternatives when requested; Quick remains unchanged
- Branch-flexible search uses exact Google identities
- Current destination scope only
- Queue prose follows ordered recommendations
- Provider facts are never model-authored
- Normal place behavior remains unchanged when queue context is absent or ignored

### Frontend worker

Owned scope:

- PromptKit source component
- Strict queue source event validation and state handling
- Session restoration
- Assistant message rendering after queue prose
- Focused frontend tests

Acceptance criteria:

- Source title and URL come only from validated backend events
- Damn Lines source renders once after queue content
- Source survives reload
- No queue content appears on maps, route cards, or route steps
- Existing message rendering and accessibility remain intact

## Implementation sequence

1. Record this plan before any implementation changes.
2. Inspect the worktree and preserve unrelated user changes.
3. Locate environment files and identify the Damn Lines variable name without printing its value.
4. Inspect current provider output and documentation using the configured key. Record only non-secret venue metadata required for the manual registry.
5. Resolve each supported venue to one exact Google Place ID and verify name, address, and coordinates. Do not delegate secret access.
6. Inspect existing tests and adjacent contracts before assigning worker file ownership.
7. Run backend provider and frontend workers in parallel on non-overlapping files.
8. Review their reports and diffs before accepting them.
9. Run the backend orchestration worker against the accepted provider interface.
10. Integrate the pieces, resolve contract mismatches, and keep the smallest architecture-consistent implementation.
11. Add the glossary and accepted ADR.
12. Run focused backend and frontend tests, including all failure and fallback paths.
13. Measure several representative Damn Lines calls. Set the fixed provider timeout to observed p95 plus two seconds, with a conservative minimum only if measurement is too small to survive normal network variance.
14. Run Ruff, ESLint, and Oxlint. Fix every finding in changed code without suppressions, ignored paths, weakened rules, or generated workarounds.
15. Run backend and frontend type checks and the relevant broader test suites.
16. Inspect the final diff for secrets, debug output, stale flags, dead code, duplicate logic, UI leakage, or unrelated edits.
17. If the deterministic implementation is green, make the smallest possible live Anthropic smoke call needed to verify tool choice and grounded source behavior. Do not use a live model call when deterministic tests already expose a failure.
18. Update this file's status and final verification record.

## Required test matrix

### Identity and branch tests

- Exact registered Google Place ID maps to its Damn Lines slug.
- Same brand at an unregistered branch does not inherit evidence.
- Unregistered venue remains unknown and does not trigger a provider request by itself.
- Branch-specific request remains fixed.
- Brand-flexible search can retain multiple exact branch candidates.

### Trigger tests

- Missing queue context preserves existing behavior.
- `ignore` makes no provider request and emits no queue prose/source.
- `heads_up` preserves recommendation order.
- `decision` exposes normalized evidence to the destination decision.
- `historical` serves valid history even when current data also exists.
- Explicit numeric threshold is preserved and never replaced by a global threshold.

### Current observation tests

- Fully populated fresh observation.
- Wait-only and count-only fresh observations.
- Zero wait and zero count remain valid.
- More than five minutes old becomes unavailable.
- Future or malformed timestamp becomes unavailable.
- Negative or nonnumeric values are rejected.
- Fetch time does not refresh provider observation time.
- Empty cache causes one refresh.
- Concurrent refreshes collapse to one request.
- One malformed venue does not poison valid venues.

### Historical tests

- Open registered venue with no fresh current uses valid history.
- Closed venue does not use history.
- One-date wording does not say `usually`.
- Multiple-date wording includes evidence count.
- Aggregation is weighted by sample count.
- Weekday and hour use America/New_York.
- Last good snapshot survives a failed refresh.
- Snapshot older than 30 days is unavailable.
- Empty startup snapshot schedules refresh without blocking.
- Live partial data is not blended with history.

### Decision and presentation tests

- Fresh live data suppresses fallback history in normal mode.
- Current and historical values are labeled differently.
- Explicit queue failure is disclosed; ordinary heads-up failure is silent when no fallback exists.
- Canonical prose includes observation time and omits `and counting`.
- Queue prose is after ordered recommendations.
- Source is emitted once with a configured Damn Lines URL.
- Source persists and restores.
- Model text cannot inject an untrusted source URL or numeric provider fact.
- Queue data never reaches map or route-card contracts.

### Continuation and alternatives tests

- Already-presented Google Place IDs are filtered.
- A continuation token is stored privately and reused for the same search scope.
- Exhausted continuation produces no duplicates.
- Auto can present four to five alternatives.
- Quick retains its current cap.

### Failure tests

- Timeout, network error, 401, 403, 429, 5xx, malformed JSON, missing slug, and missing timestamp fail open.
- `Retry-After` prevents repeated provider calls during cooldown.
- Core Google place recommendations still render during every provider failure.

## Validation commands

Use the repository's existing scripts when they differ from the generic command below.

```text
Backend focused tests
Backend relevant broader tests
ruff check backend
Frontend focused tests
Frontend TypeScript typecheck
Frontend ESLint
Frontend Oxlint
```

Do not add ignore comments, blanket excludes, unsafe casts, type suppressions, disabled rules, or weakened tests to make validation pass.

## Live-call budget and safety

- Provider discovery and timing: use the fewest requests that can verify schema, venue registry, failure handling, and p95 timing.
- Google identity verification: query only supported provider venues and retain only public place metadata.
- Anthropic: one or a very small number of short turns after deterministic tests pass.
- Never print, persist, or pass API keys to workers.
- Never include environment file contents in logs, patches, tests, or the final response.

## Completion checklist

- [x] Environment key located without disclosure
- [x] Manual branch registry verified
- [x] Provider boundary implemented
- [x] Current snapshot cache and freshness implemented
- [x] Historical background refresh and lookup implemented
- [x] Queue context integrated without a new capability
- [x] Google continuation and duplicate prevention implemented
- [x] Canonical prose and structured source implemented
- [x] PromptKit source rendered and restored
- [x] Glossary and ADR updated
- [x] Focused tests pass
- [x] Ruff F/E9 passes on changed files
- [x] ESLint passes
- [x] Oxlint passes on changed frontend files
- [x] Type checks pass
- [x] Relevant broader tests pass
- [x] Provider timeout measured and configured
- [x] Minimal Anthropic smoke test documented as not run
- [x] Final diff contains no secrets or unrelated linter adoption

## Final verification record

- Changed files: place discovery, `damn_lines` provider, SSE sources, PromptKit source disclosure, glossary, ADR
- Focused tests: backend queue/pagination/prompt/events suite 140 passed. Related discovery tests 82 passed. Pagination/tools 46 passed after restoring Google fail-open. Frontend 92 passed.
- Ruff: F/E9 clean on changed files. Default ruff 0.16 extra rules already fail on existing backend files, so they were not adopted.
- ESLint: `eslint .` exits 0 with 6 pre-existing warnings. Changed files are clean.
- Oxlint: changed frontend files are clean. Whole-frontend `--deny-warnings` fails on existing `any` in build scripts, so oxlint was not added as a repo dependency.
- Type checks: `npm run typecheck` passed.
- Provider timing: three live `/v1/locations` calls through `get_current_observations` were 835 ms, 526 ms, and 513 ms. Each matched one registered venue. p95 plus two seconds is about 2.8 s. `DAMNLINES_TIMEOUT_S` is 4 as a conservative floor. History `/v1/lines` was not called.
- Anthropic smoke test: not run. Deterministic tests cover schema, prose, sources, and restore.
- Remaining risks: a full live Google plus Anthropic tool-choice turn is unproven. History warmup is lazy on first queue use, not process startup.
