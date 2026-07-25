# SmartRoute intelligence improvement proposal

This backlog is intentionally prioritized. It does not recommend adding complexity
without a demonstrated problem.

## 2026-07-25 completion update

The P0 request-hardening, retry classification, SSE recovery, canonical
selection-decision, frontend test, configuration validation, deterministic
backend/frontend suites, typecheck, lint, and production build are complete.
Credentialed Ticketmaster and Anthropic checks remain explicitly blocked by
the execution environment; no live success, latency, model-access, or exact
historical-400-cause claim is made.

Production shadow evaluation was already implemented before this completion
pass. P1 now adds normalized freshness envelopes, deterministic one-way Quick
escalation, a fresh-artifact-only scheduled-arrival fallback, and configurable
privacy-safe shadow sampling. Paired Auto/Quick staging traces remain blocked
until credentialed provider execution is authorized; no latency claim is made.

## P0 — required before production

### Run provider certification and capture sanitized evidence

- Problem: the current environment blocked the live Ticketmaster request.
- Evidence: deterministic provider and route-scoring tests pass, but there is no
  current live status/latency sample.
- Change: run the existing one-request smoke test in an approved environment, then
  exercise one crowd-sensitive route and retain only sanitized event counts,
  latency, normalized venue/time fields, and score delta.
- User impact: confirms crowd-aware planning has live upstream evidence.
- Latency/cost: one manual request; no production impact.
- Effort/risk: low/low.

### Restore the repository npm launcher

- Problem: `npm run` commands cannot start because the global launcher references a
  missing `npm-cli.js`.
- Evidence: direct local TypeScript/test binaries work.
- Change: repair the managed Node/npm installation; do not alter application
  dependencies to work around a workstation issue.
- User impact: none directly; restores reproducible release checks.
- Latency/cost: none.
- Effort/risk: low/low.

### Add a staging trace for complete Auto/Quick requests

- Problem: configured budgets prove expected cost direction, not end-to-end hosted
  latency.
- Evidence: no Anthropic credential was available to the test process.
- Change: capture at least 30 sanitized paired staging runs over the same scenario
  set; report p50/p95 total, model, and tool latency plus candidate and token counts.
- User impact: verifies Quick is materially faster without skipping evidence.
- Latency/cost: bounded staging model spend.
- Effort/risk: low/low.

## P1 — high-value next

### Confidence-triggered Quick escalation

Status: implemented with deterministic signals only. The original heading is
retained for backlog history; model self-confidence is not used.

- Problem: a smaller candidate/model budget can encounter genuine ambiguity.
- Evidence: Quick intentionally has two candidates and no optional enrichment.
- Change: escalate to Auto only for deterministic signals such as unresolved place,
  conflicting mandatory evidence, or no valid constrained candidate. Do not use
  vague model self-confidence.
- User impact: preserves Quick speed on normal requests while recovering hard ones.
- Latency/cost: rare higher-latency second pass.
- Effort/risk: medium/medium; guard against duplicate provider calls.

### Evidence freshness envelope

Status: implemented for arrivals, alerts, events, subway/bus vehicles, and
incident-advisor evidence. Expired payloads are suppressed at the model and
scoring boundaries.

- Problem: providers expose different freshness semantics.
- Evidence: arrivals distinguish stale data; event and alert freshness are separate.
- Change: include normalized `observed_at`, `valid_until`, and `status` on every
  advisor evidence item and suppress expired evidence before model input.
- User impact: fewer stale warnings and false interventions.
- Latency/cost: negligible.
- Effort/risk: medium/low.

### Production shadow evaluation

Status: implemented, disabled by default, fail-closed, and sampled with
`ROUTE_SHADOW_SAMPLE_RATE`.

- Problem: deterministic fixtures cannot measure real-world recommendation quality.
- Evidence: route scoring is now structured enough to compare safely.
- Change: log a privacy-minimized baseline/intelligence comparison without changing
  the displayed route, then classify disagreements.
- User impact: evidence-based tuning before broader rollout.
- Latency/cost: bounded shadow overhead; sample requests rather than all traffic.
- Effort/risk: medium/medium.

### Arrival scheduled fallback

Status: implemented behind an offline-built full-GTFS schedule artifact. The
checked-in partial static database lacks the required calendar/timing tables
and is correctly treated as unavailable rather than inferred.

- Problem: GTFS-RT absence currently yields no prediction or unavailable, even when
  static schedule data may help.
- Evidence: the result schema already distinguishes `scheduled`.
- Change: map static stop times through the same resolver, label them clearly, and
  never blend scheduled and live values without provenance.
- User impact: useful fallback during feed gaps.
- Latency/cost: low local lookup.
- Effort/risk: medium/medium because service-calendar correctness matters.

## P2 — useful later

### Context compression by typed state

- Problem: replaying plain history consumes tokens and can reintroduce stale details.
- Evidence: slots/active/pending trip now hold canonical state.
- Change: summarize older conversational turns from typed state while retaining the
  last few verbatim rider/assistant messages.
- User impact: more consistent long conversations.
- Latency/cost: lower input tokens.
- Effort/risk: medium/medium.

### Provider health tracking

- Problem: one request can identify only a local failure, not provider health.
- Evidence: error mapping now avoids false outage claims.
- Change: aggregate redacted success/timeout/rate-limit counters with a short decay
  window; use it only for internal routing/telemetry.
- User impact: faster, more accurate fallback behavior.
- Latency/cost: negligible.
- Effort/risk: medium/low.

### Tool schema minimization

- Problem: every model round receives the full tool registry.
- Evidence: eight strict tools are currently sent on ordinary turns.
- Change: select a deterministic tool subset from intent while keeping all mandatory
  evidence available. Include a safe fallback when intent is ambiguous.
- User impact: lower model latency and input cost.
- Latency/cost: likely improvement; measure before release.
- Effort/risk: medium/medium because missing a required tool is worse than extra
  schema tokens.

### Broader event coverage only after gap measurement

- Problem: Ticketmaster does not cover every parade, protest, street fair, or
  unticketed venue surge.
- Evidence: no quantified miss rate exists yet.
- Change: first label missed-event shadow cases; add another source only if it
  materially fills a measured gap.
- User impact: better crowd context if justified.
- Latency/cost: provider-dependent.
- Effort/risk: medium/high.

## P3 — speculative

### Historical station activity model

- Problem: event schedules are not normal station busyness.
- Evidence: current implementation correctly avoids claiming live occupancy.
- Change: evaluate a time-of-week station baseline with documented data provenance.
- User impact: potentially better non-event crowd comparisons.
- Latency/cost: low at runtime, high data maintenance.
- Effort/risk: high/high; defer until shadow data proves value.

### Rider crowd reports

- Problem: official/event feeds can miss platform-level conditions.
- Evidence: no abuse, moderation, or freshness system currently exists.
- Change: prototype expiring, rate-limited reports only after trust/safety and
  moderation requirements are defined.
- User impact: potentially timely context.
- Latency/cost: operational moderation cost.
- Effort/risk: high/high.

### User-visible confidence labels

- Problem: exposing internal confidence can create false precision.
- Evidence: current structured evidence is suitable for internal diagnostics, not a
  calibrated rider probability.
- Change: research qualitative provenance language before exposing numeric values.
- User impact: possible transparency gain.
- Latency/cost: none.
- Effort/risk: medium/high; speculative until calibrated.
