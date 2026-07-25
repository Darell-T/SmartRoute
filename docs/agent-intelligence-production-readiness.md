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
- eight strict client tools on ordinary rounds;
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

### Credentialed checks blocked by execution policy

The environment rejected the explicit one-request Ticketmaster smoke command
because it would send a live third-party request using the local credential.
No workaround was attempted. The live result, live event count/latency, and the
two live crowd-sensitive routes therefore remain unclaimed.

The same external-credential boundary prevents the minimal Sonnet request and
Models API query. The original 400 did not include its structured Anthropic
body, so its exact cause cannot be identified honestly from the old log line.
The current code proves that the locally constructed request omits the three
documented Sonnet 5 incompatibilities, and the next failure will expose the
safe structured cause. `backend/scripts/run_anthropic_agent_smoke.py --live`
is the bounded follow-up command once credentialed network use is approved.

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

`.env` contains a Ticketmaster key. A bounded one-day NYC smoke test was prepared
and invoked without printing the key, but the execution environment rejected the
required network approval because its external-usage allowance was exhausted. The
test remains opt-in through:

```powershell
$env:TICKETMASTER_LIVE_SMOKE_TEST = "1"
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  backend/tests/test_ticketmaster_event_lookup.py -k live_smoke -s
```

Passing mocked tests prove request construction, normalization, timeout/error
handling, route association, scoring, and explanation propagation. They do not
prove the current live credential or Ticketmaster availability.

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
