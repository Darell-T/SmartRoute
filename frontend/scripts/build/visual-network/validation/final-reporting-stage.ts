import type { StopsById } from "../inputs/gtfs-topology.ts";
import type { TopologyDoc } from "../inputs/gtfs-topology-stage.ts";

type FinalTopologySummaryStageInput = {
  topologyDoc: TopologyDoc;
  minTripsPerBranch: number;
  droppedLowFreqBranches: number;
  stopsById: StopsById;
};

export function reportFinalTopologySummaryStage({
  topologyDoc,
  minTripsPerBranch,
  droppedLowFreqBranches,
  stopsById,
}: FinalTopologySummaryStageInput): void {
  // Summary log
  console.log(`[visual-network] === Gate 2A topology summary ===`);
  console.log(
    `[visual-network] distinct routes: ${topologyDoc.topology.distinct_routes}`,
  );
  console.log(
    `[visual-network] total branches (>= ${minTripsPerBranch} trips): ${topologyDoc.topology.total_branches}`,
  );
  console.log(
    `[visual-network] dropped low-frequency branches: ${droppedLowFreqBranches}`,
  );
  console.log(`[visual-network] --- per-route branch summary ---`);
  console.log(`[visual-network]   route  branches  stations  branch terminals`);
  for (const r of topologyDoc.per_route) {
    const terminals = r.branches
      .slice(0, 4)
      .map((b: any) =>
        `${(b.direction_id || "?")}:${(stopsById.get(b.terminal_start)?.name ?? b.terminal_start)} → ${(stopsById.get(b.terminal_end)?.name ?? b.terminal_end)} (${b.total_trips_in_branch}tr)`,
      )
      .join("; ");
    console.log(
      `[visual-network]   ${r.route_id.padEnd(5)} ${String(r.branch_count).padStart(8)} ${String(r.distinct_stations).padStart(9)}  ${terminals}`,
    );
  }

  console.log("[visual-network] Gate 2A complete. Topology written to debug JSON.");
  console.log("[visual-network] Gate 2B used NYC OpenData full-line geometry; GTFS shapes.txt was not used for visual rendering.");
}
