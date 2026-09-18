# SmartRoute

SmartRoute is a real-time NYC transit planner. The backend owns itinerary facts. Conversation may add optional place-queue context without changing those facts.

## Language

**Rider**:
The person asking for transit or destination help in the current conversation.
_Avoid_: User, customer, client

**Canonical itinerary**:
The one server-owned trip record for duration, timing, transfers, walking, stops, and dwell.
_Avoid_: Frontend-calculated trip, reconstructed route

**Physical venue**:
One real place identified by an exact Google Place ID, including a specific brand branch.
_Avoid_: Brand, chain, fuzzy match

**Discovery set**:
The stored, verified places from one current place search, referenced later by opaque IDs.
_Avoid_: Search results, recommendations list

**Presented place**:
A discovery-set place already shown to the rider in this destination decision.
_Avoid_: Selected destination (unless the rider has accepted it)

**Queue evidence**:
Optional current or historical wait facts for a registered physical venue. It never becomes route time.
_Avoid_: Line (when that could mean a subway route), popularity, crowd score

**Queue context**:
Turn-scoped instruction for the current destination decision: `ignore`, `heads_up`, `decision`, or `historical`.
_Avoid_: Saved preference, profile setting

**Current observation**:
A fresh join-now wait or people count captured by the queue provider, with that capture time.
_Avoid_: Wait at arrival, live count plus history blend, "and counting"

**Historical pattern**:
A weekday-and-hour wait or people average from comparable past records, distinct from a current observation.
_Avoid_: Usual wait (when only one comparable date exists), trend, forecast

**Unmonitored coverage**:
A physical venue with no registry match. Unknown wait, not a short line.
_Avoid_: Quiet, unpopular, better pick

**Join-now estimate**:
The current time to join the queue. It does not include travel, arrival delay, or order preparation.
_Avoid_: Total trip wait, added ETA

**Trusted source**:
A server-owned title and URL naming the queue provider page used for that evidence.
_Avoid_: Model-authored citation, camera feed

**Subway line**:
An MTA route identity such as the A or the 6.
_Avoid_: Line (when discussing a venue queue)
