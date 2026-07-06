import maplibregl from "maplibre-gl";

const DESKTOP_RAIL_WIDTH = 420;
const DESKTOP_PADDING = 96;
const MOBILE_PADDING = 48;
const MOBILE_BOTTOM_PADDING = 180;

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
export function flyToRoute(
  m: maplibregl.Map,
  allCoords: [number, number][],
  options: {
    duration?: number;
    maxZoom?: number;
  } = {},
) {
  if (allCoords.length === 0) return;
  const bounds = new maplibregl.LngLatBounds();
  allCoords.forEach((c) => bounds.extend(c as maplibregl.LngLatLike));
  const reducedMotion =
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  m.fitBounds(bounds, {
    padding: routePreviewPadding(m),
    duration: reducedMotion ? 0 : options.duration ?? 850,
    maxZoom: options.maxZoom ?? 15.8,
    pitch: 0,
    bearing: 0,
    easing: easeOutCubic,
  });
}

function routePreviewPadding(m: maplibregl.Map): maplibregl.PaddingOptions {
  const width =
    m.getContainer().clientWidth ||
    (typeof window !== "undefined" ? window.innerWidth : 1440);

  if (width < 760) {
    return {
      top: 76,
      bottom: MOBILE_BOTTOM_PADDING,
      left: MOBILE_PADDING,
      right: MOBILE_PADDING,
    };
  }

  return {
    top: DESKTOP_PADDING,
    bottom: DESKTOP_PADDING,
    left: Math.min(
      DESKTOP_RAIL_WIDTH + DESKTOP_PADDING,
      Math.floor(width * 0.46),
    ),
    right: DESKTOP_PADDING,
  };
}

function easeOutCubic(t: number) {
  return 1 - Math.pow(1 - t, 3);
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
