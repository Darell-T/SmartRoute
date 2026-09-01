import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const v8toIstanbul = require("v8-to-istanbul");
const libCoverage = require("istanbul-lib-coverage");

const OWNED_MODULE =
  /(?:webpack-internal:\/\/\/\([^)]+\)\/|webpack:\/\/[^/]*\/)\.\/(components|app|lib|scripts)\//;

export function stripQueryAndHash(url) {
  return String(url).replace(/[?#].*$/, "");
}

const OWNED_ROOTS = ["app/", "lib/", "components/", "scripts/"];

function ownedRelative(relative) {
  const match = relative.match(/^(?:.*\/)?((?:components|app|lib|scripts)\/.+)$/);
  return match ? match[1] : relative;
}

export function normalizeOriginalPath(sourcePath, frontendRoot) {
  let value = stripQueryAndHash(sourcePath).replaceAll("\\", "/");
  if (value.startsWith("file://")) {
    value = fileURLToPath(value).replaceAll("\\", "/");
  }
  value = value.replace(/^webpack-internal:\/\/\/\([^)]+\)\//, "");
  value = value.replace(/^webpack:\/\/[^/]*\//, "");
  if (value.startsWith("./")) value = value.slice(2);
  const absolute = path.isAbsolute(value)
    ? path.normalize(value)
    : path.resolve(frontendRoot, value);
  let relative = path.relative(frontendRoot, absolute).replaceAll("\\", "/");
  if (!relative || relative.startsWith("../") || path.isAbsolute(relative)) {
    return null;
  }
  relative = ownedRelative(relative);
  if (relative.includes("node_modules/") || relative.startsWith(".next/")) {
    return null;
  }
  if (!OWNED_ROOTS.some((root) => relative.startsWith(root))) {
    return null;
  }
  return relative;
}

export function isOwnedWebpackModule(url) {
  return OWNED_MODULE.test(stripQueryAndHash(url));
}

export function compactV8Entry(entry) {
  return {
    url: entry.url,
    source: entry.source ?? "",
    functions: entry.functions ?? [],
  };
}

function rekeyIstanbul(istanbul, frontendRoot) {
  const remapped = {};
  for (const [rawPath, fileCoverage] of Object.entries(istanbul)) {
    const relative = normalizeOriginalPath(rawPath, frontendRoot);
    if (!relative) continue;
    const keyed = path.resolve(frontendRoot, relative);
    remapped[keyed] = { ...fileCoverage, path: keyed };
  }
  return remapped;
}

export async function istanbulFromV8Entry(entry, frontendRoot) {
  const source = entry.source ?? "";
  if (!source) return {};
  const generated = path.join(frontendRoot, ".browser-coverage-script.js");
  try {
    const converter = v8toIstanbul(generated, 0, { source });
    await converter.load();
    converter.applyCoverage(entry.functions ?? []);
    return rekeyIstanbul(converter.toIstanbul(), frontendRoot);
  } catch {
    return {};
  }
}

export async function mapBrowserCoverage(entries, frontendRoot) {
  const map = libCoverage.createCoverageMap();
  const unmappedOwned = [];
  for (const entry of entries) {
    const owned = isOwnedWebpackModule(entry.url);
    const istanbul = await istanbulFromV8Entry(entry, frontendRoot);
    const files = Object.keys(istanbul);
    if (owned && files.length === 0) {
      unmappedOwned.push(stripQueryAndHash(entry.url));
      continue;
    }
    if (files.length > 0) map.merge(istanbul);
  }
  if (unmappedOwned.length > 0) {
    throw new Error(
      `browser coverage failed to map original sources:\n${unmappedOwned.join("\n")}`,
    );
  }
  return map;
}

export function mergeIstanbulCoverage(unitCoverage, browserCoverage) {
  const merged = libCoverage.createCoverageMap(unitCoverage);
  const browser =
    typeof browserCoverage?.toJSON === "function"
      ? browserCoverage
      : libCoverage.createCoverageMap(browserCoverage);
  merged.merge(browser);
  return merged.toJSON();
}

export function hitLinesForFile(coverageJson, frontendRoot, relativePath) {
  const keyed = path.resolve(frontendRoot, relativePath);
  const file = coverageJson[keyed] ?? coverageJson[relativePath];
  if (!file?.statementMap || !file.s) return [];
  const lines = [];
  for (const [id, loc] of Object.entries(file.statementMap)) {
    if (file.s[id] && loc?.start?.line) lines.push(loc.start.line);
  }
  return [...new Set(lines)].sort((a, b) => a - b);
}

export function assertOriginalLineHit(
  coverageJson,
  frontendRoot,
  relativePath,
  line,
) {
  const hits = hitLinesForFile(coverageJson, frontendRoot, relativePath);
  if (!hits.includes(line)) {
    throw new Error(
      `browser-only source mapping proof failed for ${relativePath}: expected original line ${line}, got ${hits.slice(0, 24).join(",")}`,
    );
  }
  return hits;
}

export function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

export function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value)}\n`);
}

export async function istanbulFromBrowserRawDirectory({
  frontendRoot,
  rawDirectory,
}) {
  const rawFiles = fs.existsSync(rawDirectory)
    ? fs.readdirSync(rawDirectory).filter((name) => name.endsWith(".json"))
    : [];
  if (rawFiles.length === 0) {
    throw new Error("browser coverage produced no source-mapped records");
  }
  const entries = [];
  for (const name of rawFiles) {
    entries.push(...readJson(path.join(rawDirectory, name)));
  }
  return (await mapBrowserCoverage(entries, frontendRoot)).toJSON();
}
