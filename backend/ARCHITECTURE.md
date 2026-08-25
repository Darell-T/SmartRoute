# Backend architecture

The Agent understands the rider. The backend owns truth and execution.

Chat and `POST /api/trip` must return the same kind of itinerary. Duration,
arrival time, transfers, walking, stops, and service conditions therefore have
one owner: `app/services/trips/`. The Agent can choose a prepared candidate
and supply bounded framing. It cannot calculate or replace those facts.

[How a SmartRoute chat turn stays grounded](../SMARTROUTE_AGENT_PIPELINE.md)
covers the request path, goals, and completion rules.
[Release validation](../docs/release-validation.md) lists the deterministic
checks for a change.

## Data owners

| Fact or action | Owner |
|---|---|
| Provider response parsing | The provider module under `app/services/` |
| Route candidates and canonical itinerary facts | `app/services/trips/` |
| Incident collection and storage | `app/services/incidents/` |
| Process-wide realtime transit state | `app/services/live_feed/` |
| Chat session state and model tool execution | `app/services/agent/` |
| HTTP status codes, authentication, and streaming transport | `app/routers/` |
| Route cards, route steps, and map display | The frontend, from backend facts |

The frontend does not calculate duration, arrival time, transfers, walking
time, dwell, confidence, selection, or rank.

## Why these owners exist

A route appears in chat, a route card, the route steps, and the map. If each
view calculated its own totals, a rider could see four answers for one trip.
`app/services/trips/` builds one canonical itinerary. Every consumer reads that
record. The frontend formats it. It does not rebuild it.

Provider data becomes evidence before it affects a route. Google Routes, MTA
GTFS realtime, BusTime, service alerts, indexed incidents, event data, and
place search return different shapes. SmartRoute normalizes each response at
its provider boundary, then associates evidence with a specific candidate. A
subway alert matters only when the candidate uses the affected line and
direction. Missing data stays unknown. An empty incident query does not prove
that the route is clear.

The Agent owns rider language, conversational references, goal decomposition,
capability choice, and selection among validated candidates. It does not own
route arithmetic. `prepare_route_options` crosses into
`app/services/trips/preparation/`. `present_route` accepts a candidate ID that
the server already stored.

`TurnContract` and `TurnEvidence` live outside the model response. A fluent
sentence cannot finish unresolved grounded work. Presenters render stored
facts. General conversation can finish through `complete_turn`. Grounded work
must use its presenter.

`app/services/live_feed/network_snapshot.py` keeps one process-wide realtime
generation. Rider requests filter that generation. Broad incident research runs
in `app/services/incidents/refresh.py` and writes a shared index. Route
requests read that index. They do not wait for a scout scan.

## HTTP and WebSocket entry points

| Path | Method | Owner |
|---|---|---|
| `/api/agent/chat` | `POST` | `app/routers/agent_chat.py` |
| `/api/agent/chat/session` | `POST` | `app/routers/agent_chat.py` |
| `/api/agent/chat/session/reset` | `POST` | `app/routers/agent_chat.py` |
| `/api/trip` | `POST` | `app/routers/trips.py` |
| `/api/live-feed` | `POST` | `app/routers/live_feed/router.py` |
| `/api/service-alerts` | `GET` | `app/routers/live_feed/router.py` |
| `/api/vehicles` | `GET` | `app/routers/live_feed/router.py` |
| `/ws/live-feed` | WebSocket | `app/routers/live_feed/router.py` |
| `/ws/service-alerts` | WebSocket | `app/routers/live_feed/router.py` |
| `/api/subway-stops` | `GET` | `app/routers/subway.py` |
| `/api/internal/incident-refresh` | `POST` | `app/routers/incident_refresh.py` |

`app/main.py` loads configuration, starts shared clients, registers routers,
and closes process-owned resources.

## Package map

```text
app/
  main.py
  runtime.py
  observability.py
  routers/
    agent_chat.py
    incident_refresh.py
    subway.py
    trips.py
    live_feed/
      router.py
      socket.py
      ticket.py
  services/
    admission.py
    cache.py
    directions.py
    evidence.py
    geography.py
    agent/
      model/
      turn/
      tools/
        places/
        route/
        transit/
    incidents/
    live_feed/
    mta/
      realtime.py
      static_gtfs/
    trips/
      crowds/
      preparation/
      route_incidents/

evaluation/
  route_intelligence/

scripts/
  live_checks/
  release/
```

## Agent package

`app/services/agent/loop.py` is the chat entry point. The package owns model
requests, the turn contract, session state, the offered tool list, tool
execution, and passenger output.

The package does not own route arithmetic, provider parsing, incident
collection, or realtime refresh.

| Path | Contents |
|---|---|
| `model/policy.py` | Auto and Quick model policy |
| `model/prompt.py` | System prompt and context blocks |
| `model/request.py` | Anthropic request construction |
| `model/stream.py` | Model stream parsing and retries |
| `model/output_projection.py` | Safe model output projection |
| `model/budget.py` | Request and spend limits |
| `turn/contract.py` | `TurnContract`, `OutcomeGoal`, and `GoalState` |
| `turn/evidence.py` | `TurnEvidence` |
| `turn/stream.py` | `TurnDependencies`, `TurnState`, and `stream_turn` |
| `turn/tool_round.py` | Tool validation and execution |
| `turn/completion.py` | Terminal state checks |
| `turn/finalization.py` | Final events, timings, and telemetry |
| `public_surface.py` | Tools offered for the current server state |
| `session.py` | Chat session state, leases, and pending continuations |
| `candidate_store.py` | Route candidate sets |
| `discovery_store.py` | Place discovery sets |
| `trip_state.py` | Accepted trip and rider constraints |

The model can see eight tools:

- `declare_goals`
- `discover_places`
- `check_transit`
- `prepare_route_options`
- `present_places`
- `present_transit`
- `present_route`
- `complete_turn`

`app/services/agent/tools/__init__.py` owns the tool registry.
`tools/places/`, `tools/route/`, and `tools/transit/` contain Agent-specific
capability adapters. Provider and trip implementations remain under their
provider or trip owner.

## Trip package

`app/services/trips/` owns route candidates and the canonical itinerary used
by chat, the trip endpoint, route cards, route steps, and the map.

| Path | Contents |
|---|---|
| `direct_plan.py` | Direct `/api/trip` orchestration |
| `preparation/` | Endpoint resolution, provider calls, constraints, evidence, multi-stop combination, and finalization |
| `route_incidents/` | Candidate-specific incident lookup and matching |
| `crowds/` | Event and crowd evidence for a trip |
| `scoring.py` | Deterministic recovery score |
| `selection_decision.py` | Candidate eligibility and fallback selection |
| `itinerary.py` | Canonical itinerary construction |
| `enrichment.py` | Stop and route enrichment |
| `transfer_semantics.py` | Transfer identity and timing rules |
| `location.py` | Neutral place resolution types |

`preparation.prepare.prepare_single_leg` is the shared single-leg entry point.
The same module owns `PreparedLeg`, `AggregatePreparation`, and `PreparedChain`.
`preparation.multi_stop.prepare_multi_stop` prepares ordered waypoints.
`preparation.finalize.finalize_aggregate` returns the final candidate set.

The Agent route adapter keeps single-leg aggregate conversion in
`tools/route/prepare_route_options.py`. Candidate evidence lookup and nonfatal
empty candidate-set persistence stay with
`tools/route/prepare_route_persistence.py`.

## MTA package

`app/services/mta/` owns MTA transport and parsing.

| Path | Contents |
|---|---|
| `feeds.py` | GTFS realtime fetch and protobuf parsing |
| `alerts.py` | Service alert parsing and route filtering |
| `subway.py` | Subway vehicle parsing and `get_stalled_trains` |
| `bus.py` | BusTime stops, arrivals, vehicles, and stalled buses |
| `bus_updates.py` | Nearby bus update projection and cache access |
| `bus_runtime.py` | Shared BusTime client and limited cache |
| `realtime.py` | Explicit interface used by cross-provider realtime consumers |
| `static_gtfs/` | Static store, stop patterns, scheduled arrivals, and migration |

There is no compatibility module for the old `app.services.mta_feed` path.
Code and tests import `app.services.mta.realtime` or a concrete MTA module.

## Live-feed package

`app/services/live_feed/network_snapshot.py` owns one process-wide realtime
generation. `snapshot.py` derives a rider-specific response from that
generation. `vehicle_enrichment.py` adds stop and segment context.

`app/routers/live_feed/` owns HTTP transport, WebSocket transport, admission,
and single-use tickets. The router package does not fetch or parse provider
data.

## Incident package

`app/services/incidents/refresh.py` runs the background refresh. `official.py`
and `scout.py` collect provider results. `normalization.py` and
`scout_normalization.py` produce stored incident records. `index.py` owns the
cache keys and lookups.

Trip code reads incidents through `app/services/trips/route_incidents/`.
An empty result does not prove that a route is clear when a source is missing.

## Evaluation and scripts

| Path | Contents |
|---|---|
| `evaluation/route_intelligence/` | Deterministic replays, comparison, metrics, reports, and shadow records |
| `scripts/live_checks/` | Explicit provider smoke commands |
| `scripts/release/` | Release checks and provider fault cases |
| `scripts/run_incident_refresh.py` | One deployed incident refresh cycle |

The production request path does not import evaluation modules.

## Package rules

- Package initializers stay empty unless the package exports a public entry
  point.
- A directory contains several related modules with one owner.
- A private file remains separate only when the boundary reduces reading or
  change cost.
- The repository has no `helpers`, `common`, `misc`, or generic `utils`
  package in the backend.
- Tests import concrete owners. The repository has no compatibility imports
  for deleted paths.

Change these boundaries only when a concrete behavior, lifecycle, reuse,
isolation, or reading need justifies the change.
