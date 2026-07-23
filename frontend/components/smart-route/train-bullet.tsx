"use client";

const BULLET_NAME_BY_ROUTE: Record<string, string> = {
  "6X": "6d",
  "7X": "7d",
  FX: "fd",
  FS: "sf",
  SI: "sir",
  SIR: "sir",
  GS: "s",
  H: "s",
  S: "s",
};

export const SUBWAY_BULLET_ROUTES = new Set([
  "1", "2", "3", "4", "5", "6", "6X", "7", "7X",
  "A", "B", "C", "D", "E", "F", "FX", "G", "J", "L", "M",
  "N", "Q", "R", "W", "Z", "FS", "GS", "H", "S", "SI", "SIR",
]);

export function subwayBulletName(routeId: string) {
  const normalized = routeId.trim().toUpperCase();
  if (!normalized) return "s";
  if (BULLET_NAME_BY_ROUTE[normalized]) return BULLET_NAME_BY_ROUTE[normalized];
  return normalized.toLowerCase();
}

export function subwayBulletSrc(routeId: string) {
  return `/mta-bullets/${subwayBulletName(routeId)}.svg`;
}

interface TrainBulletProps {
  line: string;
  size?: number;
  className?: string;
  title?: string;
}

export function TrainBullet({
  line,
  size = 22,
  className,
  title,
}: TrainBulletProps) {
  const normalized = line.trim().toUpperCase();
  const isSubway = SUBWAY_BULLET_ROUTES.has(normalized);
  const label = title || `${line} ${isSubway ? "train" : "bus"}`;

  if (!isSubway) {
    return (
      <span
        className={className}
        title={label}
        aria-label={label}
        style={{
          minWidth: Math.max(size + 10, 34),
          height: size,
          padding: "0 6px",
          borderRadius: 5,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          background: "#5f8fd9",
          color: "#fff",
          fontSize: Math.max(9, Math.round(size * 0.42)),
          fontWeight: 800,
          lineHeight: 1,
          letterSpacing: 0,
        }}
      >
        {normalized}
      </span>
    );
  }

  return (
    <span
      className={className}
      title={label}
      aria-label={label}
      role="img"
      style={{
        width: size,
        height: size,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
      }}
    >
      {/* Decorative: accessible name lives on the wrapper to avoid duplicate SR output. */}
      <img
        src={subwayBulletSrc(line)}
        alt=""
        aria-hidden="true"
        draggable={false}
        style={{
          width: size,
          height: size,
          display: "block",
        }}
      />
    </span>
  );
}
