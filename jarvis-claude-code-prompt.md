# JARVIS Transit Intelligence — Claude Code Task Prompt

## Project Context

This is "Will I Be Late?" — a personal transit intelligence agent for NYC. The user speaks or types a destination, the backend queries real-time MTA GTFS-RT feeds and an AI advisor (JARVIS) returns a spoken route recommendation with TTS audio. The frontend renders an animated route on a Mapbox map.

### Tech Stack
- **Frontend:** Next.js (App Router), TypeScript, Mapbox GL JS
- **Backend:** Python / FastAPI
- **Key files you will modify:**
  - `jarvis-map.tsx` — Mapbox map component (renders map, user location orb, markers)
  - `page.tsx` — Main page component (input bar, state management, API calls, audio playback)
  - `app/routers/trips.py` — `/api/trip` endpoint (orchestrates geocoding, routing, AI, TTS)
  - `app/services/route_calculator.py` — Geocoding, nearest stops, route computation, schedule fetching
  - `app/services/ai_advisor.py` — JARVIS system prompt and Claude API call

### Current API Response
The `/api/trip` endpoint currently returns only:
```json
{ "text": "...", "audio": "base64..." }
```

All the route data needed by the frontend (station coordinates, train lines, departure times) is computed in the backend but discarded before the response is built. Task 1 fixes this.

### Data Flow
```
nearest_stops()
  -> geocode_address(origin) -> (lat, lon)        <- DISCARDED, NEED TO PRESERVE
  -> geocode_address(dest) -> (lat, lon)           <- DISCARDED, NEED TO PRESERVE
  -> find_nearest_stops() -> [{stop_id, stop_name, distance_m, ...}, ...]
  -> returns {"origin_stops": [...], "dest_stops": [...]}

possible_routes(stops)
  -> returns [{"origin_stop": stop_id, "dest_stop": stop_id, "routes": {"Q","B",...}}, ...]

get_schedule(routes)
  -> returns {"user_schedule": [{route_id, stop_id, arrival_time, delay}, ...], "stalled_trains": [...]}

combine_data(route_options, schedule, closest_stops)
  -> merges everything into one JSON blob for the AI prompt
  -> returns JSON string

plan_trip() in trips.py
  -> calls all of the above
  -> sends combined_data to Claude via stream_recommendation()
  -> sends Claude text to ElevenLabs for TTS
  -> returns only {"text": ..., "audio": ...}   <- everything else is lost
```

---

## Task 1 — Expand the `/api/trip` Response

**Files:** `app/services/route_calculator.py`, `app/routers/trips.py`

The frontend needs structured route data alongside text and audio. The data already exists in the backend but is discarded. Surface it.

### 1a. Preserve geocoded coordinates

In `nearest_stops()` inside `route_calculator.py`, the `origin_coords` and `dest_coords` tuples are used to find stops but never returned. Change the return dict to include them:

```python
return {
    "origin_stops": origin_stops,
    "dest_stops": dest_stops,
    "origin_coords": {"lat": origin_coords[0], "lng": origin_coords[1]},
    "dest_coords": {"lat": dest_coords[0], "lng": dest_coords[1]},
}
```

**Important:** Verify the tuple order from `geocode_address()` in `geo.py`. If it returns `(lon, lat)` instead of `(lat, lon)`, swap accordingly. Get this right.

### 1b. Extract structured route data in `plan_trip()`

In `trips.py`, after `combined_data` is built, parse it to extract the best route info for the frontend:

```python
parsed = json.loads(combined_data)
route_options_list = parsed.get("possible_routes", [])
schedule_entries = parsed.get("schedule_for_user_stops_only", [])

best = route_options_list[0] if route_options_list else None
frontend_route = {}

if best:
    origin_stop_id = best["origin_stop"]
    dest_stop_id = best["dest_stop"]
    train_lines = best["routes"]

    origin_station = next(
        (s for s in closest_stops["origin_stops"] if s["stop_id"] == origin_stop_id), None
    )
    dest_station = next(
        (s for s in closest_stops["dest_stops"] if s["stop_id"] == dest_stop_id), None
    )

    # Find next departure from schedule
    now_ts = time.time()
    next_deps = sorted(
        [e for e in schedule_entries
         if e["stop_id"].rstrip("NS") == origin_stop_id
         and e["route_id"] in train_lines
         and e["arrival_time"] > now_ts],
        key=lambda e: e["arrival_time"]
    )
    next_dep = next_deps[0] if next_deps else None

    if origin_station and dest_station:
        frontend_route["trainLine"] = next_dep["route_id"] if next_dep else train_lines[0]
        frontend_route["originStation"] = {
            "name": origin_station["stop_name"],
            "lat": origin_station[???],   # CHECK geo.py for actual field names
            "lng": origin_station[???],   # might be "lat"/"lon", "stop_lat"/"stop_lon", etc.
        }
        frontend_route["destStation"] = {
            "name": dest_station["stop_name"],
            "lat": dest_station[???],
            "lng": dest_station[???],
        }
        if next_dep:
            dep_secs = next_dep["arrival_time"] - now_ts + next_dep.get("delay", 0)
            frontend_route["departureMinutes"] = max(1, round(dep_secs / 60))
            frontend_route["direction"] = next_dep.get("direction", "")

        # Ride duration: try to find matching trip_id with stops at both origin and dest
        # If not computable from schedule data, set to None
        frontend_route["rideDurationMinutes"] = None  # TODO: compute from trip_id matching
```

**Read `geo.py`** — specifically `find_nearest_stops` — to determine the exact field names for latitude/longitude on each stop dict. Replace the `???` placeholders.

### 1c. Build the expanded response

Replace the return in `plan_trip()`:

```python
return {
    "text": text,
    "audio": audio_bytes,
    "originCoords": closest_stops.get("origin_coords"),
    "destCoords": closest_stops.get("dest_coords"),
    **frontend_route,
}
```

---

## Task 2 — Design Refresh (JARVIS HUD)

**Goal:** The map IS the interface. Everything else is a floating translucent overlay. Think heads-up display in a helmet, not a transit app. Minimal, purposeful, quiet until activated.

### 2a. Idle State
- Full-screen Mapbox map. **No side panel.** No overlays.
- Glowing orb on user GPS location (untouched).
- Input bar pinned to bottom center (keep existing).
- Nothing else visible.

### 2b. JARVIS Response Bubble
- **Remove the right side panel entirely** from both idle and active states.
- Replace with: a frosted-glass HUD bubble floating at bottom center, just above the input bar.
- Entrance: `translateY(12px) -> 0`, `opacity 0 -> 1`, ~400ms `cubic-bezier(0.16, 1, 0.3, 1)`.
- Style:
  ```css
  max-width: 600px;
  backdrop-filter: blur(20px) saturate(1.4);
  background: rgba(8, 10, 18, 0.7);
  border: 1px solid rgba(0, 255, 255, 0.12);
  box-shadow: 0 0 30px rgba(0, 255, 255, 0.06), inset 0 1px 0 rgba(255,255,255,0.04);
  border-radius: 16px;
  padding: 20px 24px;
  color: rgba(255, 255, 255, 0.92);
  font-size: 15px;
  line-height: 1.6;
  ```
- States:
  - **Hidden** when idle
  - **Loading:** "Scanning MTA feeds, sir..." with slow pulse on border (keyframes cycling `border-color` opacity 0.08 to 0.2)
  - **Active:** word-by-word revealed text (Task 3)

### 2c. HUD Overlay Pills
- Two small pill badges, top center, side by side, 8px gap.
- Hidden when idle. Fade in on route data: `opacity 0->1`, `translateY(-6px)->0`, ~300ms, stagger 100ms.
- **NEXT TRANSIT:** `[colored circle] Q -- in 4 min · Manhattan-bound`
- **ETA:** `~24 min ride`
- Style:
  ```css
  backdrop-filter: blur(16px);
  background: rgba(8, 10, 18, 0.65);
  border: 1px solid rgba(255, 255, 255, 0.06);
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 13px;
  letter-spacing: 0.01em;
  ```

### 2d. Typography
- Import **Space Grotesk** from Google Fonts (400, 500, 600).
- Set as primary `font-family` on root/body.
- All HUD elements use it.

### 2e. Destination Orb
- When route data arrives with `destCoords`, add a second pulsing orb marker at the destination.
- Visually identical animation to the user GPS orb but in **warm amber/gold** to distinguish:
  - Core: `#F5A623`
  - Outer glow: `rgba(245, 166, 35, 0.25)`
  - Pulse ring: same keyframe as user orb, amber color
- Remove when route is cleared (new submission or page refresh).

### 2f. Keep These Elements
- JARVIS branding text (top-left, subtle, small)
- AI CORE ALPHA indicator (top-right, update with real latency per Task 6)
- Mobile drawer (apply same frosted glass styling)
- User GPS orb (do not modify)

---

## Task 3 — Word-by-Word Text Reveal Synced to Audio

After `handleSubmit` resolves, `jarvisText` holds the full response. An `Audio` element plays TTS. Reveal the text word by word timed to audio duration.

### Implementation
1. Add `displayedText` state (string), init `""`.
2. On audio `play` event:
   - Split `jarvisText` into word array.
   - Read `audio.duration` from `loadedmetadata` event (seconds).
   - Interval = `(duration * 1000) / words.length` ms per word.
   - `setInterval`: append one word + space to `displayedText` each tick.
   - Clear interval when all words shown or audio `ended`.
3. JARVIS bubble renders `displayedText`, not `jarvisText`.
4. On new request: reset `displayedText` to `""`, stop audio, clear interval.

### Edge Cases
- `audio.duration` is `NaN` or `0`: fall back to 80ms per word.
- Clean up intervals on unmount.
- New submission while audio playing: stop everything, reset.

---

## Task 4 — Animated Route Drawing on Map

**File:** `jarvis-map.tsx`

### New Props
```typescript
interface JarvisMapProps {
  // ...existing props
  routeCoordinates?: [number, number][];
  trainLine?: string;
  originStation?: { name: string; lat: number; lng: number };
  destinationStation?: { name: string; lat: number; lng: number };
  userLocation?: { lat: number; lng: number };
  finalDestination?: { lat: number; lng: number };
  isSpeaking?: boolean;
}
```

### MTA Color Map
```typescript
const MTA_COLORS: Record<string, string> = {
  'N': '#FCCC0A', 'Q': '#FCCC0A', 'R': '#FCCC0A', 'W': '#FCCC0A', 'B': '#FCCC0A',
  'A': '#0039A6', 'C': '#0039A6', 'E': '#0039A6',
  '1': '#EE352E', '2': '#EE352E', '3': '#EE352E',
  '4': '#00933C', '5': '#00933C', '6': '#00933C',
  'L': '#A7A9AC',
  '7': '#B933AD',
  'J': '#996633', 'Z': '#996633',
  'G': '#6CBE45',
};
```

### Animation Sequence
When route props become non-null:

**Segment 1 -- Walk to origin station** (~1.5s)
- Line from `userLocation` to `originStation`.
- Dashed white, `line-width: 2.5`, `line-dasharray: [2, 4]`, `line-opacity: 0.7`.
- Animate with `requestAnimationFrame`: interpolate points along the line over 1.5s.

**Segment 2 -- Subway ride** (~3s, after segment 1)
- Line from `originStation` to `destinationStation` (or along `routeCoordinates` if provided).
- Solid, `line-width: 5`, color = `MTA_COLORS[trainLine]`.
- Glow: second layer underneath, `line-width: 14`, same color, `line-opacity: 0.12`.
- Animate progressively over 3s.

**Segment 3 -- Walk to destination** (~1s, after segment 2)
- Line from `destinationStation` to `finalDestination`.
- Same dashed white as segment 1.

**Station Markers** (after segment 2)
- HTML markers at origin/destination stations.
- Pill: station name + line letter, MTA color background, white text, `border-radius: 12px`, `padding: 4px 10px`, `font-size: 12px`, `font-family: 'Space Grotesk'`.

> **Note on `routeCoordinates`:** The backend does not yet return a subway polyline. For v1, draw a straight line between stations as the subway segment. Add a `// TODO: replace with GTFS shapes polyline when available` comment.

### Camera
1. Animation start: `fitBounds` encompassing all route points with padding.
2. After all segments drawn: `flyTo` destination, then slow rotation (bearing + 0.3 every 50ms) while `isSpeaking`.
3. `isSpeaking` becomes false: stop rotation, `flyTo` back to `userLocation`, `speed: 0.5`.

### Route Persistence
- Layers, sources, markers stay until page refresh or new submission.
- On new submission: remove all route sources, layers, and DOM markers BEFORE drawing new route.
- Do NOT clear on audio end.

---

## Task 5 — Re-enable Map Interaction

**File:** `jarvis-map.tsx`

Remove any lines disabling interactions:
```
scrollZoom.disable()
dragPan.disable()
dragRotate.disable()
doubleClickZoom.disable()
touchZoomRotate.disable()
```

Map still `flyTo` on load and route arrival, but user can pan/zoom/rotate freely at all times.

---

## Task 6 — Real Data in HUD Overlays

### AI CORE ALPHA Latency
- Record `Date.now()` before the fetch in `handleSubmit`.
- Record `Date.now()` on response.
- Display delta: `AI CORE ALPHA -- 1243ms`

### NEXT TRANSIT Pill
- From response: `trainLine`, `departureMinutes`, `direction`.
- Format: `[colored circle] Q -- in 4 min · Manhattan-bound`
- If `departureMinutes` missing: `Q -- checking...`

### ETA Pill
- From response: `rideDurationMinutes`.
- Format: `~24 min ride`
- If null: `ETA pending`

---

## Task 7 — Refine JARVIS System Prompt

**File:** `app/services/ai_advisor.py`

The existing `SYSTEM_PROMPT` is good. Make these targeted edits only:

1. In the formatting rules section, ensure this rule is present (add if missing):
   ```
   No parentheses around stop IDs. Omit all stop IDs entirely. No D28, R19, or any alphanumeric station identifiers. Stations are referred to by name only, always.
   ```

2. Tighten the length:
   ```
   4 to 6 sentences maximum. Every sentence must earn its place.
   ```

3. Add to the time awareness section:
   ```
   Never say you need to know the current time. Never hedge with "if the data is current" or "assuming the schedule is accurate". You have live feed data. State times with full confidence.
   ```

**Do NOT change:** model priority list, retry/backoff logic, `_build_payload`, stream generator logic, or the overall JARVIS personality.

---

## Constraints

1. **Do not break:** GPS orb, location tracking, API calls, audio playback, input bar, mobile drawer.
2. **Do not install packages** without checking `package.json`/`requirements.txt` first. `@turf/turf` is fine if needed.
3. **Verify field names** -- read `geo.py` (`find_nearest_stops` return shape) before using lat/lng fields. Read `mta_feed.py` (`parse_bytes` return shape) before using schedule fields.
4. **All animations must clean up** -- intervals, animation frames, event listeners, on unmount and state change.
5. **Read the existing code first.** Understand state, props, and component boundaries before editing.
6. **Handle null gracefully** -- the frontend must not crash if `rideDurationMinutes`, `direction`, or other optional fields are missing.
