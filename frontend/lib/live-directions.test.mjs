import assert from "node:assert/strict";
import test from "node:test";

import { buildLiveDirectionRows } from "./live-directions.ts";

test("buildLiveDirectionRows keeps bus arrivals with numeric BusTime directions", () => {
  const rows = buildLiveDirectionRows([
    {
      route_id: "B44",
      stop_id: "308214",
      arrival_time: 1_700_000_120,
      direction: "0",
      terminal_stop_name: "Sheepshead Bay",
      parent_stop_name: "Nostrand Av/Eastern Pkwy",
      station_name: "Nostrand Av/Eastern Pkwy",
      distance_m: 240,
      mode: "bus",
    },
  ]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].routeId, "B44");
  assert.equal(rows[0].direction, "UPTOWN");
  assert.equal(rows[0].destinationLabel, "Sheepshead Bay");
});

test("buildLiveDirectionRows keeps directionless bus arrivals instead of dropping them", () => {
  const rows = buildLiveDirectionRows([
    {
      route_id: "B44",
      stop_id: "308214",
      arrival_time: 1_700_000_120,
      terminal_stop_name: "Williamsburg Bridge Plaza",
      parent_stop_name: "Nostrand Av/Eastern Pkwy",
      station_name: "Nostrand Av/Eastern Pkwy",
      distance_m: 240,
      mode: "bus",
    },
  ]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].routeId, "B44");
  assert.equal(rows[0].direction, "UPTOWN");
});
