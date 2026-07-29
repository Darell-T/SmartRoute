const CURRENT_LOCATION_STYLE_ID = "smart-route-current-location-marker-style";

const EARTH_CIRCUMFERENCE_METERS = 40075016.686;
const TILE_SIZE = 512;

export interface CurrentLocationAccuracyInput {
  lng: number;
  lat: number;
  zoom: number;
  accuracyMeters?: number | null;
}

export function createCurrentLocationDot(): HTMLDivElement {
  ensureCurrentLocationMarkerStyles();
  const el = document.createElement("div");
  el.className = "sr-current-location-marker";
  el.setAttribute("role", "img");
  el.setAttribute("aria-label", "Current location");
  el.innerHTML = `
    <span class="sr-current-location-marker__accuracy" aria-hidden="true"></span>
    <span class="sr-current-location-marker__pulse" aria-hidden="true"></span>
    <span class="sr-current-location-marker__core" aria-hidden="true">
      <span class="sr-current-location-marker__fill"></span>
    </span>
  `;
  updateCurrentLocationDot(el, {
    lng: 0,
    lat: 0,
    zoom: 0,
    accuracyMeters: null,
  });
  return el;
}

export function updateCurrentLocationDot(
  el: HTMLElement,
  { lat, zoom, accuracyMeters }: CurrentLocationAccuracyInput,
) {
  // Real GPS accuracy (68% confidence radius, in meters) → on-screen pixels
  // at the current zoom. Zoom in and the same real-world uncertainty covers
  // more pixels, so the "general area" disc grows; zoom out and it shrinks
  // below the threshold and hides, leaving just the dot.
  const radiusPx =
    typeof accuracyMeters === "number" && Number.isFinite(accuracyMeters)
      ? accuracyMeters / metersPerPixelAtLatitude(lat, zoom)
      : 0;
  const visible = radiusPx >= 24;
  const diameter = visible ? Math.round(clamp(radiusPx * 2, 60, 680)) : 0;

  el.style.setProperty("--sr-current-location-accuracy-size", `${diameter}px`);
  el.dataset.accuracyVisible = visible ? "true" : "false";
}

function ensureCurrentLocationMarkerStyles() {
  if (document.getElementById(CURRENT_LOCATION_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = CURRENT_LOCATION_STYLE_ID;
  style.textContent = `
    .sr-current-location-marker {
      --sr-current-location-blue: #0a84ff;
      --sr-current-location-accuracy-size: 0px;
      position: relative;
      width: 44px;
      height: 44px;
      overflow: visible;
      pointer-events: none;
    }

    .sr-current-location-marker__accuracy,
    .sr-current-location-marker__pulse,
    .sr-current-location-marker__core,
    .sr-current-location-marker__fill {
      position: absolute;
      left: 50%;
      top: 50%;
      border-radius: 999px;
      pointer-events: none;
    }

    /* GPS accuracy disc — the general area the user may be in. Real-meter
       sized, so it only reads as a large area once zoomed in. A flat blue
       wash with a soft edge, like Apple Maps. NOTE: the marker must NOT use
       'contain: paint' or this disc gets clipped to the 44px marker box. */
    .sr-current-location-marker__accuracy {
      width: var(--sr-current-location-accuracy-size);
      height: var(--sr-current-location-accuracy-size);
      transform: translate(-50%, -50%);
      background: rgba(10, 132, 255, 0.13);
      border: 1px solid rgba(90, 164, 255, 0.28);
      opacity: 0;
      transition:
        width 200ms cubic-bezier(0.22, 1, 0.36, 1),
        height 200ms cubic-bezier(0.22, 1, 0.36, 1),
        opacity 220ms ease;
    }

    .sr-current-location-marker[data-accuracy-visible="true"] .sr-current-location-marker__accuracy {
      opacity: 1;
    }

    /* Gentle breathing halo emanating from behind the dot — the live-location
       "aliveness" pulse. It starts occluded by the core and expands outward. */
    .sr-current-location-marker__pulse {
      width: 20px;
      height: 20px;
      transform: translate(-50%, -50%);
      background: rgba(10, 132, 255, 0.34);
      animation: srCurrentLocationPulse 2.6s cubic-bezier(0.33, 0, 0.2, 1) infinite;
    }

    /* White ring + drop shadow lifting the dot off the map. */
    .sr-current-location-marker__core {
      width: 20px;
      height: 20px;
      transform: translate(-50%, -50%);
      background: #fff;
      box-shadow:
        0 0 0 0.5px rgba(0, 0, 0, 0.12),
        0 1px 3px rgba(0, 0, 0, 0.35),
        0 3px 8px rgba(0, 0, 0, 0.28);
    }

    /* Solid iOS-blue dot with a soft same-color glow for depth. */
    .sr-current-location-marker__fill {
      width: 14px;
      height: 14px;
      transform: translate(-50%, -50%);
      background: var(--sr-current-location-blue);
      box-shadow: 0 0 6px rgba(10, 132, 255, 0.5);
    }

    @keyframes srCurrentLocationPulse {
      0% {
        transform: translate(-50%, -50%) scale(1);
        opacity: 0.5;
      }
      70% {
        opacity: 0;
      }
      100% {
        transform: translate(-50%, -50%) scale(2.9);
        opacity: 0;
      }
    }

    @media (prefers-reduced-motion: reduce) {
      .sr-current-location-marker__pulse {
        animation: none;
        opacity: 0;
      }
    }
  `;
  document.head.appendChild(style);
}

function metersPerPixelAtLatitude(lat: number, zoom: number): number {
  const latitude = clamp(lat, -85, 85);
  const latitudeRadians = (latitude * Math.PI) / 180;
  return (
    (Math.cos(latitudeRadians) * EARTH_CIRCUMFERENCE_METERS) /
    (TILE_SIZE * 2 ** zoom)
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function createDestinationPin(): HTMLDivElement {
  const el = document.createElement("div");
  el.style.cssText = [
    "width:34px",
    "height:31px",
    "position:relative",
    "overflow:visible",
    "pointer-events:none",
    "filter:drop-shadow(0 5px 10px rgba(0,0,0,0.42))",
  ].join(";");
  el.innerHTML = `
    <svg width="34" height="31" viewBox="0 0 34 31" fill="none" aria-hidden="true" style="display:block">
      <path
        d="M17 3.8c-5.6 0-10.12 4.34-10.12 9.72 0 6.68 8.18 15.7 9.25 16.84a1.16 1.16 0 0 0 1.74 0c1.07-1.14 9.25-10.16 9.25-16.84C27.12 8.14 22.6 3.8 17 3.8Z"
        fill="#ef3b5d"
      />
      <path
        d="M17 3.8c-5.6 0-10.12 4.34-10.12 9.72 0 6.68 8.18 15.7 9.25 16.84a1.16 1.16 0 0 0 1.74 0c1.07-1.14 9.25-10.16 9.25-16.84C27.12 8.14 22.6 3.8 17 3.8Z"
        stroke="rgba(255,255,255,0.72)"
        stroke-width="1.15"
      />
      <circle cx="17" cy="13.3" r="3.4" fill="#0d1117" />
      <circle cx="17" cy="13.3" r="1.35" fill="rgba(255,255,255,0.9)" />
    </svg>
  `;
  return el;
}

/** A neutral intermediate-destination treatment for canonical waypoints. */
export function createWaypointMarker(label: string, dwellMinutes?: number): HTMLDivElement {
  const el = document.createElement("div");
  el.className = "sr-route-waypoint-marker";
  el.style.cssText = [
    "display:flex",
    "align-items:center",
    "gap:7px",
    "pointer-events:none",
    "font:600 12px/1.15 Inter,ui-sans-serif,system-ui,sans-serif",
    "color:#f8fafc",
    "text-shadow:0 1px 4px rgba(0,0,0,.7)",
  ].join(";");
  el.setAttribute("role", "img");
  el.setAttribute(
    "aria-label",
    dwellMinutes && dwellMinutes > 0
      ? `${label}, ${dwellMinutes} minute stop`
      : `Waypoint: ${label}`,
  );
  el.innerHTML = `
    <span class="sr-route-waypoint-marker__dot" aria-hidden="true" style="width:16px;height:16px;box-sizing:border-box;border:3px solid #f8fafc;border-radius:999px;background:#111827;box-shadow:0 0 0 3px rgba(17,24,39,.55),0 3px 8px rgba(0,0,0,.35)"></span>
    <span class="sr-route-waypoint-marker__label" style="display:grid;gap:1px;white-space:nowrap">${escapeMarkerLabel(label)}${
      dwellMinutes && dwellMinutes > 0
        ? `<small style="font:500 10px/1.15 Inter,ui-sans-serif,system-ui,sans-serif;color:#d1d5db">${Math.round(dwellMinutes)} min stop</small>`
        : ""
    }</span>
  `;
  return el;
}

function escapeMarkerLabel(value: string) {
  return value.replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char] ?? char);
}
