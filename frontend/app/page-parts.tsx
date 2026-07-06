/** Imperative handle the map exposes to the shell for camera controls. */
export type MapActions = {
  recenter: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  resetNorth: () => void;
};
