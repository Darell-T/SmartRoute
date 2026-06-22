import maplibregl from "maplibre-gl";

/** Inject orbPulse keyframe into the document once */
let _keyframeInjected = false;
function ensureKeyframe() {
  if (_keyframeInjected) return;
  _keyframeInjected = true;
  const style = document.createElement("style");
  // Pulse is smaller and faints faster for the flat 2D basemap — keeps the
  // map quiet while still signalling location.
  style.textContent = `@keyframes orbPulse {
  0% { transform: translate(-50%, -50%) scale(1); opacity: 0.4; }
  100% { transform: translate(-50%, -50%) scale(3); opacity: 0; }
}`;
  document.head.appendChild(style);
}

/**
 * Shared orb element factory. Both user and destination orbs use
 * identical HTML/CSS; only the color changes.
 *
 * Structure: 20x20 container (overflow:visible, anchor "center")
 *   - core dot  (12px, absolute centered)
 *   - 2 pulse rings (absolute centered, same animation, staggered)
 */
export function createOrb(color: string, glowColor: string): HTMLDivElement {
  ensureKeyframe();
  const el = document.createElement("div");
  el.style.cssText =
    "width:18px;height:18px;position:relative;overflow:visible;";
  const c =
    "position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);border-radius:50%;";
  // Core dot is slightly smaller and the glow halo is trimmed for the 2D map.
  el.innerHTML = `
    <div style="${c}width:10px;height:10px;background:${color};box-shadow:0 0 8px ${glowColor}, 0 0 18px ${glowColor};"></div>
    <div style="${c}width:10px;height:10px;border:1px solid ${glowColor};animation:orbPulse 2.8s ease-out infinite;will-change:transform,opacity;"></div>
    <div style="${c}width:10px;height:10px;border:1px solid ${glowColor};animation:orbPulse 2.8s ease-out 1.4s infinite;will-change:transform,opacity;"></div>
  `;
  return el;
}

/** Create a Mapbox marker using the orb element, anchored at center */
export function createOrbMarker(
  map: maplibregl.Map,
  coords: { lng: number; lat: number },
  color: string,
  glowColor: string,
): maplibregl.Marker {
  const el = createOrb(color, glowColor);
  return new maplibregl.Marker({ element: el, anchor: "center" })
    .setLngLat([coords.lng, coords.lat])
    .addTo(map);
}
