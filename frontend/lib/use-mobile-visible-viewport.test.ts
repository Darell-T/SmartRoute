import assert from "node:assert/strict";
import test from "node:test";

import { installMobileViewportVariables } from "./use-mobile-visible-viewport";

class FakeViewport extends EventTarget {
  height = 844;
  offsetTop = 0;
}

test("visible viewport variables track iOS height and offset with one scheduled write", () => {
  const viewport = new FakeViewport();
  const values = new Map<string, string>();
  const frames = new Map<number, FrameRequestCallback>();
  let nextFrame = 1;
  const targetWindow = {
    innerHeight: 844,
    visualViewport: viewport,
    requestAnimationFrame(callback: FrameRequestCallback) {
      const id = nextFrame++;
      frames.set(id, callback);
      return id;
    },
    cancelAnimationFrame(id: number) {
      frames.delete(id);
    },
    addEventListener() {},
    removeEventListener() {},
  } as unknown as Window;
  const root = {
    style: { setProperty: (name: string, value: string) => values.set(name, value) },
  } as unknown as HTMLElement;

  const cleanup = installMobileViewportVariables(targetWindow, root);
  assert.equal(values.get("--mobile-visible-height"), "844px");
  assert.equal(values.get("--mobile-viewport-offset-top"), "0px");

  viewport.height = 420.4;
  viewport.offsetTop = 91.6;
  viewport.dispatchEvent(new Event("resize"));
  viewport.dispatchEvent(new Event("scroll"));
  assert.equal(frames.size, 1, "resize and scroll should coalesce into one frame");
  const [frameId, frame] = frames.entries().next().value!;
  frames.delete(frameId);
  frame(0);

  assert.equal(values.get("--mobile-visible-height"), "420px");
  assert.equal(values.get("--mobile-viewport-offset-top"), "92px");
  assert.equal(values.get("--visual-viewport-height"), "420px");

  cleanup();
  viewport.height = 300;
  viewport.dispatchEvent(new Event("resize"));
  assert.equal(frames.size, 0, "cleanup should detach visual viewport listeners");
});

test("visible viewport falls back to innerHeight when visualViewport is absent", () => {
  const values = new Map<string, string>();
  const targetWindow = {
    innerHeight: 667,
    visualViewport: null,
    requestAnimationFrame: () => 1,
    cancelAnimationFrame() {},
    addEventListener() {},
    removeEventListener() {},
  } as unknown as Window;
  const root = {
    style: { setProperty: (name: string, value: string) => values.set(name, value) },
  } as unknown as HTMLElement;

  const cleanup = installMobileViewportVariables(targetWindow, root);
  assert.equal(values.get("--mobile-visible-height"), "667px");
  assert.equal(values.get("--mobile-viewport-offset-top"), "0px");
  cleanup();
});
