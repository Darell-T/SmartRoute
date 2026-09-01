import maplibregl from "maplibre-gl";

const DESKTOP_RAIL_WIDTH = 420;
const DESKTOP_PADDING = 96;
const MOBILE_DEFAULT_SHEET_PADDING = 212;

export function flyToRoute(
  m: maplibregl.Map,
  allCoords: [number, number][],
  options: {
    duration?: number;
    maxZoom?: number;
  } = {},
) {
  const validCoords = allCoords.filter(
    (coord): coord is [number, number] =>
      Array.isArray(coord) &&
      coord.length >= 2 &&
      Number.isFinite(coord[0]) &&
      Number.isFinite(coord[1]),
  );
  if (validCoords.length === 0) return;

  const bounds = new maplibregl.LngLatBounds();
  validCoords.forEach((coord) => bounds.extend(coord));
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
    const sheetHeight = readMobileSheetHeight();
    return {
      top: 76,
      bottom: sheetHeight + 32,
      left: 24,
      right: 24,
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

function readMobileSheetHeight() {
  if (typeof window === "undefined") return MOBILE_DEFAULT_SHEET_PADDING;

  const raw = window
    .getComputedStyle(document.documentElement)
    .getPropertyValue("--sr-mobile-sheet-px");
  const value = Number.parseFloat(raw);

  if (Number.isFinite(value) && value > 0) {
    return Math.round(value);
  }

  return MOBILE_DEFAULT_SHEET_PADDING;
}

function easeOutCubic(t: number) {
  return 1 - Math.pow(1 - t, 3);
}
