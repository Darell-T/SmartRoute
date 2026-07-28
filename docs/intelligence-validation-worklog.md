# SmartRoute intelligence validation worklog

## Scope

Formal deterministic validation of the existing route-intelligence pipeline.
This phase adds no new city-data providers and preserves the reviewed 511NY,
Grok, MTA, stalled-vehicle, and Ticketmaster implementations.

## Repository baseline

- Base branch: `feat/511ny-snapshot-grok-tools-ticketmaster-qa`
- Validation branch: `feat/intelligence-validation-replays`
- Base commit: `4415007`
- Existing unrelated frontend/map changes and untracked files are intentionally
  preserved and excluded from validation commits.

## Worker assignments

| Worker | Scope | Status | Sol review |
| --- | --- | --- | --- |
| Test architecture baseline | Python/test environment, protobuf fixtures, advisor identity, planning-flow and hook audit | Accepted | Read-only findings reproduced by Sol; established implementation boundaries |
| Test environment stabilization | Declared pytest workflow, GTFS-RT fixtures, CI gate, advisor identity | Accepted | Complete diff reviewed; 11 focused tests and full pytest independently passed |
| Replay framework | Fixture schema, loader, frozen time, production normalization | Accepted after three correction rounds | 103 focused tests plus one optional skip independently verified |
| Planning modes | Shared baseline/intelligence advisor payload and selection parsing | Accepted after correction | Synthetic Ticketmaster penalty rejected and removed; 44 tests independently passed |
| Comparison runner | Baseline/intelligence execution and reports | Accepted after correction | 28 replay/advisor tests independently passed; offline CLI output inspected |
| Candidate evidence contract | Preserve verified 511NY candidate/mode/scope into advisor input | Accepted after correction | Duplicate sanitizers rejected; shared allowlist leaf and 99-test independent run accepted |
| Golden scenarios | Twelve scenarios and false-reroute coverage | Accepted | Sol built and inspected provider-shaped fixtures; 38 focused tests and all-scenario CLI passed |
| Shadow logging and metrics | Safe shadow records, classifications, aggregation | Accepted after correction | 45 tests independently passed, including Sol-added review CLI coverage |
| Ablation and failures | Seven source toggles, source status, lifecycle and partial-failure coverage | Accepted after two correction rounds | Complete owned diff reviewed; 117 focused tests independently passed |
| Fixture and live certification | Dev/test 511NY fixture path and opt-in 511NY, Ticketmaster, advisor commands | Accepted after correction | Complete diff reviewed; 139 tests passed with one optional skip |
| Production shadow integration | Disabled-by-default counterfactual baseline in `/api/trip` | Accepted | Fail-closed flag/path gate, immutable displayed result, privacy allowlist, timeout/failure tests; 64 focused tests passed |
| Reporting and CI | Metrics bridge, artifacts, deterministic CI gate | Accepted | Reports derive from observed comparisons/ablations; sub-ms samples no longer misreported as zero |
| Independent validation | Integrated adversarial review | Accepted after corrections | Sole Terra worker found five material issues; Sol reproduced, corrected, and reran 69 focused plus 460 full-suite tests |

## Sol review log

- Confirmed the validation branch starts from all reviewed 511NY commits.
- Confirmed pre-existing frontend/map and untracked work is outside task scope.
- Confirmed CI currently installs `pytest` ad hoc instead of declaring it and
  excludes service-alert tests because their GTFS-RT fixtures lack a required
  feed header.
- Confirmed the production route-advisor priority is currently pinned to
  `claude-haiku-4-5-20251001`; separate agent-loop defaults mention Sonnet and
  must not be confused with route-advisor identity.
- Reproduced the two GTFS-RT `EncodeError` failures and confirmed their test
  fixtures omitted the schema-required `FeedMessage.header`.
- Reviewed the environment worker's complete diff and verified it touched only
  assigned files. Valid fixtures now include a deterministic GTFS-RT header;
  an explicit partial-feed test exercises the production parser separately.
- Installed the declared test dependency into the project virtual environment
  and independently ran the canonical pytest command: 342 passed, one optional
  live Ticketmaster test skipped.
- Confirmed Ticketmaster is currently an agent tool, not a live `/api/trip`
  advisor input. Validation must not claim current live route-selection impact;
  a bounded shared advisor-input seam is being reviewed without adding a new
  network call.
- Rejected the first replay revision because an empty Ticketmaster fixture did
  not prove normalization and SIRI bus data bypassed production parsing.
- Rejected the second replay revision because non-empty malformed advisor
  transcripts could silently fall back to route zero and authored subway-stall
  evidence bypassed the live detector.
- Accepted the corrected replay foundation after it extracted pure production
  bus/subway detection helpers, derived stall evidence from raw provider-shaped
  fixtures at frozen time, validated advisor transcripts through production
  parsing, and blocked socket/httpx/requests network paths.
- Rejected an invented Ticketmaster `penalty_minutes` field from the shared
  advisor contract because production computes crowd windows, not a numeric
  event score. The corrected contract carries bounded descriptive evidence.
- Rejected the first comparison-runner handoff because stale 511NY matches
  still entered intelligence inputs, disabled 511NY was reported as fresh,
  and the reported decision timer ended before selection parsing.
- Rejected the first shadow/metrics handoff because unevaluated comparisons
  could dilute false-reroute and missed-disruption rates, latency was not
  aggregated, and no bounded counterfactual helper exercised disabled,
  timeout, evaluator-failure, and sink-failure paths while preserving the
  exact rider-facing result.
- Accepted comparison after stale/unavailable 511NY matches were excluded,
  disabled source state was explicit, local replay phase timings became honest,
  and semantic projections removed every volatile timing field.
- Accepted shadow/metrics after bounded counterfactual execution preserved the
  exact displayed result through disabled, timeout, evaluator-error, and
  sink-error paths; Sol added an end-to-end classification CLI test before
  committing.
- Golden-scenario design review found that 511NY matcher candidate/mode/scope
  fields were lost before advisor input. Scenario implementation is blocked
  until the production association contract is corrected and reviewed.
- Rejected the first production association fix because it duplicated roughly
  one hundred lines of allowlists and sanitization across scanner and advisor
  handoff modules. Accepted the extracted dependency-free contract after it
  enforced exact matcher provenance, strict `candidate-N` identifiers, bounded
  fields, and false-positive bus/subway/station-access semantics.
- Rejected the first ablation/failure handoff because replay comparison could
  revive resolved 511NY rows from matcher-only data. Accepted the lifecycle
  join only after it used the exact normalized snapshot row and applied the
  production current-incident filter before advisor evidence and diagnostics.
- Rejected the corrected ablation/failure handoff once more because a claimed
  complete scan could retain an unavailable snapshot and replay reports could
  echo arbitrary authored error text. Accepted only after complete required a
  fresh snapshot and persisted failures were reduced to fixed safe categories.
- Accepted all seven canonical source toggles without a test-only numeric
  scoring engine. Recorded advisor variants use the production selection
  parser; scenarios without variants are explicitly reported as not recorded.
- Accepted 511NY fixture mode only after it became explicit dev/test-only,
  flowed through `SnapshotStore` normalization/NYC filtering/freshness, and
  carried a bounded `fixture` origin through the route-time local matcher.
- Accepted opt-in live certification after tests proved default CLI execution
  remains offline, missing keys skip cleanly, 511NY uses one upstream attempt,
  Ticketmaster is limited to one page/request, and malformed advisor output is
  not accepted through the production route-zero fallback.
- Git staging for the accepted fixture/certification commit was blocked by the
  desktop approval quota. No workaround was attempted; reviewed changes remain
  unstaged on the dedicated validation branch until Git index access returns.
- The golden-scenario worker could not start because the worker platform usage
  limit was reached. Sol implemented and reviewed the twelve scenarios directly
  rather than running multiple workers or bypassing the requested serial-worker
  constraint.
- Rejected an initially malformed empty GTFS-RT replay payload; replaced it with
  a valid header-only feed so the production parser, not protobuf construction,
  owns the behavior under test.
- Rejected rounded `0 ms` fixture timing because it implied no work occurred.
  Positive sub-millisecond measurements are now conservatively stored as 1 ms.
- Accepted production shadow integration only after the enabled flag also
  required an explicit `.jsonl` sink. Without both, the counterfactual advisor
  is never called and the exact displayed object is returned.
- Reviewed generated JSON and text reports. Metrics are separated as
  `deterministic_fixture` evidence and carry a no-real-world-performance claim
  boundary.
- The sole independent Terra audit rejected final acceptance on five points:
  Ticketmaster was replay-only, recorded transcripts were being described too
  causally, the partial scenario used a fresh snapshot, fallback shadow choices
  were comparable, and MTA had no observable ablation effect. Sol accepted all
  five findings. The corrections explicitly narrow Ticketmaster/transcript
  claims, exercise `SnapshotStore`'s unavailable lifecycle, make fallback
  disagreements unevaluated, and add an active MTA alert with a recorded
  explanation-only ablation.

## Test and commit log

- `faf28ba` — `test: stabilize backend test environment`
- `3a3845b` — `test: define route intelligence planning modes`
- `38d5964` — `test: add deterministic intelligence replay framework`
- `820942b` — `feat: add shadow decision logging and metrics`
- `42b46a2` — `test: add baseline intelligence comparison runner`
- `c8ad1f0` — `fix: preserve verified candidate incident evidence`
- `48f6d47` — `test: harden intelligence provider failure paths`
- `09ba747` — `test: add golden intelligence replay validation`
- `b8a9b5a` — `feat: integrate privacy-safe route shadow validation`
- Documentation and CI were finalized in the closing documentation commit.
- `backend/.venv/Scripts/python.exe -m unittest -v
  tests.test_mta_feed_service_alerts tests.test_ai_advisor_mock`: 11/11 passed.
- `APP_KEY=test-key backend/.venv/Scripts/python.exe -m pytest -q`: 342 passed,
  one opt-in live Ticketmaster test skipped.
- Corrected replay/regression selection: 103 passed, one opt-in live
  Ticketmaster test skipped.
- Planning-mode/router/tool compatibility selection: 44 passed.
- Shadow/metrics/replay compatibility selection after Sol CLI test: 45 passed.
- Corrected comparison/replay/advisor selection: 28 passed.
- Candidate-evidence/incident/comparison compatibility selection: 99 passed.
- Source-ablation/failure/replay compatibility selection: 117 passed.
- Fixture/live-certification compatibility selection: 139 passed, one opt-in
  live Ticketmaster test skipped.
- Golden/replay/comparison/ablation selection: 38 passed; the all-scenario CLI
  reported 12 PASS results and exited 0.
- Shadow/reporting/golden/regression selection: 64 passed. The first attempt
  with global Python failed because pytest is intentionally project-local; the
  declared `.venv` command passed.
- Deterministic report CLI: 12/12 scenarios passed, 6/6 expected route changes,
  0/6 false reroutes, 0/6 missed known disruptions, and 100% evaluated
  association/deduplication/empty-scan checks. All numbers are fixture-only.
- Post-audit focused gate: 69 passed.
- Post-audit complete backend gate: 460 passed, one deliberately opt-in live
  Ticketmaster smoke test skipped; two non-failing local pytest-cache permission
  warnings.
- Post-audit replay CLI: all 12 scenarios passed, the partial scenario reported
  `511NY snapshot unavailable`, and MTA source contribution changed from 12/12
  no-effect to one recorded explanation-only effect plus eleven no-effect cases.

## Known constraints

- Live 511NY certification remains opt-in until the API key is approved.
- Fixture metrics demonstrate deterministic contract behavior, not real-world
  travel-time improvement or autonomous advisor accuracy.
- Ticketmaster validation covers production parsing and the advisor-input seam;
  the normal `/api/trip` endpoint does not fetch Ticketmaster events.
- The 511NY snapshot and poller remain process-local to each FastAPI process.
  The repository does not set a platform worker count; releases must record and
  monitor the deployed topology. See
  [`production-topology-contract.md`](production-topology-contract.md).
