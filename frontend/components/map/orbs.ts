import mapboxgl from "mapbox-gl";

/**
 * Shared orb element factory. Both user and destination orbs use
 * identical HTML/CSS; only the color changes.
 *
 * Structure: 20x20 container (overflow:visible, anchor "center")
 *   - core dot  (12px, absolute centered)
 *   - 2 pulse rings (absolute centered, same animation, staggered)
 */
export function createOrb(color: string, glowColor: string): HTMLDivElement {
  const el = document.createElement("div");
  el.style.cssText = "width:20px;height:20px;position:relative;overflow:visible;";
  const c = "position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);border-radius:50%;";
  el.innerHTML = `
    <div style="${c}width:12px;height:12px;background:${color};box-shadow:0 0 12px ${glowColor}, 0 0 30px ${glowColor};"></div>
    <div style="${c}width:12px;height:12px;border:1px solid ${glowColor};animation:orbPulse 2.5s ease-out infinite;will-change:transform,opacity;"></div>
    <div style="${c}width:12px;height:12px;border:1px solid ${glowColor};animation:orbPulse 2.5s ease-out 1.25s infinite;will-change:transform,opacity;"></div>
  `;
  return el;
}

/** The orbPulse keyframe CSS string for injection via <style> tag */
export const ORB_PULSE_KEYFRAME = `
@keyframes orbPulse {
  0% { transform: translate(-50%, -50%) scale(1); opacity: 0.5; }
  100% { transform: translate(-50%, -50%) scale(4); opacity: 0; }
}`;

/** Create a Mapbox marker using the orb element, anchored at center */
export function createOrbMarker(
  map: mapboxgl.Map,
  coords: { lng: number; lat: number },
  color: string,
  glowColor: string,
): mapboxgl.Marker {
  const el = createOrb(color, glowColor);
  return new mapboxgl.Marker({ element: el, anchor: "center" })
    .setLngLat([coords.lng, coords.lat])
    .addTo(map);
}
