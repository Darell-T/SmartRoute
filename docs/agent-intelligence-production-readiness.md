# SmartRoute agent intelligence production-readiness report

Date: 2026-07-25
Branch: `feat/intelligence-validation-replays`

## P0 completion pass

The production request path now has a centralized Claude capability policy,
request-shape diagnostics, structured SDK error extraction, and one explicit
application retry layer. The Anthropic client disables SDK retries. HTTP
400/401/402/403/404 failures stop after one application attempt; connection,
timeout, rate-limit, overload, and 5xx failures retain bounded retries.

The inspected Sonnet 5 production request contains:

- model `claude-sonnet-5`;
- an intent-scoped strict-tool profile; route planning exposes only
  `plan_trip` and `accessibility_status` (10 optional schema parameters),
  below the provider limit of 24;
- no manual `thinking` field;
- no `temperature`, `top_p`, or `top_k`;
- no assistant prefill;
- exact unmodified assistant content blocks on tool-result continuations;
- `max_tokens=900` on ordinary Auto rounds and `300` on wrap-up.

Safe failure telemetry now records only status, Anthropic error type, sanitized
message, request ID, model, tool presence/count, thinking presence, sampling
field names, output-token cap, and attempt. It excludes prompts, rider text,
tool inputs, coordinates, headers, URLs, and credentials. Typed SSE failures
stop the frontend thinking state and cannot create a route card.

Route planning now emits one canonical `selection_decision`. The same selected
candidate ID/index is carried by the tool result used for narration, the
recommended route card, canonical itinerary, first-leg enrichment path, map
handoff, active-trip session record, and bounded selection log. A deterministic
Ticketmaster test changes the winner and asserts these identities remain equal.

Configuration validation requires a server-side Anthropic credential whenever
the agent is enabled, rejects a public Anthropic credential, keeps Auto/Quick
model IDs centralized, and preserves unknown-mode normalization to Auto.

### P0 verification evidence

```text
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
542 passed, 1 skipped

frontend complete unit suite
161 passed, 0 failed

frontend\node_modules\.bin\tsc.cmd --noEmit
PASS

frontend\node_modules\.bin\eslint.cmd .
PASS — 0 errors, 22 pre-existing warnings

frontend\node_modules\.bin\next.cmd build
PASS — compiled, typechecked, generated 12/12 static pages
```

### Credentialed checks

A user-run production request exposed the historical Anthropic 400 cause: all
eight tool schemas were sent together and contained 30 optional parameters,
while the provider accepts at most 24. Request construction now selects tools
deterministically by intent. Route planning exposes only `plan_trip` and
`accessibility_status`; a live end-to-end replay was accepted by Sonnet, called
`plan_trip`, and completed with a canonical Q itinerary and matching narration.

The opt-in Ticketmaster smoke test also completed successfully with the configured
server-side credential and returned five normalized events. A Columbus Circle
replay completed with `event_evidence_status=no_relevant_events`, proving that
the provider was available even though no event survived the route/time/distance
association rules for that itinerary.

## P1 completion pass

All model- and scoring-facing live inputs now use one `EvidenceEnvelope`
contract with source, observation time, optional expiry, current/stale/
unavailable status, and payload. Expired or unavailable payloads are removed
before advisor/model input while the bounded provenance remains visible.
This covers explicit arrivals, MTA alerts, subway and bus vehicle evidence,
Ticketmaster event impacts, and incident-advisor evidence. Empty current
evidence remains distinct from unavailable evidence.

Quick now escalates to Auto only from allowlisted deterministic signals:
unresolved places, ambiguous stations/destinations, unsatisfied mandatory
constraints, conflicting mandatory evidence, effectively tied final scores,
or a required-tool failure with an Auto recovery path. The escalation is
one-way and capped at one per turn. Existing tool results remain in the
message sequence, so escalation does not repeat already-fetched evidence.
Turn traces and bounded logs record initial mode, final mode, and reason.

The arrivals tool now falls back from absent, unparseable, or expired subway
GTFS-RT to a separately labeled static-GTFS schedule. The schedule index
handles NYC time, prior-day times beyond 24:00, calendar ranges, added/removed
calendar exceptions, frequency headways, station-complex child stops, and
direction. It is built offline by
`backend/scripts/build_scheduled_arrival_artifact.py`, loaded once at startup,
and never served after its validity date. The repository's older partial GTFS
database does not contain calendars or arrival times, so it is not treated as
a valid schedule and no scheduled production result is claimed until a fresh
artifact is generated.

Production shadow comparison was already non-user-facing, fail-closed, and
privacy-minimized. It now also applies `ROUTE_SHADOW_SAMPLE_RATE` (default
0.05); disabled, unsampled, timeout, evaluator failure, and sink failure paths
still return the exact displayed result object.

### P1 measurement boundary

The required 30 paired Auto/Quick staging traces were not executed because
this environment did not authorize sending the configured Anthropic
credential. No Quick latency or cost advantage is claimed. The deterministic
suite validates trace fields, budgets, ordinary no-escalation behavior, and a
single evidence-reusing escalation; hosted p50/p95 measurements remain an
approved-environment follow-up.

### P1 verification evidence

```text
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
556 passed, 1 skipped

frontend complete unit suite
162 passed, 0 failed

frontend\node_modules\.bin\tsc.cmd --noEmit
PASS

frontend\node_modules\.bin\eslint.cmd .
PASS — 0 errors, 22 pre-existing warnings

frontend\node_modules\.bin\next.cmd build
PASS — compiled, typechecked, generated 12/12 static pages
```

The production build used a local empty `/api/subway-stops` fixture because
the checked-in frontend environment targets a loopback backend that was not
running. The first unmodified build attempt exhausted that proxy's three
65-second retries. The fixture validates the production compilation and static
generation path only; it is not evidence about live subway-stop data.

## Executive finding

Before this pass, Ticketmaster was a normalized standalone agent tool but was not
connected to candidate scoring. Auto and Quick used the same conversational model
and operational budgets. Arrival data existed in transit-facing UI paths but the
agent had no deterministic arrival tool. Failed trips were represented only by
conversation history.

The implementation now keeps one planning pipeline while adding:

- centralized Auto/Sonnet and Quick/Haiku policies;
- deterministic intent requirements for routes, places, events, and arrivals;
- candidate-aware Ticketmaster event evidence and a bounded route penalty;
- structured active-trip and unfinished-trip state;
- canonical NYC destination aliases;
- a GTFS/GTFS-RT-backed arrival lookup with first-leg catchability;
- a compact arrival SSE event consumed by the existing chat UI;
- safe provider error categories and bounded operational telemetry.

The deterministic backend suite passes. The live Ticketmaster credential is
configured locally, but the single-request verification was blocked by the runtime
network approval/usage gate. No live-provider result or latency claim is made.

## Request and response architecture

```text
ChatComposer / useAgentChat
  -> POST /api/agent/chat (response_presentation included)
  -> AgentChatRequest normalizes unknown mode to Auto
  -> run_agent_turn
  -> centralized AgentModePolicy + deterministic intent/evidence policy
  -> structured session context (slots, active trip, pending trip)
  -> shared model/tool loop
       -> destination aliases / POI lookup
       -> plan_trip
            -> Google route candidates
            -> MTA alerts + stalled vehicles  ┐ concurrent
            -> Ticketmaster route hubs        ┘ when crowds are requested
            -> production normalization
            -> deterministic candidate score
            -> route-advisor selection/explanation
            -> optional first-leg arrivals
       -> lookup_arrivals for explicit arrival questions
  -> SSE token/tool/arrival_card/route_card events
  -> useAgentChat reducer
  -> existing chat text, compact arrival result, recommended route card
```

Main implementation files:

- `backend/app/routers/agent_chat.py`
- `backend/app/services/agent/loop.py`
- `backend/app/services/agent/policy.py`
- `backend/app/services/agent/intelligence.py`
- `backend/app/services/agent/session.py`
- `backend/app/services/agent/tools/plan_trip.py`
- `backend/app/services/agent/tools/event_lookup.py`
- `backend/app/services/agent/tools/lookup_arrivals.py`
- `backend/app/services/trips/event_crowd.py`
- `backend/app/services/trips/scoring.py`
- `frontend/lib/agent-chat-stream.ts`
- `frontend/lib/use-agent-chat.ts`
- `frontend/components/smart-route/chat/chat-arrivals-card.tsx`

## Auto and Quick

Both modes use the same parsing, destination resolution, constraints, providers,
normalizers, deterministic scoring, payloads, and grounding rules.

| Policy | Auto | Quick |
|---|---:|---:|
| Default model family | Sonnet | Haiku |
| Model setting | `AGENT_AUTO_MODEL` | `AGENT_QUICK_MODEL` |
| Route candidates | 5 | 2 |
| Retry budget | 2 | 1 |
| Output-token cap | 900 | 360 |
| Wrap-up cap | 300 | 180 |
| Maximum rounds | 5 | 4 |
| Optional first-leg enrichment | Yes | No |
| Intent-required arrivals/events | Always | Always |

`AGENT_MODEL` remains a backwards-compatible Auto alias. Unknown modes normalize
to Auto at both the HTTP request and policy boundaries.

Configured budget deltas are deterministic:

- Quick evaluates 60% fewer route candidates (2 versus 5).
- Quick has a 60% lower primary output-token cap (360 versus 900).
- Quick has a 50% smaller retry budget (1 versus 2).
- Quick has one fewer maximum model/tool round.

These are cost/latency inputs, not a fabricated live latency benchmark. Hosted model
latency was not measured because no Anthropic credential was available to the test
process.

## Ticketmaster status

### Before

Status: partially implemented and unused by route scoring.

The provider already had server-side authentication, bounded pagination, normalized
event data, venue coordinates, timing safeguards, cancellation handling, caching,
and mocked tests. `plan_trip` did not call it, and crowd intent did not require it.

### After

Status: production-wired for explicit crowd-avoidance requests, proven through
deterministic integration tests.

For up to four route hubs, the planner performs concurrent searches within 1.25
miles. Normalized events are associated only when they are within 900 meters of an
actual candidate transit point and overlap an ingress, during-event, or egress
window. Candidate/event pairs are deduplicated. Risk is distance-adjusted and the
total event penalty is capped at 18 points.

The provider states remain distinct:

- `available`
- `no_relevant_events`
- `provider_unavailable`
- `not_required`

If Ticketmaster times out, is disabled, or lacks a key, route planning continues.
The user is not told that no events exist. Only normalized evidence reaches scoring
and explanation payloads; raw provider responses and keys do not.

### Live verification

The bounded one-day NYC smoke test passed without printing the key and returned
five normalized events. An exact Columbus Circle lookup initially returned HTTP
400 even though the credential was accepted. Ticketmaster Discovery v2 rejected
the fractional `radius=1.25` request parameter. Provider retrieval now rounds the
radius outward to the next whole mile while the existing local distance filter
continues to enforce the requested 1.25-mile boundary.

The live smoke test remains opt-in through:

```powershell
$env:TICKETMASTER_LIVE_SMOKE_TEST = "1"
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  backend/tests/test_ticketmaster_event_lookup.py -k live_smoke -s
```

Mocked tests continue to prove request construction, normalization, timeout/error
handling, route association, scoring, and explanation propagation. The live smoke
test additionally proves that the current credential is enabled and accepted.

## Conversation intelligence

The deterministic intent layer distinguishes route planning, destination discovery,
arrival lookup, simple arithmetic, and transit questions. It does not replace the
model for broad language interpretation; it enforces evidence where model choice
must not be trusted.

- `5 + 5` is answered as `10.` without a model call.
- Crowd-avoidance language forces `avoid_crowds=true` into `plan_trip`.
- Arrival questions run `lookup_arrivals` before narration in both modes.
- Destination discovery requires grounded place evidence.
- New route requests clear stale destination/constraint/pending-trip state.

Session schema version 2 stores:

- `active_trip`, including the recommended first boarding leg;
- `pending_trip.status`;
- a bounded failed request summary and retry-safe inputs;
- `resume_offered`.

A failed route can be offered once after a brief unrelated answer. The agent does
not rerun it automatically. The state transitions to `awaiting_confirmation`, which
prevents repeated resurrection. A genuinely new trip clears the stale state.

Known-place aliases cover JFK, LaGuardia/LGA, Newark/EWR, Penn Station, Grand
Central, Atlantic Terminal, Barclays Center, Madison Square Garden/MSG, Yankee
Stadium, and Citi Field.

## Arrival intelligence

`lookup_arrivals` resolves evidence in this order:

1. explicit station/stop;
2. active trip first boarding;
3. nearest stop served by the requested route;
4. clarification/unresolved state.

The backend owns aliases, route membership, station complexes, direction
normalization, bus direction filtering, GTFS-RT matching, deduplication, ordering,
freshness, and catchability. Unknown subway direction returns both available
directions. A provider failure is not converted to “no service.”

First-leg catchability uses walking time plus a two-minute boarding buffer. The route
card receives only concise grounded context such as `Next realistic B: 11 min` and
suppresses stale/unavailable evidence.

The frontend adds no permanent sidebar and no dependency. It reuses the existing
chat reducer, arrival component, route badge, spacing, colors, actions, and
responsive CSS. “Open in Live Feed” is the complete-arrival-view handoff.

## Error handling and telemetry

Provider responses map to timeout, authentication, rate limit, invalid request,
temporary provider failure, or internal error without exposing raw response bodies.
Model retry logs omit exception bodies.

The per-turn log now records only bounded operational fields:

- selected mode and sanitized model ID;
- candidate and retry budgets;
- required tools;
- optional-enrichment flag;
- tool count and failure count;
- model/tool/total milliseconds;
- input/output token counts;
- stop reason.

Trip selection logs the candidate count, selected index, event status, event impact
count, and either `advisor_selection` or `risk_adjusted_event_score`. No message
content, precise destination, provider URL, key, or hidden reasoning is logged.

## Test evidence

Backend:

```text
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
529 passed, 1 skipped in 11.29s
```

The one skip is the opt-in live Ticketmaster smoke test.

Frontend typecheck:

```text
frontend\node_modules\.bin\tsc.cmd --noEmit
PASS
```

Frontend production build:

```text
frontend\node_modules\.bin\next.cmd build
PASS — compiled, typechecked, and generated 12/12 static pages
```

Focused frontend agent/arrival tests:

```text
frontend\node_modules\.bin\tsx.cmd --test \
  lib/agent-chat-stream.test.mjs \
  lib/use-agent-chat.test.mjs \
  components/smart-route/chat/itinerary-view-model.test.mjs
55 passed
```

The complete frontend unit command executed 160 tests: 159 passed and one failed in
a pre-existing dirty `chat-composer` source-format assertion unrelated to this
feature. The implementation itself typechecks. `npm run typecheck` could not start
because the machine's global npm launcher points to a missing `npm-cli.js`; direct
local binaries were used instead.

## Root causes addressed

| Observed behavior | Root cause | Correction |
|---|---|---|
| Auto and Quick behaved alike | model/budgets were global | centralized per-mode policy |
| Crowd request ignored events | provider was not called by `plan_trip` | intent-required route-hub event evidence |
| Event existed but did not change choice | no candidate association/penalty | normalized spatiotemporal association and capped score |
| “No events” could blur with failure | status not part of planning result | explicit four-state event status |
| JFK could trigger geocoding failure | no known-location registry | canonical aliases before free-text geocoding |
| Failed trip continuity was noisy/implicit | raw history only | structured one-time pending trip |
| Arrival answer could be invented | no deterministic agent tool | required GTFS-backed preflight |
| Provider errors sounded global | generic mapping | bounded provider-specific categories |

## Remaining limitations

- The live Ticketmaster request and route-score observation still need to be run
  when network execution is permitted.
- No hosted Auto-versus-Quick latency sample was taken; only deterministic budget
  reductions are reported.
- Ticketmaster schedules estimate crowd exposure, not real-time occupancy.
- Bus arrivals depend on the existing MTA BusTime support and its directional data.
- Scheduled arrival fallback is represented in the contract, but the current
  production lookup primarily returns GTFS-RT live/stale/no-prediction states.
- Destination discovery still depends on the existing POI provider/model tool
  choice; this pass enforces grounding but does not add a new discovery provider.

## Post-P1 arrival and latency regression pass (2026-07-25)

This section supersedes the earlier test counts and latency notes for the
post-P1 regression scope.

### Root causes and corrections

| Observed behavior | Root cause | Correction |
|---|---|---|
| Active-trip Q lookup asked for a station | the selected itinerary persisted a boarding label and coordinates, but not the canonical stop/direction identity | persist normalized route ID, stop ID/name, direction ID/label, and destination stop ID from the canonical selected itinerary |
| An expired GTFS database could prevent subway stop resolution | arrival lookup queried the remote static-GTFS database even though startup had already loaded the local stop-pattern artifact | resolve route membership, parent/child stop IDs, and route segments through `StopPatternIndex` first; retain the database only as a fallback |
| Arrival questions could wait 60–66 seconds and then show a generic failure | deterministic arrival preflight still entered the general model/tool loop and could reach retries or wrap-up work | a resolved or clarification arrival result now emits its card, deterministic rider text, and one terminal event with zero model calls |
| Clarification produced a component, assistant failure, and global error | source state and terminal state were not explicit enough, and the reducer accepted later duplicate/error events | emit explicit arrival resolution and terminal states; suppress generic errors after an arrival outcome and ignore duplicate completion for the turn |
| A fresh page could reuse an expired server session | the browser stored one unversioned, unnamespaced ID and did not recover from `session_expired` | store a versioned record namespaced by origin/environment, discard incompatible records, and retry a fresh-load request once without duplicating the user or assistant turn |
| Optional crowd evidence extended the critical path | Ticketmaster ran beside MTA, but bounded web/X incident corroboration started only after those providers completed | start Ticketmaster and the trip-scoped web/X scan concurrently with required MTA evidence |

Arrival results now expose one of `resolved`, `ambiguous`,
`location_required`, `no_predictions`, or `provider_unavailable`. A provider
failure never claims that no trains are coming.

### Bounded telemetry

The turn trace records `intent_ms`, `session_load_ms`, `place_resolution_ms`,
`route_provider_ms`, `mta_ms`, `ticketmaster_ms`, `arrival_lookup_ms`,
`scoring_ms`, `model_ms`, `stream_finalize_ms`, `total_ms`,
`model_call_count`, `tool_call_count`, and `retry_count`. It does not log
message content, prompts, coordinates, credentials, or provider payloads.

Deterministic tests prove that a context-resolved arrival uses one tool call,
zero model calls, no route-planning call, no wrap-up call, and one terminal
completion. Ordinary route planning uses the existing one selection/narration
continuation and no redundant third wrap-up call.

### Provider and persistence configuration boundary

A read-only configuration diagnostic found the Ticketmaster key present and
Ticketmaster enabled. The opt-in live smoke test passed, so the current
credential and upstream endpoint are healthy. The route-specific failure was a
fractional-radius request rejected by Discovery v2, not a bad API key.
Redis-compatible session storage is not configured, so backend restarts still
discard server-side sessions; the new client recovery makes a fresh empty page
transparent, while preserving an honest error when visible conversation cannot
be recovered.

The configured static-GTFS PostgreSQL connection rejected the read-only
diagnostic. The arrival path no longer requires it when the local
stop-pattern artifact is loaded. Database renewal is still required for
features that genuinely depend on remote static GTFS or persistence.

For Auto crowd-avoidance trips, the existing xAI `web_search` and `x_search`
incident monitor is now enabled as bounded, trip-scoped corroboration and
runs concurrently with other evidence. It is not a general unrestricted
browser for arbitrary chat.

### Verification evidence

```text
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
572 passed, 1 skipped

frontend\node_modules\.bin\tsx.cmd --test <complete unit-test list>
168 passed

frontend\node_modules\.bin\tsc.cmd --noEmit
PASS

frontend\node_modules\.bin\eslint.cmd .
PASS with 22 existing warnings and 0 errors

frontend\node_modules\.bin\next.cmd build
PASS — compiled, typechecked, and generated 12/12 static pages
```

The one backend skip in the default suite remains the opt-in live Ticketmaster
smoke test; that test was run separately and passed.

Manual QA against the running local app:

| Request | Observed result |
|---|---|
| Fresh page → plan Coney Island trip | completed with grounded narration and one route card |
| `When does the next Q arrive?` | live 34 St–Herald Sq Q card and deterministic text; UI reported 1 second; no duplicate error |
| `When does the next Q arrive at Newkirk Plaza?` | explicit Newkirk Plaza Q card; UI reported 1 second; no duplicate error |
| `When is the next B train?` | explicit no-predictions state at 34 St–Herald Sq; no provider-failure claim or generic error |
| `Plan a Q route from Times Square to Coney Island` | completed; canonical card and narration both selected Q; no provider 400 |
| Crowd-avoidance trip to Columbus Circle | completed; Ticketmaster state was `no_relevant_events`, not `provider_unavailable` |

Backend restart recovery is covered deterministically by frontend tests rather
than by interrupting the user's running server.

### Follow-up correction and remaining risk

Explicit service requests are now hard planning constraints. The deterministic
intent layer extracts the requested service, the orchestrator injects it into
`plan_trip`, and candidate filtering occurs before scoring and canonical
selection. If no candidate uses the requested service, the planner fails
honestly instead of substituting another line. Focused tests and the live Q
replay cover both the selection and no-substitution paths.

Ticketmaster is not a complete NYC event directory. Free/community events may
not appear in its catalog even when a general web search finds them. The existing
bounded web/X scan is for current route incidents, not a second event-schedule
provider, so `no_relevant_events` means no associated Ticketmaster evidence—not
proof that no nearby gathering exists. Adding a second event source requires a
normalized provenance and timing contract before it can affect canonical scoring.

## Conversational streaming, discovery, and arrivals pass (2026-07-26)

### Streaming root cause and event flow

Each Anthropic model round previously collected all text deltas before the
orchestrator yielded them. Tool execution was ordered internally but appeared
to the rider as one late response. Model calls now yield safe user-facing
deltas directly from the active SDK stream:

```text
assistant tokens
-> real server-tool start
-> real server-tool completion
-> assistant continuation tokens
-> grounded card event
-> one terminal event
```

Ordinary prose is not buffered. Only text fragments that may contain Markdown
or internal identifiers are briefly held for the existing rider-text boundary
sanitizer. If a later provider operation fails, already-streamed text is
preserved and the turn receives one concise terminal failure. Cancellation is
reraised so the active SDK/tool task closes with the request.

The frontend updates tool rows and route cards by stable event IDs. Replayed
SSE events cannot duplicate a progress row or card, and a recovered provider
retry replaces its failed row instead of leaving a misleading red failure
after the operation succeeds. The existing SSE response already disables proxy
transformation and buffering.

### Model-directed NYC web discovery

Anthropic's bounded server-side web search is available to destination
discovery, route planning, and NYC transit questions. The model decides whether
current public evidence is useful; deterministic arrival and arithmetic paths
do not receive the tool. Auto allows at most three searches and Quick at most
two. The tool receives only approximate NYC locality and timezone context,
never precise rider coordinates.

The prompt requires current menu/hours evidence for place recommendations,
prohibits invented places and objective "best" claims, and requires a selected
web result to pass through the existing place provider before routing. A named,
already-resolved destination does not need to search. Search failures remain
provider failures rather than fabricated destination matches.

### Arrival paraphrase coverage

Arrival parsing now produces a structured intent containing route, stop,
direction, active-trip use, plural-result, catchability, and confidence fields.
The deterministic matrix covers:

```text
next arrivals
show me the next arrivals
show arrivals
what's coming next
when is my train
next Q
any Q trains coming
how long until the Q
when's the next bus
are there any M15s nearby
will I make the next F
```

Explicit stops and directions override active-trip context. Plural bus
shorthand such as `M15s` normalizes to `M15`; the possessive in `when's` cannot
be misclassified as the S train. A destination ETA question such as "When will
I arrive?" remains outside vehicle-arrival lookup. Resolved requests retain the
zero-model-call fast path and filter zero-minute predictions consistently in
text and cards.

### Bounded diagnostics

Turn traces include `web_search_ms`, `place_normalization_ms`, `evidence_ms`,
`stop_resolution_ms`, `feed_fetch_ms`, `feed_parse_ms`, and `render_ms`, plus
the existing intent, session, provider, scoring, model, finalization, and total
timings. Logs include exact model/tool call counts but not prompts, raw queries,
coordinates, provider payloads, URLs, or credentials. Place and arrival tools
populate these timing stages from their actual provider boundaries.

### Visual consistency

The arrival and itinerary cards now share one Font Awesome walking primitive;
the arrival surface no longer introduces a second visual metaphor. Light mode
strengthens the existing 34-pixel thinking orb through a neutral theme-only
filter and surface contrast without changing its size or dark appearance. The
expanded and collapsed light sidebar remove the black divider and use a subtle
shadow without changing rail dimensions.

Manual browser QA verified dark mode, expanded light mode, and collapsed light
mode against the running application. Accessible names, disabled coming-soon
controls, theme switching, and collapse/expand semantics remained intact.

### Verification evidence

```text
backend\.venv\Scripts\python.exe -m pytest -q
608 passed, 1 skipped

frontend complete unit suite
175 passed, 0 failed

frontend TypeScript typecheck
PASS

frontend ESLint
PASS - 0 errors, 22 existing warnings

frontend Next.js production build
PASS - compiled, typechecked, generated 12/12 static pages

git diff --check
PASS
```

The default backend skip remains an explicitly opt-in live-provider check. The
first production-build attempt could not download Google fonts inside the
network-restricted sandbox; the approved network-enabled rerun passed.

The independent subway renderer and station-overlay artifact checks passed. A
separate subway palette check still reports the pre-existing
`mta-bullets/a.svg` color mismatch; this pass does not edit generated transit
artifacts or map palette inputs.

### Remaining limitations

- Deterministic tests prove event ordering and tool-boundary behavior; hosted
  first-token and web-search latency still require an approved credentialed
  staging measurement.
- Web discovery depends on Anthropic server-tool availability and the existing
  place provider for canonical address/coordinate normalization.
- Current listings can be incomplete or disagree about hours; the response
  contract permits concise uncertainty rather than claiming an unsupported
  opening status.
