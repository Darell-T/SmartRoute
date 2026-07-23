# SmartRoute — Itinerary integrity Phase 0

**Branch:** `feat/intelligence-validation-replays`  
**Date:** 2026-07-23  
**Status:** Inspection complete — no competing model introduced yet

Correctness priority: one immutable itinerary contract; no surface independently re-derives the same semantic value.

---

## 1. End-to-end data flow (today)

```
User message
  → POST /api/agent/chat  (backend/app/routers/agent_chat.py)
  → loop.run_agent_turn()  (backend/app/services/agent/loop.py)
  → LLM tool choice
  → plan_trip.execute()    (backend/app/services/agent/tools/plan_trip.py)
       1. resolve origin/destination  (_location)
       2. directions.get_transit_route()  (Google Routes)
       3. directions.parse_response()     → list[list[step]]
       4. live alerts + stalled vehicles
       5. optional incident scan
       6. advisor_context.build_advisor_payload()
       7. ai_advisor.collect_recommendation()  (rank + prose reasons)
       8. enrichment._enrich_route()  (chosen only, intermediate stops)
       9. scoring._score_routes()
      10. candidates._build_route_candidates()
      11. emit RouteCardEvent per candidate
  → SSE route_card* + model prose
  → frontend parseSseStream / use-agent-chat  (RouteCard attached to turn)
  → ChatRouteCardList / RecommendedItineraryCard  (display view-model)
  → Open on map → agentRoutePlanFromCards → left-rail + map (steps shared)
```

**Multi-stop today:** N independent `plan_trip` calls (prompt-driven). Not one chained itinerary object. Frontend may *merge* multiple recommended cards for display only (dwell invent).

---

## 2. Relevant files

### Backend (truth / planning)

| Path | Role |
|------|------|
| `backend/app/routers/agent_chat.py` | SSE transport, session, `now_et` |
| `backend/app/services/agent/loop.py` | Tool loop, emit cards, text fallback |
| `backend/app/services/agent/events.py` | `RouteCardEvent` wire shape |
| `backend/app/services/agent/session.py` | Compact card digests in session |
| `backend/app/services/agent/prompt.py` | Multi-stop / dwell *policy in prose only* |
| `backend/app/services/agent/tools/plan_trip.py` | Orchestrates plan → cards |
| `backend/app/services/directions.py` | Google call + step parse (**primary time source**) |
| `backend/app/services/trips/scoring.py` | `total_minutes`, transfers, lines |
| `backend/app/services/trips/candidates.py` | Reasons, candidate rows |
| `backend/app/services/trips/advisor_context.py` | Advisor payload + selection parse |
| `backend/app/services/ai_advisor.py` | Selection + reason text (not timing truth) |
| `backend/app/services/trips/enrichment.py` | Intermediate stops (non-timing) |
| `backend/app/utils/geo.py` | Haversine walk estimate |
| `backend/app/routers/trips.py` | Non-agent `/api/trip` (shared parse/score path) |

### Frontend (consume / format)

| Path | Role |
|------|------|
| `frontend/lib/agent-chat-stream.ts` | SSE types + parse |
| `frontend/lib/use-agent-chat.ts` | Turn state, `routeCards[]` |
| `frontend/lib/agent-route-selection.ts` | Card → map `RouteCandidate` |
| `frontend/components/smart-route/chat/itinerary-view-model.ts` | Chat display model (**formats; invents dwell/arrive fallbacks**) |
| `frontend/components/smart-route/chat/recommended-itinerary-card.tsx` | Recommended card UI |
| `frontend/components/smart-route/chat/chat-route-card.tsx` | Alternatives + list |
| `frontend/app/page.tsx` | Map handoff |
| `frontend/lib/smart-route.ts` | `summarizeRoute` (**re-derives** totals/transfers/times) |
| `frontend/components/smart-route/left-rail/live-data/route-plan.ts` | Rail plan (**recounts transfers**, client-now clocks) |
| `frontend/components/smart-route/left-rail/live-data/route-steps.ts` | Detail step rows |

---

## 3. Where each semantic value is produced today

| Value | Calculated | Copied / renamed | Regenerated / invents | Risk |
|-------|------------|------------------|----------------------|------|
| **Total duration** | Google leg `duration` → `route_total_minutes` (`directions`); `scoring._route_total_minutes` | `plan_trip` → `summary.eta_minutes`; FE candidate `total_minutes` | Advisor mock / scoring fallback via max `minutes_until_arrival`; FE `summarizeRoute` fallback sum of default 4/8 min legs | Multiple totals can disagree |
| **Leg duration** | *Not a true field.* Relative clocks only | — | FE treats `minutes_until_arrival` as leg minutes (sum); map may treat as cumulative ETA | **Semantic overload** |
| **Walking duration** | `plan_trip._walk_minutes` haversine (digest only); scoring walk heuristic 4 min | Digest `walk_minutes` **not on card summary** | FE drops short walks for preview; may sum walk steps | Card has no walk total |
| **Waiting duration** | Implicit in board − now | — | FE invents 1 min transfer legs in `summarizeRoute` | No canonical wait field |
| **Transfer duration** | Not extracted | — | Invented / omitted | Missing |
| **Departure time** | Google `departureTime` → ET ISO on steps | Digest `departs_iso`; `RouteCardEvent.depart_iso` = **requested** leave time | Map uses `now + eta` often | `depart_iso` ≠ first board |
| **Arrival time** | Google arrival on steps | Digest `arrives_iso` (often null if last is WALK) | Chat: `depart + eta` fallback; map/rail: `now + eta` | Dual clock strategies |
| **Transfer count** | `scoring`: transit steps − 1 | `summary.transfers` | Rail recomputes from steps | Can disagree |
| **Route sequence** | Ordered transit steps + `scoring._route_lines` | `summary.lines` | FE collapses for preview only | OK if geometry shared |
| **Selected itinerary** | FE `selectedCardId` / map `activeCandidateId` | Session `selected_card_id` on next turn | — | UX only |
| **Alternative comparison** | Scoring deltas + advisor reasons | `rejection_reason` / `recommendation_reason` | Rail “slower by N min” from totals | OK if totals canonical |
| **Recommendation rationale** | Advisor prose + fallbacks | `summary.reason` | Chat splits on `·`; `summarizeRoute` chips are unrelated heuristics | Must stay backend-owned |
| **Waypoint dwell / pickup** | Prompt “+25 min” only | — | FE multi-card merge invents dwell from ISO gaps | **Must become server-owned** |

### Critical semantic trap

`minutes_until_arrival` is **minutes from parse-time “now” until that step’s arrival**, not “this leg takes X minutes.” Using it as both leg duration (chat) and trip ETA (scoring fallback / map) produces integrity bugs.

---

## 4. Current wire shape (agent)

```text
RouteCardEvent:
  card_id, turn_id, role
  origin, destination
  summary: { eta_minutes, transfers, lines, reason }
  route: AgentRouteStep[]
  alerts
  leg_label?   # unused by plan_trip
  depart_iso?  # requested departure, not first board
```

Missing vs target contract: seconds-based totals, wait/walk/ride/dwell breakdown, waypoints, timezone, planning mode, data basis/freshness, structured reasons, true leg durations, multi-stop chain id.

---

## 5. Proposed ownership (converge, do not fork)

**Rule:** LLM never calculates, repairs, or overwrites timing. Agent interprets intent and calls tools. All UI surfaces format the same object.

| Semantic value | Canonical owner | Surfaces may only |
|----------------|-----------------|-------------------|
| Absolute schedule times (ISO) | `directions.py` parse | Locale format clocks |
| Mode sequence + geometry | `directions.py` | Draw / collapse for preview |
| **Canonical itinerary totals** (door-to-door, walk, wait, in-vehicle, dwell) | New `backend/app/services/trips/itinerary.py` (or post-parse builder called from `plan_trip`) | Format seconds → display |
| Transfer count | Same itinerary builder (from legs) | Format |
| Ranking score (not ETA truth) | `scoring.py` | Show rank, not re-score ETA |
| Selection + reason text | `ai_advisor` + `candidates` | Display structured reasons |
| Intermediate stops | `enrichment.py` | List stops |
| Multi-stop chain + dwell | Server chain builder (not prompt-only) | Display |
| Card / SSE assembly | `plan_trip` maps itinerary → event | — |
| Session digests | `session.py` | Compact refs only |
| Chat preview curation | FE view-model | Drop short walks, cap rows — **no new totals** |
| Map geometry | `card.route` / itinerary legs | Draw only |
| Selection UX | FE | `selectedCardId` only |

### One field per meaning (target)

| Concept | Canonical field | Forbidden |
|---------|-----------------|-----------|
| Total trip length | `total_duration_seconds` | Re-summing legs in UI; using max `minutes_until_*` |
| Walk total | `total_walk_seconds` | Haversine in UI; invent 4 min/walk |
| Wait total | `total_wait_seconds` | Invent 1 min transfer |
| In-vehicle | `total_in_vehicle_seconds` | — |
| Dwell | `total_dwell_seconds` + waypoint `dwell_minutes` | FE ISO gap invent as truth |
| Leg ride length | `leg.ride_duration_seconds` | Overloading `minutes_until_arrival` |
| Board time | `leg.departure_at` | Client `now + offset` when ISO present |
| Alight time | `leg.arrival_at` | Same |
| Trip depart | `departure_at` | Confusing with `requested_departure` |
| Trip arrive | `arrival_at` | `now + eta` when ISO present |
| Requested leave | `requested_temporal_constraint` / planning mode | Renaming to `depart_iso` on card |
| Transfers | `transfer_count` | Rail re-count when present |
| Reasons | `structured_recommendation_reasons[]` | Hardcoded FE chips for agent path |

---

## 6. Proposed canonical object (sketch)

Immutable after planning. Name can map onto extended `RouteCard` / new `CanonicalItinerary` without a second parallel planner.

```text
CanonicalItinerary
  itinerary_id
  origin, destination
  waypoints[] { place_id, display_name, address, lat, lng, dwell_minutes, dwell_source }
  timezone                 # e.g. America/New_York
  requested_temporal_constraint
  planning_mode            # leave_now | depart_at | arrive_by
  generated_at
  data_basis               # realtime | schedule | mixed
  data_freshness
  departure_at, arrival_at
  total_duration_seconds
  total_walk_seconds
  total_wait_seconds
  total_in_vehicle_seconds
  total_dwell_seconds
  transfer_count
  legs[] {
    mode, service_id
    board, alight
    departure_at, arrival_at
    walk_seconds, wait_seconds, ride_seconds, transfer_seconds
    geometry
    service_data_basis
  }
  map_geometry             # or derived strictly from legs
  alternatives[]           # other CanonicalItinerary or light refs
  structured_recommendation_reasons[]
```

Wire compatibility: emit this as the agent `route_card` payload (or nest under `itinerary`) and keep a thin compatibility layer that fills legacy `summary.eta_minutes = round(total_duration_seconds/60)` until all consumers migrate.

---

## 7. Implementation principles (binding)

1. **Do not invent a second planner** — Google Routes remains the path engine; the new module *normalizes* results.
2. **LLM never owns numbers** — only intent + tool calls + explanation of returned plan.
3. **One write, many readers** — chat, map, rail, navigation all bind to the same itinerary object.
4. **Display formatters only** — `formatDurationMinutes`, locale clocks; no business arithmetic.
5. **Multi-stop is server-side** — chain legs + dwell on the backend; FE merge becomes a pure presenter of one itinerary.
6. **Prefer additive wire fields** — extend SSE; do not break existing tests until adapters land.

---

## 8. Recommended task order (for plan / SDD)

1. Backend itinerary builder + tests (seconds, walks, waits, transfers from parsed steps)
2. Extend `RouteCardEvent` / wire types with canonical fields (backward-compatible)
3. `plan_trip` emits canonical object; stop dual ETA paths
4. Multi-stop chain tool or plan_trip mode with waypoints + dwell
5. Frontend types + stop inventing totals/dwell/arrive when canonical present
6. Map/rail prefer canonical clocks and transfer_count
7. Structured reasons → card “Why” / map rationale
8. Route selection interaction + animation/performance (after integrity)
9. Portfolio links (last)

---

## 9. Phase 0 conclusion

| Question | Answer |
|----------|--------|
| Single source of truth today? | **No** — Google parse + scoring + advisor mock + FE invent |
| Competing models? | Display models yes (`ItineraryViewModel`, `RouteSummary`, rail `RoutePlan`); no second planner — **converge display on one contract** |
| Highest integrity bug | Overload of `minutes_until_arrival`; multi-stop dwell only on FE; map `now+eta` vs ISO |
| First code change | Backend canonical itinerary builder + tests |

**Next:** implementation plan at `docs/superpowers/plans/2026-07-23-itinerary-integrity.md`, then subagent-driven execution.
