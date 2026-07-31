# Build Pipeline Tests

## Purpose

This folder contains tests for build-pipeline behavior that spans multiple
helpers or an artifact entrypoint. Small helper tests stay colocated with their
source modules in `frontend/scripts/build/`.

## What Lives Here

- `station-anchors.test.ts`: tests the station-anchor builder as a pipeline
  rather than as one small helper.

## Important Files And Folders

- `../station-anchors/`: source modules covered by
  `station-anchors.test.ts`.
- `../../build-subway-station-anchors.ts`: station-anchor entrypoint.
- `../../build-subway-visual-network.ts`: upstream visual artifact entrypoint
  whose output is station-anchor input.

## Inputs

Tests use focused in-memory fixtures and generated-artifact shapes that mirror
the station-anchor pipeline contract. They are meant to catch changes in output
shape, snap behavior, label/badge behavior, and debug-field handling.

## Outputs And Generated Artifacts

The tests should not leave committed generated artifacts behind. If a test or
verification command writes under `frontend/public/` or `frontend/artifacts/`,
classify and restore the generated diff before finishing source-only work.

## How To Run It

Run from `frontend/`.

```powershell
.\node_modules\.bin\tsx.cmd --test scripts\build\tests\station-anchors.test.ts
```

Equivalent package script:

```powershell
npm run test:station-anchors
```

Runtime map checks intentionally remain beside runtime map code:

```powershell
node components\map\subway-station-overlay.check.mjs
node components\map\subway-palette.check.mjs
node components\map\subway-renderer.check.mjs
```

## Validation And Checks

- Run `station-anchors.test.ts` after changing station-anchor source or test
  organization.
- Run the map checks after changing generated artifact shape, route colors,
  station overlay behavior, or renderer assumptions.
- Run `npm run verify:transit-artifacts` when the local `npm` shim works.

## Safe Change Guide

- Add tests here when the behavior crosses an entrypoint boundary or needs
  multiple build modules.
- Keep helper-only unit and characterization tests beside the helper they cover.
- Update `frontend/package.json` when moving a pipeline test path.
- Keep runtime checks in `frontend/components/map/` unless the runtime code is
  reorganized too.

## What Failures Usually Mean

- Station-anchor test failures usually mean snap, label, badge, shared-stop, or
  debug stripping behavior changed.
- Station overlay check failures usually mean the runtime overlay no longer
  understands the generated station-anchor artifact.
- Palette check failures usually mean route color/order expectations drifted.
- Renderer check failures usually mean generated visual-network shape no longer
  matches renderer assumptions.

## Do-Not-Touch / Gotchas

- Keep slow visual inspection outside the committed build-test suite.
- Do not commit generated artifacts from test runs.

## Related Docs

- `../README.md`
- `../station-anchors/README.md`
- `../visual-network/README.md`
