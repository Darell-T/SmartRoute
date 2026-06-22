import { Expand, LocateFixed } from "lucide-react";

interface Props {
  onExpand?: () => void;
  onRecenter?: () => void;
}

export function MapMiniControls({ onExpand, onRecenter }: Props) {
  return (
    <div className="sr-map-mini" aria-label="Map controls">
      <button
        type="button"
        className="sr-map-mini__button"
        aria-label="Toggle fullscreen"
        onClick={onExpand}
      >
        <Expand size={18} strokeWidth={1.5} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="sr-map-mini__button"
        aria-label="Recenter map"
        onClick={onRecenter}
      >
        <LocateFixed size={18} strokeWidth={1.5} aria-hidden="true" />
      </button>
    </div>
  );
}
