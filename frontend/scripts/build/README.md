# Transit Build Scripts

## Purpose

This folder owns the offline transit artifact build logic for SmartRoute. It
contains TypeScript helpers, visual-network stages, station-anchor support code,
and build-pipeline tests used to generate and validate subway map artifacts.

The code here is build-time infrastructure. It is not browser runtime code and
it is not manually edited generated output.

## What Lives Here

- Helper modules for subway geometry, lane continuity, bundle construction,
  route repair, and location-specific NYC subway fixes.
- Colocated `*.test.ts` files for focused helper behavior.
- `visual-network/`, the decomposed visual subway network pipeline.
- `station-anchors/`, the station label/badge/snap anchor builder internals.
- `tests/`, pipeline-level build tests that are not tied to one small helper.
- Shared build types and utilities such as `types.ts`, `spine.ts`, and
  `artifact-fingerprint.ts`.

## Important Files And Folders

- `../build-subway-visual-network.ts`: orchestrates the visual subway network
  build and writes `public/subway-network.visual.geojson`.
- `../build-subway-station-anchors.ts`: orchestrates station-anchor generation.
- `visual-network/`: focused stages for inputs, shared utilities, core lane and
  bundle work, local repairs, validation, and output writing.
- `station-anchors/`: station-anchor feature construction and snap logic.
- `tests/station-anchors.test.ts`: pipeline-level station-anchor test.
- Root-level helper modules such as `physical-bundle.ts`,
  `same-color-merge.ts`, `trim-terminal-overhang.ts`, and
  `opendata-subway-lines.ts`: reusable build helpers with colocated tests.

## Inputs

The build helpers read checked-in public artifacts, GTFS-derived canonical
network data, OpenData geometry, route color/configuration data, and station
point data. Most runtime inputs are under `frontend/public/`; some source and
configuration files live beside the build scripts.

## Outputs And Generated Artifacts

Runtime artifacts are written under `frontend/public/`:

- `subway-network.canonical.geojson`
- `subway-network.visual.geojson`
- `subway-network.station-anchors.geojson`

The artifact manifest is written to
`frontend/lib/artifact-manifest.json` because it is imported by runtime code.

Engineering debug artifacts are written under `frontend/artifacts/debug/`.
Those files are useful for reviewing lane, bundle, snap, and validation state,
but they are ignored by Git and should not be treated as shipped runtime data.

## Current Folder Map

- `visual-network/inputs/`: GTFS parsing, branch selection, topology edges, and
  OpenData visual input normalization.
- `visual-network/shared/`: route configuration, shared feature types, geometry
  utilities, and diagnostics.
- `visual-network/core/`: high-level lane, corridor, bundle, spine, and repair
  pipeline stages.
- `visual-network/repairs/`: ordered local subway geometry repair stages.
- `visual-network/validation/`: validation gates, debug reporting, and final
  build summaries.
- `visual-network/output/`: final visual artifact metadata and write/promotion
  logic.
- `station-anchors/`: station label, badge, shared-stop, and snap anchor logic.
- `tests/`: build-pipeline tests that cover entrypoint-level behavior.

## How To Run It

Run commands from `frontend/`.

```powershell
npm run typecheck:scripts
npm run build:visual-network
npm run build:station-anchors
npm run verify:transit-artifacts
```

Local binary equivalents:

```powershell
.\node_modules\.bin\tsc.cmd --project scripts\tsconfig.json --noEmit
.\node_modules\.bin\tsx.cmd scripts\build-subway-visual-network.ts
.\node_modules\.bin\tsx.cmd scripts\build-subway-station-anchors.ts
.\node_modules\.bin\tsx.cmd --test scripts\build\tests\station-anchors.test.ts
```

## Validation And Checks

- Run script typecheck after build-helper or pipeline changes.
- Run the relevant colocated helper test when touching a helper.
- Run `scripts/build/tests/station-anchors.test.ts` when station-anchor behavior
  or station-anchor test organization changes.
- Run the visual build when visual-network logic changes.
- Compare `public/subway-network.visual.geojson` semantically after stripping
  timestamp-like metadata. Feature count, order, schema, hashes, geometry,
  properties, and provenance should remain unchanged unless the task explicitly
  expects an artifact update.
- Run the runtime map checks that remain under `frontend/components/map/`:
  `subway-station-overlay.check.mjs`, `subway-palette.check.mjs`, and
  `subway-renderer.check.mjs`.

## Safe Change Guide

- Make one coherent build-stage change at a time, then run a full gate.
- Preserve mutate-in-place semantics in visual-network repair and lane stages.
- Keep `.mjs` provenance literals unchanged unless an explicit artifact
  migration changes the shipped metadata contract.
- Prefer adding or updating characterization tests before changing high-risk
  geometry behavior.
- If a build changes generated artifacts, classify the diff before finishing:
  timestamp-only, formatting-only, expected deterministic regeneration, or real
  geometry/output drift.
- Restore timestamp-only generated diffs before committing source-only refactors.

## Change Map

- GTFS parsing or route topology: `visual-network/inputs/`.
- Route colors, route order, and route-family constants:
  `visual-network/shared/route-config.ts`.
- Lane continuity, lane offsets, corridor metadata, bundles, and spines:
  `visual-network/core/`.
- Local geometry repairs and authored subway patches:
  `visual-network/repairs/`.
- Validation gates, anomaly reports, and debug summaries:
  `visual-network/validation/`.
- Visual artifact metadata, candidate/final writing, and promotion:
  `visual-network/output/`.
- Station label, badge, shared-stop, and snap anchor behavior:
  `station-anchors/`.
- Build-pipeline test organization: `tests/`.

## Do-Not-Touch / Gotchas

- Do not hand-edit generated artifacts under `frontend/public/`.
- Do not commit anything under `frontend/artifacts/`.
- Do not move runtime map checks out of `frontend/components/map/`; they validate
  renderer expectations as well as generated artifacts.
- Do not treat long local geometry helpers as dead code without checking
  importers and running the visual artifact build.

## Historical Note

The build helpers were migrated gradually from `.mjs` to TypeScript and the
visual-network orchestrator was decomposed into focused stages. That history is
useful context, but the folder should now be read as the current artifact build
system rather than as a migration map.

## Related Docs

- `../README.md`
- `visual-network/README.md`
- `station-anchors/README.md`
- `tests/README.md`
