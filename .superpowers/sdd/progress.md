# SDD Progress — Itinerary Integrity

Plan: `docs/superpowers/plans/2026-07-23-itinerary-integrity.md`
Spec: `docs/superpowers/specs/2026-07-23-itinerary-integrity-phase0.md`
Branch: `feat/intelligence-validation-replays`

## Status

- Phase 0: complete (inspection documented)
- Task 1: complete (commits 78d6635..7581f4c, review clean)
- Task 2: complete (commits 7581f4c..b922d7c, review clean)
- Task 3: complete (commits b922d7c..e6531db, tests green)
- Task 4: complete (commits e6531db..a9cc624, 16 itinerary tests pass)
- Task 5: complete (commits a9cc624..f53d9d9, review deferred controller accept)
- Audit follow-up (2026-07-24): in progress. Confirmed that the original
  multi-stop chain helper is not wired into the agent, direct `/api/trip`
  previously bypassed canonical itineraries, and the chat preview still read
  legacy relative clocks despite a canonical itinerary being present.
- Repair started: provider duration seconds are retained, direct map candidates
  now emit canonical itineraries, chat previews prefer canonical leg seconds,
  and chat-to-map entry context suppresses duplicated rail reasoning.
- Multi-stop runtime repair: complete. One plan_trip call can now carry ordered
  waypoints; the backend plans each production leg, owns dwell timing, and
  emits one chained RouteCardEvent / itinerary ID. The frontend merger is
  legacy-only and bypassed whenever a canonical card exists.
- Card review repair: complete. The recommendation card has no liquid-metal
  wrapper, uses neutral start/transfer/destination spine states, and keeps
  Open on map as its only secondary action.
- Task 6: complete for active chat and direct-map plans. Canonical
  recommendation facts use typed, scorer-owned records; legacy prose remains
  a compatibility alias for older sessions.
- Time and place-identity follow-up (2026-07-24): arrive-by requests now
  derive an offset-aware provider departure internally and preserve their
  requested arrival in the canonical itinerary. Resolved rider-facing place
  identity now survives private address-to-coordinate recovery, and recovered
  retries replace failed reasoning chips instead of leaving a red failure row.
- Task 7: in progress; focused backend and frontend regression coverage added.
