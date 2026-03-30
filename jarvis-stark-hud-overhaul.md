# JARVIS — Stark HUD Visual Overhaul

## Design Vision

Transform the map interface from a "transit app on a dark map" into something that feels like a holographic tactical display projected onto glass. Think Iron Man HUD meets topographic intelligence briefing. The map should look like it was rendered by an AI system, not by Google Maps.

**Reference aesthetic:** Dark topographic display with cyan/teal line rendering, glowing points of interest with animated radar pulses, data particles flowing along routes, tilted perspective like looking down at a holographic table. Everything rendered as thin luminous lines on pure black -- no filled shapes, no solid colors, no conventional map styling.

**Two reference images have been provided** (saved in the project or described previously):
- Image 1: Cyan topographic contour lines on black, with orange accent points, thin grid overlays, arc elements at the edges
- Image 2: Dark tactical map with a red point emitting animated radial scan lines, white contour-style terrain rendering, high pitch angle

---

## Part 1 — Restyle the Mapbox Base Map

**File:** `jarvis-map.tsx` (and optionally a Mapbox style JSON)

Strip the map down to a dark tactical aesthetic. This is done by overriding the Mapbox style layers programmatically after the map loads, or by using a custom style.

### Remove these layer types (hide or set opacity to 0):
- All **fill** layers (buildings, parks, land use, water fill) -- we want outlines only, not filled shapes
- All **raster** layers (satellite imagery if any)
- All **symbol** layers EXCEPT essential street name labels -- keep minimal labels but restyle them
- All **background** layers -- replace with pure black (`#000000` or `#050508`)

### Restyle these layers:
- **Road networks:** Thin lines (`line-width: 0.5-1px`), color `rgba(0, 212, 255, 0.12)` for minor roads, `rgba(0, 212, 255, 0.25)` for major roads. No casings. The roads should look like a faint grid, not like actual streets.
- **Water boundaries:** Thin outline only (`line-width: 1px`), color `rgba(0, 212, 255, 0.2)`. No fill. Water should be black like everything else, just with a subtle boundary line.
- **Borough/neighborhood boundaries:** Very faint dashed lines, `rgba(0, 212, 255, 0.08)`.
- **Labels:** Restyle remaining text labels to use `Space Grotesk`, color `rgba(0, 212, 255, 0.3)`, small font size (`10-11px`), no halo. Labels should be barely visible -- they're reference points, not the focus.
- **Land/terrain:** If there are any land contour or hillshade layers available in the style, make them visible as very faint cyan lines (`rgba(0, 212, 255, 0.06)`). This creates the topographic feel from the reference images.

### Implementation approach:
After the map loads (`map.on('load', ...)`), iterate through all layers with `map.getStyle().layers` and modify them:

```javascript
map.on('load', () => {
  const layers = map.getStyle().layers;
  for (const layer of layers) {
    // Hide all fill layers
    if (layer.type === 'fill') {
      map.setPaintProperty(layer.id, 'fill-opacity', 0);
    }
    // Restyle line layers (roads, boundaries)
    if (layer.type === 'line') {
      map.setPaintProperty(layer.id, 'line-color', 'rgba(0, 212, 255, 0.15)');
      map.setPaintProperty(layer.id, 'line-width', 0.8);
    }
    // Restyle or hide symbols
    if (layer.type === 'symbol') {
      map.setPaintProperty(layer.id, 'text-color', 'rgba(0, 212, 255, 0.3)');
      map.setLayoutProperty(layer.id, 'text-font', ['Open Sans Regular']); // Mapbox GL doesn't support custom fonts in tiles, use closest available
      map.setPaintProperty(layer.id, 'text-halo-width', 0);
    }
  }
  // Override background
  map.setPaintProperty('background', 'background-color', '#050508');
});
```

> **Important:** This is approximate. Some layer IDs may differ based on the Mapbox style being used (e.g., `mapbox://styles/mapbox/dark-v11`). Log `map.getStyle().layers` to see the actual layer list and adjust selectively. Not all line layers should be the same brightness -- major roads slightly brighter than minor roads, etc. Use the layer source and source-layer to distinguish.

> **Alternative approach:** If the programmatic override is too brittle, create a custom style in Mapbox Studio with these properties and reference it by style URL. Either approach works.

### Map perspective:
- Set the initial pitch to `60` degrees (tilted, looking at an angle like a holographic table)
- Set initial bearing to something slightly off-axis like `15` degrees for visual interest
- Keep the existing zoom level behavior

---

## Part 2 — Radar Pulse Orbs

**File:** `jarvis-map.tsx`

Replace or upgrade the existing orb markers with animated radar-pulse style markers.

### User Location Orb (cyan)
The marker is an HTML element added via `new mapboxgl.Marker({element})`. Build it with this structure:

```html
<div class="orb-container">
  <!-- Core dot -->
  <div class="orb-core"></div>
  <!-- Pulse ring 1 -->
  <div class="orb-pulse"></div>
  <!-- Pulse ring 2 (delayed) -->
  <div class="orb-pulse orb-pulse-delayed"></div>
  <!-- Radar scan lines (4 thin lines radiating outward, rotating) -->
  <div class="orb-radar"></div>
</div>
```

**Core dot:** 12px circle, `background: #00D4FF`, `box-shadow: 0 0 12px rgba(0, 212, 255, 0.6), 0 0 30px rgba(0, 212, 255, 0.2)`.

**Pulse rings:** 12px circles that scale up to 60px and fade out on a loop.
```css
@keyframes orbPulse {
  0% { transform: scale(1); opacity: 0.5; }
  100% { transform: scale(5); opacity: 0; }
}
.orb-pulse {
  position: absolute;
  width: 12px; height: 12px;
  border-radius: 50%;
  border: 1px solid rgba(0, 212, 255, 0.4);
  animation: orbPulse 3s ease-out infinite;
}
.orb-pulse-delayed {
  animation-delay: 1.5s;
}
```

**Radar scan lines:** This is the key detail from the reference images. 4 thin lines (or more) radiating outward from the center, slowly rotating as a group.
```css
@keyframes radarSpin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
.orb-radar {
  position: absolute;
  width: 80px; height: 80px;
  top: 50%; left: 50%;
  transform-origin: center;
  animation: radarSpin 8s linear infinite;
}
```

The radar lines themselves can be built with 4 absolutely positioned divs inside `.orb-radar`, each rotated 90 degrees apart:
```css
.orb-radar-line {
  position: absolute;
  top: 50%; left: 50%;
  width: 40px; height: 1px;
  background: linear-gradient(to right, rgba(0, 212, 255, 0.4), rgba(0, 212, 255, 0));
  transform-origin: left center;
}
```
Rotate each line: 0deg, 90deg, 180deg, 270deg. The gradient fading to transparent at the tip creates the "scan line" effect. The parent rotating makes them sweep.

### Destination Orb (amber)
Same structure, same animations, but in amber:
- Core: `#F5A623`
- Glow/borders: `rgba(245, 166, 35, ...)`
- Only visible when route is active

### Activation animation
When a route is found and the animation sequence begins:
- The user orb's radar lines should briefly speed up (change animation-duration to 2s for 3 seconds, then back to 8s)
- The destination orb should fade in with the radar lines already spinning
- This creates the feeling of "scanning" when JARVIS processes a route

---

## Part 3 — Route Line Particle Flow

**File:** `jarvis-map.tsx`

After the route segments are drawn and the animation sequence completes, add flowing particles along the subway segment to make it feel like data is moving through the line.

### Implementation
Use a Mapbox `circle` layer with a GeoJSON source containing point features that move along the route coordinates.

1. Create an array of 5-8 point features spaced evenly along the subway segment coordinates.
2. Use `requestAnimationFrame` to move each point forward along the route line over time.
3. When a point reaches the end, loop it back to the start.
4. Style the points:
   - `circle-radius: 3`
   - `circle-color`: same as the subway line MTA color
   - `circle-opacity: 0.8`
   - `circle-blur: 0.5` (gives a soft glow)
5. Add a faint trail effect: a second set of points slightly behind each main point with lower opacity (`0.3`) and smaller radius (`2`).

The particles should flow from origin station toward destination station, creating the illusion of data/energy moving along the route.

### Movement logic:
```javascript
// Pseudo-code for particle movement along route coordinates
const routePoints = [...]; // the subway segment coordinates
const numParticles = 6;
const particlePositions = Array(numParticles).fill(0).map((_, i) => i / numParticles); // 0 to 1 progress

function animateParticles() {
  for (let i = 0; i < numParticles; i++) {
    particlePositions[i] += 0.002; // speed
    if (particlePositions[i] > 1) particlePositions[i] = 0;
    // Interpolate position along routePoints based on progress
  }
  // Update GeoJSON source with new positions
  map.getSource('particles').setData(buildGeoJSON(particlePositions));
  requestAnimationFrame(animateParticles);
}
```

Use `turf.along` or manual linear interpolation to convert progress (0-1) into actual [lng, lat] coordinates along the route line.

### Cleanup
- Stop particle animation and remove source/layer when route is cleared.
- Cancel the animation frame on unmount.

---

## Part 4 — Viewport Effects

**File:** `page.tsx` or a new CSS file

### Vignette overlay
Add a CSS overlay on top of the map viewport (pointer-events: none) that creates a dark vignette around the edges, focusing attention toward the center:

```css
.map-vignette {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  pointer-events: none;
  z-index: 1; /* above map, below UI elements */
  background: radial-gradient(
    ellipse at center,
    transparent 50%,
    rgba(0, 0, 0, 0.4) 80%,
    rgba(0, 0, 0, 0.7) 100%
  );
}
```

### Subtle scan line texture (optional, test if it looks good)
A very faint horizontal line pattern overlaid on the entire viewport to give it a "display screen" texture:
```css
.map-scanlines {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  pointer-events: none;
  z-index: 1;
  background: repeating-linear-gradient(
    0deg,
    transparent,
    transparent 2px,
    rgba(0, 0, 0, 0.03) 2px,
    rgba(0, 0, 0, 0.03) 4px
  );
}
```

> This should be extremely subtle. If it looks distracting or reduces map readability, skip it.

### Corner brackets
Add thin L-shaped brackets in the four corners of the viewport -- a classic HUD framing element:
```css
.hud-corner {
  position: fixed;
  width: 30px; height: 30px;
  border-color: rgba(0, 212, 255, 0.15);
  border-style: solid;
  pointer-events: none;
  z-index: 2;
}
.hud-corner.top-left { top: 16px; left: 16px; border-width: 1px 0 0 1px; }
.hud-corner.top-right { top: 16px; right: 16px; border-width: 1px 1px 0 0; }
.hud-corner.bottom-left { bottom: 16px; left: 16px; border-width: 0 0 1px 1px; }
.hud-corner.bottom-right { bottom: 16px; right: 16px; border-width: 0 1px 1px 0; }
```

---

## Part 5 — Typography Update

The frontend-design skill advises against overusing Space Grotesk. For a Stark HUD feel, switch to:

- **Primary (HUD elements, pills, labels):** `Geist Mono` from Vercel -- monospaced, clean, feels like a system readout. Import from `https://cdn.jsdelivr.net/npm/geist@1.0.0/dist/fonts/geist-mono/GeistMono-Regular.woff2` or use the npm package if already available in the Next.js project (Geist ships with `create-next-app`).
- **Response bubble text:** Keep a proportional font for readability -- `Space Grotesk` is fine here since it's the conversational text, not HUD chrome.
- **Map labels:** Already handled in Part 1 (Mapbox font stack).

The monospaced font on HUD elements (AI CORE ALPHA, pills, station markers, JARVIS branding) will immediately make everything feel more like a system interface and less like a web app.

---

## Part 6 — Restyle Existing HUD Elements

Apply the new aesthetic to all existing overlays:

### JARVIS branding (top-left)
- `font-family: 'Geist Mono'`
- `font-size: 11px`
- `letter-spacing: 0.15em`
- `color: rgba(0, 212, 255, 0.3)`
- Barely visible, like a watermark

### AI CORE ALPHA (top-right)
- `font-family: 'Geist Mono'`
- `font-size: 11px`
- `letter-spacing: 0.1em`
- `color: rgba(0, 212, 255, 0.5)`
- The latency number should be brighter: `color: rgba(0, 212, 255, 0.8)`

### HUD pills (NEXT TRANSIT, ETA)
- `font-family: 'Geist Mono'`
- `font-size: 12px`
- Reduce the background opacity further: `rgba(8, 10, 18, 0.5)`
- Border: `1px solid rgba(0, 212, 255, 0.08)`
- The MTA color circle for the train line should remain its actual color -- that's the one pop of non-cyan color allowed

### Station markers
- `font-family: 'Geist Mono'`
- `font-size: 11px`
- `letter-spacing: 0.05em`
- Thinner: `padding: 3px 8px`
- `background: rgba(8, 10, 18, 0.6)`
- `border: 1px solid rgba(0, 212, 255, 0.12)`
- MTA color only on the train line letter, not the whole background. White station name, colored letter.

### Input bar
- Match the frosted glass HUD aesthetic
- `font-family: 'Geist Mono'` for the placeholder text
- Border: `1px solid rgba(0, 212, 255, 0.1)`
- Subtle cyan glow on focus: `box-shadow: 0 0 20px rgba(0, 212, 255, 0.08)`

### Response bubble
- Keep `Space Grotesk` for the body text (readability for TTS transcript)
- But add a small `Geist Mono` label at the top of the bubble: `JARVIS` in `10px`, `rgba(0, 212, 255, 0.4)`, `letter-spacing: 0.15em` -- like a system label above the message text

---

## Constraints

1. **Do not break any existing functionality.** Route animation, audio playback, API calls, GPS tracking, service alerts -- all must still work.
2. **Performance matters.** The particle animation and radar spin use `requestAnimationFrame` -- make sure they don't tank frame rate. Use `will-change: transform` on animated elements. If particles cause jank on mobile, reduce count to 3.
3. **The map must remain interactive.** Pan, zoom, rotate must all still work through the vignette and scan line overlays (`pointer-events: none` on overlays).
4. **Test the map restyle carefully.** Different Mapbox styles have different layer names. Log the layers and adjust selectively. Don't blindly restyle layers that should remain visible (like the route lines you're drawing).
5. **Route-related layers must be excluded from the restyle.** When iterating through layers to apply the cyan theme, skip any layers that were added for route rendering (walking paths, subway line, shuttle bus, station markers, particles). Only restyle the BASE map layers.
6. **Clean up all animations** on unmount and route clear -- cancel animation frames, remove radar elements, stop particle flow.
7. **Mobile considerations.** The radar scan lines and particle flow might be too much on smaller screens. Consider disabling particles on viewports under 768px width, and reducing radar line count to 2 instead of 4.
