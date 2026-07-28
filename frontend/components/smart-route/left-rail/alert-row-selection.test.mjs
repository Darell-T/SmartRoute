import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  toggleAlertRowSelection,
  useAlertRowSelection,
} from "./alert-row-selection.ts";

test("alert row selection toggles from its closed default", () => {
  assert.equal(toggleAlertRowSelection(false), true);
  assert.equal(toggleAlertRowSelection(true), false);
});

test("alert row selection hook preserves its initial open state", () => {
  assert.equal(renderSelection(false), '<span data-open="false"></span>');
  assert.equal(renderSelection(true), '<span data-open="true"></span>');
});

function renderSelection(initialOpen) {
  return renderToStaticMarkup(
    createElement(AlertRowSelectionProbe, { initialOpen }),
  );
}

function AlertRowSelectionProbe({ initialOpen }) {
  const { open } = useAlertRowSelection(initialOpen);
  return createElement("span", { "data-open": String(open) });
}
