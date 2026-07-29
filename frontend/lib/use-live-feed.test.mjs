import assert from "node:assert/strict";
import test from "node:test";

import { withLiveFeedNow } from "./use-live-feed.ts";

test("the first live-feed callback supplies a browser clock without page-local state", () => {
  assert.deepEqual(withLiveFeedNow({ nowMs: 0, value: "snapshot" }, 1_234), { nowMs: 1_234, value: "snapshot" });
  assert.equal(withLiveFeedNow({ nowMs: 0 }, 0).nowMs, 0);
});
