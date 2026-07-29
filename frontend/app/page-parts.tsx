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
