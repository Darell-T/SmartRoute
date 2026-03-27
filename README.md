# JARVIS MTA Assistant

JARVIS MTA Assistant is a full-stack NYC transit assistant with:

- A Next.js frontend for map-based trip planning and voice interaction
- A FastAPI backend that combines Google Routes, MTA feeds, AI route reasoning, and text-to-speech
- Deployment split across Vercel (frontend) and Render (backend)

## Tech Stack

- Frontend: Next.js, React, TypeScript, Mapbox GL
- Backend: FastAPI, Uvicorn, Python
- Data APIs: Google Routes API, MTA GTFS / GTFS-RT, MTA Bus SIRI
- AI / Voice: Anthropic, xAI, ElevenLabs
- Caching: Redis (optional, in-memory fallback)
- Hosting: Vercel + Render

## Repository Structure

```text
.
├─ frontend/                  # Next.js web app
│  ├─ app/                    # App Router pages
│  ├─ components/             # UI and map components
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

1. Frontend sends `POST /api/trip` with user coordinates + destination text.
2. Backend requests alternatives from Google Routes.
3. Backend enriches routes with:
   - GTFS static stop chaining
   - MTA service alerts
   - Stalled train / bus checks
   - Incident intelligence
4. Anthropic selects and explains the best route.
5. ElevenLabs generates speech for the response.
6. Frontend renders chosen route and plays audio.

## Environment Variables

Set these in your local `.env` and hosting providers.

### Frontend (Vercel)

| Variable | Required | Example | Purpose |
|---|---|---|---|
| `NEXT_PUBLIC_API_URL` | Yes | `https://jarvis-mta-assistant.onrender.com` | Backend base URL used by the browser |
| `NEXT_PUBLIC_MAPBOX_TOKEN` | Yes | `pk...` | Map rendering token |

### Backend (Render / local backend process)

| Variable | Required | Example | Purpose |
|---|---|---|---|
| `GOOGLE_ROUTES_API_KEY` | Yes | `AIza...` | Google Directions v2 computeRoutes |
| `ANTHROPIC_API_KEY` | Yes | `sk-ant-...` | Route recommendation text generation |
| `ELEVENLABS_API_KEY` | Yes (for audio) | `sk_...` | Text-to-speech audio generation |
| `XAI_API_KEY` | Optional but used | `xai-...` | Incident intelligence |
| `MTA_BUS_API_KEY` | Yes (bus monitoring) | `...` | MTA Bus SIRI API access |
| `REDIS_URL` | Optional | `redis://...` | Shared caching; fallback is in-memory |
| `CORS_ORIGINS` | Yes in production | `https://jarvis-mta-assistant.vercel.app` | Allowed frontend origins |
| `CORS_ORIGIN_REGEX` | Optional | `^https://yourapp(-[a-z0-9-]+)?\\.vercel\\.app$` | Preview domain support |

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
  "recommendation": "Take the Q from ...",
  "audio": "<base64-mp3>",
  "route": [],
  "alerts": []
}
```

### `POST /api/thinking`

Returns a short phrase and optional base64 audio used while trip planning is running.

### `GET /health`

Reports service status and GTFS readiness.

## Deployment

## Frontend (Vercel)

Project settings:

- Framework Preset: `Next.js`
- Root Directory: `frontend`
- Build Command: `npm run build`
- Output Directory: empty/default
- Production Branch: `main`

Environment:

- `NEXT_PUBLIC_API_URL=https://jarvis-mta-assistant.onrender.com`
- `NEXT_PUBLIC_MAPBOX_TOKEN=<token>`

## Backend (Render)

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

### Vercel `/_vercel/insights/script.js` 404

Non-blocking. Enable Vercel Web Analytics if you want this script available.

### Browser extension `ERR_FILE_NOT_FOUND`

Non-blocking. Ignore extension-origin resource errors when debugging app functionality.

## Notes for v0 Workflow

This project originated from v0. You can continue design iteration there, but production behavior should always be validated against:

- `main` branch deployment
- Vercel production domain
- Render backend logs
