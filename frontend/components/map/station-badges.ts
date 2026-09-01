import maplibregl from "maplibre-gl";
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
  // A neutral border keeps the badge legible without competing with official
  // line colors or the custom basemap.
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

export function clearBadges(markers: maplibregl.Marker[]) {
  markers.forEach((mk) => mk.remove());
  markers.length = 0;
}
