import { Expand, LocateFixed } from "lucide-react";

interface Props {
  onExpand?: () => void;
  onRecenter?: () => void;
}

export function MapMiniControls({ onExpand, onRecenter }: Props) {
  const buttonClassName =
    "grid h-11 w-11 place-items-center rounded-xl border border-white/10 bg-[#07101b]/85 text-[rgba(255,255,255,.82)] shadow-[0_16px_34px_rgba(0,0,0,0.34),inset_0_1px_0_rgba(255,255,255,0.08)] backdrop-blur-md transition hover:border-white/20 hover:bg-white/[0.08] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400 active:scale-95";

  return (
    <div
      className="absolute right-4 top-4 z-10 flex flex-col gap-2"
      aria-label="Map controls"
    >
      <button
        type="button"
        className={buttonClassName}
        aria-label="Toggle fullscreen"
        onClick={onExpand}
      >
        <Expand size={18} strokeWidth={1.5} aria-hidden="true" />
      </button>
      <button
        type="button"
        className={buttonClassName}
        aria-label="Recenter map"
        onClick={onRecenter}
      >
        <LocateFixed size={18} strokeWidth={1.5} aria-hidden="true" />
      </button>
    </div>
  );
}
