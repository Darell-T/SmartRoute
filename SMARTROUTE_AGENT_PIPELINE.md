# How a SmartRoute chat turn stays grounded

A rider can ask SmartRoute to find a place, check transit, plan a route, or do
several of those jobs in one message. The Agent interprets the request. Server
state decides what work counts as complete, and backend services supply every
fact shown to the rider.

This split prevents a fluent answer from replacing a verified result.

## A model response is not a completed turn

A model can say "Here is the best route" without preparing a route. It can
also answer the first half of a compound request and ignore the rest. SmartRoute
does not use final prose as proof that the requested work happened.

Each turn has two server-owned records. `TurnContract` records the goals.
`TurnEvidence` records the work and results. `turn/completion.py` compares the
two records before the server accepts a terminal answer.

## The request crosses four boundaries

```text
Browser
  -> Next.js proxy
  -> FastAPI agent router
  -> Agent turn loop
  -> Domain services and providers
```

The browser sends chat messages and current location to the Next.js route. The
Next.js server adds the backend credential and an opaque client principal.
`app/routers/agent_chat.py` validates the request, acquires the session lease,
and starts the SSE response.

`app/services/agent/loop.py` then loads the session and calls
`turn/stream.py::stream_turn`.

## The server builds the model context

The model receives a limited view of server state. The context can include the
accepted trip, recent messages, stored place references, rider constraints,
pending candidates, transit evidence, and the current time.

The server does not send raw provider responses or every stored event. Each
domain projects the fields that the model needs for the current decision.

This limit serves two purposes. It keeps provider data out of model-authored
facts, and it keeps old state from competing with the current request.

## `declare_goals` gives compound work a shape

The model calls `declare_goals` for substantive work. Each goal has a key, a
kind, and optional dependencies.

For "find ramen and route me there," the place goal has no dependency. The
route goal depends on the place goal. The turn cannot finish after place
presentation because the route goal remains open.

The server checks goal kinds, duplicate keys, missing dependencies, cycles,
and invalid terminal states. The model proposes the structure. The server
accepts or rejects it.

## Server state limits the offered tools

The agent registry contains the eight model-visible tools:

- `declare_goals`
- `discover_places`
- `check_transit`
- `prepare_route_options`
- `present_places`
- `present_transit`
- `present_route`
- `complete_turn`

`public_surface.py` selects the tools that fit the current state. It does not
hide tools through phrase matching. A route presenter appears only when the
session has a candidate set that the presenter can validate.

The model can still request an invalid tool. `turn/tool_round.py` rejects that
call before the executor reaches a provider or changes session state.

## One model round can call several tools

`model/stream.py` reads the model response and yields text or tool calls.
`turn/tool_round.py` validates each call, checks its attempt limit, runs its
executor, and records the result in `TurnEvidence`.

The loop then gives the tool result to the model. Another round can select a
presenter, ask for clarification, or continue the remaining goal. Budgets in
`model/budget.py` limit rounds, tool calls, tokens, and spend.

The server records only bounded usage and timing fields. It does not expose the
system prompt, hidden reasoning, or raw provider data to the rider.

## Place work uses stored identities

`discover_places` calls the place adapter and stores a discovery set.
`present_places` accepts an ID from that set and emits verified place results.

`discover_places.queue_context` controls optional queue evidence for the
current destination decision. Google Places remains the place and branch
authority. A manual Google Place ID registry identifies the physical venues
that Damn Lines supports. `ignore` performs no queue work. `heads_up` checks
only selected places during presentation. `decision` lets the Agent consider
normalized queue evidence before it selects a destination. `historical`
answers an explicit past-pattern question. This remains part of
`discover_places`. It does not add a ninth model-visible tool.

Current queue observations retain the provider capture time and never change
route duration. Historical patterns refresh outside the request path and stay
distinct from live evidence. `present_places` owns the passenger wording and a
structured Damn Lines source event. The frontend renders that source after the
conversation text. Maps, route cards, route steps, and itinerary facts do not
receive queue data.

The session keeps place identities for later turns. "The second one" refers to
the latest compatible list. A duplicate name or missing list causes
clarification instead of a guess.

When a route uses a discovered place, the route input contains the stored
place identity. The model does not rewrite its coordinates.

## Transit work produces scoped evidence

`check_transit` handles service status, arrivals, accessibility, area
conditions, facts, events, and crowd windows. Agent adapters live under
`tools/transit/`. MTA parsing, incident storage, and event providers remain
under their domain packages.

The result records the checked lines, direction, time, sources, and missing
coverage. `present_transit` validates the evidence ID and writes passenger text
from those stored facts.

Stalled-train signals come from `app/services/mta/subway.py`. The agent reaches
them through `app.services.mta.realtime`. The deleted `mta_feed.py` module is
not part of this path.

## Route work crosses into the trips domain

`prepare_route_options` resolves the origin, destination, time, constraints,
and waypoints. Its agent adapter then calls
`app/services/trips/preparation/`.

The trips domain calls Google Routes and gathers candidate-specific evidence.
It applies hard constraints, combines multi-stop legs, and builds canonical
itineraries. The agent receives candidate IDs and a limited comparison record.
It does not receive permission to change route facts.

The model selects one candidate ID. `present_route` confirms that the ID belongs
to the active candidate set and still satisfies the required constraints. The
presenter then emits one route card from the stored canonical itinerary.

If the model does not return a valid selection, the backend can use the
deterministic recovery score. That score is a fallback, not the normal model
decision path.

## Presenters complete grounded goals

Place, transit, and route goals require their matching presenter. A plain text
sentence cannot satisfy those goals.

`complete_turn` handles general conversation, clarification, refusal,
unsupported requests, cancellation, and recovery when no grounded presenter
is required.

`turn/completion.py` accepts the turn when every goal is satisfied, waiting for
the rider, unavailable after a real attempt, unsupported, cancelled, or a
recorded partial success with recovery.

## SSE events keep transport separate from facts

The agent yields typed events such as activity text, message text, place
results, transit results, route cards, errors, and the final done record.
`app/routers/agent_chat.py` serializes those events as SSE.

The frontend validates each event before it changes chat state. Cards render
structured backend data. Message text never becomes a second route record.

## Session state supports follow-up requests

The session stores recent transcript context, the accepted trip, discovery
sets, candidate sets, presented entities, rider constraints, and pending
continuations.

A follow-up can reuse stable identity while refreshing live evidence. "Avoid
the Q instead" keeps the trip endpoints and changes the route constraint.
"What is wrong with the Q?" keeps the accepted trip context and requests new
transit evidence.

Starting a new trip clears incompatible route state. Cancelling a turn drains
request-owned tasks before another turn can write presentation state.

## Auto and Quick use the same contract

Auto and Quick change model policy and budgets. They do not change the tool
registry, route ownership, completion checks, or presenter rules.

Quick can use a lower-cost request policy. If the turn needs work outside that
policy, the server can move the turn to Auto. The accepted server state remains
the same across that change.

## Failure keeps verified results

A compound turn can succeed in part. If place discovery succeeds and route
preparation fails, the place result still renders. The route goal records the
failure and the model gives a short recovery message.

Provider timeouts, invalid tool calls, stale candidate IDs, and missing source
coverage all have different records. The server does not collapse them into a
generic success or claim that missing evidence proves safety.

## Related documents

- [Backend architecture](backend/ARCHITECTURE.md)
- [Release validation](docs/release-validation.md)
