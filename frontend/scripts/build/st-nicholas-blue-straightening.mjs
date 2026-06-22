const M_PER_DEG_LAT = 110574;
const BLUE = "#0A84FF";
const ORANGE = "#FF6319";

const DEFAULT_BBOX = {
  minLon: -73.9495,
  maxLon: -73.9325,
  minLat: 40.8180,
  maxLat: 40.8395,
};

const DEFAULT_ST_NICHOLAS_BLUE_SPINE = [
  [-73.944216, 40.824783], // 145 St A/C/B/D
  [-73.941514, 40.830518], // 155 St A/C
  [-73.939892, 40.836013], // 163 St-Amsterdam Av A/C
];

function metersPerDegLng(lat) {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function projectAt(point, lat) {
  return [point[0] * metersPerDegLng(lat), point[1] * M_PER_DEG_LAT];
}

function unprojectAt(point, lat) {
  return [point[0] / metersPerDegLng(lat), point[1] / M_PER_DEG_LAT];
}

function distanceM(a, b) {
  const lat = (a[1] + b[1]) / 2;
  const pa = projectAt(a, lat);
  const pb = projectAt(b, lat);
  return Math.hypot(pb[0] - pa[0], pb[1] - pa[1]);
}

function expandBBox(bbox, marginM) {
  const lat = (bbox.minLat + bbox.maxLat) / 2;
  const lonMargin = marginM / metersPerDegLng(lat);
  const latMargin = marginM / M_PER_DEG_LAT;
  return {
    minLon: bbox.minLon - lonMargin,
    maxLon: bbox.maxLon + lonMargin,
    minLat: bbox.minLat - latMargin,
    maxLat: bbox.maxLat + latMargin,
  };
}

function inBBox(point, bbox) {
  return (
    point[0] >= bbox.minLon &&
    point[0] <= bbox.maxLon &&
    point[1] >= bbox.minLat &&
    point[1] <= bbox.maxLat
  );
}

function isLineFeature(feature) {
  return (
    feature?.geometry?.type === "LineString" &&
    Array.isArray(feature.geometry.coordinates) &&
    feature.geometry.coordinates.length >= 2
  );
}

function routeIdsOf(feature) {
  const props = feature.properties ?? {};
  return Array.from(new Set([
    ...(Array.isArray(props.route_ids) ? props.route_ids : []),
    ...(Array.isArray(props.color_route_ids) ? props.color_route_ids : []),
    props.route_id,
  ].filter(Boolean).map(String)));
}

function isTargetBlueFeature(feature, bbox) {
  if (!isLineFeature(feature)) return false;
  const color = String(feature.properties?.color ?? "").toUpperCase();
  const routes = routeIdsOf(feature);
  return (
    color === BLUE.toUpperCase() &&
    (routes.includes("A") || routes.includes("C")) &&
    feature.geometry.coordinates.some((coord) => inBBox(coord, bbox))
  );
}

function isReferenceOrangeFeature(feature, bbox) {
  if (!isLineFeature(feature)) return false;
  const color = String(feature.properties?.color ?? "").toUpperCase();
  const routes = routeIdsOf(feature);
  return (
    color === ORANGE.toUpperCase() &&
    (routes.includes("B") || routes.includes("D")) &&
    feature.geometry.coordinates.some((coord) => inBBox(coord, bbox))
  );
}

function fitAxis(points, refLat) {
  const projected = points.map((point) => projectAt(point, refLat));
  const centroid = projected.reduce(
    (sum, point) => [sum[0] + point[0], sum[1] + point[1]],
    [0, 0],
  ).map((value) => value / projected.length);

  let xx = 0;
  let xy = 0;
  let yy = 0;
  for (const point of projected) {
    const dx = point[0] - centroid[0];
    const dy = point[1] - centroid[1];
    xx += dx * dx;
    xy += dx * dy;
    yy += dy * dy;
  }

  const angle = 0.5 * Math.atan2(2 * xy, xx - yy);
  let direction = [Math.cos(angle), Math.sin(angle)];
  // Keep the axis roughly north/south for stable diagnostics and output.
  if (Math.abs(direction[1]) < Math.abs(direction[0])) {
    direction = [-direction[1], direction[0]];
  }
  if (direction[1] < 0) direction = [-direction[0], -direction[1]];

  return { centroid, direction };
}

function axisWithDirectionThroughPoints(direction, points, refLat) {
  const projected = points.map((point) => projectAt(point, refLat));
  const centroid = projected.reduce(
    (sum, point) => [sum[0] + point[0], sum[1] + point[1]],
    [0, 0],
  ).map((value) => value / projected.length);
  return { centroid, direction };
}

function median(values) {
  if (values.length === 0) return null;
  const sorted = values.slice().sort((left, right) => left - right);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

function axisParallelToReference(referenceAxis, points, refLat, maxReferenceDistanceM) {
  const normal = [-referenceAxis.direction[1], referenceAxis.direction[0]];
  const offsets = [];
  for (const point of points) {
    const p = projectAt(point, refLat);
    const dx = p[0] - referenceAxis.centroid[0];
    const dy = p[1] - referenceAxis.centroid[1];
    const signedOffset = dx * normal[0] + dy * normal[1];
    if (Math.abs(signedOffset) <= maxReferenceDistanceM) {
      offsets.push(signedOffset);
    }
  }
  const selectedOffset = median(offsets);
  if (selectedOffset === null) {
    return {
      axis: axisWithDirectionThroughPoints(referenceAxis.direction, points, refLat),
      selectedOffsetCount: 0,
    };
  }
  return {
    axis: {
      centroid: [
        referenceAxis.centroid[0] + normal[0] * selectedOffset,
        referenceAxis.centroid[1] + normal[1] * selectedOffset,
      ],
      direction: referenceAxis.direction,
    },
    selectedOffsetCount: offsets.length,
  };
}

function projectPointToAxis(point, axis, refLat) {
  const p = projectAt(point, refLat);
  const dx = p[0] - axis.centroid[0];
  const dy = p[1] - axis.centroid[1];
  const along = dx * axis.direction[0] + dy * axis.direction[1];
  return unprojectAt([
    axis.centroid[0] + axis.direction[0] * along,
    axis.centroid[1] + axis.direction[1] * along,
  ], refLat);
}

function distanceToAxisM(point, axis, refLat) {
  const p = projectAt(point, refLat);
  const dx = p[0] - axis.centroid[0];
  const dy = p[1] - axis.centroid[1];
  const along = dx * axis.direction[0] + dy * axis.direction[1];
  const projected = [
    axis.centroid[0] + axis.direction[0] * along,
    axis.centroid[1] + axis.direction[1] * along,
  ];
  return Math.hypot(p[0] - projected[0], p[1] - projected[1]);
}

function removeAdjacentDuplicates(coords) {
  const output = [];
  for (const coord of coords) {
    if (output.length === 0 || distanceM(output[output.length - 1], coord) > 0.02) {
      output.push(coord);
    }
  }
  return output;
}

function sampleLine(start, end, spacingM) {
  const length = distanceM(start, end);
  const steps = Math.max(1, Math.ceil(length / spacingM));
  const output = [];
  for (let index = 0; index <= steps; index += 1) {
    const t = index / steps;
    output.push([
      start[0] + (end[0] - start[0]) * t,
      start[1] + (end[1] - start[1]) * t,
    ]);
  }
  return output;
}

function buildPolylineSpine(coordinates, refLat) {
  if (!Array.isArray(coordinates) || coordinates.length < 2) return null;
  const points = coordinates.map((coord) => projectAt(coord, refLat));
  const cumulative = [0];
  for (let index = 1; index < points.length; index += 1) {
    cumulative.push(
      cumulative[index - 1] + Math.hypot(
        points[index][0] - points[index - 1][0],
        points[index][1] - points[index - 1][1],
      ),
    );
  }
  return {
    coordinates,
    points,
    cumulative,
    length: cumulative[cumulative.length - 1],
  };
}

function projectPointToPolyline(point, spine, refLat) {
  const p = projectAt(point, refLat);
  let best = {
    distance: Infinity,
    along: 0,
    point: spine.points[0],
  };

  for (let index = 0; index < spine.points.length - 1; index += 1) {
    const a = spine.points[index];
    const b = spine.points[index + 1];
    const vx = b[0] - a[0];
    const vy = b[1] - a[1];
    const denom = vx * vx + vy * vy || 1;
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / denom));
    const projected = [a[0] + vx * t, a[1] + vy * t];
    const distance = Math.hypot(p[0] - projected[0], p[1] - projected[1]);
    if (distance < best.distance) {
      best = {
        distance,
        along: spine.cumulative[index] + Math.hypot(projected[0] - a[0], projected[1] - a[1]),
        point: projected,
      };
    }
  }

  return {
    ...best,
    coord: unprojectAt(best.point, refLat),
  };
}

function samplePolylineAt(spine, along) {
  const clamped = Math.max(0, Math.min(spine.length, along));
  for (let index = 0; index < spine.points.length - 1; index += 1) {
    const startAlong = spine.cumulative[index];
    const endAlong = spine.cumulative[index + 1];
    if (clamped <= endAlong || index === spine.points.length - 2) {
      const span = endAlong - startAlong || 1;
      const t = Math.max(0, Math.min(1, (clamped - startAlong) / span));
      const a = spine.points[index];
      const b = spine.points[index + 1];
      return [
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
      ];
    }
  }
  return spine.points[spine.points.length - 1];
}

function samplePolylineBetween(spine, startAlong, endAlong, refLat, spacingM) {
  const distance = Math.abs(endAlong - startAlong);
  const steps = Math.max(1, Math.ceil(distance / spacingM));
  const output = [];
  for (let index = 0; index <= steps; index += 1) {
    const t = index / steps;
    const along = startAlong + (endAlong - startAlong) * t;
    output.push(unprojectAt(samplePolylineAt(spine, along), refLat));
  }
  return output;
}

function pushCoords(output, coords) {
  for (const coord of coords) {
    if (output.length === 0 || distanceM(output[output.length - 1], coord) > 0.02) {
      output.push(coord);
    }
  }
}

function replaceRangesWithStraightSegments(coords, ranges, axis, refLat, sampleSpacingM) {
  const output = [];
  let cursor = 0;
  for (const range of ranges) {
    pushCoords(output, coords.slice(cursor, range.start));
    const start = projectPointToAxis(coords[range.start], axis, refLat);
    const end = projectPointToAxis(coords[range.end], axis, refLat);
    pushCoords(output, sampleLine(start, end, sampleSpacingM));
    cursor = range.end + 1;
  }
  pushCoords(output, coords.slice(cursor));
  return output;
}

function replaceRangesWithPolylineSegments(coords, ranges, spine, refLat, sampleSpacingM) {
  const output = [];
  let cursor = 0;
  for (const range of ranges) {
    pushCoords(output, coords.slice(cursor, range.start));
    const start = projectPointToPolyline(coords[range.start], spine, refLat);
    const end = projectPointToPolyline(coords[range.end], spine, refLat);
    pushCoords(output, samplePolylineBetween(spine, start.along, end.along, refLat, sampleSpacingM));
    cursor = range.end + 1;
  }
  pushCoords(output, coords.slice(cursor));
  return output;
}

function findProjectionRanges(coords, bbox, rangeExtensionM) {
  const ranges = [];
  let rangeStart = null;
  for (let index = 0; index < coords.length; index += 1) {
    if (inBBox(coords[index], bbox)) {
      if (rangeStart === null) rangeStart = index;
    } else if (rangeStart !== null) {
      ranges.push({ start: rangeStart, end: index - 1 });
      rangeStart = null;
    }
  }
  if (rangeStart !== null) {
    ranges.push({ start: rangeStart, end: coords.length - 1 });
  }

  const extended = ranges.map((range) => {
    let start = range.start;
    let end = range.end;
    let walked = 0;
    while (start > 0) {
      const segment = distanceM(coords[start - 1], coords[start]);
      if (walked + segment > rangeExtensionM) break;
      walked += segment;
      start -= 1;
    }

    walked = 0;
    while (end < coords.length - 1) {
      const segment = distanceM(coords[end], coords[end + 1]);
      if (walked + segment > rangeExtensionM) break;
      walked += segment;
      end += 1;
    }

    return { start, end };
  });

  const merged = [];
  for (const range of extended) {
    const previous = merged[merged.length - 1];
    if (previous && range.start <= previous.end + 1) {
      previous.end = Math.max(previous.end, range.end);
    } else {
      merged.push({ ...range });
    }
  }
  return merged;
}

function isIndexInRanges(index, ranges) {
  return ranges.some((range) => index >= range.start && index <= range.end);
}

function snapEndpointClusters(features, targetIndexes, bbox, endpointSnapM) {
  const endpoints = [];
  for (const featureIndex of targetIndexes) {
    const coords = features[featureIndex].geometry.coordinates;
    const specs = [
      { coordIndex: 0, point: coords[0] },
      { coordIndex: coords.length - 1, point: coords[coords.length - 1] },
    ];
    for (const spec of specs) {
      if (inBBox(spec.point, bbox)) {
        endpoints.push({ featureIndex, ...spec });
      }
    }
  }

  const seen = new Set();
  let snappedClusters = 0;
  for (let i = 0; i < endpoints.length; i += 1) {
    if (seen.has(i)) continue;
    const cluster = [i];
    seen.add(i);
    for (let j = i + 1; j < endpoints.length; j += 1) {
      if (seen.has(j)) continue;
      const matches = cluster.some((clusterIndex) => (
        distanceM(endpoints[clusterIndex].point, endpoints[j].point) <= endpointSnapM
      ));
      if (matches) {
        cluster.push(j);
        seen.add(j);
      }
    }
    if (cluster.length < 2) continue;

    const refLat = cluster.reduce((sum, index) => sum + endpoints[index].point[1], 0) / cluster.length;
    const projected = cluster.map((index) => projectAt(endpoints[index].point, refLat));
    const average = projected.reduce(
      (sum, point) => [sum[0] + point[0], sum[1] + point[1]],
      [0, 0],
    ).map((value) => value / projected.length);
    const snappedPoint = unprojectAt(average, refLat);
    for (const index of cluster) {
      const endpoint = endpoints[index];
      features[endpoint.featureIndex].geometry.coordinates[endpoint.coordIndex] = snappedPoint.slice();
    }
    snappedClusters += 1;
  }

  return snappedClusters;
}

export function applyStNicholasBlueStraightening(features, options = {}) {
  const bbox = options.bbox ?? DEFAULT_BBOX;
  const marginM = options.marginM ?? 25;
  const endpointSnapM = options.endpointSnapM ?? 18;
  const rangeExtensionM = options.rangeExtensionM ?? 35;
  const sampleSpacingM = options.sampleSpacingM ?? 8;
  const maxReferenceDistanceM = options.maxReferenceDistanceM ?? 150;
  const spineCoordinates = options.spineCoordinates
    ?? (options.bbox ? null : DEFAULT_ST_NICHOLAS_BLUE_SPINE);
  const effectiveBBox = expandBBox(bbox, marginM);
  const targetIndexes = [];
  const points = [];
  const referencePoints = [];
  const projectionRangesByIndex = new Map();

  for (let index = 0; index < features.length; index += 1) {
    const feature = features[index];
    if (isReferenceOrangeFeature(feature, effectiveBBox)) {
      for (const coord of feature.geometry.coordinates) {
        if (inBBox(coord, effectiveBBox)) referencePoints.push(coord);
      }
    }
    if (!isTargetBlueFeature(feature, effectiveBBox)) continue;
    targetIndexes.push(index);
    const projectionRanges = findProjectionRanges(feature.geometry.coordinates, effectiveBBox, rangeExtensionM);
    projectionRangesByIndex.set(index, projectionRanges);
    for (const coord of feature.geometry.coordinates) {
      if (inBBox(coord, effectiveBBox)) points.push(coord);
    }
  }

  if (targetIndexes.length < 2 || points.length < 4) {
    return {
      features,
      diagnostics: {
        applied: false,
        target_feature_count: targetIndexes.length,
        projected_point_count: points.length,
        snapped_endpoint_clusters: 0,
        reason: "insufficient_target_geometry",
      },
    };
  }

  const refLat = points.reduce((sum, point) => sum + point[1], 0) / points.length;
  const stationSpine = spineCoordinates ? buildPolylineSpine(spineCoordinates, refLat) : null;
  const referenceAxis = !stationSpine && referencePoints.length >= 4 ? fitAxis(referencePoints, refLat) : null;
  const axisSelection = !stationSpine && referenceAxis
    ? axisParallelToReference(referenceAxis, points, refLat, maxReferenceDistanceM)
    : { axis: fitAxis(points, refLat), selectedOffsetCount: 0 };
  const axis = axisSelection.axis;
  const maxBefore = stationSpine
    ? Math.max(...points.map((point) => projectPointToPolyline(point, stationSpine, refLat).distance))
    : Math.max(...points.map((point) => distanceToAxisM(point, axis, refLat)));
  const output = features.slice();

  for (const featureIndex of targetIndexes) {
    const feature = output[featureIndex];
    const projectionRanges = projectionRangesByIndex.get(featureIndex) ?? [];
    const coords = stationSpine
      ? replaceRangesWithPolylineSegments(
        feature.geometry.coordinates,
        projectionRanges,
        stationSpine,
        refLat,
        sampleSpacingM,
      )
      : replaceRangesWithStraightSegments(
        feature.geometry.coordinates,
        projectionRanges,
        axis,
        refLat,
        sampleSpacingM,
      );
    output[featureIndex] = {
      ...feature,
      geometry: {
        ...feature.geometry,
        coordinates: removeAdjacentDuplicates(coords),
      },
      properties: {
        ...feature.properties,
        st_nicholas_blue_straightened: true,
      },
    };
  }

  const snappedEndpointClusters = snapEndpointClusters(output, targetIndexes, effectiveBBox, endpointSnapM);
  const afterPoints = [];
  for (const featureIndex of targetIndexes) {
    for (const coord of output[featureIndex].geometry.coordinates) {
      if (inBBox(coord, effectiveBBox)) afterPoints.push(coord);
    }
  }
  const maxAfter = afterPoints.length
    ? Math.max(...afterPoints.map((point) => (
      stationSpine
        ? projectPointToPolyline(point, stationSpine, refLat).distance
        : distanceToAxisM(point, axis, refLat)
    )))
    : 0;

  for (const featureIndex of targetIndexes) {
    output[featureIndex].properties.st_nicholas_blue_endpoint_clusters = snappedEndpointClusters;
    output[featureIndex].properties.st_nicholas_blue_max_perp_before_m = Number(maxBefore.toFixed(2));
    output[featureIndex].properties.st_nicholas_blue_max_perp_after_m = Number(maxAfter.toFixed(2));
  }

  return {
    features: output,
    diagnostics: {
      applied: true,
      target_feature_count: targetIndexes.length,
      projected_point_count: points.length,
      snapped_endpoint_clusters: snappedEndpointClusters,
      reference_feature_point_count: referencePoints.length,
      reference_axis_source: stationSpine ? "station_spine" : (referenceAxis ? "orange_bd" : "blue_fit"),
      reference_offset_point_count: axisSelection.selectedOffsetCount,
      max_perpendicular_before_m: Number(maxBefore.toFixed(2)),
      max_perpendicular_after_m: Number(maxAfter.toFixed(2)),
    },
  };
}
