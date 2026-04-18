interface Props {
  accent?: string;
}

export function SmartRouteMark({ accent = "#d4a7ff" }: Props) {
  return (
    <div className="flex items-center gap-2.5">
      <svg width="28" height="28" viewBox="0 0 28 28">
        <circle cx="14" cy="14" r="12" fill="none" stroke={accent} strokeWidth="1.2" opacity="0.6" />
        <circle cx="14" cy="14" r="5" fill="none" stroke={accent} strokeWidth="1.2" />
        <circle cx="14" cy="14" r="1.6" fill={accent} />
        <path
          d="M 4 14 L 9 14 M 19 14 L 24 14 M 14 4 L 14 9 M 14 19 L 14 24"
          stroke={accent}
          strokeWidth="1.2"
          strokeLinecap="round"
        />
      </svg>
      <div
        style={{
          fontFamily: "var(--font-geist), system-ui, sans-serif",
          fontWeight: 600,
          letterSpacing: "0.06em",
          fontSize: 14,
        }}
      >
        <span style={{ color: "rgba(255,255,255,0.55)" }}>SMART</span>
        <span style={{ color: "#fff", marginLeft: 4 }}>ROUTE</span>
      </div>
    </div>
  );
}
