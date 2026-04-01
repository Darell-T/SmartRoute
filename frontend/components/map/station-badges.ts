import mapboxgl from "mapbox-gl";

/** Interpolate a position along a coordinate array given progress 0..1 */
function interpolateAlongLine(coords: [number, number][], progress: number): [number, number] {
  if (coords.length < 2) return coords[0] || [0, 0];
  const segLens: number[] = [];
  let total = 0;
  for (let i = 1; i < coords.length; i++) {
    const dx = coords[i][0] - coords[i - 1][0];
    const dy = coords[i][1] - coords[i - 1][1];
    const d = Math.sqrt(dx * dx + dy * dy);
    segLens.push(d);
    total += d;
  }
  if (total === 0) return coords[0];
  const targetDist = progress * total;
  let traveled = 0;
  for (let i = 0; i < segLens.length; i++) {
    if (traveled + segLens[i] >= targetDist) {
      const t = (targetDist - traveled) / segLens[i];
      return [
        coords[i][0] + t * (coords[i + 1][0] - coords[i][0]),
        coords[i][1] + t * (coords[i + 1][1] - coords[i][1]),
      ];
    }
    traveled += segLens[i];
  }
  return coords[coords.length - 1];
}

/** Add a station badge marker (line letter + station name).
 *  badgeIndex alternates anchor between top/bottom to avoid overlap. */
export function addStationBadge(
  m: mapboxgl.Map,
  coords: [number, number],
  name: string,
  lineLetter: string,
  lineColor: string,
  badgeIndex: number = 0,
): mapboxgl.Marker {
  const el = document.createElement("div");
  el.style.cssText = `
    display: flex;
    align-items: center;
    gap: 6px;
    background: rgba(8, 10, 18, 0.6);
    border: 1px solid rgba(0, 212, 255, 0.12);
    border-radius: 12px;
    padding: 3px 8px;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.05em;
    white-space: nowrap;
    pointer-events: none;
    font-family: var(--font-geist-mono), 'Geist Mono', monospace;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
  `;
  el.innerHTML = `<span style="font-weight:700;color:${lineColor}">${lineLetter}</span><span style="color:rgba(255,255,255,0.85)">${name}</span>`;

  const anchor = badgeIndex % 2 === 0 ? "bottom" : "top";
  const yOffset = anchor === "bottom" ? -6 : 6;

  return new mapboxgl.Marker({ element: el, anchor, offset: [0, yOffset] })
    .setLngLat(coords)
    .addTo(m);
}

/** Add intermediate stop dot markers with labels along a transit polyline */
export function addIntermediateStopLabels(
  m: mapboxgl.Map,
  coords: [number, number][],
  stopNames: string[],
  lineColor: string,
): mapboxgl.Marker[] {
  const markers: mapboxgl.Marker[] = [];
  if (!stopNames || stopNames.length < 3 || coords.length < 2) return markers;

  const inner = stopNames.slice(1, -1);
  const totalStops = stopNames.length - 1;

  for (let i = 0; i < inner.length; i++) {
    // Evenly distribute stops by distance along the polyline
    const progress = (i + 1) / totalStops;
    const coord = interpolateAlongLine(coords, progress);
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
    dot.style.cssText = `
      width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
      background: ${lineColor};
      border: 1.5px solid rgba(255, 255, 255, 0.7);
      box-shadow: 0 0 6px ${lineColor}, 0 0 12px rgba(0, 0, 0, 0.5);
    `;

    const label = document.createElement("span");
    label.textContent = inner[i];
    label.style.cssText = `
      font-family: var(--font-geist-mono), 'Geist Mono', monospace;
      font-size: 10px;
      color: rgba(255, 255, 255, 0.8);
      white-space: nowrap;
      letter-spacing: 0.02em;
      text-shadow: 0 0 4px rgba(0, 0, 0, 0.9), 0 1px 2px rgba(0, 0, 0, 0.7);
      background: rgba(8, 10, 18, 0.55);
      padding: 1px 5px;
      border-radius: 4px;
    `;

    el.appendChild(dot);
    el.appendChild(label);

    const xOffset = anchor === "right" ? -4 : 4;
    const mk = new mapboxgl.Marker({ element: el, anchor, offset: [xOffset, 0] })
      .setLngLat(coord)
      .addTo(m);
    markers.push(mk);
  }
  return markers;
}

/** Remove all markers in the array and clear it */
export function clearBadges(markers: mapboxgl.Marker[]) {
  markers.forEach((mk) => mk.remove());
  markers.length = 0;
}
