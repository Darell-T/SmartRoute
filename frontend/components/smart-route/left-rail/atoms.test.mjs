import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  BusChip,
  Dot,
  LineBullet,
  LocationPin,
  Meta,
  RouteBulletGroup,
  StepIcon,
  TransitText,
  btnGhost,
  btnPrimary,
  cleanTransitParagraphText,
} from "./atoms.tsx";

test("rail atoms render identity, pulse, overflow, and pins", () => {
  const meta = renderToStaticMarkup(createElement(Meta, { tone: "ink" }, "Route"));
  assert.match(meta, /Route/);
  const dot = renderToStaticMarkup(createElement(Dot, { pulse: true, color: "#f00" }));
  assert.match(dot, /srPulse/);
  const bullets = renderToStaticMarkup(
    createElement(RouteBulletGroup, { lines: ["Q", "B", "D"], size: 18, limit: 2 }),
  );
  assert.match(bullets, /\+1/);
  const bus = renderToStaticMarkup(createElement(BusChip, { route: "b54" }));
  assert.match(bus, /B54/);
  const start = renderToStaticMarkup(createElement(LocationPin, { tone: "start" }));
  const arrive = renderToStaticMarkup(createElement(LocationPin, { tone: "arrive" }));
  assert.match(start, /aria-hidden="true"/);
  assert.match(arrive, /aria-hidden="true"/);
  assert.ok(LineBullet);
});

test("TransitText swaps subway and bus tokens and strips icon placeholders", () => {
  const identity = renderToStaticMarkup(
    createElement(TransitText, { text: "[Q] and [B54] delayed", bulletSize: 13 }),
  );
  assert.match(identity, /sr-line-token/);
  const paragraph = renderToStaticMarkup(
    createElement(TransitText, {
      text: "[shuttle bus icon] Take the [Q].",
      mode: "paragraph",
    }),
  );
  assert.match(paragraph, /Take the Q/);
  assert.equal(
    cleanTransitParagraphText("[bus]  Wait  at  Jay."),
    "Wait at Jay.",
  );
});

test("step icons cover every mode glyph", () => {
  for (const type of ["walk", "board", "ride", "bus", "transfer", "exit", "destination", "arrive"]) {
    const html = renderToStaticMarkup(createElement(StepIcon, { type, size: 12, color: "#fff" }));
    assert.match(html, /data-step-icon|aria-hidden="true"/);
  }
  assert.equal(typeof btnPrimary().background, "string");
  assert.equal(typeof btnGhost().border, "string");
});
