# Station Anchors

## Purpose

Station anchors connect checked-in subway station points to the rendered visual
network. The generated anchor artifact drives stop dots, labels, route badges,
shared-stop bars, and station snapping behavior in the map renderer.

## What Lives Here

This folder contains the station-anchor builder internals imported by
`frontend/scripts/build-subway-station-anchors.ts`.

## Important Files And Folders

- `index.ts`: station-anchor build API used by the entrypoint.
- `types.ts`: station-anchor feature and debug shape definitions.
- `debug-features.ts`: debug-only raw station, snap, rejected, and ambiguous
  feature builders.
- `index.ts` also owns the current snap, cluster, shared-stop, label, and route
  badge layout logic.
- `../tests/station-anchors.test.ts`: pipeline-level test for this builder.

## Inputs

- `frontend/public/subway-network.visual.geojson`
- `frontend/public/subway-network.stations.geojson`
- Route/color metadata used by anchor, label, and badge logic.

## Outputs And Generated Artifacts

- Runtime artifact:
  `frontend/public/subway-network.station-anchors.geojson`
- Debug artifacts under `frontend/artifacts/debug/`:
  raw station points, snap candidates, rejected snaps, ambiguous snaps, and
  debug-rich runtime anchors.

Debug fields must be stripped from the committed runtime artifact.

## How To Run It

Run from `frontend/`.

```powershell
npm run build:station-anchors
npm run test:station-anchors
```

Local binary equivalents:

```powershell
.\node_modules\.bin\tsx.cmd scripts\build-subway-station-anchors.ts
.\node_modules\.bin\tsx.cmd --test scripts\build\tests\station-anchors.test.ts
```

## Validation And Checks

The station-anchor test covers snapped stop dots, shared-stop bars, labels,
route badges, color rims, rejected/ambiguous snap handling, relaxed snap rescue
behavior, and debug-field stripping.

Run the runtime overlay check when changing anchor output used by the map:

```powershell
node components\map\subway-station-overlay.check.mjs
```

## Safe Change Guide

- Start here for station label, route badge, shared-stop, or station snap
  behavior.
- Keep the pipeline test in `../tests/` when changing entrypoint-level behavior.
- If generated station-anchor artifacts change, classify the diff before
  committing.
- Keep debug-only fields out of the runtime public artifact.

## Do-Not-Touch / Gotchas

- Do not hand-edit `frontend/public/subway-network.station-anchors.geojson`.
- Do not move runtime overlay checks out of `frontend/components/map/`.
- Do not assume a snap failure is only a station-anchor issue; it may indicate
  visual-network geometry drift.

## Related Docs

- `../README.md`
- `../tests/README.md`
- `../visual-network/README.md`
