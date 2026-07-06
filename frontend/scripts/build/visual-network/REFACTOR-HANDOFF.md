# Visual-Network Orchestrator Decomposition — Handoff

**Repo:** `C:\Users\19293\jarvis\jarvis-design` · **Branch:** `codex-frontend-v2-cleanup`
(NOT the session worktree — always operate via absolute paths into this repo.)

**Goal:** decompose `frontend/scripts/build-subway-visual-network.ts` (a top-level side-effect build
script) into focused modules under `frontend/scripts/build/visual-network/` until it reads like a thin
pipeline, **without changing any build output**. Behavior-preserving structural refactor only.

The shipped artifact is `frontend/public/subway-network.visual.geojson`. It is the ONLY behavioral
gate. `frontend/artifacts/debug/` is git-ignored (canary only).

## Status

| Commit | What | Orchestrator |
|--------|------|--------------|
| `3b1fbee` | Tier-1: types, geometry-utils, diagnostics, opendata-inputs | 4302 → 3778 |
| `279d359` | Stage A: bundle-stage.ts (buildBundleArtifacts + 8 helpers) | 3778 → 3206 |
| `70b37fe` | Stage C: artifact-metadata.ts (candidateDoc builder) | 3203 → 3180 |
| `(this checkpoint)` | Tier 3: geometry-smoothing-pass.ts | 3180 → 3159 |

**Net: 4302 → 3159 (−27%).** Every step gated + committed as its own rollback point.

Modules extracted so far (all under `build/visual-network/`): `types.ts`, `geometry-utils.ts`,
`diagnostics.ts`, `opendata-inputs.ts`, `gtfs-topology-stage.ts`, `route-config.ts`, `bundle-stage.ts`,
`artifact-metadata.ts`, `geometry-smoothing-pass.ts` (+ leaf helpers `gtfs-ingest`, `gtfs-topology`,
`branch-selection`, `topology-edges`).

## What remains: Tier 3 — the mutate-in-place patch chain

The region from the bundle-stage call (`const bundleArtifacts = buildBundleArtifacts(...)`, ~`:1246`)
to final emission (candidateDoc, ~`:3117`) is a chain of passes that mutate **shared mutable state in
place** — chiefly `bundleArtifacts.visualFeatures` and `bundleArtifacts.bundleLaneFeatures`, also
`corridorFeatures` upstream. **Order matters. The #1 way to corrupt the artifact is to change a pass's
mutate-in-place semantics or reorder a hidden read/write.**

> NOTE: line numbers shift after every extraction — locate passes by their **section-header text** or
> **helper name** (grep), never by stored line numbers.

Ordered passes (each is an extraction candidate; target state in parens):

1. **Phase 3b branch transitions** — `buildBranchTransitions(...)` (visualFeatures/bundleLaneFeatures).
2. **Phase 3c lane continuity** — `markOrphanLanes`, `removeOrphanErrorLanes`, `assertNoBogusTransitions`,
   `assertQContinuousInBrooklyn` (bundleLaneFeatures; mutates via `.length=0`+push). Contains gates.
3. **Cross-color spread** — `detectCrossColorAdjacency` → `findSharedArcExtent` →
   `offsetPolylineOverExtent` (geometry coords). `parallelOffsetCrossColor` stays DISABLED (`void`).
4. **Phase 2D connectivity** — per-route validation + hard gate (gate before the section header).
5. **DeKalb-zone redundant-lane collapse** — section `DeKalb-zone redundant-lane collapse`.
6. **Geometry smoothing** — DONE in `geometry-smoothing-pass.ts`; mutates
   `f.geometry.coordinates`; inline endpoint-preservation guard moved with the pass verbatim.
7. **Tight-curve simplification** — `simplifyTightCurves(...)`; inline guard ("tight-curve simplify
   moved an endpoint").
8. **Same-color junction merge / joint-offset tapers** — section after smoothing (~"At junctions where
   several routes of one color merge").
9. **Route-gap bridges** — additive bridge pass (Connectivity is GTFS-based, bridges don't affect it).
10. **Mott Haven 5/6 schematic** — QA block with inline gate ("QA FAIL: Mott Haven").
11. **63 St tunnel F membership** — `addSixtyThirdStreetF(...)`.
12. **Rockaway wye** — `connectRockawayWye(...)`.
13. **Final emission** — DONE (artifact-metadata.ts); promotion gate stays in orchestrator.

**Stage B (the 7 `process.exit` gates) folds into Tier 3:** 5 of them are inline invariant-guards
inside passes 2/6/7/10 (e.g. `f.geometry.coordinates = after` immediately follows the smoothing guard).
When a pass moves, its guard moves with it **verbatim** — do not extract gates separately.

## Per-stage process (run EVERY sub-stage — same as Stages A/C)

1. **Extract the pass VERBATIM** into a `build/visual-network/<name>.ts` module as
   `applyXxxPass(features, opts) → {counts}` (or a stage facade). Copy the body byte-identical (sed
   `-n 'A,Bp'` to avoid transcription error); only add the function wrapper + param threading + imports.
   Pass the live array refs in and **mutate them in place exactly as today**; return only the
   logged counts. Move any inline `process.exit` guard WITH the pass. Keep disabled `void` lines.
2. In the orchestrator: replace the block with the single call + the same console.log of the counts;
   remove now-orphaned imports/constants (grep count == 1 → orphaned).
3. `cd frontend && ./node_modules/.bin/tsc --project scripts/tsconfig.json --noEmit` → **0 errors**.
4. Snapshot baselines: `cp public/subway-network.visual.geojson <scratch>/base.geojson` and
   `cp -r artifacts/debug <scratch>/debug-base`.
5. `./node_modules/.bin/tsx scripts/build-subway-visual-network.ts` → **exit 0** + log ends with
   "All gates passed".
6. **Gate A (ship):** semantic-compare new vs base stripping `generated_at`/`*_at`/`*generated*`
   (script `scratchpad/cmp_visual.py`). Must be IDENTICAL (same feature count). Then
   `git restore frontend/public/subway-network.visual.geojson`.
7. **Gate B (canary):** debug artifacts semantic-compare (timestamps stripped) → 0 drift.
8. Commit (this repo, this branch) with a behavior-preserving message. Update this doc's Status table
   + "current position". **Each committed sub-stage is a clean handoff point.**

## Hard invariants / guardrails (do not violate)

- **Preserve mutate-in-place semantics.** Pass live refs, mutate in place, same order. Do NOT convert
  to return-a-new-array unless you also rebind in the orchestrator AND prove the artifact is identical.
- **STOP at the first sign of mutate-semantics ambiguity** (hidden alias, reorder risk). Report; don't force it.
- Keep all 21 `build-subway-visual-network.mjs Gate …` provenance literals verbatim (`.mjs`).
- Keep the 5 disabled `void` helpers + their imports; never re-enable `parallelOffsetCrossColor`.
- Don't tighten Batch-26 `any`-bags (`FeatureProps = Record<string, any>`, scattered `as any`/`!`) —
  move them as-is; tightening is a separate gated pass.
- **All `fs` writes + `OUT_*` path resolution stay in the orchestrator** (a module's `__dirname`
  differs). Modules return data only.
- No circular imports (stages import leaf helpers + types; orchestrator imports stages).
- Never modify `scripts/script-inventory.json`, generated artifacts, or unrelated files. Never commit
  unless the owner asked (this session: owner authorized committing each gated sub-stage).
- One sub-stage = one full build gate. Never batch two extractions before a build (drift localization).

## Tooling

- Typecheck: `cd frontend && ./node_modules/.bin/tsc --project scripts/tsconfig.json --noEmit`
- Build (~8 min): `cd frontend && ./node_modules/.bin/tsx scripts/build-subway-visual-network.ts`
- Semantic artifact compare: `python <scratch>/cmp_visual.py <base.geojson> <new.geojson>` (strips
  timestamp-ish keys; exits 0 = identical-modulo-timestamps). Recreate from this repo's git history if
  the scratch copy is gone — it just recursively drops keys matching `(_at$|generated)` then deep-compares.
- The off-repo plan with full rationale: `C:\Users\19293\.claude\plans\cached-kindling-flurry.md`
  (not repo-tracked — this file is the portable source of truth).

## Current position

Tier 1, Stages A/C, and the first Tier-3 geometry-smoothing pass are done and
gated. **NEXT: extract the tight-curve simplification pass** immediately after
the smoothing section. It has the same endpoint-pinned shape and inline guard;
move that guard with the pass. Then continue upward through same-color
junction/bridge passes one gated sub-stage at a time.
