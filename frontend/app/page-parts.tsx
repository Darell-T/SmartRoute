import type { RouteRailStatus } from "@/components/smart-route/left-rail";
import type { RouteCard } from "@/lib/agent-chat/route-card-contract";
import { normalizeRouteCoordinate } from "@/lib/agent-chat/route-selection";
import type { ArrivalsTurnPayload, ChatTurn } from "@/lib/agent-chat/state";
import type { DestinationSelection, RouteStep } from "@/types";

/** Imperative handle the map exposes to the shell for camera controls. */
export type MapActions = {
  recenter: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  resetNorth: () => void;
};

/** The two top-level app tabs (`.sr-tab-shell[data-tab]`). Distinct from the
 *  left rail's own `TabId` ("route" | "alerts"), which is an internal tab
 *  set scoped to the Live Map panel's rail. */
export type AppTab = "chat" | "livemap";

type ShellPanelProps = {
  hiddenClass: string;
  inert: true | undefined;
};

export function shellPanelProps(isActive: boolean) {
  if (isActive) return { hiddenClass: "", inert: undefined } satisfies ShellPanelProps;
  return { hiddenClass: " sr-tab-shell__panel--hidden", inert: true } satisfies ShellPanelProps;
}

export function transitRouteData<TStep, TItinerary>(
  steps: TStep[],
  itinerary: TItinerary | undefined,
) {
  return steps.length > 0 ? { steps, itinerary } : null;
}

export function routeRailStatus(
  isLoading: boolean,
  errorText: string | null | undefined,
  hasCandidate: boolean,
): RouteRailStatus {
  if (isLoading) return "thinking";
  if (errorText) return "error";
  return hasCandidate ? "result" : "standby";
}

export function destinationCoordinatesFromRoute(
  steps: RouteStep[],
  selectedCoordinates: { lat: number; lng: number } | null | undefined,
): { lat: number; lng: number } | null {
  const lastStep = steps[steps.length - 1];
  const rawDest =
    lastStep?.type === "WALK" ? lastStep.end_point : lastStep?.arrival_coords;
  return normalizeRouteCoordinate(rawDest) ?? selectedCoordinates ?? null;
}

export function routeCardsForSelection(
  messages: ChatTurn[],
  cardId: string,
  fallback: RouteCard[],
): RouteCard[] {
  const sourceTurn = [...messages]
    .reverse()
    .find(
      (turn) =>
        turn.role === "assistant" &&
        turn.routeCards.some((candidate) => candidate.card_id === cardId),
    );
  return sourceTurn?.role === "assistant" ? sourceTurn.routeCards : fallback;
}

export function nearbyStationSelection(
  arrivals: ArrivalsTurnPayload,
): DestinationSelection | null {
  if (!arrivals.stationCoordinates) return null;
  return {
    label: `${arrivals.stationName} station`,
    coordinates: arrivals.stationCoordinates,
  };
}

export async function toggleElementFullscreen(
  target: HTMLElement | null,
  fullscreen: { element: Element | null; exit: () => Promise<void> },
  onFailure: () => void,
): Promise<void> {
  if (!target) return;
  try {
    if (fullscreen.element) {
      await fullscreen.exit();
      return;
    }
    await target.requestFullscreen();
  } catch {
    onFailure();
  }
}
