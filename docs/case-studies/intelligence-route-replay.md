# Case study: avoiding a recorded stalled-subway signal

## Situation

At the frozen replay time of July 22, 2026 at 5:30 PM EDT, the authored scenario
offers two valid uptown routes from Midtown:

- Q from 34 St-Herald Sq to 57 St-7 Av: 20 scheduled minutes;
- R from 28 St to Lexington Av/59 St: 26 scheduled minutes.

The baseline is not deliberately weakened. It receives the same candidates and
core MTA state, but not the enhanced stalled-vehicle and incident sources being
evaluated.

## Observed evidence

The provider-shaped GTFS-RT vehicle fixture contains a southbound Q vehicle that
has not progressed for ten minutes at the frozen time. The payload passes through
SmartRoute's production protobuf parser and stalled-subway detector. There is no
official MTA alert, 511NY incident, Ticketmaster crowd window, or bus anomaly in
this scenario, so the result does not depend on unrelated evidence.

## Decisions

**Baseline:** Q, 20 scheduled minutes.

**Intelligence:** R, 26 scheduled minutes.

The rider-facing reason is structured and bounded: the unaffected R avoids the
recorded stalled Q signal. This is application-level evidence, not hidden model
reasoning. Both choices come from strict recorded advisor outputs that pass the
same production selection parser used by trip planning.

## Validation result

- Expected route change: yes.
- Actual route change: yes.
- Result: PASS.
- Incident association: no unrelated incident was attached.
- Local replay construction/parsing latency: recorded as 1 ms per phase after
  integer bounding; this is harness overhead, not live provider/model latency.
- In the recorded no-stalled-subway counterfactual: selection returns to the Q.
- Without Grok X, Grok web, 511NY, Ticketmaster, stalled-bus detection, or MTA
  alert evidence: the stalled-subway signal remains the route-changing source.

Across the complete authored suite, 12/12 scenarios matched expectations, 6/6
expected changes occurred, and 0/6 no-change scenarios rerouted. These are
deterministic fixture metrics, not production-performance measurements.

## Why this is useful

The replay demonstrates a narrow, falsifiable backend contract: SmartRoute can
consume raw vehicle-position evidence, identify a meaningful no-progress signal,
attach it to the affected route, and present the exact structured payload paired
with a valid recorded advisor choice. The recorded ablation establishes the
expected counterfactual contract when that evidence is removed. It does not by
itself prove that a live advisor causally chose the R because of the signal.

## Limitations

The vehicle positions and advisor outputs are recorded/authored. This case study
does not prove the train was stalled in the real world, that the R would have
arrived sooner, or that the advisor will make the same decision on every live
model call. Live shadow classification and later provider certification are
required for autonomous-advisor or real-world performance claims. The current stalled-subway detector
also lacks an explicit direction field, which remains a documented validation
gap.
