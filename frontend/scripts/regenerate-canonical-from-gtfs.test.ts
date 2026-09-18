import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { deflateRawSync } from "node:zlib";
import {
  buildCanonicalFeatureCollection,
  normalizeColor,
  normalizeRouteId,
  parseCsv,
  parseZipEntries,
  sha256,
  validateFeatureCollection,
} from "./regenerate-canonical-from-gtfs.ts";

test("normalizes route aliases and route colors", () => {
  assert.equal(normalizeRouteId("6D"), "6X");
  assert.equal(normalizeRouteId("7d"), "7X");
  assert.equal(normalizeRouteId("FD"), "FX");
  assert.equal(normalizeRouteId("SIR"), "SI");
  assert.equal(normalizeRouteId("fs"), "S");

  assert.equal(normalizeColor("0039a6", "A"), "#0039A6");
  assert.equal(normalizeColor("#ee352e", "1"), "#EE352E");
  assert.equal(normalizeColor("", "unknown-route"), "#A7A9AC");
});

test("parseCsv handles quoted commas, escaped quotes, and CRLF", () => {
  const rows = parseCsv('route_id,route_short_name,route_desc\r\nA,A,"8 Av, express"\r\nB,B,"He said ""go"""\r\n');
  assert.deepEqual(rows, [
    { route_id: "A", route_short_name: "A", route_desc: "8 Av, express" },
    { route_id: "B", route_short_name: "B", route_desc: 'He said "go"' },
  ]);
});

test("buildCanonicalFeatureCollection filters, sorts, dedupes, and preserves route metadata", () => {
  const collection = buildCanonicalFeatureCollection({
    routesRows: [
      { route_id: "A", route_short_name: "A", route_color: "0039A6" },
      { route_id: "6D", route_short_name: "6D", route_color: "EE352E" },
    ],
    tripsRows: [
      { route_id: "A", "shape_id": "shape-a" },
      { route_id: "A", "shape_id": "shape-a-duplicate" },
      { route_id: "6D", "shape_id": "shape-6d" },
      { route_id: "missing-shape-route", "shape_id": "" },
    ],
    polylineRows: [
      { "shape_id": "shape-a", "shape_pt_lat": "40.7000", "shape_pt_lon": "-73.9900", "shape_pt_sequence": "2" },
      { "shape_id": "shape-a", "shape_pt_lat": "40.7100", "shape_pt_lon": "-73.9800", "shape_pt_sequence": "3" },
      { "shape_id": "shape-a", "shape_pt_lat": "41.5000", "shape_pt_lon": "-73.9800", "shape_pt_sequence": "1" },
      { "shape_id": "shape-a-duplicate", "shape_pt_lat": "40.700001", "shape_pt_lon": "-73.990001", "shape_pt_sequence": "1" },
      { "shape_id": "shape-a-duplicate", "shape_pt_lat": "40.710001", "shape_pt_lon": "-73.980001", "shape_pt_sequence": "2" },
      { "shape_id": "shape-6d", "shape_pt_lat": "40.7200", "shape_pt_lon": "-73.9700", "shape_pt_sequence": "1" },
      { "shape_id": "shape-6d", "shape_pt_lat": "40.7300", "shape_pt_lon": "-73.9600", "shape_pt_sequence": "2" },
    ],
  });

  assert.equal(collection.type, "FeatureCollection");
  assert.equal(collection.metadata.dedupe.input_features, 3);
  assert.equal(collection.metadata.dedupe.dropped_features, 1);
  assert.deepEqual(
    collection.features.map((feature) => feature.properties.route_id),
    ["6X", "A"],
  );

  const express = collection.features[0];
  assert.equal(express.properties.display_route, "6X");
  assert.equal(express.properties.color, "#EE352E");
  assert.deepEqual(express.geometry.coordinates, [
    [-73.97, 40.72],
    [-73.96, 40.73],
  ]);

  const aTrain = collection.features[1];
  assert.equal(aTrain.properties.color, "#0039A6");
  assert.deepEqual(aTrain.geometry.coordinates, [
    [-73.99, 40.7],
    [-73.98, 40.71],
  ]);
});

test("validateFeatureCollection rejects incomplete route coverage before writing", () => {
  const collection = buildCanonicalFeatureCollection({
    routesRows: [{ route_id: "A", route_short_name: "A", route_color: "0039A6" }],
    tripsRows: [{ route_id: "A", "shape_id": "shape-a" }],
    polylineRows: [
      { "shape_id": "shape-a", "shape_pt_lat": "40.7000", "shape_pt_lon": "-73.9900", "shape_pt_sequence": "1" },
      { "shape_id": "shape-a", "shape_pt_lat": "40.7100", "shape_pt_lon": "-73.9800", "shape_pt_sequence": "2" },
    ],
  });

  assert.throws(
    () => validateFeatureCollection(collection),
    /Expected routes missing from canonical output:/,
  );
});

test("sha256 is deterministic for a GTFS zip buffer", () => {
  assert.equal(
    sha256(Buffer.from("fixture-gtfs-zip")),
    "7df32c220de9142cf892f76902042296007a88987ec856da757b29a337732ffd",
  );
});

function storedZip(files: Record<string, string>): Buffer {
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;
  const names = Object.keys(files);
  for (const name of names) {
    const data = Buffer.from(files[name], "utf8");
    const nameBuf = Buffer.from(name, "utf8");
    const local = Buffer.alloc(30 + nameBuf.length + data.length);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBuf.length, 26);
    nameBuf.copy(local, 30);
    data.copy(local, 30 + nameBuf.length);
    locals.push(local);

    const central = Buffer.alloc(46 + nameBuf.length);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(nameBuf.length, 28);
    central.writeUInt32LE(offset, 42);
    nameBuf.copy(central, 46);
    centrals.push(central);
    offset += local.length;
  }
  const centralDirectory = Buffer.concat(centrals);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(names.length, 8);
  eocd.writeUInt16LE(names.length, 10);
  eocd.writeUInt32LE(centralDirectory.length, 12);
  eocd.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, centralDirectory, eocd]);
}

test("parseZipEntries reads stored members and rejects a missing required file", () => {
  const zip = storedZip({
    "routes.txt": "route_id\nA\n",
    "trips.txt": "trip_id\nt1\n",
    "shapes.txt": "shape_id\ns1\n",
  });
  const entries = parseZipEntries(zip, ["routes.txt", "trips.txt", "shapes.txt"]);
  assert.equal(entries.get("routes.txt"), "route_id\nA\n");
  assert.equal(entries.get("trips.txt"), "trip_id\nt1\n");
  assert.equal(parseZipEntries(zip, ["routes.txt", "trips.txt", "shapes.txt"]).get("shapes.txt"), "shape_id\ns1\n");
  assert.throws(
    () => parseZipEntries(zip, ["routes.txt", "missing.txt"]),
    /GTFS zip did not include required file: missing.txt/,
  );
});

test("parseZipEntries rejects a buffer with no end-of-central-directory record", () => {
  assert.throws(() => parseZipEntries(Buffer.from("not-a-zip"), ["routes.txt"]), /end-of-central-directory/);
});

function deflatedZip(name: string, text: string, method = 8): Buffer {
  const data = Buffer.from(text, "utf8");
  const payload = method === 8 ? deflateRawSync(data) : data;
  const nameBuf = Buffer.from(name, "utf8");
  const local = Buffer.alloc(30 + nameBuf.length + payload.length);
  local.writeUInt32LE(0x04034b50, 0);
  local.writeUInt16LE(method, 8);
  local.writeUInt32LE(payload.length, 18);
  local.writeUInt32LE(data.length, 22);
  local.writeUInt16LE(nameBuf.length, 26);
  nameBuf.copy(local, 30);
  payload.copy(local, 30 + nameBuf.length);
  const central = Buffer.alloc(46 + nameBuf.length);
  central.writeUInt32LE(0x02014b50, 0);
  central.writeUInt16LE(method, 10);
  central.writeUInt32LE(payload.length, 20);
  central.writeUInt32LE(data.length, 24);
  central.writeUInt16LE(nameBuf.length, 28);
  nameBuf.copy(central, 46);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(1, 8);
  eocd.writeUInt16LE(1, 10);
  eocd.writeUInt32LE(central.length, 12);
  eocd.writeUInt32LE(local.length, 16);
  return Buffer.concat([local, central, eocd]);
}

test("parseZipEntries inflates deflated members and strips a UTF-8 BOM", () => {
  const zip = deflatedZip("routes.txt", "\uFEFFroute_id\nA\n");
  assert.equal(parseZipEntries(zip, ["routes.txt"]).get("routes.txt"), "route_id\nA\n");
});

test("parseZipEntries rejects unsupported compression and malformed headers", () => {
  assert.throws(
    () => parseZipEntries(deflatedZip("routes.txt", "route_id\nA\n", 99), ["routes.txt"]),
    /Unsupported ZIP compression method 99/,
  );
  const zip = storedZip({ "routes.txt": "route_id\nA\n" });
  const localSig = zip.indexOf(Buffer.from([0x50, 0x4b, 0x03, 0x04]));
  zip.writeUInt32LE(0xffffffff, localSig);
  assert.throws(() => parseZipEntries(zip, ["routes.txt"]), /Malformed ZIP local header/);
});

test("parseCsv returns no rows for empty input", () => {
  assert.deepEqual(parseCsv(""), []);
});

test("validateFeatureCollection rejects a non-collection and a non-LineString feature", () => {
  const notCollection = {
    type: "Feature",
    metadata: {
      source: "test",
      generated_at: "2026-01-01T00:00:00.000Z",
      canonical_hash_basis: "routes.txt,trips.txt,shapes.txt",
      phase: "phase-1-canonical-gtfs-regeneration",
      dedupe: { strategy: "route-plus-rounded-geometry", input_features: 0, dropped_features: 0 },
    },
    features: [],
  };
  // SAFETY: the production validator is the runtime check for a FeatureCollection tag.
  assert.throws(
    () => validateFeatureCollection(notCollection as ReturnType<typeof buildCanonicalFeatureCollection>),
    /Canonical output is not a FeatureCollection/,
  );

  const invalid = buildCanonicalFeatureCollection({
    routesRows: [{ route_id: "A", route_short_name: "A", route_color: "0039A6" }],
    tripsRows: [{ route_id: "A", "shape_id": "shape-a" }],
    polylineRows: [
      { "shape_id": "shape-a", "shape_pt_lat": "40.7000", "shape_pt_lon": "-73.9900", "shape_pt_sequence": "1" },
      { "shape_id": "shape-a", "shape_pt_lat": "40.7100", "shape_pt_lon": "-73.9800", "shape_pt_sequence": "2" },
    ],
  });
  const broken = JSON.parse(JSON.stringify(invalid));
  broken.features[0].geometry.type = "Point";
  assert.throws(() => validateFeatureCollection(broken), /Invalid canonical feature for route A/);
});

test("validateFeatureCollection rejects empty collections and invalid coordinates", () => {
  assert.throws(
    () =>
      validateFeatureCollection({
        type: "FeatureCollection",
        metadata: {
          source: "test",
          generated_at: "2026-01-01T00:00:00.000Z",
          canonical_hash_basis: "routes.txt,trips.txt,shapes.txt",
          phase: "phase-1-canonical-gtfs-regeneration",
          dedupe: { strategy: "route-plus-rounded-geometry", input_features: 0, dropped_features: 0 },
        },
        features: [],
      }),
    /Canonical output has no features/,
  );

  const invalid = buildCanonicalFeatureCollection({
    routesRows: [{ route_id: "A", route_short_name: "A", route_color: "0039A6" }],
    tripsRows: [{ route_id: "A", "shape_id": "shape-a" }],
    polylineRows: [
      { "shape_id": "shape-a", "shape_pt_lat": "40.7000", "shape_pt_lon": "-73.9900", "shape_pt_sequence": "1" },
      { "shape_id": "shape-a", "shape_pt_lat": "40.7100", "shape_pt_lon": "-73.9800", "shape_pt_sequence": "2" },
    ],
  });
  invalid.features[0].geometry.coordinates[0][0] = Number.NaN;
  assert.throws(() => validateFeatureCollection(invalid), /Invalid coordinate in A\/shape-a/);
});

const ALL_EXPECTED_ROUTES = [
  "1", "2", "3", "4", "5", "6", "6X", "7", "7X",
  "A", "B", "C", "D", "E", "F", "FX", "G", "J", "L", "M", "N", "Q", "R", "S", "SI", "W", "Z",
];

test("validateFeatureCollection rejects a bbox outside NYC and a count far from the current network", () => {
  const collection = buildCanonicalFeatureCollection({
    routesRows: ALL_EXPECTED_ROUTES.map((route) => ({
      route_id: route,
      route_short_name: route,
      route_color: "EE352E",
    })),
    tripsRows: ALL_EXPECTED_ROUTES.map((route) => ({ route_id: route, "shape_id": `shape-${route}` })),
    polylineRows: ALL_EXPECTED_ROUTES.flatMap((route) => [
      { "shape_id": `shape-${route}`, "shape_pt_lat": "40.7000", "shape_pt_lon": "-73.9900", "shape_pt_sequence": "1" },
      { "shape_id": `shape-${route}`, "shape_pt_lat": "40.7100", "shape_pt_lon": "-73.9800", "shape_pt_sequence": "2" },
    ]),
  });
  const outside = JSON.parse(JSON.stringify(collection));
  outside.features[0].geometry.coordinates[0][0] = -80;
  assert.throws(() => validateFeatureCollection(outside), /outside NYC envelope/);
  assert.throws(() => validateFeatureCollection(collection), /outside \+\/-20% of current/);
});

test("parseZipEntries rejects an uncompressed-size mismatch and a malformed central directory", () => {
  const name = "routes.txt";
  const text = "route_id\nA\n";
  const data = Buffer.from(text, "utf8");
  const nameBuf = Buffer.from(name, "utf8");
  const local = Buffer.alloc(30 + nameBuf.length + data.length);
  local.writeUInt32LE(0x04034b50, 0);
  local.writeUInt32LE(data.length, 18);
  local.writeUInt32LE(data.length + 4, 22);
  local.writeUInt16LE(nameBuf.length, 26);
  nameBuf.copy(local, 30);
  data.copy(local, 30 + nameBuf.length);
  const central = Buffer.alloc(46 + nameBuf.length);
  central.writeUInt32LE(0x02014b50, 0);
  central.writeUInt32LE(data.length, 20);
  central.writeUInt32LE(data.length + 4, 24);
  central.writeUInt16LE(nameBuf.length, 28);
  nameBuf.copy(central, 46);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(1, 8);
  eocd.writeUInt16LE(1, 10);
  eocd.writeUInt32LE(central.length, 12);
  eocd.writeUInt32LE(local.length, 16);
  assert.throws(
    () => parseZipEntries(Buffer.concat([local, central, eocd]), ["routes.txt"]),
    /Unexpected uncompressed size for routes.txt/,
  );

  const zip = storedZip({ "routes.txt": "route_id\nA\n" });
  const centralSig = zip.indexOf(Buffer.from([0x50, 0x4b, 0x01, 0x02]));
  zip.writeUInt32LE(0xffffffff, centralSig);
  assert.throws(() => parseZipEntries(zip, ["routes.txt"]), /Malformed ZIP central directory/);
});

test("parseZipEntries skips extra and comment bytes in the central directory", () => {
  const name = "routes.txt";
  const text = "route_id\nA\n";
  const data = Buffer.from(text, "utf8");
  const nameBuf = Buffer.from(name, "utf8");
  const extra = Buffer.from([0x01, 0x02]);
  const comment = Buffer.from("hi");
  const local = Buffer.alloc(30 + nameBuf.length + extra.length + data.length);
  local.writeUInt32LE(0x04034b50, 0);
  local.writeUInt32LE(data.length, 18);
  local.writeUInt32LE(data.length, 22);
  local.writeUInt16LE(nameBuf.length, 26);
  local.writeUInt16LE(extra.length, 28);
  nameBuf.copy(local, 30);
  extra.copy(local, 30 + nameBuf.length);
  data.copy(local, 30 + nameBuf.length + extra.length);
  const central = Buffer.alloc(46 + nameBuf.length + extra.length + comment.length);
  central.writeUInt32LE(0x02014b50, 0);
  central.writeUInt32LE(data.length, 20);
  central.writeUInt32LE(data.length, 24);
  central.writeUInt16LE(nameBuf.length, 28);
  central.writeUInt16LE(extra.length, 30);
  central.writeUInt16LE(comment.length, 32);
  nameBuf.copy(central, 46);
  extra.copy(central, 46 + nameBuf.length);
  comment.copy(central, 46 + nameBuf.length + extra.length);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(1, 8);
  eocd.writeUInt16LE(1, 10);
  eocd.writeUInt32LE(central.length, 12);
  eocd.writeUInt32LE(local.length, 16);
  const entries = parseZipEntries(Buffer.concat([local, central, eocd]), ["routes.txt"]);
  assert.equal(entries.get("routes.txt"), "route_id\nA\n");
});

test("parseCsv keeps a last row that has no trailing newline and fills missing columns", () => {
  assert.deepEqual(parseCsv("route_id,color\nA,#0039A6"), [
    { route_id: "A", color: "#0039A6" },
  ]);
  assert.deepEqual(parseCsv("route_id,color\nA"), [{ route_id: "A", color: "" }]);
});

test("buildCanonicalFeatureCollection keeps an unknown trip route and sorts same-route polylines", () => {
  const collection = buildCanonicalFeatureCollection({
    routesRows: [
      { route_id: "", route_short_name: "", route_color: "0039A6" },
      { route_id: "A", route_short_name: "A", route_color: "0039A6" },
    ],
    tripsRows: [
      { route_id: "", "shape_id": "shape-blank" },
      { route_id: "ZZ", "shape_id": "shape-zz" },
      { route_id: "A", "shape_id": "shape-a2" },
      { route_id: "A", "shape_id": "shape-a1" },
    ],
    polylineRows: [
      { "shape_id": "shape-zz", "shape_pt_lat": "40.7200", "shape_pt_lon": "-73.9700", "shape_pt_sequence": "1" },
      { "shape_id": "shape-zz", "shape_pt_lat": "40.7300", "shape_pt_lon": "-73.9600", "shape_pt_sequence": "2" },
      { "shape_id": "shape-a2", "shape_pt_lat": "40.7000", "shape_pt_lon": "-73.9900", "shape_pt_sequence": "1" },
      { "shape_id": "shape-a2", "shape_pt_lat": "40.7100", "shape_pt_lon": "-73.9800", "shape_pt_sequence": "2" },
      { "shape_id": "shape-a1", "shape_pt_lat": "40.7010", "shape_pt_lon": "-73.9910", "shape_pt_sequence": "1" },
      { "shape_id": "shape-a1", "shape_pt_lat": "40.7110", "shape_pt_lon": "-73.9810", "shape_pt_sequence": "2" },
      { "shape_id": "shape-out", "shape_pt_lat": "39.0000", "shape_pt_lon": "-73.9900", "shape_pt_sequence": "1" },
    ],
  });
  assert.deepEqual(
    collection.features.map((feature) => `${feature.properties.route_id}:${feature.properties["shape_id"]}`),
    ["A:shape-a1", "A:shape-a2", "ZZ:shape-zz"],
  );
  assert.equal(collection.features[2].properties.color, "#A7A9AC");
});

test("validateFeatureCollection rejects missing identity fields and a one-point line", () => {
  const base = buildCanonicalFeatureCollection({
    routesRows: [{ route_id: "A", route_short_name: "A", route_color: "0039A6" }],
    tripsRows: [{ route_id: "A", "shape_id": "shape-a" }],
    polylineRows: [
      { "shape_id": "shape-a", "shape_pt_lat": "40.7000", "shape_pt_lon": "-73.9900", "shape_pt_sequence": "1" },
      { "shape_id": "shape-a", "shape_pt_lat": "40.7100", "shape_pt_lon": "-73.9800", "shape_pt_sequence": "2" },
    ],
  });

  const noRoute = JSON.parse(JSON.stringify(base));
  noRoute.features[0].properties.route_id = "";
  assert.throws(() => validateFeatureCollection(noRoute), /Invalid canonical feature for route unknown/);

  const noPolylineId = JSON.parse(JSON.stringify(base));
  noPolylineId.features[0].properties["shape_id"] = "";
  assert.throws(() => validateFeatureCollection(noPolylineId), /Invalid canonical feature for route A/);

  const noColor = JSON.parse(JSON.stringify(base));
  noColor.features[0].properties.color = "";
  assert.throws(() => validateFeatureCollection(noColor), /Invalid canonical feature for route A/);

  const short = JSON.parse(JSON.stringify(base));
  short.features[0].geometry.coordinates = [[-73.99, 40.7]];
  assert.throws(() => validateFeatureCollection(short), /Invalid canonical feature for route A/);

  const notFeature = JSON.parse(JSON.stringify(base));
  notFeature.features[0].type = "NotFeature";
  assert.throws(() => validateFeatureCollection(notFeature), /Invalid canonical feature for route A/);
});

type GtfsTextFiles = {
  "routes.txt": string;
  "trips.txt": string;
  "shapes.txt": string;
};

function allRouteGtfsFiles(): GtfsTextFiles {
  const routeRows = ["route_id,route_short_name,route_color"];
  const tripRows = ["route_id,shape_id"];
  const polylineRows = ["shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence"];
  for (const route of ALL_EXPECTED_ROUTES) {
    routeRows.push(`${route},${route},EE352E`);
    tripRows.push(`${route},shape-${route}`);
    polylineRows.push(`shape-${route},40.7000,-73.9900,1`);
    polylineRows.push(`shape-${route},40.7100,-73.9800,2`);
  }
  return {
    "routes.txt": `${routeRows.join("\n")}\n`,
    "trips.txt": `${tripRows.join("\n")}\n`,
    "shapes.txt": `${polylineRows.join("\n")}\n`,
  };
}

function spawnCanonicalCli(root: string, extraArgs: string[] = []) {
  const frontendRoot = process.cwd();
  const tsxCli = path.join(frontendRoot, "node_modules", "tsx", "dist", "cli.mjs");
  mkdirSync(path.join(root, "scripts", "build"), { recursive: true });
  mkdirSync(path.join(root, "public"), { recursive: true });
  mkdirSync(path.join(root, ".gtfs-cache"), { recursive: true });
  mkdirSync(path.join(root, "lib"), { recursive: true });
  copyFileSync(
    path.join(frontendRoot, "scripts", "regenerate-canonical-from-gtfs.ts"),
    path.join(root, "scripts", "regenerate-canonical-from-gtfs.ts"),
  );
  copyFileSync(
    path.join(frontendRoot, "scripts", "build", "mta-colors.ts"),
    path.join(root, "scripts", "build", "mta-colors.ts"),
  );
  copyFileSync(
    path.join(frontendRoot, "scripts", "build", "types.ts"),
    path.join(root, "scripts", "build", "types.ts"),
  );
  copyFileSync(path.join(frontendRoot, "lib", "mta-colors.json"), path.join(root, "lib", "mta-colors.json"));
  return spawnSync(
    process.execPath,
    [tsxCli, path.join(root, "scripts", "regenerate-canonical-from-gtfs.ts"), ...extraArgs],
    { cwd: root, encoding: "utf8" },
  );
}

test("CLI main writes canonical GeoJSON from a cached zip in a temp tree", () => {
  const root = mkdtempSync(path.join(tmpdir(), "gtfs-cli-ok-"));
  try {
    mkdirSync(path.join(root, ".gtfs-cache"), { recursive: true });
    writeFileSync(path.join(root, ".gtfs-cache", "google_transit.zip"), storedZip(allRouteGtfsFiles()));
    const first = spawnCanonicalCli(root);
    assert.equal(first.status, 0, first.stderr || first.stdout);
    assert.match(first.stdout, /wrote /);
    assert.match(first.stdout, /canonical routes: 27/);
    const outputPath = path.join(root, "public", "subway-network.canonical.geojson");
    const doc = JSON.parse(readFileSync(outputPath, "utf8"));
    assert.equal(doc.type, "FeatureCollection");
    assert.equal(doc.features.length, 27);
    assert.equal(doc.features.find((feature: { properties: { route_id: string } }) => feature.properties.route_id === "A").properties.color, "#EE352E");
    const second = spawnCanonicalCli(root);
    assert.equal(second.status, 0, second.stderr || second.stdout);
    assert.deepEqual(JSON.parse(readFileSync(outputPath, "utf8")).features.length, 27);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("CLI main exits 1 when expected routes are missing and does not write public artifacts in the repo", () => {
  const root = mkdtempSync(path.join(tmpdir(), "gtfs-cli-fail-"));
  try {
    mkdirSync(path.join(root, ".gtfs-cache"), { recursive: true });
    writeFileSync(
      path.join(root, ".gtfs-cache", "google_transit.zip"),
      storedZip({
        "routes.txt": "route_id,route_short_name,route_color\nA,A,0039A6\n",
        "trips.txt": "route_id,shape_id\nA,shape-a\n",
        "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\nshape-a,40.7000,-73.9900,1\nshape-a,40.7100,-73.9800,2\n",
      }),
    );
    const result = spawnCanonicalCli(root);
    assert.equal(result.status, 1);
    assert.match(`${result.stderr}${result.stdout}`, /Expected routes missing from canonical output/);
    assert.throws(() => readFileSync(path.join(root, "public", "subway-network.canonical.geojson"), "utf8"), /ENOENT/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("CLI main rejects a current network count that is far from the next collection", () => {
  const root = mkdtempSync(path.join(tmpdir(), "gtfs-cli-count-"));
  try {
    mkdirSync(path.join(root, ".gtfs-cache"), { recursive: true });
    mkdirSync(path.join(root, "public"), { recursive: true });
    writeFileSync(path.join(root, ".gtfs-cache", "google_transit.zip"), storedZip(allRouteGtfsFiles()));
    writeFileSync(
      path.join(root, "public", "subway-network.geojson"),
      `${JSON.stringify({ type: "FeatureCollection", features: Array.from({ length: 400 }, () => ({ type: "Feature" })) })}\n`,
    );
    const result = spawnCanonicalCli(root);
    assert.equal(result.status, 1);
    assert.match(`${result.stderr}${result.stdout}`, /outside \+\/-20% of current 400/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("CLI main treats a non-object current network as a zero-count baseline", () => {
  const root = mkdtempSync(path.join(tmpdir(), "gtfs-cli-zero-"));
  try {
    mkdirSync(path.join(root, ".gtfs-cache"), { recursive: true });
    mkdirSync(path.join(root, "public"), { recursive: true });
    writeFileSync(path.join(root, ".gtfs-cache", "google_transit.zip"), storedZip(allRouteGtfsFiles()));
    writeFileSync(path.join(root, "public", "subway-network.geojson"), "[]\n");
    const result = spawnCanonicalCli(root);
    assert.equal(result.status, 0, result.stderr || result.stdout);
    const doc = JSON.parse(readFileSync(path.join(root, "public", "subway-network.canonical.geojson"), "utf8"));
    assert.equal(doc.features.length, 27);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("buildCanonicalFeatureCollection skips blank polyline ids, NaN vertices, and one-point polylines", () => {
  const collection = buildCanonicalFeatureCollection({
    routesRows: [
      { route_id: "A", route_short_name: "", route_color: "0039A6" },
    ],
    tripsRows: [
      { route_id: "A", "shape_id": "shape-a" },
      { route_id: "A", "shape_id": "" },
      { route_id: "A", "shape_id": "shape-one" },
    ],
    polylineRows: [
      { "shape_id": "", "shape_pt_lat": "40.7000", "shape_pt_lon": "-73.9900", "shape_pt_sequence": "1" },
      { "shape_id": "shape-a", "shape_pt_lat": "not-a-lat", "shape_pt_lon": "-73.9900", "shape_pt_sequence": "1" },
      { "shape_id": "shape-a", "shape_pt_lat": "40.7000", "shape_pt_lon": "-73.9900", "shape_pt_sequence": "1" },
      { "shape_id": "shape-a", "shape_pt_lat": "40.7100", "shape_pt_lon": "-73.9800", "shape_pt_sequence": "2" },
      { "shape_id": "shape-one", "shape_pt_lat": "40.7000", "shape_pt_lon": "-73.9900", "shape_pt_sequence": "1" },
    ],
  });
  assert.equal(collection.features.length, 1);
  assert.equal(collection.features[0].properties.display_route, "A");
  assert.equal(collection.features[0].properties["shape_id"], "shape-a");
});

test("validateFeatureCollection rejects a FeatureCollection whose feature is not a LineString", () => {
  const collection = buildCanonicalFeatureCollection({
    routesRows: [{ route_id: "A", route_short_name: "A", route_color: "0039A6" }],
    tripsRows: [{ route_id: "A", "shape_id": "shape-a" }],
    polylineRows: [
      { "shape_id": "shape-a", "shape_pt_lat": "40.7000", "shape_pt_lon": "-73.9900", "shape_pt_sequence": "1" },
      { "shape_id": "shape-a", "shape_pt_lat": "40.7100", "shape_pt_lon": "-73.9800", "shape_pt_sequence": "2" },
    ],
  });
  const notLine = JSON.parse(JSON.stringify(collection));
  notLine.features[0].geometry.type = "Point";
  notLine.features[0].geometry.coordinates = [-73.99, 40.7];
  assert.throws(() => validateFeatureCollection(notLine), /Invalid canonical feature for route A/);
});
