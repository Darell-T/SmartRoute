import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
function loadTsModule(relativePath) {
  const helperPath = new URL(relativePath, import.meta.url);
  const helperSource = readFileSync(helperPath, "utf8");
  const helperModule = { exports: {} };
  const transpiled = ts.transpileModule(helperSource, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
      resolveJsonModule: true,
    },
  });

  vm.runInNewContext(transpiled.outputText, {
    Image: class {},
    console,
    exports: helperModule.exports,
    module: helperModule,
    require: (specifier) => {
      if (specifier === "./subway-lane-separation") {
        return loadTsModule("./subway-lane-separation.ts");
      }
      return require(specifier);
    },
  }, { filename: helperPath.pathname });

  return helperModule.exports;
}

const helperModuleExports = loadTsModule("./subway-network.ts");
const { validateStyleMin } = require("@maplibre/maplibre-gl-style-spec");
const { subwayLaneOffsetExpression } = helperModuleExports;
const subwayNetworkSource = readFileSync(new URL("./subway-network.ts", import.meta.url), "utf8");

assert.equal(
  typeof subwayLaneOffsetExpression,
  "function",
  "subway lane offset expression should be exported for validation",
);

const style = {
  version: 8,
  sources: {
    subway: {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    },
  },
  layers: [
    {
      id: "subway-line-test",
      type: "line",
      source: "subway",
      paint: {
        "line-offset": subwayLaneOffsetExpression(),
      },
    },
  ],
};

// Lane offsets are now baked into geometry coordinates by
// frontend/scripts/build-corridor-groups.mjs. The runtime line-offset MUST
// be 0 — any non-zero value would double-shift the line and produce 2× the
// intended spacing. The legacy zoom-interpolated expression lives in git
// history if a fallback to runtime offsets is ever needed.
assert.equal(
  subwayLaneOffsetExpression(),
  0,
  "subway lane offset expression must return 0; offsets are baked into geometry by build-corridor-groups.mjs",
);

assert.equal(
  (subwayNetworkSource.match(/"line-offset": subwayLaneOffsetExpression\(\)/g) ?? []).length,
  4,
  "shadow, casing, glow, and color subway layers should all share the same line-offset expression",
);
assert.match(
  subwayNetworkSource,
  /"line-sort-key": subwayLineSortKeyExpression\(\)/,
  "subway line layers should use deterministic line-sort-key",
);

assert.deepEqual(
  validateStyleMin(style).map((error) => error.message),
  [],
  "subway lane offset expression should be valid MapLibre style syntax",
);

console.log("subway network style checks passed");
