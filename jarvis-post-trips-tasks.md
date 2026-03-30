# JARVIS — Post-Trips.py Tasks

## Task 1: Update JARVIS System Prompt

**File:** `app/services/ai_advisor.py`

The data shape JARVIS receives has changed completely. Update the `SYSTEM_PROMPT` to reflect the new structure.

### Replace the JSON keys description with:

```
You will receive a JSON object with the following keys:

- "routes": a list of alternative transit routes from Google Routes API,
  each ranked by Google from best to worst. Each route is a list of steps.
  Each step is either:

  WALK step: has "type": "WALK", start/end coordinates, and a polyline.

  TRANSIT step: has "type": "SUBWAY" or "BUS", with:
    - "train_line": the line letter (e.g., "Q", "F", "4")
    - "direction": the headsign (e.g., "Manhattan-bound")
    - "departure_stop" / "arrival_stop": station names
    - "minutes_until_train_arrives": real-time minutes until departure
    - "minutes_until_arrival": real-time minutes until reaching that stop
    - "stop_count": number of stops on that segment

- "service_alerts": active MTA service alerts affecting routes the rider
  might take. Each has a header describing the disruption and the affected
  route IDs.

- "incidents": real-time incidents near the rider's stations (fires,
  police activity, etc.). May be empty.

Your job:
1. Look at ALL route alternatives, not just the first one.
2. Cross-reference each route with service_alerts and incidents.
3. If the best route (routes[0]) has a service alert indicating a
   suspension, major delay, or an incident at a station along the
   route, recommend a different alternative that avoids the problem.
4. If all routes are clear, recommend routes[0] as it is Google's
   top pick.
5. Describe the chosen route to the rider: which train to take,
   from where, any transfers, and total time.
6. If there is a relevant service alert or incident, mention it
   briefly as context for why you chose this route.
```

### Keep these existing rules (do not remove):

- JARVIS personality (calm, witty, British, addresses rider as "sir")
- Time awareness (relative times only, no absolute clock times)
- Formatting rules (no markdown, natural sentences, read aloud by TTS)
- The "3 sentences maximum" rule

### Update the sentence rules to:

```
ABSOLUTE RULE: 3-4 sentences maximum. No exceptions. No run-on sentences
joined with dashes or semicolons.
Sentence 1: What to do right now (which train, which station, how soon).
Sentence 2: Any transfer, disruption, or key detail as one fact.
Sentence 3: Total trip time or a brief quip.
This is read aloud. The rider stops listening after 15 seconds.
```

### Update `_build_payload` or `stream_recommendation`:

The function signature for `stream_recommendation` currently takes
`transit_data: str` and `incident_data: str`. Update it to accept
the new combined payload shape. The payload sent to JARVIS should be
a single JSON string containing:

```python
{
    "routes": parsed_routes,       # from parse_response()
    "service_alerts": alerts,      # from filter_alerts_for_routes()
    "incidents": incidents          # from safe_incidents()
}
```

Adjust `_build_payload` and `stream_recommendation` accordingly.
Keep the retry logic and model priority list unchanged.

---

## Task 2: Update Frontend API Call

**File:** `page.tsx`

The frontend currently sends two address strings to `/api/trip`.
Update it to send GPS coordinates for origin and a destination string.

### Change the request body:

From:

```typescript
{ origin: "some address", destination: "some address" }
```

To:

```typescript
{
  origin_lat: userLatitude,    // from browser geolocation
  origin_lng: userLongitude,   // from browser geolocation
  destination: inputText       // raw string the user typed
}
```

### Update the response handling:

The API response shape changes. It now returns:

```typescript
{
  text: string,
  audio: string,          // base64
  routes: [               // all alternatives
    [                     // each route is a list of steps
      {
        type: "WALK" | "SUBWAY" | "BUS",
        // walk fields: start_point, end_point, polyline
        // transit fields: train_line, line_color, direction,
        //   departure_stop, arrival_stop, departure_coords,
        //   arrival_coords, minutes_until_train_arrives,
        //   minutes_until_arrival, stop_count, polyline
      }
    ]
  ],
  serviceAlerts: [{header: string, routeIds: string[]}],
  // First transit step of chosen route provides HUD pill data:
  trainLine: string,
  departureTimestamp: number | null,
  rideDurationMinutes: number | null,
  direction: string
}
```

Extract `trainLine`, `departureTimestamp`, `rideDurationMinutes`,
and `direction` from the response for the HUD pills. The `routes`
array is passed to the map component for rendering.

---

## Task 3: Update Map Component for New Route Data

**File:** `jarvis-map.tsx`

### Replace single-line rendering with legs-based rendering:

The map currently draws one straight line from origin to destination.
Replace this with rendering from the `routes` array (specifically the
first/chosen route's steps).

For each step in the route:

**WALK steps:**

- Decode `polyline.encodedPolyline` using `@mapbox/polyline` package
  (install if needed: `npm install @mapbox/polyline`)
- Draw as dashed white line, `line-width: 2.5`, `line-dasharray: [2, 4]`,
  `line-opacity: 0.7`

**SUBWAY steps:**

- Decode the polyline
- Draw as solid line, `line-width: 5`, color from `line_color` field
  (Google provides the hex color)
- Glow layer underneath: `line-width: 14`, same color, `line-opacity: 0.12`

**BUS steps:**

- Decode the polyline
- Draw as dashed line, `line-width: 4`, `line-dasharray: [6, 3]`,
  color `#0057B8`
- Blue glow underneath

### Station markers:

At each transit step's departure and arrival stops, add pill markers
showing station name + line letter.

### Animate sequentially:

Each step starts drawing after the previous finishes:

- Walk: ~1s
- Subway: ~2s
- Bus: ~1.5s

### Polyline decoding:

```typescript
import polyline from "@mapbox/polyline";

const decoded = polyline.decode(step.polyline.encodedPolyline);
const coordinates = decoded.map(([lat, lng]) => [lng, lat]);
// Note: polyline.decode returns [lat, lng], Mapbox uses [lng, lat]
```

### Camera:

- fitBounds to encompass all route points when animation starts
- flyTo destination after all segments drawn
- Slow rotation while isSpeaking
- flyTo back to user location when speech ends

### Route persistence:

- Layers and markers stay until new submission or page refresh
- On new submission: clear all route sources, layers, markers first

---

## After These Tasks

- Test from multiple locations using Chrome DevTools sensor override
- Fix destination orb pulse animation if not working
