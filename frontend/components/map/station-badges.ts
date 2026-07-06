import maplibregl from "maplibre-gl";
import { interpolateAlongLine } from "./route-stops-features";
import { subwayBulletSrc } from "../smart-route/train-bullet";

/** Add a station badge marker (line bullet + station name).
 *  Subway lines show the official MTA roundel; buses keep a small colored
 *  chip. badgeIndex alternates anchor between top/bottom to avoid overlap. */
export function addStationBadge(
  m: maplibregl.Map,
  coords: [number, number],
  name: string,
  lineLetter: string,
  lineColor: string,
  badgeIndex: number = 0,
  isSubway: boolean = true,
): maplibregl.Marker {
  const el = document.createElement("div");
  // Neutral chip — avoids the legacy cyan HUD border so the custom 2D
  // basemap reads cleanly. ~12% fewer pixels per chip vs. old design.
  // Liquid glass, matching the left rail's recipe (blur 16 / saturate, a warm
  // translucent fill with a top-left refraction, a 1px specular top inset, and
  // a soft drop shadow) so the on-map badges read as the same material as the
  // rail cards. The rail's --sr-glass-* tokens are scoped under .sr-rail, so
  // the values are baked here.
  el.style.cssText = `
    display: flex;
    align-items: center;
    gap: 5px;
    background:
      radial-gradient(120% 80% at 16% -10%, rgba(255, 255, 255, 0.13), transparent 44%),
      linear-gradient(180deg, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0.028)),
      rgba(10, 14, 22, 0.74);
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 999px;
    padding: 3px 9px 3px 4px;
    font-size: 10px;
    font-weight: 620;
    letter-spacing: 0.02em;
    white-space: nowrap;
    pointer-events: none;
    font-family: var(--font-archivo), "Helvetica Now", Helvetica, Arial, sans-serif;
    box-shadow:
      inset 0 1px 0 rgba(255, 255, 255, 0.2),
      inset 0 -1px 0 rgba(255, 255, 255, 0.035),
      0 8px 18px rgba(0, 0, 0, 0.28);
    backdrop-filter: blur(10px) saturate(1.15);
    -webkit-backdrop-filter: blur(10px) saturate(1.15);
  `;
  // Subway: official MTA bullet SVG. Bus: a compact colored route chip.
  const bullet = isSubway
    ? `<img src="${subwayBulletSrc(lineLetter)}" alt="" style="width:15px;height:15px;display:block;flex-shrink:0;" />`
    : `<span style="background:${lineColor};color:#fff;font-weight:700;font-size:9px;line-height:1;padding:2px 4px;border-radius:3px;flex-shrink:0;">${lineLetter}</span>`;
  el.innerHTML = `${bullet}<span style="color:rgba(255,255,255,0.86)">${name}</span>`;

  // Offset clears the stop dot at the same coordinate so the chip sits beside
  // it rather than on top of it.
  const anchor = badgeIndex % 2 === 0 ? "bottom" : "top";
  const yOffset = anchor === "bottom" ? -11 : 11;

  return new maplibregl.Marker({ element: el, anchor, offset: [0, yOffset] })
    .setLngLat(coords)
    .addTo(m);
}

/** Add intermediate stop dot markers with labels along a transit polyline.
 *  Density is capped: labels only appear for a small evenly-spaced sample
 *  of inner stops (max 3) so the 2D map stays legible. Unlabeled inner
 *  stops render as subtle dots only. */
function addIntermediateStopLabels(
  m: maplibregl.Map,
  coords: [number, number][],
  stopNames: string[],
  lineColor: string,
): maplibregl.Marker[] {
  const markers: maplibregl.Marker[] = [];
  if (!stopNames || stopNames.length < 3 || coords.length < 2) return markers;

  const inner = stopNames.slice(1, -1);
  const totalStops = stopNames.length - 1;

  // Pick up to 3 evenly-spaced indices from `inner` to actually label.
  const MAX_LABELS = 3;
  const labelIdxSet = new Set<number>();
  if (inner.length <= MAX_LABELS) {
    for (let i = 0; i < inner.length; i++) labelIdxSet.add(i);
  } else {
    for (let k = 0; k < MAX_LABELS; k++) {
      const idx =
        Math.round(((k + 1) * (inner.length + 1)) / (MAX_LABELS + 1)) - 1;
      labelIdxSet.add(Math.max(0, Math.min(inner.length - 1, idx)));
    }
  }

  for (let i = 0; i < inner.length; i++) {
    const progress = (i + 1) / totalStops;
    const coord = interpolateAlongLine(coords, progress);
    const showLabel = labelIdxSet.has(i);
    const anchor = i % 2 === 0 ? "left" : "right";

    const el = document.createElement("div");
    el.style.cssText = `
      display: flex;
      align-items: center;
      gap: 5px;
      pointer-events: none;
      flex-direction: ${anchor === "right" ? "row-reverse" : "row"};
    `;

    const dot = document.createElement("div");
    const dotSize = showLabel ? 7 : 4;
    dot.style.cssText = `
      width: ${dotSize}px; height: ${dotSize}px; border-radius: 50%; flex-shrink: 0;
      background: ${lineColor};
      border: ${showLabel ? "1.5px solid rgba(255, 255, 255, 0.7)" : "1px solid rgba(255, 255, 255, 0.45)"};
      box-shadow: ${showLabel ? `0 0 5px ${lineColor}aa` : "none"};
      opacity: ${showLabel ? 1 : 0.8};
    `;

    const label = document.createElement("span");
    if (showLabel) {
      label.textContent = inner[i];
      label.style.cssText = `
        font-family: var(--font-geist), system-ui, sans-serif;
        font-size: 10.5px;
        color: rgba(255, 255, 255, 0.88);
        white-space: nowrap;
        letter-spacing: 0.02em;
        text-shadow: 0 1px 3px rgba(0, 0, 0, 0.8);
        background: rgba(10, 13, 19, 0.62);
        padding: 2px 6px;
        border-radius: 4px;
        border: 1px solid rgba(255,255,255,0.06);
      `;
    }

    el.appendChild(dot);
    if (showLabel) el.appendChild(label);

    const xOffset = anchor === "right" ? -4 : 4;
    const mk = new maplibregl.Marker({
      element: el,
      anchor,
      offset: [xOffset, 0],
    })
      .setLngLat(coord)
      .addTo(m);
    markers.push(mk);
  }
  return markers;
}

/** Remove all markers in the array and clear it */
export function clearBadges(markers: maplibregl.Marker[]) {
  markers.forEach((mk) => mk.remove());
  markers.length = 0;
}
