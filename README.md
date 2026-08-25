# SmartRoute

Real-time NYC transit planning that combines live service conditions, route
facts, and conversational guidance without letting the model invent the trip.

![Next.js](https://img.shields.io/badge/Next.js-16-black?logo=nextdotjs)
![React](https://img.shields.io/badge/React-19-61dafb?logo=react&logoColor=111827)
![TypeScript](https://img.shields.io/badge/TypeScript-5.7-3178c6?logo=typescript&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.12-3776ab?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![MapLibre](https://img.shields.io/badge/MapLibre_GL-396cb2?logo=maplibre&logoColor=white)

![SmartRoute route recommendation](docs/assets/route_found.png)

## What SmartRoute does

SmartRoute helps a rider decide how to move through New York City right now.
It can find a destination, compare transit routes, explain current conditions,
show nearby arrivals, and carry the same accepted trip into the map and route
steps.

The recommendation is not a free-form model answer. The backend prepares
canonical route candidates and verified evidence. The Agent interprets the
rider, manages multi-step goals, and selects among eligible candidates. Chat,
the route card, route steps, and the map render the same server-owned trip.

> The Agent understands the rider. The backend owns truth and execution.

## Product highlights

- An Agent-led conversational flow that understands compound requests,
  follow-ups, saved references, and explicit route constraints.
- One backend-owned canonical itinerary shared by chat, route cards, route
  steps, and the map.
- Live subway and bus context from GTFS realtime, MTA alerts, and BusTime.
- Stalled-train evidence from stale GTFS realtime vehicle timestamps and
  stalled-bus evidence from BusTime `noProgress` status outside layovers.
- Route comparison using duration, walking, transfers, incidents, event
  exposure, crowd context, accessibility, and rider choices.
- Nearby arrivals, issue-first alerts, vehicle positions, and WebSocket updates.
- A generated MapLibre subway network with deterministic station anchors and
  official route colors.
- Background incident scouting, so broad incident research is not part of the
  rider request path.

## Product views

| Route steps | Alternate routes |
|---|---|
| ![Detailed trip steps](docs/assets/route_steps.png) | ![Alternate route comparison](docs/assets/alt_routes.png) |

| Nearby arrivals | Service alerts |
|---|---|
| ![Nearby arrivals](docs/assets/nearby_arrivals.png) | ![Nearby service alerts](docs/assets/nearby_alerts.png) |

## How it works

```text
Rider request and session context
              |
              v
       Agent understands goals
              |
              v
  Validated backend capabilities
              |
              v
Providers, GTFS, realtime feeds, indexed incidents
              |
              v
 Canonical candidates and evidence
              |
              v
  Agent chooses a valid option
              |
              v
Server presenter renders verified facts
              |
              v
 Chat card, route steps, and map
```

The Agent uses eight model-visible capabilities:
`declare_goals`, `discover_places`, `check_transit`,
`prepare_route_options`, `present_places`, `present_transit`, `present_route`,
and `complete_turn`. Internal provider helpers are not model-visible.

`POST /api/trip` builds a model-free direct plan. `POST /api/agent/chat` adds
Agent-led goal interpretation and selection while reusing backend-owned route
preparation and canonical itinerary facts.

Use the [documentation map](docs/README.md) to find the backend architecture,
agent pipeline, production contracts, and release checks.

## Route intelligence

SmartRoute distinguishes conditions that should not be treated as the same
kind of failure:

- Physical infeasibility cannot be overridden. A suspended required segment or
  invalid itinerary remains blocked.
- Rider-owned constraints can be changed by the rider. If the rider withdraws
  an avoid-line or walking preference, the active trip is reevaluated.
- Operational advisories can make a route less desirable without making it
  impossible. A rider may select a viable delayed or crowded route and still
  receive the warning.

Every candidate is finalized against one evidence snapshot before selection.
The Agent receives an unordered factor comparison without numeric scores or
rank labels. Private deterministic ranking remains a fallback and validation
mechanism when the Agent cannot return a valid choice.

## Architecture

| Layer | Responsibility |
|---|---|
| Next.js frontend | Chat streaming, route cards, left rail, map, and API proxies |
| FastAPI routers | Admission, authentication, REST, SSE, and WebSocket boundaries |
| Conversational Agent | Goals, capability loop, Agent state, evidence obligations, and canonical presentation |
| Trip services | Model-neutral route preparation, constraints, candidate facts, evidence association, fallback selection, and canonical itinerary |
| Transit and provider services | MTA, GTFS, GTFS realtime, BusTime, and provider normalization |
| Live-feed services | Arrivals, alerts, vehicles, and nearby transit context |
| Background refresh | Bounded city incident collection into the shared Redis index |

Static GTFS and map artifacts are prepared before the request path. Broad city
incident research runs every 30 minutes through the Render cron service. Normal
route requests read the shared incident index and never wait for a broad xAI or
Web scan.

## Technology

Frontend:

- Next.js 16, React 19, TypeScript 5.7, Tailwind CSS 4
- MapLibre GL, Motion, Radix, Zod, Lucide, Iconoir, and Font Awesome

Backend:

- Python 3.12, FastAPI, Pydantic, and Uvicorn
- Redis-compatible session, admission, and incident storage
- Google Routes, Anthropic, xAI, Ticketmaster, MTA GTFS/GTFS-RT, and BusTime

Deployment:

- Vercel for the frontend
- Render-compatible FastAPI web service
- Render cron for background incident refresh

## Reliability guarantees

The repository protects these product invariants:

- One canonical itinerary owns duration, transfers, walking, stops, and timing.
- Missing evidence stays unknown and never becomes a false all-clear.
- Raw model prose cannot finish unresolved grounded work.
- Accepted unchanged advisories cannot create a repeated consent loop.
- Passenger output never includes tool names, schema fields, raw IDs, prompts,
  provider payloads, or chain-of-thought.
- Production chat requires durable Redis-backed session and admission state.

## Release gates

```bash
cd backend
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q

cd ../frontend
npm install
npm run typecheck
npm run test:unit
npm run lint
npm run verify:transit-artifacts
npm run build
```

## Run locally

Copy `.env.example` to a local `.env` and provide the services you want to use.
Never commit real keys. At minimum, the backend requires `APP_KEY`. Route
planning requires `GOOGLE_ROUTES_API_KEY`, and hosted chat requires
`ANTHROPIC_API_KEY`. Production chat and the incident cron require a shared
`REDIS_URL`.

Backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

Put frontend configuration in `frontend/.env.local`:

- `NEXT_PUBLIC_MAPBOX_TOKEN` enables predictive destination search.
- `API_URL` selects the backend for server-side proxy routes. Use
  `NEXT_PUBLIC_API_URL` only when browser code also needs the hosted URL.
- `NEXT_PUBLIC_MAPTILER_API_KEY` optionally enables 3D building tiles.

Open `http://localhost:3000`. The frontend uses `http://localhost:8000` by
default when no hosted backend URL is configured.

For local chat UI work without paid model or route calls, set
`AGENT_MOCK_MODE=1` under an explicit local or test runtime profile. Production
startup rejects mock and fixture modes.

## Repository map

```text
backend/
  app/routers/                 FastAPI and WebSocket entry points
  app/services/agent/          Conversational runtime, state, model boundary, evidence, and completion
  app/services/agent/tools/    Model-visible capability adapters and Agent-specific execution
  app/services/trips/          Model-neutral route preparation, constraints, scoring, and canonical itinerary
  app/services/mta/            MTA / GTFS / BusTime provider normalization and realtime data
  app/services/live_feed/      Current arrivals, vehicles, and service snapshots
  app/services/incidents/      Incident collection, normalization, indexing, and refresh
  evaluation/                  Deterministic offline evaluation and replay infrastructure
  scripts/                     Reproducible build, release, and maintenance commands
  tests/                       Backend contracts and conversation matrices

frontend/
  app/                         Next.js shell and server API proxies
  components/smart-route/      Chat, route cards, and left-rail UI
  components/map/              MapLibre transit and station rendering
  lib/                         Typed clients, state, and WebSocket helpers
  scripts/build/               Transit artifact generation and checks
  public/                      Generated runtime map artifacts
```

Do not hand-edit generated subway GeoJSON. Regenerate it through
`npm run build:transit-artifacts` and inspect the resulting diff.

## Interface credits

SmartRoute includes or adapts interface work from these open-source projects:

- [Prompt Kit by ibelick](https://github.com/ibelick/prompt-kit), MIT license,
  for chat container, composer, message, suggestion, and scroll primitives.
- [Thinking Orbs by Jakub Antalik](https://github.com/Jakubantalik/thinking-orbs),
  MIT license, for the animated agent activity orb.
- [Vercel AI Elements](https://github.com/vercel/ai-elements), Apache-2.0
  license, for reasoning and shimmer foundations.
- [shadcn/ui](https://github.com/shadcn-ui/ui), MIT license, for accessible
  button, textarea, tooltip, and collapsible primitives.

These components are adapted to SmartRoute's own product behavior, transit
contracts, accessibility rules, and visual system. SmartRoute is not affiliated
with or endorsed by the MTA.
