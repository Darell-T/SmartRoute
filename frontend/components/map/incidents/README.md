# SmartRoute Incident Marker System

> Pin anatomy: ground glow → pin face → rim → well → glyph. Eight types, three sizes, three states. One source of truth for the legend, the canvas markers, the screen-reader mirror, and the atlas generator.

## Module map

| File | Role |
|---|---|
| `incident-marker-types.ts` | TypeScript types (`IncidentType`, `IncidentMarkerSize`, `IncidentMarkerState`, `MapIncident`, atlas entry shape) + the `liveFeedIncidentToMapIncident` adapter that maps the backend `LiveFeedIncident` payload onto the marker system. |
| `incident-marker-tokens.ts` | Color tokens (`hue` / `deep` / `glow`), pixel dimensions per `S`/`M`/`L`, collision priority (stabbing > weapon > assault > police > fire > medical > passenger > general), zoom-to-size mapping, sprite-key serializer, and the legacy-type normalizer (`hazard` / `incident` → `general`). |
| `incident-marker-svg.tsx` | Single inline React SVG component. Used by static UI (legend popover, popups). **Not** rendered per-pin on the map — see "Why an atlas?" below. |
| `incident-legend.tsx` | Reusable type-grid component. Consumed by `smart-route/disruption-legend.tsx`'s `<details>` popover; available for any other surface that needs to teach users the marker visual language. |
| `incident-icon-layer.ts` | Two factories: `createIncidentIconLayer` (the base markers from the atlas) and `createIncidentPulseHaloLayer` (a separate halo layer driven by `requestAnimationFrame` for active incidents). Both produce deck.gl `IconLayer` instances. |
| `incident-a11y-list.tsx` | Visually hidden `<ul role="list" aria-live="polite">` that mirrors the same data as the canvas markers. Required because deck.gl renders to `<canvas>` and so does not participate in the accessibility tree. |
| `../../../scripts/generate-incident-marker-atlas.tsx` | Atlas-builder scaffold. Documents the contract (8 × 3 × 3 = 72 sprites @3x) and the recommended Resvg pipeline. |

## Why an atlas?

deck.gl's `IconLayer` reads sprite UVs from a single texture every frame. If you mounted React DOM markers (one `<IncidentMarkerSvg>` per pin) the browser would re-layout and paint that subtree on every map drag — fine at 5 incidents, terminal at 200+. The atlas pre-rasterizes every (type × size × state) combination once, then `IconLayer` does GPU sprite blits at constant cost regardless of incident count.

> **Hard rule:** never render React DOM markers per pin on the canvas. Use the atlas.

## State model

```
state ∈ { default, pulse, selected }

selected = (incident.id === selectedIncidentId)
pulse    = incident.active && !selected
default  = otherwise
```

`selected` always wins. `pulse` is rendered through a *separate* halo layer, not by re-rasterizing the main marker — so the active-state pin still uses its `default` sprite from the atlas while the halo layer draws a breathing ring above it.

## Integration recipe (when the atlas is ready)

1. **Generate the atlas** (one-time + re-run when glyphs change):
   ```bash
   cd frontend
   # wire @resvg/resvg-js into scripts/generate-incident-marker-atlas.tsx
   # then:
   npm run atlas
   ```
   Outputs:
   - `public/incident-markers/incident-marker-atlas@3x.png`
   - `public/incident-markers/incident-marker-atlas.json`
   - `public/incident-markers/incident-halo@3x.png`

2. **Load the icon mapping** (cache in module scope or React state):
   ```ts
   import { DEFAULT_INCIDENT_ATLAS_MAPPING_URL } from "@/components/map/incidents/incident-icon-layer";
   const iconMapping = await fetch(DEFAULT_INCIDENT_ATLAS_MAPPING_URL).then((r) => r.json());
   ```

3. **Build the layer + halo layer in `jarvis-map.tsx`** alongside the existing transit network layers:
   ```ts
   import {
     createIncidentIconLayer,
     createIncidentPulseHaloLayer,
   } from "@/components/map/incidents/incident-icon-layer";

   const layers = [
     // ...existing transit + buildings layers...
     ...(reduceMotion
       ? []
       : [
           createIncidentPulseHaloLayer({
             incidents: mapIncidents.filter(
               (i) => i.active && i.id !== selectedIncidentId,
             ),
             zoom: currentZoom,
             time: performance.now(),
           }),
         ]),
     createIncidentIconLayer({
       incidents: mapIncidents,
       zoom: currentZoom,
       selectedIncidentId,
       iconMapping,
       onSelect: (incident) => setSelectedIncidentId(incident.id),
     }),
   ];

   overlay.setProps({ layers });
   ```

4. **Drive the halo via `requestAnimationFrame`** — call `setProps` on the overlay with a fresh time value each frame. Don't set React state at 60fps; the time value is a layer-local prop only.

5. **Pair the atlas with the screen-reader mirror.** Already wired: `IncidentA11yList` is rendered next to `JarvisMap` in `app/page.tsx` so canvas markers and AT users see the same incident inventory. Keep them synced from the same `mapIncidents` memo.

## Reduced-motion behavior

When `prefers-reduced-motion: reduce` is on:

- The pulse halo layer must NOT be added to `layers`. Active incidents render their `default` marker from the atlas only.
- Selection still works (it's a click → state change, not a continuous animation).
- The legend's static SVG markers are unaffected (no animation in the legend).

## Type → color quick reference

| Type | Hue | Use |
|---|---|---|
| `police` | `#4DA3FF` | Police activity — ops blue, distinct from medical cyan |
| `fire` | `#FF6A1A` | Fire — operational orange |
| `assault` | `#FF3F4E` | Assault — bright red, the "danger" register |
| `stabbing` | `#E63A6E` | Stabbing — magenta-leaning red, distinct from assault |
| `weapon` | `#9B1422` | Weapon incident — deep maroon, signals contained threat |
| `passenger` | `#A66BFF` | Disruptive passenger — purple, lower-severity |
| `medical` | `#3DE7FF` | Medical emergency — cyan, **only** marker that shares cyan with the user-location dot. They're kept apart through ring treatment + size, not just hue. |
| `general` | `#F6B93B` | Catch-all amber for unclassified or legacy `hazard` codes. |

## Spec status

| Spec item | Status | Notes |
|---|---|---|
| 8 types × 3 sizes × 3 states | ✅ all glyphs authored | Stabbing + Disruptive Passenger glyphs replaced placeholder `ReservedGlyph`. S variants shipped per the 24-unit grid rule. |
| `IncidentMarkerSvg` static React component | ✅ | Used by legend; not used per-pin on map. |
| Atlas generator | 🟡 scaffold | Wire `@resvg/resvg-js` to ship the actual PNGs/JSON. See script header. |
| `createIncidentIconLayer` factory | ✅ | Ready to consume the atlas once generated. |
| `createIncidentPulseHaloLayer` factory | ✅ | Caller drives `time` via `requestAnimationFrame`. |
| Atlas PNG + JSON in `public/incident-markers/` | ❌ | Generate via the script. |
| Map integration in `jarvis-map.tsx` | ⏳ blocked on atlas | Snippet above shows the wiring once assets exist. |
| `DisruptionLegend` uses new marker visuals | ✅ | Compact `Incident` sample + `<details>` popover containing all 8 types via `IncidentLegend`. |
| Screen-reader hidden incident list | ✅ | `IncidentA11yList` rendered in `liveWorkspace`. |
| Selection via `selectedIncidentId` + `updateTriggers` | ✅ | Implemented in `createIncidentIconLayer`. |
| Collision priority | ✅ | `INCIDENT_PRIORITY` exported from `incident-marker-tokens.ts`; pass to `getCollisionPriority` once `CollisionFilterExtension` is loaded. |
| Cluster strategy | 📝 documented, not built | Use `supercluster` upstream of the IconLayer if/when needed. Do **not** swap to `CPUGridLayer`. |
