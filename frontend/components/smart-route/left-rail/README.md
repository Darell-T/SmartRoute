# SmartRoute Left Rail

The left rail renders passenger-facing display models. Backend payloads should
be normalized before they reach React markup.

## Display Adapters

- `live-data.ts` formats nearby transit, route planning, arrivals, and public
  loading status into rail-ready rows.
- `alert-feed.ts` formats MTA service alerts and recent live-feed events into
  `AlertFeedItem` rows. It also owns the shared subway line-family table used
  for alert grouping and service-name labels.

Keep grouping, title cleanup, arrival formatting, and alert text compaction in
these adapters. Components should render the display shape instead of parsing
raw backend fields inline.

Do not render backend fields directly in `route-view.tsx` or
`alerts-view.tsx` when the value needs passenger-facing cleanup. Add or update a
display adapter first, then render the normalized field.

## Guard Tests

The `.test.mjs` files in this folder are intentional source guards, not only
runtime tests. `hydration.test.mjs`, `live-data.test.mjs`, and
`alert-feed.test.mjs` check product and markup invariants such as no fake rows,
stable grouped feeds, and no legacy public copy. Update those assertions only
when the visible contract changes.

Guard tests may assert source strings, CSS class names, and deleted legacy
symbols. That is deliberate: this subsystem has had several presentation
regressions from stale shells, fake loading rows, and old Jarvis/ATLAS copy.
When a guard fails, verify whether the visible product contract changed before
loosening the assertion.

## Naming

Public UI should say SmartRoute. Do not reintroduce Jarvis, assistant chatter,
or internal AI labels into left-rail copy. Some legacy backend names remain in
configuration and wire contracts; keep those documented at their source rather
than renaming them in UI code.
