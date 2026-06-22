import maplibregl from "maplibre-gl";

/** Calculate bearing in degrees from point A to point B */
function calculateBearing(
  from: [number, number],
  to: [number, number],
): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const toDeg = (r: number) => (r * 180) / Math.PI;
  const dLng = toRad(to[0] - from[0]);
  const lat1 = toRad(from[1]);
  const lat2 = toRad(to[1]);
  const x = Math.sin(dLng) * Math.cos(lat2);
  const y =
    Math.cos(lat1) * Math.sin(lat2) -
    Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLng);
  return (toDeg(Math.atan2(x, y)) + 360) % 360;
}

/** Fly camera to fit route bounds — flat 2D view to match custom style */
export function flyToRoute(m: maplibregl.Map, allCoords: [number, number][]) {
  if (allCoords.length === 0) return;
  const bounds = new maplibregl.LngLatBounds();
  allCoords.forEach((c) => bounds.extend(c as maplibregl.LngLatLike));
  m.fitBounds(bounds, { padding: 96, duration: 1500, pitch: 0, bearing: 0 });
}

/** Fly to destination and hold — gentle 2D settle, no rotation */
function startRotation(
  m: maplibregl.Map,
  center: [number, number],
  refs: {
    rotationTimeout: { current: ReturnType<typeof setTimeout> | null };
    rotationInterval: { current: ReturnType<typeof setInterval> | null };
  },
) {
  m.flyTo({ center, zoom: 15.2, pitch: 0, bearing: 0, duration: 2000 });
  // Rotation disabled for flat cartographic style — kept refs wired so the
  // stopRotation() cleanup path continues to behave.
  void refs;
}

/** Stop camera rotation */
export function stopRotation(refs: {
  rotationTimeout: { current: ReturnType<typeof setTimeout> | null };
  rotationInterval: { current: ReturnType<typeof setInterval> | null };
}) {
  if (refs.rotationInterval.current) {
    clearInterval(refs.rotationInterval.current);
    refs.rotationInterval.current = null;
  }
  if (refs.rotationTimeout.current) {
    clearTimeout(refs.rotationTimeout.current);
    refs.rotationTimeout.current = null;
  }
}

/** Fly back to origin, flat 2D — bearing kept neutral to match custom style */
function flyToOrigin(
  m: maplibregl.Map,
  origin: [number, number],
  firstTransitCoords: [number, number] | null,
) {
  // Bearing calc preserved for future angled tilts; currently held at 0 to
  // keep the cartographic style quiet.
  void firstTransitCoords;
  m.flyTo({
    center: origin,
    zoom: 15.6,
    pitch: 0,
    bearing: 0,
    speed: 0.5,
    duration: 3000,
  });
}
