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
- Task 6: pending typed fact schema and production wiring.
- Task 7: in progress; focused backend and frontend regression coverage added.
