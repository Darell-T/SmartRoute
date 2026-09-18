import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { PromptSuggestion } from "./prompt-suggestion.tsx";

test("prompt suggestions highlight a query inside the label", () => {
  const marked = renderToStaticMarkup(
    createElement(PromptSuggestion, { highlight: "jfk", size: "sm" }, "JFK Airport"),
  );
  assert.match(marked, /<mark>JFK<\/mark>/i);
  const missed = renderToStaticMarkup(
    createElement(PromptSuggestion, { highlight: "  ", variant: "outline" }, "JFK Airport"),
  );
  assert.doesNotMatch(missed, /<mark>/);
  const absent = renderToStaticMarkup(
    createElement(PromptSuggestion, { highlight: "zzz" }, "JFK Airport"),
  );
  assert.doesNotMatch(absent, /<mark>/);
});
