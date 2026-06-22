import assert from "node:assert/strict";
import test from "node:test";

import { shouldApplyServiceAlertPoll } from "./service-alert-poll.ts";

test("service-alert polling result is ignored after a websocket epoch opens", () => {
  assert.equal(
    shouldApplyServiceAlertPoll({
      startedWsEpoch: 0,
      currentWsEpoch: 1,
      wsIsOpen: false,
    }),
    false,
  );
});

test("service-alert polling result applies while websocket has not opened", () => {
  assert.equal(
    shouldApplyServiceAlertPoll({
      startedWsEpoch: 1,
      currentWsEpoch: 1,
      wsIsOpen: false,
    }),
    true,
  );
});

test("service-alert polling result is ignored while websocket is open", () => {
  assert.equal(
    shouldApplyServiceAlertPoll({
      startedWsEpoch: 1,
      currentWsEpoch: 1,
      wsIsOpen: true,
    }),
    false,
  );
});
