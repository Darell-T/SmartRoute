import { readFileSync } from "node:fs";
import { trimTerminalOverhang } from "../../trim-terminal-overhang.ts";
import type { LineFeature } from "../shared/types.ts";

type TerminalOverhangTrimBundleArtifacts = {
  visualFeatures: LineFeature[];
};

type TerminalOverhangBranch = {
  terminal_start: string;
  terminal_end: string;
};

type TerminalOverhangStop = {
  lon: number;
  lat: number;
};

type TerminalOverhangTrimStageInput = {
  bundleArtifacts: TerminalOverhangTrimBundleArtifacts;
  stationsGeoJsonPath: string;
  branchesByRoute: Map<string, TerminalOverhangBranch[]>;
  stopsById: Map<string, TerminalOverhangStop>;
};

export function applyTerminalOverhangTrimStage({
  bundleArtifacts,
  stationsGeoJsonPath,
  branchesByRoute,
  stopsById,
}: TerminalOverhangTrimStageInput): void {
  // =====================================================================
  // Terminal overhang trim
  // =====================================================================
  // Lanes are sliced from full OpenData line geometry, which keeps running past
  // the last passenger station into yards / non-revenue track. Trim every free
  // lane end back to the outermost station that projects onto it (+ grace).
  {
    const stationsDoc = JSON.parse(
      readFileSync(stationsGeoJsonPath, "utf8"),
    );
    // True service terminals from the Gate 2A GTFS branch sequences. Cuts are
    // only allowed where the boundary coincides with one of these -- station
    // route lists alone are weekday-pattern and misclassify branch geometry.
    const routeTerminals = [];
    for (const [routeId, branches] of branchesByRoute) {
      for (const branch of branches) {
        for (const stopId of [branch.terminal_start, branch.terminal_end]) {
          const stop = stopsById.get(stopId);
          if (!stop || !Number.isFinite(stop.lon) || !Number.isFinite(stop.lat)) continue;
          routeTerminals.push({ route: routeId, coord: [stop.lon, stop.lat] });
        }
      }
    }
    // Two passes: dropping a spur can expose the end it was attached to (the
    // attachment snapshot is taken before splicing), so a second pass reaches
    // the fixpoint (e.g. the SI tail past Tottenville chained to a yard spur).
    for (let pass = 1; pass <= 2; pass += 1) {
      const trimSummary = trimTerminalOverhang({
        features: bundleArtifacts.visualFeatures,
        stations: stationsDoc,
        terminals: routeTerminals as any,
      });
      console.log(
        `[visual-network] terminal overhang pass ${pass}: ${trimSummary.trimmedEnds} free ends trimmed, ${trimSummary.removedM}m removed, ${trimSummary.droppedSpurs} spurs dropped`,
      );
      for (const action of trimSummary.actions ?? []) {
        console.log(`[visual-network]   trim ${JSON.stringify(action)}`);
      }
      if (trimSummary.trimmedEnds === 0 && trimSummary.droppedSpurs === 0) break;
    }
  }
}
