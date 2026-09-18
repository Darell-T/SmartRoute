import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { subwayBulletName, subwayBulletSrc, TrainBullet } from "./train-bullet.tsx";

test("subway bullet names map express and shuttle aliases", () => {
  assert.equal(subwayBulletName("6X"), "6d");
  assert.equal(subwayBulletName("7X"), "7d");
  assert.equal(subwayBulletName("FX"), "fd");
  assert.equal(subwayBulletName("SIR"), "sir");
  assert.equal(subwayBulletName("  "), "s");
  assert.equal(subwayBulletName("Q"), "q");
  assert.equal(subwayBulletSrc("Q"), "/mta-bullets/q.svg");
});

test("bus bullets render a text pill instead of an MTA image", () => {
  const html = renderToStaticMarkup(createElement(TrainBullet, { line: "B44", size: 18 }));
  assert.match(html, /B44/);
  assert.match(html, /bus/);
  assert.doesNotMatch(html, /mta-bullets/);
});
