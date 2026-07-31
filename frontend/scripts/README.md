# Frontend Scripts

## Purpose

This directory contains build-time scripts, artifact generators, and validation
checks for SmartRoute. These scripts prepare checked-in transit data for the
Next.js runtime; they are not browser runtime modules.

## What Lives Here

- Transit artifact entrypoints such as `build-subway-visual-network.ts`,
  `build-subway-station-anchors.ts`, `regenerate-canonical-from-gtfs.ts`, and
  `build-artifact-manifest.ts`.
- Build helper modules under `build/`.
- Script-only TypeScript configuration in `tsconfig.json`.

## Important Files And Folders

- `build-subway-visual-network.ts`: builds the shipped subway line rendering
  artifact and engineering debug artifacts.
- `build-subway-station-anchors.ts`: builds station label, badge, and snap
  anchor artifacts.
- `regenerate-canonical-from-gtfs.ts`: regenerates the canonical subway network
  from GTFS source data.
- `build-artifact-manifest.ts`: writes the manifest consumed by runtime code.
- `build/`: transit build helpers, focused visual-network stages, and build
  pipeline tests.

## Inputs

Inputs are mostly checked-in transit artifacts, GTFS-derived canonical data,
OpenData subway geometry, and static station point data. The build scripts read
from `frontend/public/` and script-local source/configuration files depending
on the entrypoint.

## Outputs And Generated Artifacts

Generated runtime artifacts are written under `frontend/public/`, including
subway visual, station, and station-anchor files. The cache-busting artifact
manifest is written to `frontend/lib/artifact-manifest.json`. Engineering-only
debug artifacts are written under `frontend/artifacts/debug/` and are not served
by the app.

Generated artifacts should not be hand-edited. Change the source script or
helper, run the relevant build, and review the generated diff.

## How To Run It

Run commands from `frontend/`.

```powershell
npm run typecheck:scripts
npm run build:network
npm run build:visual-network
npm run build:station-anchors
npm run build:artifact-manifest
npm run verify:transit-artifacts
```

If the local Windows `npm` shim is unavailable, use the local binaries:

```powershell
.\node_modules\.bin\tsc.cmd --project scripts\tsconfig.json --noEmit
.\node_modules\.bin\tsx.cmd scripts\build-subway-visual-network.ts
.\node_modules\.bin\tsx.cmd scripts\build-subway-station-anchors.ts
```

## Validation And Checks

- Script typecheck: `npm run typecheck:scripts`.
- Station-anchor test: `npm run test:station-anchors`.
- Runtime transit checks: `npm run verify:transit-artifacts`.
- Visual-network artifact gate: rebuild
  `public/subway-network.visual.geojson`, compare it semantically after
  stripping timestamp-like metadata, then restore timestamp-only diffs.

## Safe Change Guide

- Identify the entrypoint and generated outputs before editing.
- Prefer focused helper/stage changes over broad rewrites.
- Keep generated artifact diffs out of commits unless the task explicitly asks
  to update artifacts.
- Update docs when moving tests, folders, or validation commands.
- Never claim a build or test passed unless that command actually ran.

## Do-Not-Touch / Gotchas

- Do not hand-edit generated files under `frontend/public/`.
- Do not commit debug artifacts under `frontend/artifacts/`.
- Do not rewrite `.mjs` provenance strings embedded in generated subway
  artifacts; some are intentionally preserved for artifact parity.
- Runtime map checks live under `frontend/components/map/`, even when they
  validate generated script outputs.

## Change Map

- GTFS parsing and topology: start in `build/visual-network/inputs/`.
- Route colors and route ordering: start in
  `build/visual-network/shared/route-config.ts`.
- Lane, bundle, corridor, and spine behavior: start in
  `build/visual-network/core/`.
- Local subway geometry fixes: start in `build/visual-network/repairs/`.
- Validation gates and debug reporting: start in
  `build/visual-network/validation/`.
- Artifact writing and metadata: start in `build/visual-network/output/`.
- Station label, badge, and snap anchor behavior: start in
  `build/station-anchors/`.

## Related Docs

- `build/README.md`
- `build/visual-network/README.md`
- `build/station-anchors/README.md`
- `build/tests/README.md`
