# SmartRoute

SmartRoute is a real-time NYC transit assistant that combines live MTA data, AI route reasoning, and voice narration into a single interface. You tell it where you want to go. It pulls routes from Google, enriches every one of them with live train positions, service alerts, stalled vehicle detection, and breaking incidents near stations — then hands the full picture to Claude, which picks the absolute best route and explains why. Out loud, with a synchronized 3D map animation.

It is not a wrapper around Google Maps. Google provides the route candidates. JARVIS layers on real-time intelligence that Google doesn't have — a train that hasn't moved in six minutes, a partial suspension on the D between 36 St and Atlantic, a fire near the Canal St station entrance that @NYCrimeNow tweeted about ten minutes before the MTA posts an alert — and Claude makes the final call with all of that context.

## What It Does

**Route selection with real-time intelligence.** Enter a destination in natural language ("Atlantic Terminal," "JFK," "774 Grand St"). JARVIS geocodes it, pulls transit route alternatives from Google Routes, then enriches every route with live MTA data. All routes — along with service alerts, stalled trains, stalled buses, and nearby incidents — are packaged together and sent to Claude. Claude evaluates the full picture and selects the best route. The response is three short sentences, specific enough to act on: "Take the Q from DeKalb. It's 4 minutes out, heading uptown. 16-minute ride, no alerts on the line."

**Voice narration.** Every recommendation is converted to speech via ElevenLabs. Audio plays automatically while the text reveals word-by-word in sync. Abbreviations are expanded before TTS so "Pkwy" becomes "Parkway" and "Av" becomes "Avenue."

**Live incident intelligence.** A background job runs every ten minutes, using Grok to scan X accounts like @NYCrimeNow and @CitizenAppNYC for breaking incidents — fires, police activity, medical emergencies, water main breaks — within roughly 0.3 miles of any subway station. These incidents are included in the package sent to Claude, so route decisions account for ground-level conditions before the MTA officially reports them.

**Real-time feed integration.** The backend consumes MTA GTFS-RT feeds (train positions, trip updates, arrival predictions), MTA service alerts (planned work, suspensions, delays with affected segments), and MTA BusTIME SIRI data (live bus positions and progress). Feed data is cached with 30–60 second TTL to balance freshness against rate limits.

**Stalled vehicle detection.** If a train hasn't reported a new position in over five minutes, or a bus is stuck mid-route (not at a layover), JARVIS flags it. Claude sees this alongside service alerts and can steer you away from a line that looks fine on paper but has a train sitting dead between stations.

**Partial suspension awareness.** When the MTA suspends service on part of a line, JARVIS doesn't discard the entire line. It understands which segment is affected and Claude can route you through the working portion with a transfer or shuttle bus connection.

**Apple-Maps-style subway map.** The full NYC subway system is rendered natively from GTFS and NYC OpenData geometry as a precomputed artifact. Shared trunks are split into parallel colored lanes with consistent baked offsets, then drawn with a four-layer stroke — dark ground shadow, near-black casing, bright color fill, and a faint inner sheen — plus zoom-interpolated widths and a dark gutter between adjacent colors, so bundled corridors read as distinct lines at every zoom instead of a tangle of overlapping strokes.

## What It Looks Like

The frontend is a full-screen MapLibre GL map on a premium dark basemap (muted CARTO Dark Matter) with native 3D buildings. The entire subway network is drawn in the Apple-Maps style described above — baked parallel lanes, casings, and round caps — so the map reads cleanly before any route is even requested.

A left rail (ATLAS) overlays the map with three views: a route brief with synchronized spoken narration, live next-arrivals (train and bus) for the nearest stations, and a service-alerts board. Live train positions stream over a WebSocket and animate as markers on the map.

When a route loads:

1. The camera flies to fit the full route, your location, and the destination
2. Route segments draw in context — dashed for walking legs, MTA-colored lines for subway, distinct styling for bus
3. Intermediate stops appear as labeled dots along the route
4. Your position pulses as a cyan orb; the destination pulses red

On mobile, the rail collapses to keep the map visible and expands for full route detail.

## Tech Stack

| Layer | Technologies |
|---|---|
| Frontend | Next.js 16, React, TypeScript, MapLibre GL, deck.gl, MapTiler (3D buildings), Three.js |
| Backend | FastAPI, Python, Uvicorn, PostgreSQL |
| Data | Google Routes API, MTA GTFS / GTFS-RT, MTA BusTime SIRI, NYC OpenData (subway geometry) |
| Realtime | WebSocket live feed with short-lived HMAC ticket auth |
| AI | Anthropic Claude (route reasoning), xAI Grok (incident intelligence) |
| Voice | ElevenLabs (text-to-speech) |
| Cache | Redis (optional, in-memory fallback) |
| Hosting | Vercel (frontend), Render (backend + PostgreSQL) |

## Repository Structure

```text
.
├─ frontend/                  # Next.js web app
│  ├─ app/                    # App Router pages, layout, global CSS
│  ├─ components/             # Map, route layers, station badges, orbs
│  ├─ lib/api.ts              # Frontend API client
│  └─ next.config.mjs         # Loads root + frontend env files
├─ backend/                   # FastAPI service
│  ├─ app/main.py             # App startup, CORS, GTFS lifecycle
│  ├─ app/routers/            # API routes (/api/trip, /api/thinking)
│  ├─ app/services/           # Directions, AI advisor, feeds, TTS, incidents
│  ├─ app/utils/              # GTFS loader, cache wrapper, helpers
│  └─ requirements.txt
└─ README.md
```

## Core Flow

1. Frontend sends `POST /api/trip` with the user's GPS coordinates and a destination string.
2. Backend requests transit route alternatives from Google Routes (subway and bus only, preferring fewer transfers).
3. For every route, the backend enriches each transit step with GTFS static data — intermediate stop sequences, line identification, station coordinates.
4. In parallel, the backend fetches:
   - MTA service alerts — suspensions, delays, planned work with affected route segments
   - Stalled trains — vehicles on relevant lines that haven't reported a new position in 5+ minutes
   - Stalled buses — buses stuck mid-route, not at layovers
   - Incidents — breaking events near any station along the routes, sourced from social media via Grok
5. Everything is packaged together — all route alternatives, service alerts, stalled vehicles, and incidents — and streamed to Claude.
6. Claude evaluates the full context, selects the best route, and returns a three-sentence recommendation tagged with the chosen route index.
7. ElevenLabs generates speech audio from the recommendation (with abbreviation expansion).
8. Frontend receives the chosen route, recommendation text, audio, and alerts. It plays the audio, reveals text word-by-word, and animates the route on the 3D map.

## Environment Variables

Set these in your local `.env` and hosting providers.

### Frontend (Vercel)

| Variable | Required | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | Yes | Backend base URL used by the browser |
| `NEXT_PUBLIC_MAPTILER_API_KEY` | Yes | Basemap tiles and 3D building tiles |
| `NEXT_PUBLIC_MAPBOX_TOKEN` | Yes | Destination geocoding / search |
| `APP_KEY` | Yes | Shared secret; the frontend signs short-lived WebSocket tickets with it |

### Backend (Render / local backend process)

| Variable | Required | Purpose |
|---|---|---|
| `APP_KEY` | Yes | Shared secret; the backend refuses to start without it. Gates the API and verifies WebSocket tickets |
| `GOOGLE_ROUTES_API_KEY` | Yes | Google Directions v2 computeRoutes |
| `ANTHROPIC_API_KEY` | Yes | Route recommendation text generation |
| `ELEVENLABS_API_KEY` | Yes (for audio) | Text-to-speech audio generation |
| `XAI_API_KEY` | Optional | Incident intelligence via Grok |
| `MTA_BUS_API_KEY` | Yes (bus monitoring) | MTA Bus SIRI API access |
| `DATABASE_URL` | Optional | PostgreSQL used for GTFS enrichment; without it the GTFS query path is skipped (`GTFS_DB_FALLBACK=1` enables a local fallback) |
| `REDIS_URL` | Optional | Shared caching; fallback is in-memory |
| `CORS_ORIGINS` | Yes in production | Allowed frontend origins |
| `CORS_ORIGIN_REGEX` | Optional | Preview domain support |

## Local Development

### 1. Backend

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Backend health check:

```bash
curl http://localhost:8000/health
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`.

## API Endpoints

### `POST /api/trip`

Request:

```json
{
  "origin_lat": 40.7412,
  "origin_lng": -73.9896,
  "destination": "Atlantic Terminal"
}
```

Response (shape):

```json
{
  "recommendation": "Take the Q from DeKalb...",
  "audio": "<base64-mp3>",
  "route": [
    {
      "type": "WALK",
      "start_point": { "latitude": 40.7412, "longitude": -73.9896 },
      "end_point": { "latitude": 40.7390, "longitude": -73.9901 },
      "polyline": { "encodedPolyline": "..." }
    },
    {
      "type": "SUBWAY",
      "train_line": "Q",
      "direction": "Uptown & The Bronx",
      "departure_stop": "DeKalb Ave",
      "arrival_stop": "Atlantic Av-Barclays Ctr",
      "minutes_until_train_arrives": 4,
      "minutes_until_arrival": 16,
      "stop_count": 3,
      "intermediate_stops": ["7 Av", "Atlantic Av-Barclays Ctr"]
    }
  ],
  "alerts": []
}
```

### `POST /api/thinking`

Returns a short thinking phrase and optional base64 audio, played while the trip request is processing.

### `GET /health`

Reports service status and GTFS data readiness.

## Deployment

### Frontend (Vercel)

Project settings:

- Framework Preset: `Next.js`
- Root Directory: `frontend`
- Build Command: `npm run build`
- Output Directory: empty/default
- Production Branch: `main`

Environment:

- `NEXT_PUBLIC_API_URL=https://jarvis-mta-assistant.onrender.com`
- `NEXT_PUBLIC_MAPBOX_TOKEN=<token>`

### Backend (Render)

Build/start:

- Build: install from `backend/requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port 10000`

Environment:

- Add backend keys listed above.
- Set `CORS_ORIGINS` to your production frontend domain exactly, no trailing slash.

## Troubleshooting

### CORS errors from frontend

- Confirm `CORS_ORIGINS` includes the exact frontend origin.
- No trailing slash.
- Redeploy backend after env changes.

Quick preflight test:

```bash
curl -i -X OPTIONS "https://jarvis-mta-assistant.onrender.com/api/trip" \
  -H "Origin: https://jarvis-mta-assistant.vercel.app" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type"
```

Expected:

- `200 OK`
- `access-control-allow-origin: https://jarvis-mta-assistant.vercel.app`

### No open ports detected on Render

This usually means startup work blocked port binding. Current backend startup defers heavy GTFS loading to a background task so the port opens first.

### `500` during `/api/trip`

Check response `detail` in browser and corresponding Render logs:

- `quota_exceeded` responses from upstream AI/TTS providers indicate account credit limits.
- Missing API keys produce upstream 401/403 errors.
- Invalid Google Routes configuration typically fails in directions stage.
