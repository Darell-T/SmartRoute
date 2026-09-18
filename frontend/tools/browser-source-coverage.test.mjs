import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  assertOriginalLineHit,
  hitLinesForFile,
  istanbulFromV8Entry,
  isOwnedWebpackModule,
  mapBrowserCoverage,
  mergeIstanbulCoverage,
  normalizeOriginalPath,
} from "./browser-source-coverage.mjs";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixtureRelative = "components/coverage-fixture.ts";
const original = "export function mark() {\n  return 42;\n}\nmark();\n";

function inlineSourceMap(sources, mappings = "AAAA;AACA;AACA;AACA") {
  const map = {
    version: 3,
    file: "generated.js",
    sources,
    sourcesContent: [original],
    mappings,
  };
  const encoded = Buffer.from(JSON.stringify(map), "utf8").toString("base64");
  return `${original}//# sourceMappingURL=data:application/json;base64,${encoded}\n`;
}

function wholeFileHit(source) {
  return {
    url: "webpack-internal:///(app-pages-browser)/./components/coverage-fixture.ts",
    source,
    functions: [
      {
        functionName: "",
        isBlockCoverage: true,
        ranges: [{ startOffset: 0, endOffset: source.length, count: 1 }],
      },
    ],
  };
}

test("normalizes webpack, file, Windows, and query-string original paths", () => {
  const expected = fixtureRelative;
  assert.equal(
    normalizeOriginalPath(
      "webpack-internal:///(app-pages-browser)/./components/coverage-fixture.ts",
      frontendRoot,
    ),
    expected,
  );
  assert.equal(
    normalizeOriginalPath(
      "webpack://smartroute/./components/coverage-fixture.ts?v=1",
      frontendRoot,
    ),
    expected,
  );
  assert.equal(
    normalizeOriginalPath(
      `C:\\Users\\example\\frontend\\${expected.replaceAll("/", "\\")}`,
      path.normalize("C:/Users/example/frontend"),
    ),
    expected,
  );
  assert.equal(
    isOwnedWebpackModule(
      "webpack-internal:///(app-pages-browser)/./components/coverage-fixture.ts",
    ),
    true,
  );
  assert.equal(
    isOwnedWebpackModule(
      "webpack-internal:///(app-pages-browser)/./node_modules/next/index.js",
    ),
    false,
  );
  assert.equal(
    isOwnedWebpackModule(
      "about://React/Server/webpack-internal:///(rsc)/./app/layout.tsx",
    ),
    false,
  );
});

test("maps a synthetic webpack source map onto original fixture lines", async () => {
  const source = inlineSourceMap([path.resolve(frontendRoot, fixtureRelative)]);
  const istanbul = await istanbulFromV8Entry(wholeFileHit(source), frontendRoot);
  const keyed = path.resolve(frontendRoot, fixtureRelative);
  assert.equal(Object.keys(istanbul).length, 1);
  assert.ok(istanbul[keyed]);
  const hits = hitLinesForFile(istanbul, frontendRoot, fixtureRelative);
  assert.deepEqual(hits, [1, 2, 3, 4]);
});

test("merges duplicate file records by adding Istanbul counters", async () => {
  const source = inlineSourceMap([path.resolve(frontendRoot, fixtureRelative)]);
  const first = await mapBrowserCoverage([wholeFileHit(source)], frontendRoot);
  const second = await mapBrowserCoverage([wholeFileHit(source)], frontendRoot);
  const merged = mergeIstanbulCoverage(first.toJSON(), second);
  const keyed = path.resolve(frontendRoot, fixtureRelative);
  const counts = Object.values(merged[keyed].s);
  assert.equal(counts.length > 0, true);
  assert.equal(
    counts.every((count) => count === 2),
    true,
  );
});

test("rejects an owned webpack module that has no original source map", async () => {
  await assert.rejects(
    () =>
      mapBrowserCoverage(
        [
          {
            url: "webpack-internal:///(app-pages-browser)/./components/coverage-fixture.ts",
            source: "function orphan() { return 1 }",
            functions: [],
          },
        ],
        frontendRoot,
      ),
    /failed to map original sources/,
  );
});

test("skips Next RSC about:// records that embed a webpack-internal path", async () => {
  const map = await mapBrowserCoverage(
    [
      {
        url: "about://React/Server/webpack-internal:///(rsc)/./app/layout.tsx",
        source: "function orphan() { return 1 }",
        functions: [],
      },
    ],
    frontendRoot,
  );
  assert.deepEqual(Object.keys(map.toJSON()), []);
});

function coverageForLines(relative, lines, hit) {
  const keyed = path.resolve(frontendRoot, relative);
  const statementMap = {};
  const s = {};
  for (const [index, line] of lines.entries()) {
    statementMap[String(index)] = {
      start: { line, column: 0 },
      end: { line, column: 1 },
    };
    s[String(index)] = hit ? 1 : 0;
  }
  return { [keyed]: { path: keyed, statementMap, s } };
}

test("browser proof fails when only unit coverage hit the original line", () => {
  const proofLine = 143;
  const lines = [1, proofLine, 271];
  const unitHit = coverageForLines(fixtureRelative, lines, true);
  const browserMiss = coverageForLines(fixtureRelative, lines, false);
  assert.deepEqual(hitLinesForFile(unitHit, frontendRoot, fixtureRelative), lines);
  assert.equal(
    hitLinesForFile(browserMiss, frontendRoot, fixtureRelative).includes(proofLine),
    false,
  );
  assert.throws(
    () => assertOriginalLineHit(browserMiss, frontendRoot, fixtureRelative, proofLine),
    /browser-only source mapping proof failed/,
  );
});

test("browser proof passes only on browser-only hits", () => {
  const proofLine = 143;
  const browserHit = coverageForLines(fixtureRelative, [1, proofLine, 271], true);
  const hits = assertOriginalLineHit(
    browserHit,
    frontendRoot,
    fixtureRelative,
    proofLine,
  );
  assert.deepEqual(hits, [1, proofLine, 271]);
});
