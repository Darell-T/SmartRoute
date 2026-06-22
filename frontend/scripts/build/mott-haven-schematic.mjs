import { hermiteBetween } from "./offset-bow.mjs";

const M_PER_DEG_LAT = 110574;

function metersPerDegLng(lat) {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

export function distanceMeters([lon1, lat1], [lon2, lat2]) {
  const r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r;
  const dLon = (lon2 - lon1) * r;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(a));
}

function addMeters(point, eastM, northM) {
  const k = metersPerDegLng(point[1]);
  return [point[0] + eastM / k, point[1] + northM / M_PER_DEG_LAT];
}

function addVectorMeters(point, [eastM, northM]) {
  return addMeters(point, eastM, northM);
}

function normalize([x, y], fallback = [1, 0]) {
  const len = Math.hypot(x, y);
  return len > 1e-9 ? [x / len, y / len] : fallback;
}

function projectAtLat(point, lat0) {
  return [point[0] * metersPerDegLng(lat0), point[1] * M_PER_DEG_LAT];
}

function unprojectAtLat(point, lat0) {
  return [point[0] / metersPerDegLng(lat0), point[1] / M_PER_DEG_LAT];
}

function vectorMeters(a, b) {
  const k = metersPerDegLng((a[1] + b[1]) / 2);
  return [(b[0] - a[0]) * k, (b[1] - a[1]) * M_PER_DEG_LAT];
}

function tangentAt(coords, index, forward = true) {
  if (!Array.isArray(coords) || coords.length < 2) return [0, -1];
  const a = coords[Math.max(0, Math.min(coords.length - 1, forward ? index : index - 1))];
  const b = coords[Math.max(0, Math.min(coords.length - 1, forward ? index + 1 : index))];
  const k = metersPerDegLng(((a?.[1] ?? 40.8) + (b?.[1] ?? 40.8)) / 2);
  return normalize([(b[0] - a[0]) * k, (b[1] - a[1]) * M_PER_DEG_LAT], [0, -1]);
}

function pointAtDistance(coords, targetM) {
  return pointAtDistanceWithContext(coords, targetM)?.point ?? null;
}

function pointAtDistanceWithContext(coords, targetM) {
  if (!Array.isArray(coords) || coords.length === 0) return null;
  if (targetM <= 0) return { point: coords[0], segmentIndex: 0, t: 0 };
  let acc = 0;
  for (let i = 1; i < coords.length; i += 1) {
    const a = coords[i - 1];
    const b = coords[i];
    const seg = distanceMeters(a, b);
    if (acc + seg >= targetM) {
      const t = seg > 0 ? (targetM - acc) / seg : 0;
      return {
        point: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t],
        segmentIndex: i - 1,
        t,
      };
    }
    acc += seg;
  }
  return {
    point: coords.at(-1),
    segmentIndex: Math.max(0, coords.length - 2),
    t: 1,
  };
}

function lineFromPointOnSegment(coords, segmentIndex, t) {
  const index = Math.max(0, Math.min(coords.length - 2, segmentIndex));
  const a = coords[index];
  const b = coords[index + 1];
  const start = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  return [start, ...coords.slice(index + 1)];
}

function linearlySampleSegment(start, end, sampleM) {
  const dist = distanceMeters(start, end);
  const steps = Math.max(1, Math.ceil(dist / Math.max(1, sampleM)));
  const out = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    out.push([start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t]);
  }
  return out;
}

function cubicBezier(start, control1, control2, end, sampleM) {
  const roughLength =
    distanceMeters(start, control1) +
    distanceMeters(control1, control2) +
    distanceMeters(control2, end);
  const steps = Math.max(12, Math.ceil(roughLength / Math.max(1, sampleM)));
  const out = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const u = 1 - t;
    out.push([
      u ** 3 * start[0] + 3 * u * u * t * control1[0] + 3 * u * t * t * control2[0] + t ** 3 * end[0],
      u ** 3 * start[1] + 3 * u * u * t * control1[1] + 3 * u * t * t * control2[1] + t ** 3 * end[1],
    ]);
  }
  return out;
}

function append(out, coords) {
  for (const coord of coords) {
    if (out.length && distanceMeters(out.at(-1), coord) < 0.25) continue;
    out.push(coord);
  }
}

function smoothWaypointPath(points, startTangent, endTangent, sampleM) {
  if (points.length < 2) return points;
  const tangents = points.map((point, index) => {
    if (index === 0) return startTangent;
    if (index === points.length - 1) return endTangent;
    return normalize(vectorMeters(points[index - 1], points[index + 1]), startTangent);
  });
  const out = [points[0]];
  for (let i = 0; i < points.length - 1; i += 1) {
    append(out, hermiteBetween(points[i], points[i + 1], tangents[i], tangents[i + 1], {
      handleFrac: 0.34,
      sampleM,
    }).slice(1));
  }
  return out;
}

function nearestIndex(coords, point) {
  let best = 0;
  let bestDistance = Infinity;
  coords.forEach((coord, index) => {
    const d = distanceMeters(coord, point);
    if (d < bestDistance) {
      best = index;
      bestDistance = d;
    }
  });
  return { index: best, distanceM: bestDistance };
}

function maxTurnDegrees(coords) {
  let maxTurn = 0;
  for (let i = 1; i < coords.length - 1; i += 1) {
    const a = coords[i - 1];
    const b = coords[i];
    const c = coords[i + 1];
    const k = metersPerDegLng(b[1]);
    const v1 = normalize([(b[0] - a[0]) * k, (b[1] - a[1]) * M_PER_DEG_LAT]);
    const v2 = normalize([(c[0] - b[0]) * k, (c[1] - b[1]) * M_PER_DEG_LAT]);
    let angle = (Math.atan2(v2[1], v2[0]) - Math.atan2(v1[1], v1[0])) * 180 / Math.PI;
    while (angle > 180) angle -= 360;
    while (angle < -180) angle += 360;
    maxTurn = Math.max(maxTurn, Math.abs(angle));
  }
  return maxTurn;
}

function distanceToPolylineM(point, coords) {
  let best = Infinity;
  for (let i = 1; i < coords.length; i += 1) {
    const a = coords[i - 1];
    const b = coords[i];
    const lat0 = (a[1] + b[1] + point[1]) / 3;
    const k = metersPerDegLng(lat0);
    const ax = a[0] * k;
    const ay = a[1] * M_PER_DEG_LAT;
    const bx = b[0] * k;
    const by = b[1] * M_PER_DEG_LAT;
    const px = point[0] * k;
    const py = point[1] * M_PER_DEG_LAT;
    const dx = bx - ax;
    const dy = by - ay;
    const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy || 1e-9)));
    best = Math.min(best, Math.hypot(px - (ax + dx * t), py - (ay + dy * t)));
  }
  return best;
}

function pointToSegmentClosestMeters(point, start, end) {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const len2 = dx * dx + dy * dy || 1e-9;
  const t = Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / len2));
  const closest = [start[0] + dx * t, start[1] + dy * t];
  return {
    point: closest,
    t,
    distanceM: Math.hypot(point[0] - closest[0], point[1] - closest[1]),
  };
}

function segmentIntersectionMeters(a0, a1, b0, b1) {
  const den = (a0[0] - a1[0]) * (b0[1] - b1[1]) - (a0[1] - a1[1]) * (b0[0] - b1[0]);
  if (Math.abs(den) < 1e-9) return null;
  const t =
    ((a0[0] - b0[0]) * (b0[1] - b1[1]) - (a0[1] - b0[1]) * (b0[0] - b1[0])) /
    den;
  const u =
    ((a0[0] - b0[0]) * (a0[1] - a1[1]) - (a0[1] - b0[1]) * (a0[0] - a1[0])) /
    den;
  if (t < -1e-6 || t > 1 + 1e-6 || u < -1e-6 || u > 1 + 1e-6) return null;
  return {
    point: [a0[0] + (a1[0] - a0[0]) * t, a0[1] + (a1[1] - a0[1]) * t],
    t: Math.max(0, Math.min(1, t)),
    u: Math.max(0, Math.min(1, u)),
  };
}

function findPolylineCrossingOrClosest(trunkCoords, referenceCoords) {
  if (!Array.isArray(referenceCoords) || referenceCoords.length < 2) return null;
  const lat0 =
    [...trunkCoords, ...referenceCoords].reduce((sum, coord) => sum + coord[1], 0) /
    (trunkCoords.length + referenceCoords.length);
  let best = null;

  for (let ti = 0; ti < trunkCoords.length - 1; ti += 1) {
    const ta = projectAtLat(trunkCoords[ti], lat0);
    const tb = projectAtLat(trunkCoords[ti + 1], lat0);
    for (let ri = 0; ri < referenceCoords.length - 1; ri += 1) {
      const ra = projectAtLat(referenceCoords[ri], lat0);
      const rb = projectAtLat(referenceCoords[ri + 1], lat0);
      const intersection = segmentIntersectionMeters(ta, tb, ra, rb);
      if (intersection) {
        const tangent = normalize([rb[0] - ra[0], rb[1] - ra[1]], [1, 0]);
        const eastTangent = tangent[0] >= 0 ? tangent : [-tangent[0], -tangent[1]];
        return {
          trunkPoint: unprojectAtLat(intersection.point, lat0),
          referencePoint: unprojectAtLat(intersection.point, lat0),
          trunkSegmentIndex: ti,
          trunkT: intersection.t,
          referenceDistanceM: 0,
          referenceTangentEast: eastTangent,
        };
      }

      const candidates = [];
      const aToRef = pointToSegmentClosestMeters(ta, ra, rb);
      candidates.push({
        trunkPoint: ta,
        referencePoint: aToRef.point,
        trunkT: 0,
        refT: aToRef.t,
        distanceM: aToRef.distanceM,
      });
      const bToRef = pointToSegmentClosestMeters(tb, ra, rb);
      candidates.push({
        trunkPoint: tb,
        referencePoint: bToRef.point,
        trunkT: 1,
        refT: bToRef.t,
        distanceM: bToRef.distanceM,
      });
      const rAToTrunk = pointToSegmentClosestMeters(ra, ta, tb);
      candidates.push({
        trunkPoint: rAToTrunk.point,
        referencePoint: ra,
        trunkT: rAToTrunk.t,
        refT: 0,
        distanceM: rAToTrunk.distanceM,
      });
      const rBToTrunk = pointToSegmentClosestMeters(rb, ta, tb);
      candidates.push({
        trunkPoint: rBToTrunk.point,
        referencePoint: rb,
        trunkT: rBToTrunk.t,
        refT: 1,
        distanceM: rBToTrunk.distanceM,
      });

      for (const candidate of candidates) {
        if (!best || candidate.distanceM < best.referenceDistanceM) {
          const tangent = normalize([rb[0] - ra[0], rb[1] - ra[1]], [1, 0]);
          const eastTangent = tangent[0] >= 0 ? tangent : [-tangent[0], -tangent[1]];
          best = {
            trunkPoint: unprojectAtLat(candidate.trunkPoint, lat0),
            referencePoint: unprojectAtLat(candidate.referencePoint, lat0),
            trunkSegmentIndex: ti,
            trunkT: candidate.trunkT,
            referenceDistanceM: candidate.distanceM,
            referenceTangentEast: eastTangent,
          };
        }
      }
    }
  }

  return best;
}

export function buildMottHavenFiveSchematicLens({
  branchCoords,
  trunkCoords,
  parallelReferenceCoords = null,
  parallelOffsetM = 10,
  mergeDistanceM = 310,
  sampleM = 6,
  eastEntryM = 420,
  westShoulderM = 118,
  westOuterM = 214,
  westLowerM = 160,
  shoulderSouthM = 18,
  outerSouthM = 128,
  lowerSouthM = 240,
} = {}) {
  if (!Array.isArray(branchCoords) || branchCoords.length < 2) {
    return { coordinates: branchCoords ?? [], diagnostics: { ok: false, reason: "missing_branch" } };
  }
  if (!Array.isArray(trunkCoords) || trunkCoords.length < 2) {
    return { coordinates: branchCoords, diagnostics: { ok: false, reason: "missing_trunk" } };
  }

  const referenceCrossing = findPolylineCrossingOrClosest(trunkCoords, parallelReferenceCoords);
  const hasReferenceCrossing = referenceCrossing && referenceCrossing.referenceDistanceM <= 90;
  const referenceNormalRight = hasReferenceCrossing
    ? [referenceCrossing.referenceTangentEast[1], -referenceCrossing.referenceTangentEast[0]]
    : null;
  const topBasePoint = hasReferenceCrossing ? referenceCrossing.referencePoint : trunkCoords[0];
  const topPoint = hasReferenceCrossing
    ? addVectorMeters(topBasePoint, [referenceNormalRight[0] * parallelOffsetM, referenceNormalRight[1] * parallelOffsetM])
    : trunkCoords[0];
  const trunkFromTop = hasReferenceCrossing
    ? lineFromPointOnSegment(trunkCoords, referenceCrossing.trunkSegmentIndex, referenceCrossing.trunkT)
    : trunkCoords;
  const mergePoint = pointAtDistance(trunkFromTop, mergeDistanceM);
  if (!mergePoint) {
    return { coordinates: branchCoords, diagnostics: { ok: false, reason: "missing_merge" } };
  }

  const entryPoint = hasReferenceCrossing
    ? addVectorMeters(topPoint, [
        referenceCrossing.referenceTangentEast[0] * eastEntryM,
        referenceCrossing.referenceTangentEast[1] * eastEntryM,
      ])
    : addMeters(topPoint, eastEntryM, 0);
  const westShoulder = addMeters(topPoint, -westShoulderM, -shoulderSouthM);
  const westOuter = addMeters(topPoint, -westOuterM, -outerSouthM);
  const westLower = addMeters(topPoint, -westLowerM, -lowerSouthM);

  const cut = nearestIndex(branchCoords, entryPoint);
  const prefix = branchCoords.slice(0, cut.index + 1);
  const out = prefix.slice(0, -1);

  const prefixEnd = prefix.at(-1);
  if (prefixEnd && distanceMeters(prefixEnd, entryPoint) > 2) {
    append(out, hermiteBetween(prefixEnd, entryPoint, tangentAt(branchCoords, cut.index, false), [-1, 0], {
      handleFrac: 0.18,
      sampleM,
    }));
  } else {
    append(out, [entryPoint]);
  }

  const topRun = linearlySampleSegment(entryPoint, topPoint, sampleM);
  const topApproachLatSpreadM =
    (Math.max(...topRun.map((coord) => coord[1])) - Math.min(...topRun.map((coord) => coord[1]))) *
    M_PER_DEG_LAT;
  append(out, topRun.slice(1));
  const topPointOutputIndex = out.length - 1;
  const topControl = hasReferenceCrossing
    ? addVectorMeters(topPoint, [
        -referenceCrossing.referenceTangentEast[0] * 255,
        -referenceCrossing.referenceTangentEast[1] * 255,
      ])
    : addMeters(topPoint, -255, -2);
  const mergeControl = addMeters(mergePoint, -235, 88);
  append(out, cubicBezier(topPoint, topControl, mergeControl, mergePoint, sampleM).slice(1));

  const bowCoords = out.slice(topPointOutputIndex);
  const schematicCoords = out.slice(Math.max(0, topPointOutputIndex - 2));
  const maxTrunkDistanceM = Math.max(...bowCoords.map((coord) => distanceToPolylineM(coord, trunkCoords)));
  const mergeDistance = distanceMeters(out.at(-1), mergePoint);

  return {
    coordinates: out,
    diagnostics: {
      ok: true,
      entryPoint,
      topPoint,
      westShoulder,
      westOuter,
      westLower,
      topControl,
      mergeControl,
      mergePoint,
      prefixCutIndex: cut.index,
      prefixCutDistanceM: cut.distanceM,
      topApproachLatSpreadM,
      maxTrunkDistanceM,
      mergeDistanceM: mergeDistance,
      minLon: Math.min(...out.map((coord) => coord[0])),
      maxTurnDeg: maxTurnDegrees(schematicCoords),
      parallelReferenceDistanceM: referenceCrossing?.referenceDistanceM ?? null,
      parallelReferenceUsed: Boolean(hasReferenceCrossing),
    },
  };
}

export function buildMottHavenSixSchematicMerge({
  branchCoords,
  mainlineCoords,
  mergeDistanceM = 620,
  entryEastM = 430,
  entryNorthM = 120,
  sampleM = 6,
} = {}) {
  if (!Array.isArray(branchCoords) || branchCoords.length < 2) {
    return { coordinates: branchCoords ?? [], sharedMainlineCoords: [], diagnostics: { ok: false, reason: "missing_branch" } };
  }
  if (!Array.isArray(mainlineCoords) || mainlineCoords.length < 2) {
    return { coordinates: branchCoords, sharedMainlineCoords: [], diagnostics: { ok: false, reason: "missing_mainline" } };
  }

  const merge = pointAtDistanceWithContext(mainlineCoords, mergeDistanceM);
  if (!merge) {
    return { coordinates: branchCoords, sharedMainlineCoords: [], diagnostics: { ok: false, reason: "missing_merge" } };
  }

  const mergePoint = merge.point;
  const entryTarget = addMeters(mergePoint, entryEastM, entryNorthM);
  const cut = nearestIndex(branchCoords, entryTarget);
  const prefix = branchCoords.slice(0, cut.index + 1);
  const out = [...prefix];
  const prefixEnd = out.at(-1);
  const sharedMainlineCoords = lineFromPointOnSegment(mainlineCoords, merge.segmentIndex, merge.t);
  const endTangent = tangentAt(sharedMainlineCoords, 0, true);
  const startTangent = tangentAt(branchCoords, cut.index, true);

  if (prefixEnd && distanceMeters(prefixEnd, mergePoint) > 1) {
    append(out, hermiteBetween(prefixEnd, mergePoint, startTangent, endTangent, {
      handleFrac: 0.42,
      sampleM,
    }).slice(1));
  } else {
    append(out, [mergePoint]);
  }

  return {
    coordinates: out,
    sharedMainlineCoords,
    diagnostics: {
      ok: true,
      entryTarget,
      entryPoint: prefixEnd,
      mergePoint,
      cutIndex: cut.index,
      prefixCutDistanceM: cut.distanceM,
      mergeDistanceM: distanceMeters(out.at(-1), mergePoint),
      maxTurnDeg: maxTurnDegrees(out.slice(Math.max(0, cut.index - 2))),
      minLon: Math.min(...out.map((coord) => coord[0])),
    },
  };
}
