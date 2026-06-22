"use client";

import type { CSSProperties } from "react";
import { AlertTriangle, Clock, Info } from "lucide-react";
import type { LiveVehicle } from "@/types";
import { TrainBullet } from "@/components/smart-route/train-bullet";

interface SmartTrainMarkerProps {
  vehicle: LiveVehicle;
  selected: boolean;
  speedLabel: string;
  onSelect: () => void;
}

function withAlpha(color: string, alpha: string) {
  return /^#[0-9a-fA-F]{6}$/.test(color) ? `${color}${alpha}` : color;
}

function sourceLabel(vehicle: LiveVehicle) {
  if (vehicle.position_source === "vehicle_position") return "Vehicle GPS";
  if (vehicle.position_source === "polyline_estimate") return "Track estimate";
  if (vehicle.position_source === "stop_id") return "Stop report";
  return "MTA report";
}

function serviceLabel(vehicle: LiveVehicle) {
  const routeName = vehicle.route_name?.toLowerCase() || "";
  if (routeName.includes("express")) return "EXP";
  if (routeName.includes("local")) return "LOCAL";
  if (vehicle.status?.toUpperCase().includes("STOPPED")) return "AT STOP";
  return "MOVING";
}

function lastPing(vehicle: LiveVehicle) {
  if (vehicle.age_seconds == null) return "--";
  if (vehicle.age_seconds < 60) return `${vehicle.age_seconds}s ago`;
  return `${Math.floor(vehicle.age_seconds / 60)}m ${vehicle.age_seconds % 60}s ago`;
}

function lastPingCompact(vehicle: LiveVehicle) {
  if (vehicle.age_seconds == null) return "--";
  if (vehicle.age_seconds < 60) return `${vehicle.age_seconds}s`;
  return `${Math.floor(vehicle.age_seconds / 60)}m ${vehicle.age_seconds % 60}s`;
}

function VehicleBody({
  routeColor,
  stale,
  selected,
}: {
  routeColor: string;
  stale: boolean;
  selected: boolean;
}) {
  const transform = "translate(-50%, -50%) rotate(var(--train-bearing, 0deg))";
  const shellBorder = stale
    ? "rgba(248, 113, 113, 0.55)"
    : withAlpha(routeColor, "66");
  const shellBackground = stale
    ? "linear-gradient(180deg, #d1d5db 0%, #9ca3af 100%)"
    : "linear-gradient(180deg, #f8fafc 0%, #dbe4ec 52%, #b6c1cd 100%)";
  const cabBackground = stale
    ? "linear-gradient(180deg, #3f3f46 0%, #18181b 100%)"
    : "linear-gradient(180deg, #17202c 0%, #090d13 100%)";

  return (
    <div
      className="pointer-events-none absolute left-1/2 top-1/2 z-30 origin-center"
      style={{ transform, transformOrigin: "50% 50%" }}
    >
      {!stale ? (
        <div
          className="sr-train-glow absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
          style={{ background: routeColor }}
        />
      ) : (
        <div className="sr-train-alert-ping absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2" />
      )}

      <div
        className="relative flex h-[10px] w-[30px] items-center rounded-[4px] border shadow-[0_3px_6px_rgba(0,0,0,0.28)]"
        style={{
          borderColor: shellBorder,
          background: shellBackground,
          boxShadow: selected
            ? `0 0 0 1px ${withAlpha(routeColor, "44")}, 0 4px 10px rgba(0,0,0,0.34)`
            : undefined,
        }}
      >
        <div
          className="absolute inset-[1px] rounded-[3px]"
          style={{
            background: stale
              ? "linear-gradient(180deg, rgba(255,255,255,0.18), rgba(255,255,255,0.02))"
              : "linear-gradient(180deg, rgba(255,255,255,0.42), rgba(255,255,255,0.12))",
          }}
        />
        <div
          className="absolute left-[4px] top-[2px] h-[5px] w-[15px] rounded-[2px] border"
          style={{
            borderColor: stale
              ? "rgba(82, 82, 91, 0.7)"
              : "rgba(71, 85, 105, 0.55)",
            background: stale
              ? "linear-gradient(180deg, rgba(161,161,170,0.68), rgba(82,82,91,0.88))"
              : "linear-gradient(180deg, rgba(203,213,225,0.9), rgba(148,163,184,0.7))",
          }}
        />
        <div
          className="absolute left-[5px] right-[9px] top-[3px] h-[1px] rounded-full"
          style={{
            background: stale ? "rgba(63,63,70,0.3)" : "rgba(15,23,42,0.18)",
          }}
        />

        <div
          className="absolute right-[1px] top-[1px] bottom-[1px] w-[6px] rounded-[3px]"
          style={{
            background: stale
              ? "repeating-linear-gradient(45deg, #dc2626, #dc2626 2px, #fee2e2 2px, #fee2e2 4px)"
              : cabBackground,
            boxShadow: stale
              ? "inset 0 0 0 1px rgba(24,24,27,0.28)"
              : "inset 1px 0 0 rgba(255,255,255,0.3)",
          }}
        >
          {!stale ? (
            <>
              <div className="sr-dest-sign absolute right-[2px] top-1/2 h-[3px] w-[1px] -translate-y-1/2 bg-[#ffb000]" />
              <div className="absolute left-[1px] top-[2px] flex flex-col gap-[1px]">
                <div className="sr-headlight" />
                <div
                  className="sr-headlight"
                  style={{ animationDelay: "-0.8s" }}
                />
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function VehicleStatusChip({
  label,
  tone,
}: {
  label: string;
  tone: "live" | "stale";
}) {
  const palette =
    tone === "stale"
      ? {
          border: "rgba(248,113,113,0.28)",
          background: "rgba(127,29,29,0.36)",
          color: "#fca5a5",
        }
      : {
          border: "rgba(110,231,183,0.22)",
          background: "rgba(6,78,59,0.28)",
          color: "#86efac",
        };

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        padding: "4px 8px",
        borderRadius: 999,
        border: `1px solid ${palette.border}`,
        background: palette.background,
        color: palette.color,
        fontFamily: "var(--font-jetbrains-mono), monospace",
        fontSize: 9,
        fontWeight: 700,
        letterSpacing: "0.1em",
        textTransform: "uppercase",
        whiteSpace: "nowrap",
      }}
    >
      <span
        style={{
          width: 5,
          height: 5,
          borderRadius: "50%",
          background: palette.color,
          boxShadow: `0 0 8px ${palette.color}`,
          flexShrink: 0,
        }}
      />
      {label}
    </span>
  );
}

function VehicleDetailCard({
  vehicle,
  routeColor,
  routeId,
  speedLabel,
}: {
  vehicle: LiveVehicle;
  routeColor: string;
  routeId: string;
  speedLabel: string;
}) {
  const stopName = vehicle.stop_name || vehicle.stop_id || "Next stop pending";
  const borderColor = vehicle.stale
    ? "rgba(248,113,113,0.28)"
    : withAlpha(routeColor, "44");
  const accentColor = vehicle.stale ? "#f87171" : routeColor;

  return (
    <div className="pointer-events-none flex flex-col items-start">
      <div
        className="min-w-[148px] max-w-[168px] overflow-hidden rounded-[13px] border shadow-[0_12px_28px_rgba(0,0,0,0.32)]"
        style={{
          borderColor,
          background: vehicle.stale
            ? "linear-gradient(180deg, rgba(31,11,14,0.96), rgba(13,8,10,0.96))"
            : "linear-gradient(180deg, rgba(11,15,23,0.96), rgba(7,10,16,0.96))",
        }}
      >
        <div
          className="flex items-center justify-between gap-2 px-2.5 py-2"
          style={{
            borderBottom: `1px solid ${vehicle.stale ? "rgba(248,113,113,0.16)" : "rgba(255,255,255,0.08)"}`,
          }}
        >
          <div className="flex min-w-0 items-center gap-1.5">
            <TrainBullet
              line={routeId}
              size={20}
              className="shrink-0"
              title={`${routeId} train`}
            />
            <div className="min-w-0">
              <div
                className="truncate"
                style={{
                  fontFamily: "var(--font-geist), sans-serif",
                  fontSize: 11.5,
                  fontWeight: 600,
                  color: "#f8fafc",
                }}
              >
                {routeId} Train
              </div>
              <div
                style={{
                  marginTop: 2,
                  fontFamily: "var(--font-jetbrains-mono), monospace",
                  fontSize: 8.5,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  color: vehicle.stale ? "#fca5a5" : "#7dd3fc",
                }}
              >
                {vehicle.stale ? "Stale telemetry" : serviceLabel(vehicle)}
              </div>
            </div>
          </div>

          {vehicle.stale ? (
            <div className="flex items-center gap-1 text-[#fca5a5]">
              <Clock size={10} />
              <span
                style={{
                  fontFamily: "var(--font-jetbrains-mono), monospace",
                  fontSize: 9,
                  fontWeight: 700,
                }}
              >
                {lastPingCompact(vehicle)}
              </span>
            </div>
          ) : (
            <VehicleStatusChip label={speedLabel} tone="live" />
          )}
        </div>

        <div className="px-2.5 py-2">
          <div className="flex items-center gap-1.5">
            {vehicle.stale ? (
              <AlertTriangle size={10} className="shrink-0 text-[#f87171]" />
            ) : (
              <Info size={10} className="shrink-0 text-[#7dd3fc]" />
            )}
            <span
              className="truncate"
              style={{
                fontFamily: "var(--font-geist), sans-serif",
                fontSize: 10,
                fontWeight: 500,
                color: "#e2e8f0",
              }}
            >
              {vehicle.stale
                ? "Holding position near tracked route segment"
                : stopName}
            </span>
          </div>

          <div
            className="mt-2 flex items-center justify-between gap-2"
            style={{
              fontFamily: "var(--font-jetbrains-mono), monospace",
              fontSize: 8.5,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              color: "rgba(226,232,240,0.52)",
            }}
          >
            <span className="truncate">{sourceLabel(vehicle)}</span>
            <span style={{ color: vehicle.stale ? "#fca5a5" : accentColor }}>
              {lastPing(vehicle)}
            </span>
          </div>
        </div>
      </div>

      <div className="relative h-5 w-6 overflow-visible">
        <svg
          className="absolute left-2 top-0 h-5 w-6 overflow-visible"
          viewBox="0 0 24 20"
          aria-hidden="true"
        >
          <polyline
            points="2,0 2,8 14,18"
            fill="none"
            stroke={accentColor}
            strokeWidth="1.35"
            opacity="0.78"
          />
          <circle cx="14" cy="18" r="1.4" fill={accentColor} />
        </svg>
      </div>
    </div>
  );
}

export function SmartTrainMarker({
  vehicle,
  selected,
  speedLabel,
  onSelect,
}: SmartTrainMarkerProps) {
  const routeColor = vehicle.color || "#FCCC0A";
  const routeId = vehicle.route_id || "?";
  const cssVars = { "--route-color": routeColor } as CSSProperties;

  return (
    <button
      type="button"
      aria-label={`${routeId} train ${selected ? "selected" : "marker"}`}
      className="group relative grid h-10 w-10 cursor-pointer place-items-center border-0 bg-transparent p-0 text-left outline-none focus-visible:rounded-full focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#0a0d13]"
      style={cssVars}
      onClick={(event) => {
        event.stopPropagation();
        onSelect();
      }}
    >
      <VehicleBody
        routeColor={routeColor}
        stale={vehicle.stale}
        selected={selected}
      />

      <div
        className={[
          "pointer-events-none absolute z-20 transition-all duration-200",
          vehicle.stale
            ? "-top-[58px] left-1/2 w-[160px] -translate-x-1/2"
            : "bottom-[36px] left-[28px] w-[168px]",
          selected ? "translate-y-0 opacity-100" : "translate-y-1 opacity-0",
        ].join(" ")}
      >
        <VehicleDetailCard
          vehicle={vehicle}
          routeColor={routeColor}
          routeId={routeId}
          speedLabel={speedLabel}
        />
      </div>

      <span className="sr-only">
        Tactical live-train marker centered on the current rail position.
      </span>
    </button>
  );
}
