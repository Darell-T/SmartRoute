import mapboxgl from "mapbox-gl";

/** Calculate bearing in degrees from point A to point B */
export function calculateBearing(from: [number, number], to: [number, number]): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const toDeg = (r: number) => (r * 180) / Math.PI;
  const dLng = toRad(to[0] - from[0]);
  const lat1 = toRad(from[1]);
  const lat2 = toRad(to[1]);
  const x = Math.sin(dLng) * Math.cos(lat2);
  const y = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLng);
  return (toDeg(Math.atan2(x, y)) + 360) % 360;
}

/** Fly camera to fit route bounds */
export function flyToRoute(m: mapboxgl.Map, allCoords: [number, number][]) {
  if (allCoords.length === 0) return;
  const bounds = new mapboxgl.LngLatBounds();
  allCoords.forEach((c) => bounds.extend(c as mapboxgl.LngLatLike));
  m.fitBounds(bounds, { padding: 80, duration: 1500, pitch: 60 });
}

/** Fly to destination and start slow rotation around it */
export function startRotation(
  m: mapboxgl.Map,
  center: [number, number],
  refs: {
    rotationTimeout: { current: ReturnType<typeof setTimeout> | null };
    rotationInterval: { current: ReturnType<typeof setInterval> | null };
  },
) {
  m.flyTo({ center, zoom: 15, pitch: 60, duration: 2000 });
  refs.rotationTimeout.current = setTimeout(() => {
    refs.rotationInterval.current = setInterval(() => {
      m.easeTo({
        center,
        bearing: (m.getBearing() + 1) % 360,
        duration: 200,
        easing: (t: number) => t,
      });
    }, 200);
  }, 2100);
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

/** Fly back to origin, bearing toward the first transit stop */
export function flyToOrigin(
  m: mapboxgl.Map,
  origin: [number, number],
  firstTransitCoords: [number, number] | null,
) {
  const bearing = firstTransitCoords
    ? calculateBearing(origin, firstTransitCoords)
    : m.getBearing();
  m.flyTo({ center: origin, zoom: 16, pitch: 60, bearing, speed: 0.5, duration: 3000 });
}
