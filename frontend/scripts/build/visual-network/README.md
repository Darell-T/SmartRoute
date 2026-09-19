# Visual network build

## Purpose

The visual-network build produces the subway line geometry consumed by the
SmartRoute map renderer:

- `frontend/public/subway-network.visual.geojson`
- engineering debug artifacts under `frontend/artifacts/debug/`

It turns GTFS topology, OpenData line geometry, route metadata, and authored NYC
subway repair rules into a deterministic render-ready artifact.

## What lives here

This folder contains focused stages extracted from
`frontend/scripts/build-subway-visual-network.ts`. Each stage owns one pipeline
area and should preserve the orchestrator's behavior exactly.

## Important files and folders

- `../../build-subway-visual-network.ts`: the top-level orchestrator. It wires
  stages together, owns output paths, and runs the build as a side-effect script.
- `inputs/`: GTFS ingest, branch selection, topology edge construction, and
  OpenData corridor normalization.
- `shared/`: shared feature types, route configuration, geometry utilities, and
  diagnostics used by multiple stages.
- `core/`: lane continuity, corridor metadata, bundle/spine construction,
  offset finalization, same-color merge staging, and the repair-pipeline facade.
- `repairs/`: ordered local geometry repair stages and authored location
  patches. These often mutate live feature arrays in place.
- `validation/`: validation gates, anomaly/debug reporting, and final summary
  logging.
- `output/`: artifact metadata construction and candidate/final write promotion.

## Inputs

- GTFS-derived canonical subway data.
- OpenData subway route geometry.
- Checked-in station points from `frontend/public/subway-network.stations.geojson`.
- Route color/order configuration.
- Intermediate live arrays and maps owned by the orchestrator.

## Outputs and generated artifacts

- Shipped artifact: `frontend/public/subway-network.visual.geojson`.
- Candidate artifact: `frontend/artifacts/debug/subway-network.visual.candidate.geojson`.
- Debug artifacts for topology, corridors, route components, lane continuity,
  bundles, physical bundles, cross-color spread, junction snaps, anomalies, and
  related validation state.

Only the shipped public artifact is runtime data. Debug artifacts are development
tools and are not committed.

## Pipeline order

1. Read GTFS and OpenData inputs.
2. Build route topology, branch selections, and topology edge features.
3. Normalize visual corridors and attach route/lane metadata.
4. Build bundle, spine, and physical-bundle state.
5. Apply lane continuity and finalize lane offsets.
6. Clip redundant DeKalb source traces, then enforce shared-corridor separation.
7. Run connectivity and anomaly reporting and write debug artifacts.
8. Apply same-color collapse, smoothing, junction repairs, and local route repairs.
9. Build final metadata and promote the visual artifact only after gates pass.

DeKalb clipping must precede separation. Otherwise the separation pass measures
source traces that duplicate the retained physical lanes. It can reject the
build or move a retained lane toward another duplicate.

Separation repairs use short, overlapping curve fits. Adjacent fits blend across
their overlap using the same source-window positions for both lanes. Slicing each
fitted lane by output arc length misaligns curved joins and can create a pinch
even when both individual fits pass. Junction exclusions and separation floors
still apply. Inspect `frontend/artifacts/debug/subway-network.visual-debug-shared-corridor-separation.json`
for the measured hotspot results.

Lane spacing uses the perpendicular distance between samples. Their displacement
along the track must not widen the bundle or stretch station connectors.
If a tangent fit strays farther than one lane gap from the source centerline,
the repair follows the source bend instead. This prevents a later GTFS repair
from pulling the separated lanes back together.

Off-revenue repairs choose a GTFS shape by its distance from samples across the
visual segment. A segment can end at a junction halfway along a scheduled trip.
Comparing only endpoints can select a short-turn shape that omits that junction.

## How to run it

Run from `frontend/`.

```powershell
npm run build:visual-network
```

Local binary equivalent:

```powershell
.\node_modules\.bin\tsx.cmd scripts\build-subway-visual-network.ts
```

## Validation and checks

For behavior-preserving refactors:

1. Run `.\node_modules\.bin\tsc.cmd --project scripts\tsconfig.json --noEmit`.
2. Run `.\node_modules\.bin\tsx.cmd scripts\build-subway-visual-network.ts`.
3. Compare `frontend/public/subway-network.visual.geojson` semantically against
   the baseline after stripping timestamp-like metadata.
4. Confirm feature count, order, schema, geometry, properties, hashes, and
   provenance are unchanged unless the task intentionally updates artifacts.
5. Compare `frontend/artifacts/debug/` against a pre-stage canary after stripping
   timestamp-like metadata.
6. Restore timestamp-only generated diffs before committing source-only changes.
7. Run the runtime map checks documented in `../tests/README.md`.

## Artifact parity expectations

Visual-network changes are accepted only when the generated artifact is identical
except timestamp-like metadata, unless a task explicitly calls for a generated
artifact update. A raw one-line GeoJSON diff is not enough; use semantic JSON
comparison and strip timestamp-ish fields before deciding.

## Provenance strings

Some generated metadata strings intentionally still reference
`build-subway-visual-network.mjs`. Those strings are baked into artifact parity
expectations and should not be rewritten during organization or refactor work.

## Safe change guide

- Keep stage boundaries meaningful; avoid one-call wrapper modules.
- Preserve pass order and mutate-in-place behavior for repair and core stages.
- Pass live refs into stages when the original code mutated live arrays.
- Move guards and diagnostics with the block they protect.
- Avoid tightening broad feature property bags during structural refactors; keep
  behavior-neutral typing changes separate.
- Run one full visual build gate per coherent stage extraction.

## Change map

- GTFS parsing and branch topology: `inputs/`.
- OpenData visual line normalization: `inputs/opendata-visual-input-stage.ts`.
- Route colors/order: `shared/route-config.ts`.
- General geometry math: `shared/geometry-utils.ts`.
- Diagnostics shared by gates: `shared/diagnostics.ts`.
- Lane/corridor/bundle/spine behavior: `core/`.
- Local subway geometry fixes: `repairs/`.
- Validation gates and debug reports: `validation/`.
- Metadata, candidate output, and final artifact promotion: `output/`.

## Constraints

- Do not re-enable disabled helper sentinels without a dedicated behavior
  change and full artifact gate.
- Do not hand-edit `frontend/public/subway-network.visual.geojson`.
- Do not commit `frontend/artifacts/debug/`.
- Do not change `.mjs` provenance strings during refactors.
- Do not reorder local repair stages casually; later repairs often depend on
  previous in-place mutations.

## Related docs

- `../README.md`
- `../tests/README.md`
- `../../README.md`
