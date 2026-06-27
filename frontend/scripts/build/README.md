# Transit Build Helpers

This folder contains offline helper modules for the SmartRoute transit artifact
pipeline. The helpers are used by `frontend/scripts/build-subway-visual-network.mjs`
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

- `frontend/scripts/build-subway-visual-network.mjs` is the visual-network
  orchestrator. It wires the helper passes together, runs validation gates, and
  writes generated artifacts.
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
   `.\node_modules\.bin\tsx.cmd scripts\build-subway-visual-network.mjs`.
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
| `frontend/scripts/regenerate-canonical-from-gtfs.mjs` | 14.9 KB / 532 lines | `package.json`, visual orchestrator, palette check | no | yes | High-risk / migrate later | migrate later with artifact gate |
| `frontend/scripts/build-subway-visual-network.mjs` | 201 KB / 4701 lines | `package.json`, renderer code, debug artifacts | no | yes | High-risk / migrate later | orchestrator migration plan first |
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

- `regenerate-canonical-from-gtfs` only with a canonical-artifact gate (it writes generated canonical geometry)

Following dedicated review batch:

- `build-subway-visual-network.mjs` only after a dedicated orchestrator migration plan

Avoid for now:

- `build-subway-visual-network.mjs`

Needs characterization before conversion:

- `regenerate-canonical-from-gtfs.mjs` because it writes generated canonical
  artifacts and should be handled as an entrypoint with artifact checks.

Wait until after an orchestrator migration plan:

- `build-subway-visual-network.mjs`
- root artifact entrypoints and QA scripts that write screenshots or debug
  output
