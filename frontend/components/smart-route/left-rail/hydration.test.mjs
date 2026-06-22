import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const ROOT = path.resolve(import.meta.dirname, "../../..");

test("left rail route view does not render an inline client clock during SSR", () => {
  const source = fs.readFileSync(
    path.join(ROOT, "components/smart-route/left-rail/route-view.tsx"),
    "utf8",
  );

  assert.doesNotMatch(
    source,
    /Today\s*·\s*\{[\s\S]{0,160}new Date\(\)\.toLocaleTimeString/,
    "route-view.tsx should not format the clock inline during render; it causes React #418 hydration text mismatches",
  );
  assert.match(
    source,
    /const clock = useClientClock\(\);/,
    "route-view.tsx should render an SSR-stable placeholder and update the clock after hydration",
  );
});
