import assert from "node:assert/strict";
import { deflateRawSync } from "node:zlib";
import { test } from "node:test";
import { parseCsv, parseZipEntries } from "./gtfs-ingest.ts";

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

test("parseCsv handles quoted commas, escaped quotes, and CRLF", () => {
  const rows = parseCsv('route_id,route_short_name,route_desc\r\nA,A,"8 Av, express"\r\nB,B,"He said ""go"""\r\n');
  assert.deepEqual(rows, [
    { route_id: "A", route_short_name: "A", route_desc: "8 Av, express" },
    { route_id: "B", route_short_name: "B", route_desc: 'He said "go"' },
  ]);
});

test("parseZipEntries reads stored members and rejects a missing required file", () => {
  const zip = storedZip({
    "stops.txt": "stop_id\nA40\n",
    "trips.txt": "trip_id\nt1\n",
  });
  const entries = parseZipEntries(zip, ["stops.txt", "trips.txt"]);
  assert.equal(entries.get("stops.txt"), "stop_id\nA40\n");
  assert.equal(entries.get("trips.txt"), "trip_id\nt1\n");
  assert.throws(
    () => parseZipEntries(zip, ["stops.txt", "missing.txt"]),
    /Missing missing.txt/,
  );
});

test("parseZipEntries rejects a buffer with no end-of-central-directory record", () => {
  assert.throws(() => parseZipEntries(Buffer.from("not-a-zip"), ["stops.txt"]), /end-of-central-directory/);
});

test("parseCsv returns no rows for empty or blank input", () => {
  assert.deepEqual(parseCsv(""), []);
  assert.deepEqual(parseCsv("\r\n\r\n"), []);
});

function deflatedZip(name: string, text: string): Buffer {
  const data = Buffer.from(text, "utf8");
  const compressed = deflateRawSync(data);
  const nameBuf = Buffer.from(name, "utf8");
  const local = Buffer.alloc(30 + nameBuf.length + compressed.length);
  local.writeUInt32LE(0x04034b50, 0);
  local.writeUInt16LE(8, 8);
  local.writeUInt32LE(compressed.length, 18);
  local.writeUInt32LE(data.length, 22);
  local.writeUInt16LE(nameBuf.length, 26);
  nameBuf.copy(local, 30);
  compressed.copy(local, 30 + nameBuf.length);
  const central = Buffer.alloc(46 + nameBuf.length);
  central.writeUInt32LE(0x02014b50, 0);
  central.writeUInt16LE(8, 10);
  central.writeUInt32LE(compressed.length, 20);
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

test("parseZipEntries inflates deflated members and strips a latin-1 BOM prefix", () => {
  const zip = deflatedZip("stops.txt", "ï»¿stop_id\nA40\n");
  const entries = parseZipEntries(zip, ["stops.txt"]);
  assert.equal(entries.get("stops.txt"), "stop_id\nA40\n");
});

test("parseZipEntries rejects a malformed central directory", () => {
  const zip = storedZip({ "stops.txt": "stop_id\nA40\n" });
  const centralSig = zip.indexOf(Buffer.from([0x50, 0x4b, 0x01, 0x02]));
  assert.ok(centralSig >= 0);
  zip.writeUInt32LE(0xffffffff, centralSig);
  assert.throws(() => parseZipEntries(zip, ["stops.txt"]), /Malformed ZIP central directory/);
});

test("parseZipEntries skips extra and comment bytes and parseCsv fills missing columns", () => {
  const name = "stops.txt";
  const text = "stop_id\nA40\n";
  const data = Buffer.from(text, "utf8");
  const nameBuf = Buffer.from(name, "utf8");
  const extra = Buffer.from([0x01, 0x02]);
  const comment = Buffer.from("x");
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
  const entries = parseZipEntries(Buffer.concat([local, central, eocd]), ["stops.txt"]);
  assert.equal(entries.get("stops.txt"), "stop_id\nA40\n");
  assert.deepEqual(parseCsv("stop_id,stop_name\nA40"), [{ stop_id: "A40", stop_name: "" }]);
});
