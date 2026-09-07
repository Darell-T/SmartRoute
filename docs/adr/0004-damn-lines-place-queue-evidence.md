---
status: accepted
---

# Damn Lines place queue evidence

SmartRoute treats Damn Lines as optional evidence inside the existing place
discovery and destination decision flow. It does not add a ninth Agent
capability. The Agent selects a turn-scoped Queue Context on
`discover_places`, while the backend owns provider access, normalization,
freshness, historical aggregation, canonical queue prose, failure handling,
and trusted source attribution.

Google Places remains authoritative for Physical Venue identity, branch,
location, and open status. The Supported Venue Registry maps an exact Google
Place ID to one Damn Lines API slug and source URL. SmartRoute performs no
runtime fuzzy matching or brand-level evidence transfer. A missing registry
entry means only that queue coverage is unknown.

Current evidence comes from one cached `/v1/locations` snapshot requested only
when a relevant registered venue needs it. A Queue Observation is usable for
five minutes from the provider's `captured_at` timestamp. Partial observations
remain partial. SmartRoute does not predict a wait at arrival, calculate recent
queue trends, add queue wait to route duration, or use Damn Lines open status
as the venue's operating status.

Historical Queue Patterns refresh weekly outside the rider-critical path. Each
lookup is keyed by Physical Venue, New York weekday, and hour and summarizes
the most recent 30 days with provider sample weighting. History is a fallback
for an open registered venue without current coverage and a direct answer to an
explicit historical question. It is never blended with a partial current
observation or presented as equivalent to current evidence.

Queue evidence is conversational. Canonical backend prose follows the ordered
place recommendations, and a structured source component attributes Damn
Lines once for the response. Queue information never enters route arithmetic,
the Route Card, route steps, map markers, or map presentation. Camera images,
video, streams, and computer-vision data remain outside SmartRoute.

Provider failure does not block Google place discovery, destination selection,
or routing. Ordinary heads-up requests remain silent when neither current nor
valid historical evidence is available. Explicit queue requests state that
current information is unavailable and may provide valid historical context.
The Agent asks for rider confirmation only when a Decision-Material Queue Gap
could realistically change the current destination choice.
