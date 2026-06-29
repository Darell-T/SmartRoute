# Transit Build Helpers

This folder contains offline helper modules for the SmartRoute transit artifact
pipeline. The helpers are used by `frontend/scripts/build-subway-visual-network.ts`
to turn canonical transit data and NYC OpenData line geometry into renderable
subway network artifacts.

## What This Folder Is

- Offline transit artifact build helpers.
- Geometry cleanup and validation passes for subway network rendering.
- Location-specific NYC subway repair patches for places such as river tunnels,
  junctions, wyes, shared corridors, and schematic corrections.
- Bundle, lane, offset, corridor, and station-anchor support utilities.
- Deterministic build-time code whose output is committed under
  `frontend/public/`.

## What This Folder Is Not

- It is not browser runtime UI code.
- It is not random leftover JavaScript.
- It is not manually edited generated output.
- It is not a place to tune rendered artifacts by hand. Change source helpers,
  run the artifact build, and review generated diffs instead.

## Architecture Notes

- `frontend/scripts/build-subway-visual-network.ts` is the visual-network
  orchestrator. It wires the helper passes together, runs validation gates, and
  writes generated artifacts.
- `frontend/scripts/build/visual-network/` contains focused modules extracted
  from the large orchestrator. Keep extractions small and behavior-preserving.
- Helper files either implement reusable geometry operations or encode
  location-specific map fixes. Many helpers intentionally contain local subway
  knowledge that should not be erased during cleanup.
- Generated public artifacts live in `frontend/public/`, especially
  `subway-network.visual.geojson` and station-anchor artifacts.
- Generated artifacts should not be hand-edited. Rebuild them from source and
  explain any generated diff.

## Why Some Files Are Still `.mjs`

The TypeScript migration is gradual because this code produces deterministic
subway rendering artifacts. Converting a helper can change module resolution,
typing assumptions, or import boundaries even when the runtime logic is intended
to stay identical.

High-risk geometry helpers are migrated only after their importers are known,
their tests are converted or intentionally handled, the script typecheck passes,
the relevant tests pass, the visual artifact build passes, and generated output
diffs are reviewed.

## Migration Status

Already migrated transit build helpers include:

- `branch-transitions`
- `simplify-tight-curves`
- `offset-bow`
- `suppress-shadow-orphans`
- `sixty-third-street-f`
- `line-geometry-cleanup`
- `rockaway-wye`
- `joralemon-green-river`
- `snap-off-revenue-to-shape`
- `colocate-same-color`
- `joint-offset-taper`
- `staten-island-cleanup`
- `lane-continuity-filter`
- `same-route-junction-fabric`
- `cartographic-junction-overrides`
- `trim-terminal-overhang`
- `snap-dangling-same-color`
- `opendata-subway-lines`
- `schematic-hairpin-arc`
- `nostrand-eastern-schematic`
- `culver-fg-prospect-smoothing`
- `mott-haven-schematic`
- `bridge-route-gaps`
- `collapse-same-color`
- `st-nicholas-blue-straightening`
- `brighton-bq-church-spacing`
- `parallel-offset-cross-color`
- `cross-color-spread`
- `physical-bundle-materialization`
- `physical-bundle`
- `same-color-merge`
- `regenerate-canonical-from-gtfs`

Pre-existing TypeScript helpers include station-anchor modules, spine helpers,
lane ordering, artifact fingerprinting, smooth polyline utilities, and duplicate
corridor dedupe helpers.

## Validation Rules

For each migration batch:

1. Run `npm run typecheck:scripts`.
2. If local `npm` is broken, run
   `.\node_modules\.bin\tsc.cmd --project scripts\tsconfig.json --noEmit` from
   `frontend/`.
3. Run the converted tests with local `tsx --test`.
4. Run the visual artifact build. If `npm` is unavailable, use the underlying
   package command:
   `.\node_modules\.bin\tsx.cmd scripts\build-subway-visual-network.ts`.
5. Inspect `git diff` for generated artifacts.
6. Revert timestamp-only generated metadata diffs before commit.
7. Stop and report if real geometry or output drift appears.

Never claim a build or test passed unless the command actually ran and returned
success.

## Remaining `.mjs` Migration Map

Categories:

- **Keep as `.mjs`**: config or conventional ESM entrypoints.
- **Safe leaf helper**: small helper, few imports, focused colocated test, no
  direct artifact writes.
- **Medium-risk helper**: tested helper that mutates geometry, is larger, has
  several importers, or encodes location-specific behavior.
- **High-risk / migrate later**: broad bundle/materialization/orchestrator-like
  code, physical bundle code, cross-color offset code, or files that should wait
  for a dedicated migration plan.
- **QA/check script**: tests, visual QA scripts, audits, and checks that are not
  core source helpers.

| File | Size / complexity | Known importers | Test | Writes artifacts | Category | Recommended action |
| --- | --- | --- | --- | --- | --- | --- |
| `frontend/next.config.mjs` | 772 B / 28 lines | none | no | no | Keep as `.mjs` | keep `.mjs` |
| `frontend/postcss.config.mjs` | 144 B / 9 lines | none | no | no | Keep as `.mjs` | keep `.mjs` |
| `frontend/eslint.config.mjs` | 1.0 KB / 37 lines | none | no | no | Keep as `.mjs` | keep `.mjs` |
| `frontend/scripts/regenerate-canonical-from-gtfs.ts` | 14.9 KB / 532 lines | `package.json`, visual orchestrator, palette check, characterization test | yes | yes | Migrated TypeScript entrypoint | done |
| `frontend/scripts/regenerate-canonical-from-gtfs.test.ts` | 4.0 KB / 100 lines | none | n/a | no | Migrated characterization test | done |
| `frontend/scripts/build-subway-visual-network.ts` | 106 KB / 2370 lines | `package.json`, renderer code, debug artifacts | no | yes | Migrated TypeScript entrypoint | decompose gradually |
| `frontend/scripts/build/visual-network/route-config.ts` | 2.0 KB / 61 lines | visual orchestrator | no | no | Extracted TypeScript helper | done |
| `frontend/scripts/build/visual-network/gtfs-ingest.ts` | 3.0 KB / 91 lines | visual orchestrator | no | no | Extracted TypeScript helper | done |
| `frontend/scripts/build/visual-network/types.ts` | 446 B / 19 lines | visual orchestrator, visual-network helpers | no | no | Extracted shared TypeScript types | done |
| `frontend/scripts/build/visual-network/geometry-utils.ts` | 9.5 KB / 271 lines | visual orchestrator, visual-network stages | no | no | Extracted TypeScript helper | done |
| `frontend/scripts/build/visual-network/gtfs-topology.ts` | 4.4 KB / 146 lines | visual orchestrator | no | no | Extracted TypeScript helper | done |
| `frontend/scripts/build/visual-network/branch-selection.ts` | 4.0 KB / 119 lines | visual orchestrator | no | no | Extracted TypeScript helper | done |
| `frontend/scripts/build/visual-network/topology-edges.ts` | 3.5 KB / 118 lines | visual orchestrator | no | no | Extracted TypeScript helper | done |
| `frontend/scripts/build/visual-network/gtfs-topology-stage.ts` | 5.6 KB / 192 lines | visual orchestrator | no | no | Extracted TypeScript stage | done |
| `frontend/scripts/build/visual-network/diagnostics.ts` | 6.1 KB / 219 lines | visual orchestrator | no | no | Extracted TypeScript helper | done |
| `frontend/scripts/build/visual-network/opendata-inputs.ts` | 4.5 KB / 133 lines | visual orchestrator | no | no | Extracted TypeScript stage | done |
| `frontend/scripts/build/visual-network/bundle-stage.ts` | 24.7 KB / 594 lines | visual orchestrator | no | no | Extracted TypeScript stage | done |
| `frontend/scripts/build/visual-network/artifact-metadata.ts` | 3.0 KB / 86 lines | visual orchestrator | no | no | Extracted TypeScript helper | done |
| `frontend/scripts/build/visual-network/geometry-smoothing-pass.ts` | 1.8 KB / 58 lines | visual orchestrator | no | no | Extracted Tier 3 TypeScript pass | done |
| `frontend/scripts/build/visual-network/tight-curve-simplification-pass.ts` | 1.5 KB / 47 lines | visual orchestrator | no | no | Extracted Tier 3 TypeScript pass | done |
| `frontend/scripts/build/visual-network/same-route-endpoint-crossing-pass.ts` | 923 B / 27 lines | visual orchestrator | no | no | Extracted Tier 3 TypeScript pass | done |
| `frontend/scripts/build/visual-network/same-color-junction-types.ts` | 289 B / 9 lines | same-color junction stage | no | no | Extracted TypeScript types | done |
| `frontend/scripts/build/visual-network/same-color-junction-stage.ts` | 4.0 KB / 76 lines | visual orchestrator | no | no | Extracted Tier 3 TypeScript stage | done |
| `frontend/scripts/build/visual-network/route-continuity-repair-types.ts` | 395 B / 12 lines | route-continuity repair stage | no | no | Extracted TypeScript types | done |
| `frontend/scripts/build/visual-network/route-continuity-repair-stage.ts` | 4.8 KB / 97 lines | visual orchestrator | no | no | Extracted Tier 3 TypeScript stage | done |
| `frontend/scripts/build/visual-network/authored-location-patches-stage.ts` | 6.1 KB / 106 lines | visual orchestrator | no | no | Extracted Tier 3 TypeScript stage | done |
| `frontend/scripts/build/visual-network/mott-haven-stage.ts` | 16.2 KB / 278 lines | visual orchestrator | no | no | Extracted Tier 3 TypeScript stage | done |
| `frontend/scripts/build/visual-network/post-mott-local-fixes-stage.ts` | 2.6 KB / 50 lines | visual orchestrator | no | no | Extracted Tier 3 TypeScript stage | done |
| `frontend/scripts/build/visual-network/terminal-overhang-trim-stage.ts` | 2.8 KB / 68 lines | visual orchestrator | no | no | Extracted Tier 3 TypeScript stage | done |
| `frontend/scripts/build/visual-network/artifact-writer-stage.ts` | 2.2 KB / 66 lines | visual orchestrator | no | yes | Extracted TypeScript artifact writer stage | done |
| `frontend/scripts/build/visual-network/final-reporting-stage.ts` | 1.7 KB / 41 lines | visual orchestrator | no | no | Extracted TypeScript reporting stage | done |
| `frontend/scripts/build/schematic-hairpin-arc.ts` | 5.3 KB / 129 lines | visual orchestrator | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/parallel-offset-cross-color.ts` | 8.1 KB / 240 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/lane-continuity-filter.ts` | 7.3 KB / 187 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/same-route-junction-fabric.ts` | 9.1 KB / 270 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/opendata-subway-lines.ts` | 11.1 KB / 368 lines | visual orchestrator, palette check, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/cartographic-junction-overrides.ts` | 11.4 KB / 345 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/trim-terminal-overhang.ts` | 12.1 KB / 328 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/cross-color-spread.ts` | 15.2 KB / 430 lines | visual orchestrator, `parallel-offset-cross-color.ts`, `physical-bundle-materialization.ts`, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/snap-dangling-same-color.ts` | 14.8 KB / 394 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/nostrand-eastern-schematic.ts` | 18.0 KB / 547 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/culver-fg-prospect-smoothing.ts` | 18.5 KB / 544 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/mott-haven-schematic.ts` | 18.6 KB / 525 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/bridge-route-gaps.ts` | 19.8 KB / 530 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/collapse-same-color.ts` | 21.0 KB / 658 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/physical-bundle-materialization.ts` | 21.8 KB / 550 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/st-nicholas-blue-straightening.ts` | 21.1 KB / 678 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/brighton-bq-church-spacing.ts` | 25.2 KB / 705 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/same-color-merge.ts` | 25.8 KB / 737 lines | visual orchestrator, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/physical-bundle.ts` | 26.8 KB / 713 lines | visual orchestrator, `cross-color-spread.ts`, `same-color-merge.ts`, colocated test | yes | no | Migrated TypeScript helper | done |
| `frontend/scripts/build/bridge-route-gaps.test.ts` | 9.1 KB / 203 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/brighton-bq-church-spacing.test.ts` | 5.4 KB / 160 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/cartographic-junction-overrides.test.ts` | 5.3 KB / 154 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/collapse-same-color.test.ts` | 6.4 KB / 141 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/cross-color-spread.test.ts` | 10.6 KB / 226 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/culver-fg-prospect-smoothing.test.ts` | 4.9 KB / 158 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/lane-continuity-filter.test.ts` | 10.2 KB / 258 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/mott-haven-schematic.test.ts` | 7.0 KB / 258 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/nostrand-eastern-schematic.test.ts` | 4.9 KB / 168 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/opendata-subway-lines.test.ts` | 4.3 KB / 148 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/parallel-offset-cross-color.test.ts` | 8.5 KB / 183 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/physical-bundle-materialization.test.ts` | 8.9 KB / 238 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/physical-bundle.test.ts` | 19.2 KB / 372 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/same-color-merge.test.ts` | 23.5 KB / 539 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/same-route-junction-fabric.test.ts` | 4.3 KB / 122 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/schematic-hairpin-arc.test.ts` | 3.1 KB / 95 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/snap-dangling-same-color.test.ts` | 5.8 KB / 103 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/st-nicholas-blue-straightening.test.ts` | 11.9 KB / 354 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/build/trim-terminal-overhang.test.ts` | 10.5 KB / 305 lines | none | n/a | no | Migrated test | done |
| `frontend/scripts/qa/analyze-route-coverage.mjs` | 18.4 KB / 470 lines | none | no | yes | QA/check script | document only |
| `frontend/scripts/qa/audit-lane-continuity.mjs` | 40.0 KB / 960 lines | none | no | yes | QA/check script | document only |
| `frontend/scripts/qa/capture-dekalb-compare.mjs` | 3.3 KB / 64 lines | none | no | yes | QA/check script | document only |
| `frontend/scripts/qa/render-bbox.mjs` | 1.7 KB / 28 lines | none | no | no | QA/check script | document only |
| `frontend/scripts/qa/render-canonical.mjs` | 1.6 KB / 26 lines | none | no | no | QA/check script | document only |
| `frontend/scripts/qa/screenshot-bundle-zooms.mjs` | 1.6 KB / 51 lines | none | no | no | QA/check script | document only |
| `frontend/scripts/qa/screenshot-bus-arrivals.mjs` | 1.5 KB / 35 lines | none | no | no | QA/check script | document only |
| `frontend/scripts/qa/screenshot-polish-pass.mjs` | 1.9 KB / 51 lines | none | no | yes | QA/check script | document only |
| `frontend/scripts/qa/screenshot-real-app-routes.mjs` | 13.1 KB / 281 lines | none | no | yes | QA/check script | document only |
| `frontend/scripts/qa/screenshot-real-app.mjs` | 3.9 KB / 81 lines | none | no | yes | QA/check script | document only |
| `frontend/scripts/qa/screenshot-route-display.mjs` | 3.7 KB / 103 lines | none | no | no | QA/check script | document only |
| `frontend/scripts/qa/screenshot-visual-qa.mjs` | 2.7 KB / 67 lines | none | no | yes | QA/check script | document only |
| `frontend/components/map/incidents/incident-maplibre-layer.check.mjs` | 4.8 KB / 175 lines | none | no | no | QA/check script | document only |
| `frontend/components/map/incidents/incident-popup.check.mjs` | 2.8 KB / 96 lines | none | no | no | QA/check script | document only |
| `frontend/components/map/route-stops.check.mjs` | 4.5 KB / 134 lines | `route-stops-features.ts` | no | no | QA/check script | document only |
| `frontend/components/map/subway-palette.check.mjs` | 4.5 KB / 144 lines | `package.json` | no | no | QA/check script | document only |
| `frontend/components/map/subway-renderer.check.mjs` | 6.5 KB / 178 lines | `package.json`, `subway-network.ts` | no | no | QA/check script | document only |
| `frontend/components/map/subway-station-overlay.check.mjs` | 9.0 KB / 243 lines | `package.json`, visual orchestrator | no | no | QA/check script | document only |
| `frontend/components/smart-route/left-rail/atlas-thinking-flow.check.mjs` | 1.2 KB / 47 lines | none | no | no | QA/check script | document only |
| `frontend/components/smart-route/left-rail/hydration.test.mjs` | 804 B / 25 lines | `package.json` | n/a | no | QA/check script | document only |
| `frontend/components/smart-route/left-rail/live-data.test.mjs` | 13.5 KB / 437 lines | `package.json` | n/a | no | QA/check script | document only |
| `frontend/lib/backend-proxy.test.mjs` | 2.3 KB / 78 lines | `package.json` | n/a | no | QA/check script | document only |
| `frontend/lib/live-directions.test.mjs` | 1.3 KB / 45 lines | none | n/a | no | QA/check script | document only |
| `frontend/lib/use-service-alerts.test.mjs` | 847 B / 38 lines | `package.json` | n/a | no | QA/check script | document only |
| `frontend/lib/ws-ticket.test.mjs` | 2.3 KB / 82 lines | `package.json` | n/a | no | QA/check script | document only |

## Recommended Migration Batches

Next dedicated review batch:

- QA/check scripts only if that scope is explicitly approved

Following dedicated review batch:

- no remaining core transit artifact `.mjs` helpers

Avoid for now:

- visual QA/check scripts that write screenshots or debug output

Needs characterization before conversion:

- none in the core transit artifact source path; remaining `.mjs` files are
  config or QA/check scripts.

Wait until after an orchestrator migration plan:

- no remaining core transit artifact `.mjs` helpers
- QA scripts that write screenshots or debug output should remain document-only
  unless explicitly approved

## Orchestrator Decomposition Status

The visual-network orchestrator is now TypeScript and should be reduced through
small extraction batches. Batch 27 extracted pure route/color configuration into
`frontend/scripts/build/visual-network/route-config.ts`. Batch 28 extracted
pure GTFS ZIP/CSV parsing into
`frontend/scripts/build/visual-network/gtfs-ingest.ts`. Batch 29 extracted
pure GTFS row-to-map topology builders into
`frontend/scripts/build/visual-network/gtfs-topology.ts`. Batch 30 extracted
pure branch aggregation and canonical branch selection into
`frontend/scripts/build/visual-network/branch-selection.ts`. Batch 31 extracted
pure topology edge feature construction into
`frontend/scripts/build/visual-network/topology-edges.ts`. Batch 32 extracted
the coordinated GTFS topology stage into
`frontend/scripts/build/visual-network/gtfs-topology-stage.ts`. The next
autonomous decomposition pass extracted shared visual-network feature types,
pure geometry utilities, pure diagnostics helpers, and the OpenData corridor
input stage into `types.ts`, `geometry-utils.ts`, `diagnostics.ts`, and
`opendata-inputs.ts`. Follow-up decomposition extracted bundle construction
into `bundle-stage.ts`, candidate artifact metadata into `artifact-metadata.ts`,
final artifact writing/promotion into `artifact-writer-stage.ts`,
final topology summary reporting into `final-reporting-stage.ts`,
and the first Tier 3 mutate-in-place geometry smoothing, tight-curve,
same-route endpoint-crossing, same-color junction, route-continuity repair,
authored location patch, Mott Haven, post-Mott local fix, and terminal overhang
trim passes into
`geometry-smoothing-pass.ts`, `tight-curve-simplification-pass.ts`,
`same-route-endpoint-crossing-pass.ts`, `same-color-junction-stage.ts`,
`route-continuity-repair-stage.ts`, `authored-location-patches-stage.ts`, and
`mott-haven-stage.ts`, `post-mott-local-fixes-stage.ts`, and
`terminal-overhang-trim-stage.ts`. Remaining high-risk ordered location patch
passes should continue one gated sub-stage at a time.

## Historical Orchestrator Migration Plan

`build-subway-visual-network.mjs` (~4,700 lines) is the visual-network
orchestrator. A Batch 24 review concluded that a direct `.mjs` -> `.ts`
conversion is **not** mechanically contained and should be **deferred** to a
dedicated, staged effort. Key findings:

- **All sibling imports are already `.ts`** (36 build helpers) and there are
  **no remaining `.mjs` imports**, so the conversion needs **no TS1479
  suppressions** and **no declaration files**. Node built-ins used: `node:fs`
  (`existsSync`/`mkdirSync`/`readFileSync`/`renameSync`/`writeFileSync`),
  `node:path`, `node:url` (`fileURLToPath` + `import.meta.url`), `node:zlib`
  (`inflateRawSync`).
- **Top-level side-effect script**: no `main()`, no exports, no `process.argv`,
  no `process.env`. The entire build runs on import, so a characterization test
  **cannot import it** without running the full (~8 min) build. The only
  behavioral gate is the artifact-identical visual build.
- **Strict typing is the real cost**: under `scripts/tsconfig.json`
  (`strict: true`, `include: ["**/*.ts"]`) the file would be typechecked, so
  every untyped parameter/feature-bag needs annotation. This is a large,
  signature-by-signature typing effort, not a rename.
- **Artifact-drift trap**: the public `subway-network.visual.geojson` embeds
  `metadata.source: "build-subway-visual-network.mjs Gate 2A-2H"`, and the
  debug artifacts embed similar `source: "...mjs Gate 2X"` strings. These
  self-referential literals **must be kept verbatim** (`.mjs`) through the
  conversion, or the generated artifact changes (a real, non-timestamp diff).
- **Must preserve exactly**: the 7 `process.exit(1)` validation gates, the
  gated atomic `renameSync` promotion of `subway-network.visual.geojson`, the
  artifact schema/ordering/hashing, and the disabled `void parallelOffsetCrossColor;`.
- **Cross-file path updates required on conversion**: `package.json`
  `build:visual-network` (`tsx scripts/build-subway-visual-network.mjs` ->
  `.ts`) and `components/map/subway-palette.check.mjs` (which reads the
  orchestrator source by path). `scripts/script-inventory.json` references are
  stale metadata and are **not** updated.

Recommended staging:

1. **Prep batch** (still `.mjs`, behavior-preserving): freeze/verify the
   self-referential provenance strings stay `.mjs`; confirm
   `scripts/build/types.ts` covers the orchestrator's GeoJSON/feature shapes;
   finalize this plan. Gate: artifact-identical build.
2. **Conversion batch** (dedicated, large): rename `.mjs` -> `.ts`; type
   function signatures against the shared types (let internal vars infer; use a
   permissive feature-properties bag for dynamic fields); keep provenance
   literals and `void parallelOffsetCrossColor;` unchanged; update the
   `package.json` + `subway-palette.check.mjs` paths. Gate: `typecheck:scripts`
   + full artifact-identical visual build (timestamp-only) + `verify:transit-artifacts`.

### Batch 26 conversion checklist

Pre-flight:

- Clean tree; baseline `tsx scripts/build-subway-visual-network.mjs` produces a
  timestamp-only artifact diff.

Convert (mechanical rename + erasable types only — no behavior change):

- `git mv scripts/build-subway-visual-network.mjs scripts/build-subway-visual-network.ts`
  to preserve history.
- Keep all 21 `source: "build-subway-visual-network.mjs ..."` literals **verbatim**
  (do not rewrite to `.ts`). They are output provenance only — never used in
  control flow — and the `Gate 2A-2H` one is embedded in the public
  `subway-network.visual.geojson` metadata, so rewriting them is real artifact drift.
- Keep `void parallelOffsetCrossColor;` and its commented-out call disabled.
- Types: import `Position`, `LineStringGeometry`, `Feature`, `FeatureCollection`,
  `BBox`, `RouteId`, `VisualFeatureProperties` from `./build/types.ts` (covers all
  GeoJSON feature/property shapes). Reuse `physical-bundle.ts` exports (`Spine`,
  `PhysicalBundleGroup`) where bundles are handled.
- Add narrow local types for: GTFS rows (`stops`/`trips`/`stop_times`/`routes.txt`
  from `parseCsv`), the ZIP-entry map, gate-diagnostic records, and the
  candidate/final metadata doc. Avoid broad `any`; permissive bags =
  `VisualFeatureProperties` / `Record<string, unknown>`. Type the ~45 top-level
  helper signatures; let internal vars infer.

Active path updates (NOT `script-inventory.json` — it is generated metadata):

- `package.json` `build:visual-network`: `...network.mjs` -> `...network.ts`.
- `components/map/subway-palette.check.mjs` consumers array:
  `scripts/build-subway-visual-network.mjs` -> `...network.ts`.

Gate (stop on any real geometry/output drift):

- `npm run typecheck:scripts` -> 0 errors.
- `tsx scripts/build-subway-visual-network.ts` -> exit 0; artifact diff
  timestamp-only (revert `generated_at`).
- `npm run verify:transit-artifacts` green; `git diff --check`; do not commit.

Optional later (separate batches, only after the rename is locked): extract
cohesive seams to shrink the orchestrator — GTFS zip/csv ingest, the validation
gates + diagnostic builders, route-family constants, station-anchor/junction
helpers, and artifact write/promotion + metadata/hash generation.
