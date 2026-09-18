import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  ChatWorkingPanel,
  workingPanelTriggerLabel,
} from "./chat-working-panel.tsx";

function render(props) {
  return renderToStaticMarkup(createElement(ChatWorkingPanel, {
    toolChips: [],
    reasoning: "",
    isStreaming: false,
    ...props,
  }));
}

test("an idle working panel renders nothing", () => {
  assert.equal(render({}), "");
});

test("a running search with an empty label keeps the generic thinking copy", () => {
  assert.equal(
    workingPanelTriggerLabel({
      isStreaming: true,
      reasoning: "",
      toolChips: [{ id: "s", tool: "web_search", label: "", status: "running" }],
    }),
    "Thinking through your request…",
  );
});

test("visible tool rows show running, failed, and durationless ok states", () => {
  const html = render({
    reasoning: "Checking the Q.",
    toolChips: [
      { id: "run", tool: "custom_status", label: "Checking arrivals", status: "running" },
      { id: "ok", tool: "check_transit", label: "Checked the Q", status: "ok" },
      { id: "fail", tool: "lookup_facts", label: "Could not load facts", status: "failed" },
    ],
  });
  assert.match(html, /Checking arrivals/);
  assert.match(html, /Checked the Q/);
  assert.match(html, /Could not load facts/);
  assert.doesNotMatch(html, / · /);
  assert.match(html, /sr-chat-tool-row__spinner/);
  assert.match(html, /sr-chat-tool-row__state-dot/);
});

test("an ok row with a duration prints seconds", () => {
  const html = render({
    reasoning: "Done.",
    toolChips: [{
      id: "ok",
      tool: "check_transit",
      label: "Checked the Q",
      status: "ok",
      durationMs: 1500,
    }],
  });
  assert.match(html, /Checked the Q · 1\.5s/);
});

test("a streaming search stage keeps the semantic label", () => {
  const html = render({
    isStreaming: true,
    progress: { stage: "checking_live_conditions", status: "active" },
    reasoning: "Checking live service and current incidents",
    toolChips: [{
      id: "search",
      tool: "check_transit",
      label: "Checking live service and current incidents",
      status: "running",
    }],
  });
  assert.match(html, /Checking live service and current incidents/);
  assert.match(html, /sr-chat-working-panel__semantic-stage/);
});
