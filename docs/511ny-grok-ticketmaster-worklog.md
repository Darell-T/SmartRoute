# 511NY, Grok Tools, and Ticketmaster QA Worklog

## Safety checkpoint

- Starting branch: `feat/transit-agent-harness`
- UI checkpoint: `6ec659f` (`feat: polish SmartRoute chat and route handoff`)
- Implementation branch: `feat/511ny-snapshot-grok-tools-ticketmaster-qa`
- Unrelated subway visual-network edits and local artifacts remain unstaged and out of scope.

## Worker assignments

| Worker | Scope | Status | Report / review |
| --- | --- | --- | --- |
| Architecture | Trace route generation, Grok, Claude, cache/lifecycle, stalled vehicles, and Ticketmaster boundaries | Complete | Report reviewed and accepted; no files changed |
| 511NY client/poller | Typed client, normalization, snapshot store, polling loop, and focused tests in new service/test files | Foundation accepted | Revision reviewed; 25/25 focused tests pass independently. Lifecycle integration remains with Grok worker. |
| Local matching/tool | Candidate stop context, deterministic half-mile matching, controlled local tool, merge helpers, and focused tests in new files | Accepted | Second correction reviewed; 24/24 focused tests and compilation pass independently; committed as `1c7d05f` |
| Grok orchestration | Lifecycle wiring, candidate contexts, bounded local-tool loop, X/web coexistence, scan metadata, and route regressions | Accepted | Three correction cycles reviewed; corrected suite discovers/passes 82 focused tests plus 17 agent regressions independently |
| Ticketmaster QA | Harden Discovery v2 integration and fixture tests without touching 511NY/Grok files | Accepted | Corrected contribution reviewed; 91 tests passed independently with one opt-in smoke skip; committed as `5f1af04` |
| Documentation/config | README architecture/configuration and placeholder-only `.env.example` | Accepted | Two review revisions; deployment constraint, exact status semantics, timeout, merge behavior, and test commands verified |
| Independent validation | Integrated implementation only | Not started | — |

## Sol review log

- 2026-07-22: Confirmed existing incident flow enters through
  `routers/trips.py`, `services/trips/incidents.py`, and
  `services/incident_monitor.py`; advisor selection is in
  `services/ai_advisor.py`.
- 2026-07-22: Confirmed FastAPI lifecycle/background-task conventions live in
  `app/main.py`; cache helpers live in `app/utils/cache.py`.
- 2026-07-22: Confirmed Ticketmaster logic is in
  `services/agent/tools/event_lookup.py` with fixture tests in
  `tests/test_agent_tools_p1.py`.
- 2026-07-22: Reviewed the architecture worker's complete report and personally
  re-read the core route, incident, advisor, lifecycle, cache, and Ticketmaster
  files. Accepted its file-boundary recommendations.
- 2026-07-22: Corrected assumptions in the supplied brief: the current advisor
  model is `claude-haiku-4-5-20251001`, the existing Grok path enables X search
  but not web search, and disabled/failed/empty incident scans all currently
  collapse to `{"incidents": []}`. The implementation will preserve the
  advisor's existing input contract while adding internal scan metadata.
- 2026-07-22: Verified the official 511NY v2 event endpoint is
  `https://511ny.org/api/v2/get/event`, authenticated with the `key` query
  parameter and throttled to 10 calls per 60 seconds. Verified official xAI
  SDK support for mixing `x_search()`, `web_search()`, and a client-side
  `tool(...)`/`tool_result(...)` loop. Verified Ticketmaster Discovery v2 uses
  `/discovery/v2/events.json` and the `apikey` query parameter.
- 2026-07-22: Personally reviewed every line of the initial `ny511.py` and
  `test_ny511.py` contribution and independently ran its 19 tests (all pass).
  Rejected the first revision because a non-empty list of entirely malformed
  records could be recorded as a fresh empty/all-clear snapshot, the snapshot
  lacked invalid-record diagnostics, and known non-NYC counties were admitted
  solely by the broad coordinate envelope. Requested focused corrections and
  additional retry-sanitization coverage.
- 2026-07-22: Reviewed the corrected 511NY normalization/filtering code and
  all six new correction tests. Independently ran 25/25 focused tests. Accepted
  the foundation for integration: malformed-all records no longer become a
  false all-clear, invalid counts are tracked, county fallback is bounded, and
  retry diagnostics remain sanitized.
- 2026-07-22: Personally reviewed the initial candidate context, matching,
  merge, controlled-tool modules, and all tests; independently ran 13/13 tests.
  Rejected the first revision for dropping route endpoints when intermediates
  exist, non-finite radius behavior, artificial cross-component geometry
  segments, missing snapshot freshness propagation, and treating active “all
  lanes closed” reports as resolved.
- 2026-07-22: Personally reviewed the initial Ticketmaster implementation and
  focused test file; independently ran 45 tests with one intentional live-smoke
  skip. Rejected the first revision because it did not locally reject distant
  venues, did not attach the existing station/line association, and did not
  cover the plan's pre-event crowd window.
- 2026-07-22: Reviewed the Ticketmaster correction across endpoint construction,
  local NYC-radius filtering, bounded pagination/deduplication, cache/single-flight,
  event timing/status handling, recognized-venue station associations, crowd
  windows, and sanitized smoke output. Independently ran 91 tests with one
  intentional opt-in smoke skip. Accepted and committed as `5f1af04`.
- 2026-07-22: Reviewed the first local-matching correction and independently
  ran 19/19 focused tests. Endpoint retention, finite radius bounds,
  component-safe geometry, and snapshot metadata were corrected. Requested a
  second revision because route segment text contains a mojibake delimiter,
  bare `closed` status can incorrectly suppress an active road closure, and
  the required cross-source/mode-scoping merge cases lack explicit tests.
- 2026-07-22: Reviewed the second local-matching correction line by line.
  Accepted the ASCII route-segment delimiter, unambiguous terminal-status
  handling, candidate-only mode scoping, cross-source evidence tests, and
  station-access/roadway impact distinctions. Independently ran 24/24 focused
  tests, compiled all three production modules, and ran scoped diff checks.
- 2026-07-22: Personally reviewed the first Grok/lifecycle implementation and
  every new/modified focused test. Rejected it because valid JSON could be
  labeled `complete` without any source tool being used, only local (not total)
  calls were bounded, tool-call IDs were not validated, the synchronous SDK
  client had no request timeout, and the deterministic merge helper remained
  unused. Requested a focused correction with explicit empty/partial/failure,
  source coexistence, call-budget, timeout, and merge-integration tests.
- 2026-07-22: Reviewed the corrected Grok implementation and rejected remaining
  edge cases: budget exhaustion without any evidence was marked `partial`,
  contributing merge sources were discarded before the advisor, and prompt
  wording contradicted required cached-tool use. Requested a narrow revision.
- 2026-07-22: Reviewed the next correction and found duplicate Python unittest
  method names silently overriding budget and sampling cases. Required unique
  test names plus an AST duplicate-name audit. The corrected suite now discovers
  one additional real test. Independently ran 82/82 focused tests and 17/17
  agent-tool tests, compiled every changed production module, imported
  `app.main`, checked scoped diffs, and verified request-path modules contain no
  511NY client, provider URL, or upstream fetch.

## Test and commit log

- UI checkpoint verification was completed before commit: frontend typecheck,
  lint, 94 frontend tests, 157 backend agent tests, production build, and mock
  harness visual review.
- Full backend implementation suite: pending final integrated run.
- `backend/.venv/Scripts/python.exe -m unittest -v tests.test_ny511`:
  19/19 passed during the initial review; 25/25 passed after correction.
- `backend/.venv/Scripts/python.exe -m unittest -v
  tests.test_incident_context_matching tests.test_trips_incidents`: 13/13
  passed during initial review; 19/19 passed after the first correction; second
  correction passed 24/24.
- `backend/.venv/Scripts/python.exe -m unittest -q tests.test_ny511
  tests.test_incident_context_matching tests.test_incident_monitor
  tests.test_incident_lifecycle tests.test_trips_incidents`: 82/82 passed after
  all Grok/lifecycle corrections.
- `backend/.venv/Scripts/python.exe -m unittest -q tests.test_agent_tools`:
  17/17 passed after Grok/lifecycle integration.
- `backend/.venv/Scripts/python.exe -m unittest -v
  tests.test_ticketmaster_event_lookup tests.test_agent_tools_p1
  tests.test_agent_tools_p2 tests.test_agent_prompt`: 91 passed, one optional
  live smoke test skipped after correction.
- Commits: `ca20fb4` (`feat: add scheduled 511ny incident snapshots`),
  `5f1af04` (`fix: harden ticketmaster discovery integration`), `1c7d05f`
  (`feat: add candidate-aware local incident matching`), `e200eee`
  (`feat: expose cached 511ny incidents through grok tools`).

## Known constraints

- The requested GPT-5.6 Luna worker profile is unavailable in this runtime;
  available GPT-5.6 workers are used with the same narrow ownership and review
  gates.
- Subway-exit guidance, unrelated transit feeds, and canonical GTFS/artifact
  changes are explicitly out of scope.
