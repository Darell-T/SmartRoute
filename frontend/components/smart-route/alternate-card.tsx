"use client";

export interface AlternateRoute {
  id: string;
  label: string;
  verdict: "RECOMMENDED" | "DELAYED" | "SLOWER" | "ALT";
  confidence: number;
  eta: string;
  totalMin: number;
  reasonShort: string;
}

interface Props {
  route: AlternateRoute;
  accent: string;
}

export function AlternateCard({ route, accent }: Props) {
  const badgeColor =
    route.verdict === "DELAYED"
      ? "#ff6868"
      : route.verdict === "SLOWER"
        ? "#f0b04a"
        : accent;

  return (
    <div
      className="cursor-pointer"
      style={{
        background: "rgba(255,255,255,0.025)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: 12,
        padding: 14,
        transition: "background 0.2s, border-color 0.2s",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "rgba(255,255,255,0.045)";
        e.currentTarget.style.borderColor = "rgba(255,255,255,0.12)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "rgba(255,255,255,0.025)";
        e.currentTarget.style.borderColor = "rgba(255,255,255,0.06)";
      }}
    >
      <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
        <span
          style={{
            fontFamily: "var(--font-geist), sans-serif",
            fontSize: 9,
            letterSpacing: "0.14em",
            color: badgeColor,
            padding: "2px 7px",
            borderRadius: 999,
            border: `1px solid ${badgeColor}55`,
            background: `${badgeColor}11`,
            fontWeight: 600,
          }}
        >
          {route.verdict}
        </span>
        <span
          style={{
            fontFamily: "var(--font-jetbrains-mono), monospace",
            fontSize: 9,
            color: "rgba(255,255,255,0.4)",
            letterSpacing: "0.08em",
          }}
        >
          0.{route.confidence}
        </span>
      </div>
      <div
        style={{
          fontFamily: "var(--font-instrument-serif), serif",
          fontSize: 19,
          color: "#fff",
          lineHeight: 1.1,
          marginBottom: 6,
        }}
      >
        {route.label}
      </div>
      <div
        className="flex gap-3.5"
        style={{
          fontFamily: "var(--font-jetbrains-mono), monospace",
          fontSize: 11,
          color: "rgba(255,255,255,0.65)",
        }}
      >
        <span>{route.totalMin} min</span>
        <span style={{ color: "rgba(255,255,255,0.35)" }}>ETA {route.eta}</span>
      </div>
      <div
        style={{
          marginTop: 8,
          fontFamily: "var(--font-geist), sans-serif",
          fontSize: 11.5,
          color: "rgba(255,255,255,0.55)",
          lineHeight: 1.4,
        }}
      >
        {route.reasonShort}
      </div>
    </div>
  );
}
