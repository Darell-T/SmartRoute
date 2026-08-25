import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

import {
  workingPanelDetailText,
  workingPanelTriggerLabel,
} from "./chat-working-panel.tsx";

test("intent copy replaces deliberating until route progress starts", () => {
  assert.equal(
    workingPanelTriggerLabel({
      isStreaming: true,
      reasoning: "Comparing routes to Barclays Center…\nLater deliberation.",
      toolChips: [],
    }),
    "Comparing routes to Barclays Center…",
  );
});

test("live route progress still wins over the intent label", () => {
  assert.equal(
    workingPanelTriggerLabel({
      isStreaming: true,
      reasoning: "Comparing live routes…",
      progress: { stage: "finding_routes", status: "active" },
      toolChips: [],
    }),
    "Finding viable routes",
  );
});

test("generic thinking remains the last fallback", () => {
  assert.equal(
    workingPanelTriggerLabel({
      isStreaming: true,
      reasoning: "",
      toolChips: [],
    }),
    "Thinking through your request…",
  );
});

test("a real place-search start replaces thinking copy with its specific label", () => {
  assert.equal(
    workingPanelTriggerLabel({
      isStreaming: true,
      reasoning: "Reviewing your place request…",
      toolChips: [{
        id: "search-1",
        tool: "discover_places",
        label: "Searching verified places in Manhattan…",
        status: "running",
      }],
    }),
    "Searching verified places in Manhattan…",
  );
});

test("the trigger copy is not rendered again in expanded details", () => {
  assert.equal(
    workingPanelDetailText(
      "Reviewing your place request…\nReviewing your place request…\nUsing current preferences.",
      "Reviewing your place request…",
    ),
    "Using current preferences.",
  );
});

test("presentation and completion internals stay out of activity rows", () => {
  const source = fs.readFileSync(new URL("./chat-working-panel.tsx", import.meta.url), "utf8");
  assert.match(source, /!isHiddenActivityTool\(chip\.tool\)/);
});
