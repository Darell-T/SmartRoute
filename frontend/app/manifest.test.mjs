import assert from "node:assert/strict";
import test from "node:test";

import * as manifestModule from "./manifest.ts";

test("the web app manifest names SmartRoute and ships maskable icons", () => {
  const exported = manifestModule.default;
  const manifest = typeof exported === "function" ? exported : exported?.default;
  assert.equal(typeof manifest, "function");
  const record = manifest();
  assert.equal(record.name, "SmartRoute");
  assert.equal(record.display, "standalone");
  assert.ok(record.icons.some((icon) => icon.purpose === "maskable"));
});
