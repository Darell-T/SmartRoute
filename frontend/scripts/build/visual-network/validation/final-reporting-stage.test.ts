import assert from "node:assert/strict";
import test from "node:test";
import { reportFinalTopologySummaryStage } from "./final-reporting-stage.ts";
import type { TopologyDoc } from "../inputs/gtfs-topology-stage.ts";

test("reportFinalTopologySummaryStage logs Gate 2A terminals for each route", () => {
  const topologyDoc: TopologyDoc = {
    generated_at: "2026-01-01T00:00:00.000Z",
    source: "build-subway-visual-network.mjs Gate 2A",
    parameters: { min_trips_per_branch: 5 },
    gtfs_input: { stops: 2, trips: 1, stop_times: 2, routes: 1 },
    topology: { distinct_routes: 1, total_branches: 1, dropped_low_freq_branches: 0 },
    per_route: [
      {
        route_id: "G",
        branch_count: 1,
        distinct_stations: 2,
        branches: [
          {
            branch_id: "G-0-G01-G02",
            direction_id: "0",
            terminal_start: "G01",
            terminal_start_name: "Court Sq",
            terminal_end: "G02",
            terminal_end_name: "Greenpoint Av",
            stop_count: 2,
            total_trips_in_branch: 12,
            canonical_pattern_trips: 12,
            canonical_pattern_share: 1,
            distinct_patterns: 1,
            "sample_shape_ids": ["poly-g"],
            sample_headsigns: ["Church Av"],
            stop_sequence: ["G01", "G02"],
          },
        ],
      },
    ],
  };
  const stopsById = new Map([
    ["G01", { stop_id: "G01", name: "Court Sq", lat: 40.75, lon: -73.94, parent_station: null, location_type: 1 }],
    ["G02", { stop_id: "G02", name: "Greenpoint Av", lat: 40.73, lon: -73.95, parent_station: null, location_type: 1 }],
  ]);

  const lines: string[] = [];
  const original = console.log;
  console.log = (...args: unknown[]) => {
    lines.push(args.map(String).join(" "));
  };
  try {
    reportFinalTopologySummaryStage({
      topologyDoc,
      minTripsPerBranch: 5,
      droppedLowFreqBranches: 0,
      stopsById,
    });
    reportFinalTopologySummaryStage({
      topologyDoc,
      minTripsPerBranch: 5,
      droppedLowFreqBranches: 0,
      stopsById,
    });
  } finally {
    console.log = original;
  }

  const summary = lines.filter((line) => line.includes("Gate 2A topology summary"));
  assert.equal(summary.length, 2);
  assert.ok(lines.some((line) => line.includes("Court Sq") && line.includes("Greenpoint Av") && line.includes("12tr")));
  assert.ok(lines.some((line) => line.includes("distinct routes: 1")));
});

test("reportFinalTopologySummaryStage falls back to stop ids and slices extra branches", () => {
  const topologyDoc: TopologyDoc = {
    generated_at: "2026-01-01T00:00:00.000Z",
    source: "build-subway-visual-network.mjs Gate 2A",
    parameters: { min_trips_per_branch: 5 },
    gtfs_input: { stops: 0, trips: 0, stop_times: 0, routes: 1 },
    topology: { distinct_routes: 1, total_branches: 5, dropped_low_freq_branches: 2 },
    per_route: [
      {
        route_id: "A",
        branch_count: 5,
        distinct_stations: 2,
        branches: ["1", "2", "3", "4", "5"].map((n) => ({
          branch_id: `A-0-A${n}-A9`,
          direction_id: "",
          terminal_start: `A${n}`,
          terminal_start_name: `A${n}`,
          terminal_end: "A9",
          terminal_end_name: "A9",
          stop_count: 2,
          total_trips_in_branch: 5,
          canonical_pattern_trips: 5,
          canonical_pattern_share: 1,
          distinct_patterns: 1,
          "sample_shape_ids": [],
          sample_headsigns: [],
          stop_sequence: [`A${n}`, "A9"],
        })),
      },
    ],
  };
  const lines: string[] = [];
  const original = console.log;
  console.log = (...args: unknown[]) => {
    lines.push(args.map(String).join(" "));
  };
  try {
    reportFinalTopologySummaryStage({
      topologyDoc,
      minTripsPerBranch: 5,
      droppedLowFreqBranches: 2,
      stopsById: new Map(),
    });
  } finally {
    console.log = original;
  }
  assert.ok(lines.some((line) => line.includes("?:A1 → A9")));
  assert.ok(!lines.some((line) => line.includes("A5 → A9")));
  assert.ok(lines.some((line) => line.includes("dropped low-frequency branches: 2")));
});
