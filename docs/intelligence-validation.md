# SmartRoute route-intelligence validation

This document describes the deterministic validation system for SmartRoute's
existing route-intelligence pipeline. It is a test and evidence system, not a
second planner and not a claim that authored fixtures prove real-world travel
time improvements.

## Test environment

The supported backend environment is Python 3.12. Install runtime and declared
test dependencies into a project virtual environment:

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

After activating the virtual environment, the canonical command is simply
`python -m pytest -q`. The global Python installation is not expected to have
pytest. CI uses the same dependency files and command.

The previously failing GTFS-RT service-alert fixtures now include the required
`FeedMessage.header`. A separate malformed/partial-feed test exercises graceful
application behavior intentionally; fixture construction no longer fails before
the parser is called.

## Advisor identity

The production route advisor is Anthropic
`claude-haiku-4-5-20251001`, configured in
`backend/app/services/ai_advisor.py`. It has no silent Sonnet fallback. Sonnet
references elsewhere belong to a separate conversational agent loop and must
not be used to label route-selection results. Validation reports obtain the
safe provider/model diagnostic from `advisor_identity()` and never record a
prompt, credential, or hidden reasoning.

## Planning modes

- **Baseline** retains route generation and core MTA service-alert context. It
  omits the enhanced inputs under evaluation: stalled subway and bus detection,
  Grok X/web incident evidence, cached 511NY matches, and Ticketmaster crowd
  windows. This is the closest meaningful existing planner, not an intentionally
  weakened comparator.
- **Intelligence replay** exercises the complete advisor-input boundary: core
  MTA alerts, stalled subway and bus signals, normalized Grok X/web results,
  candidate-bound cached 511NY incidents, supplied Ticketmaster crowd windows,
  and the existing advisor contract. The normal `/api/trip` endpoint does not
  currently fetch Ticketmaster events; the Ticketmaster scenario validates the
  production normalization and payload seam, not that endpoint integration.
- **Shadow** keeps the normal intelligence recommendation as the displayed
  result and evaluates a baseline counterfactual afterward. It cannot replace
  or mutate the displayed route.

Deterministic comparisons use strict recorded advisor transcripts. They exercise
production advisor-payload construction and the selection parser while isolating
model nondeterminism. Because the transcript is authored per scenario/ablation,
route-change and source-contribution rates measure conformance to those recorded
contracts; they do not establish autonomous model accuracy or causal route
improvement. Optional live-model and human-classified shadow validation are the
separate evidence paths for that question.

## Replay architecture

Fixtures live in `backend/tests/replays/<scenario-id>/`. Each scenario has:

- `scenario.json`: frozen time, optional 511NY snapshot fetch time,
  origin/destination, enabled sources, source status, fixture filenames, and
  expected outcomes.
- `route_candidates.json`: provider-shaped route alternatives.
- `mta_alerts.pb64`: GTFS-RT bytes encoded as base64.
- `subway_vehicle_positions.pb64`: GTFS-RT vehicle positions encoded as
  base64.
- `bus_vehicle_positions.json`: BusTime SIRI data.
- `grok_x_results.json` and `grok_web_results.json`: strict recorded incident
  outputs.
- `ny511_events.json`: upstream-shaped 511NY events.
- `ticketmaster_events.json`: Discovery API-shaped events.
- `advisor_outputs.json`: baseline, intelligence, and one-source ablation
  transcripts.
- the `expected` object in `scenario.json`: expected route and evidence
  assertions.

The loader validates every file, freezes the scenario clock, and blocks socket,
HTTPX, and Requests network access. Raw payloads pass through the production
GTFS-RT/SIRI parsers, 511NY normalization and freshness rules, Ticketmaster
normalization/crowd-window tool, stalled-vehicle detectors, geospatial matcher,
incident lifecycle filter, conservative merger, advisor-input builder, and
selection parser. A malformed or incomplete fixture fails with a bounded
diagnostic rather than becoming a false all-clear.

Run all scenarios and write CI-compatible artifacts:

```powershell
cd backend
.\.venv\Scripts\python.exe -m scripts.replay_route_intelligence `
  --json-out validation-results.json `
  --text-out validation-report.txt `
  --metrics-out validation-metrics.json `
  --ablations-out validation-ablations.json
```

Pass a scenario id, such as `stalled-subway`, before the output flags to run one
scenario. Exit code 0 means every recorded expectation matched, 1 means an
expectation failed, and 2 means a fixture was invalid.

## Golden scenarios

The deterministic suite includes twelve scenarios:

1. clear route;
2. stalled subway;
3. stalled bus;
4. road closure directly affecting a bus corridor;
5. severe roadway event near but unrelated to a subway;
6. station-access restriction without a subway service disruption;
7. Ticketmaster crowd window;
8. multi-source corroboration and deduplication;
9. weak single social report;
10. stale or resolved incident;
11. just-inside, exact-boundary, and just-outside radius behavior; and
12. partial source failure without a false all-clear.

The suite emphasizes non-intervention: high severity, stop proximity, alarming
social language, nearby events, empty successful scans, stale evidence, and
partial failures do not independently justify a reroute.

## Source ablation and failures

Every scenario has a deterministic variant for each canonical source toggle:
MTA alerts, stalled-subway detection, stalled-bus detection, Grok X, Grok web,
511NY, and Ticketmaster. Reports record whether disabling a source changed the
route, changed the structured explanation/evidence only, or had no effect.
They do not invent a parallel numeric score.

Failure tests cover disabled sources, timeouts, malformed Grok JSON, missing
keys, stale/unavailable snapshots, and partial scans. The scan status can be
`complete`, `partial`, `failed`, or `disabled`; only a fresh required 511NY
snapshot can participate in a complete scan.

## Metrics

Fixture metrics include correct route-change rate, false reroute rate, missed
disruption rate, incident-association accuracy, deduplication accuracy,
empty-scan correctness, source contribution, scenario pass rate, and local
replay latency. Zero-denominator values remain unevaluated rather than being
reported as perfect.

Every fixture report is labeled `deterministic_fixture` and carries this claim
boundary: recorded advisor transcripts validate deterministic payload and
selection-contract behavior; they do not prove autonomous advisor accuracy,
causal route improvement, or real-world travel-time gains. Live-shadow
observations are aggregated separately and require human classification before
route-quality rates become available.

## Production shadow mode

Shadow mode is disabled by default and fails closed unless both settings exist:

```text
ROUTE_SHADOW_VALIDATION_ENABLED=true
ROUTE_SHADOW_LOG_PATH=<local-path-ending-in-.jsonl>
ROUTE_SHADOW_TIMEOUT_SECONDS=2.0
```

The timeout is bounded by the shared executor. Disabled, timeout, evaluator
failure, record failure, and sink failure paths return the exact same displayed
result object. A record contains generated observation id, advisor identity,
candidate ids/lines/timing summaries, production and counterfactual selections,
fixed source counts, incident count, scan/snapshot status, and bounded latency.
It excludes prompts, model prose, coordinates, stop names, URLs, user text,
credentials, API keys, and hidden reasoning.

Human review is append-only and has no free-text field:

```powershell
python -m scripts.review_shadow_decisions records.jsonl reviews.jsonl `
  <observation-uuid> equivalent_route
```

Supported classes are `correct_improvement`, `equivalent_route`,
`unnecessary_reroute`, `missed_disruption`,
`incorrect_incident_association`, `model_decision_error`,
`data_quality_issue`, and `unable_to_determine`.

## Provider fixture and live certification

`NY511_FIXTURE_PATH` is accepted only in explicit development/test mode. It
loads upstream-shaped events through the same `SnapshotStore` normalization,
NYC filtering, metadata, staleness, and local candidate matching used by live
snapshots. Production fails closed rather than serving fixture incidents.

Live checks are opt-in, bounded, excluded from normal pytest/CI, and sanitize
diagnostics:

```powershell
python -m scripts.run_live_511ny_validation --live
python -m scripts.run_live_ticketmaster_validation --live
python -m scripts.run_live_advisor_validation --live
```

The 511NY command skips cleanly until its key is approved, makes one upstream
attempt, reports counts/timestamp without the credential-bearing URL, invokes
one snapshot cycle, and confirms route-time matching remains local. The
Ticketmaster command makes one NYC request/page. The advisor command accepts
only the production structured selection contract.

## Deployment assumption

The 511NY snapshot is process-local. The supported deployment is one FastAPI
process, one poller, and one in-memory snapshot. Start with the documented
single-process command, without `--workers`:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

One worker does not mean one request at a time: the FastAPI process remains
asynchronous and can handle concurrent I/O-bound requests. Multiple backend
processes would each own a different snapshot and are unsupported until shared
snapshot storage is deliberately introduced. The application cannot reliably
detect every hosting provider's external process count, so this is documented
rather than enforced with a misleading startup heuristic.

## CI and limitations

CI runs backend pytest and the complete deterministic replay suite, then keeps
the four validation artifacts for 14 days. Standard CI needs no provider key
and makes no live provider or live model call.

Known limits:

- Authored/recorded fixtures prove repeatable contracts, not real-world savings.
- Live 511NY certification awaits key approval.
- Ticketmaster crowd evidence has a validated production normalization and
  advisor-input seam; it is not yet fetched inside the normal `/api/trip`
  request, so the Ticketmaster replay is not endpoint certification.
- The current subway stalled-vehicle detector does not expose a travel-direction
  field, so opposite-direction isolation cannot yet be proven end to end.
- Enabled shadow mode performs a second bounded advisor evaluation and therefore
  adds model cost and up to the configured timeout to that request.
- The in-memory 511NY snapshot requires a single backend process.
