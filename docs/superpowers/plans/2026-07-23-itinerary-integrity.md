# Itinerary Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish one immutable canonical itinerary contract so chat, map, and navigation never independently re-derive trip timing or multi-stop structure.

**Architecture:** Keep Google Routes as the path engine. Add a pure backend itinerary normalizer that turns parsed steps into a single `CanonicalItinerary` with seconds-based totals and explicit leg/waypoint fields. Extend agent `route_card` SSE to carry that object (plus thin legacy summary aliases). Frontend becomes format-only consumers; multi-stop chaining and dwell move server-side.

**Tech Stack:** Python FastAPI backend, existing `directions`/`trips` modules, Next.js frontend, `agent-chat-stream` SSE types, pytest + frontend `tsx --test`.

**Spec / Phase 0:** `docs/superpowers/specs/2026-07-23-itinerary-integrity-phase0.md`

## Global Constraints

- Branch: `feat/intelligence-validation-replays` only; no branch switches.
- Do not create a second route planner; normalize Google/`directions` output only.
- LLM must not calculate, repair, or overwrite itinerary timing.
- One canonical field per semantic value; UI may format only.
- Prefer additive, backward-compatible wire fields; keep existing tests green via adapters.
- `minutes_until_arrival` must not be treated as leg duration once `ride_duration_seconds` exists.
- Multi-stop dwell defaults (25 minutes unless user-specified) are server-owned, not FE invent.
- Make small reviewable commits; run the relevant tests for each task.
- Do not modify generated transit map artifacts.

---

### Task 1: Backend canonical itinerary types + pure builder

**Files:**
- Create: `backend/app/services/trips/itinerary.py`
- Create: `backend/tests/test_itinerary_canonical.py`
- Modify: `backend/app/services/trips/__init__.py` (export if needed)

**Interfaces:**
- Consumes: parsed route steps as produced by `directions.parse_response` / `_parse_leg_steps`
- Produces:
  - `build_canonical_itinerary(steps, *, origin, destination, planning_mode, requested_departure=None, generated_at=None, data_basis="mixed", reasons=None, itinerary_id=None) -> dict`
  - Fields: `itinerary_id`, `origin`, `waypoints`, `destination`, `timezone="America/New_York"`, `planning_mode`, `generated_at`, `data_basis`, `data_freshness`, `departure_at`, `arrival_at`, `total_duration_seconds`, `total_walk_seconds`, `total_wait_seconds`, `total_in_vehicle_seconds`, `total_dwell_seconds`, `transfer_count`, `legs[]` with `mode`, `service_id`, `board`, `alight`, `departure_at`, `arrival_at`, `walk_seconds`, `wait_seconds`, `ride_seconds`, `transfer_seconds`, `geometry`, `service_data_basis`

- [ ] **Step 1: Write failing tests** for a WALK→SUBWAY→WALK fixture: totals in seconds, transfer_count 0, leg ride duration from ISO delta not from `minutes_until_arrival` as duration, walk seconds from haversine or ISO when available, departure_at/arrival_at from first/last absolute times.

- [ ] **Step 2: Run** `python -m pytest backend/tests/test_itinerary_canonical.py -q` — expect FAIL (module missing).

- [ ] **Step 3: Implement** pure builder in `itinerary.py` (no network). Prefer absolute ISO deltas for leg lengths; fall back to Google `route_total_minutes` only for whole-trip total if leg ISO incomplete; never invent 1-minute transfer filler.

- [ ] **Step 4: Run tests** — expect PASS.

- [ ] **Step 5: Commit** `feat(trips): add canonical itinerary builder`

---

### Task 2: Wire builder into plan_trip + extend RouteCardEvent

**Files:**
- Modify: `backend/app/services/trips/itinerary.py` (helpers if needed)
- Modify: `backend/app/services/agent/tools/plan_trip.py`
- Modify: `backend/app/services/agent/events.py`
- Modify: `backend/tests/test_agent_loop.py` and/or plan_trip tests if present
- Create/extend: `backend/tests/test_plan_trip_itinerary.py`

**Interfaces:**
- Consumes: Task 1 builder
- Produces: each `RouteCardEvent` includes:
  - existing fields unchanged for compatibility
  - `itinerary: <canonical dict>` (full object)
  - `summary.eta_minutes` = `round(total_duration_seconds / 60)` from itinerary only
  - `summary.transfers` = `transfer_count` from itinerary only
  - stop inventing a second walk ETA path for card totals

- [ ] **Step 1: Failing test** — plan_trip result / RouteCardEvent carries `itinerary.total_duration_seconds` and summary minutes match floor/round of that value.

- [ ] **Step 2: Implement** event field + plan_trip assembly; keep `route` steps for geometry/map.

- [ ] **Step 3: Run** agent/plan_trip related pytest — PASS.

- [ ] **Step 4: Commit** `feat(agent): emit canonical itinerary on route cards`

---

### Task 3: Frontend types + stop inventing totals when itinerary present

**Files:**
- Modify: `frontend/lib/agent-chat-stream.ts`
- Modify: `frontend/components/smart-route/chat/itinerary-view-model.ts`
- Modify: `frontend/components/smart-route/chat/itinerary-view-model.test.mjs`
- Modify: `frontend/lib/agent-route-selection.ts` (copy total from itinerary when present)

**Interfaces:**
- Consumes: SSE `itinerary` optional on `RouteCard`
- Produces: view-model total/arrive/transfers from itinerary seconds/ISO when present; leg durations from `leg.ride_seconds` / walk_seconds when present

- [ ] **Step 1: Failing tests** — card with `itinerary.total_duration_seconds=5340` formats duration from that; does not sum invented walk defaults for hero total.

- [ ] **Step 2: Parse itinerary in stream builder; prefer canonical fields in view-model and agentRoutePlanFromCards.**

- [ ] **Step 3: `npm run typecheck` + itinerary unit tests — PASS.**

- [ ] **Step 4: Commit** `feat(chat): consume canonical itinerary on cards`

---

### Task 4: Multi-stop chain builder (server dwell + waypoints)

**Files:**
- Modify: `backend/app/services/trips/itinerary.py` (`build_chained_itinerary` or merge helpers)
- Modify: `backend/app/services/agent/tools/plan_trip.py` and/or new tool wrapper
- Modify: `backend/app/services/agent/prompt.py` (point model at server chain; still no timing invent)
- Tests: `backend/tests/test_itinerary_chain.py`

**Interfaces:**
- Consumes: ordered legs (each a route result) + waypoint dwell_minutes / dwell_source
- Produces: single `CanonicalItinerary` with waypoints, `total_dwell_seconds`, one `itinerary_id`, summed totals

- [ ] **Step 1: Failing tests** for two OD legs + 25 min dwell: total_duration includes dwell; waypoint.dwell_source=`default`.

- [ ] **Step 2: Implement chain merge; emit one recommended multi-stop card (or one itinerary with multi waypoints) instead of relying on FE merge invent.**

- [ ] **Step 3: Pytest PASS.**

- [ ] **Step 4: Commit** `feat(trips): multi-stop chain itinerary with server dwell`

---

### Task 5: Map/rail prefer canonical clocks and transfer_count

**Files:**
- Modify: `frontend/lib/smart-route.ts`
- Modify: `frontend/lib/agent-route-selection.ts`
- Modify: `frontend/components/smart-route/left-rail/live-data/route-plan.ts` (prefer candidate transfer_count / arrival ISO when present)
- Tests: existing unit tests + small new cases

**Interfaces:**
- Consumes: `RouteCandidate` enriched with itinerary totals / ISO
- Produces: arriveLabel from `arrival_at` when present; transferCount from candidate not re-count when provided

- [ ] **Step 1: Failing tests** for prefer ISO arrival over `now+eta`.

- [ ] **Step 2: Implement preference order: itinerary ISO → summary → legacy fallback.**

- [ ] **Step 3: Tests PASS.**

- [ ] **Step 4: Commit** `fix(map): prefer canonical itinerary times and transfers`

---

### Task 6: Structured recommendation reasons + selection interaction

**Files:**
- Modify: `backend/app/services/trips/candidates.py` / plan_trip to populate `structured_recommendation_reasons: string[]`
- Modify: frontend card to read `itinerary.structured_recommendation_reasons` (or summary.reason split only as fallback)
- Route selection: ensure selecting a card does not replan; only sets selection + map from same itinerary object

- [ ] **Step 1: Tests** for reasons array round-trip.

- [ ] **Step 2: Implement.**

- [ ] **Step 3: Commit** `feat(agent): structured itinerary recommendation reasons`

---

### Task 7: Integrity regression suite

**Files:**
- Create: `backend/tests/test_itinerary_integrity.py`
- Optional FE: `frontend/lib/agent-route-selection.test.mjs` extensions

- [ ] **Step 1: Tests asserting** chat/map totals cannot diverge when itinerary present (same seconds → same displayed minutes).

- [ ] **Step 2: Run full backend pytest subset + FE unit tests.**

- [ ] **Step 3: Commit** `test: itinerary integrity regressions`

---

## Out of scope for this plan (defer)

- Animation/performance polish (item 6) — after integrity green
- External portfolio links (item 7)
- Rewriting Google routing itself
- Full canonical refactor of non-agent `/api/trip` UI (share builder, migrate later)

## Self-review

- Phase 0 ownership covered by Tasks 1–5.
- No second planner.
- Multi-stop + dwell server-side in Task 4.
- FE invent removed when itinerary present (Tasks 3–5).
