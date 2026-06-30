import { inflateRawSync } from "node:zlib";

// Parsed CSV/GTFS rows are string-keyed string maps.
export type CsvRow = Record<string, string>;

function readUInt16(buf: Buffer, off: number) { return buf.readUInt16LE(off); }
function readUInt32(buf: Buffer, off: number) { return buf.readUInt32LE(off); }

// =====================================================================
// Mini-ZIP reader (no external deps; mirrors regenerate-canonical-from-gtfs.mjs)
// =====================================================================

export function parseZipEntries(zipBuffer: Buffer, wantedNames: string[]) {
  const wanted = new Set(wantedNames);
  const entries = new Map<string, string>();

  let eocd = -1;
  for (let i = zipBuffer.length - 22; i >= 0; i -= 1) {
    if (readUInt32(zipBuffer, i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("ZIP end-of-central-directory not found");

  const cdSize = readUInt32(zipBuffer, eocd + 12);
  const cdOff = readUInt32(zipBuffer, eocd + 16);
  let off = cdOff;
  const end = cdOff + cdSize;

  while (off < end) {
    if (readUInt32(zipBuffer, off) !== 0x02014b50) {
      throw new Error("Malformed ZIP central directory");
    }
    const cm = readUInt16(zipBuffer, off + 10);
    const cs = readUInt32(zipBuffer, off + 20);
    const nameLen = readUInt16(zipBuffer, off + 28);
    const extraLen = readUInt16(zipBuffer, off + 30);
    const commLen = readUInt16(zipBuffer, off + 32);
    const lho = readUInt32(zipBuffer, off + 42);
    const name = zipBuffer.subarray(off + 46, off + 46 + nameLen).toString("utf8");

    if (wanted.has(name)) {
      if (readUInt32(zipBuffer, lho) !== 0x04034b50) {
        throw new Error(`Bad local header for ${name}`);
      }
      const lnl = readUInt16(zipBuffer, lho + 26);
      const lel = readUInt16(zipBuffer, lho + 28);
      const dataOff = lho + 30 + lnl + lel;
      const compressed = zipBuffer.subarray(dataOff, dataOff + cs);
      const data = cm === 0 ? compressed : inflateRawSync(compressed);
      entries.set(name, data.toString("utf8").replace(/^ï»¿/, ""));
    }

    off += 46 + nameLen + extraLen + commLen;
  }
  for (const w of wanted) if (!entries.has(w)) throw new Error(`Missing ${w}`);
  return entries;
}

// =====================================================================
// CSV reader (handles quoted fields and CR/LF)
// =====================================================================

export function parseCsv(text: string): CsvRow[] {
  const rows: string[][] = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i += 1; }
        else { quoted = false; }
      } else { field += c; }
      continue;
    }
    if (c === '"') quoted = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; }
    else if (c !== "\r") { field += c; }
  }
  if (field.length > 0 || row.length > 0) { row.push(field); rows.push(row); }

  const filtered = rows.filter((r) => r.some((v) => v !== ""));
  if (filtered.length === 0) return [];
  const headers = filtered[0];
  return filtered.slice(1).map((cols) => {
    const obj: CsvRow = {};
    headers.forEach((h, j) => { obj[h] = cols[j] ?? ""; });
    return obj;
  });
}
