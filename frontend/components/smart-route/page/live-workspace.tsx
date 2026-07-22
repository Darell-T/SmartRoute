"use client";

import { useEffect, useState, type CSSProperties, type RefObject } from "react";
import { SmartRouteMap } from "@/components/smart-route/map/smart-route-map";
import {
  LeftRail,
  type LeftRailProps,
  type RouteRailStatus,
} from "@/components/smart-route/left-rail";
import { MapMiniControls } from "@/components/smart-route/map-mini-controls";
import type { MapActions } from "@/app/page-parts";
import type { TransitRouteData } from "@/types";
import type { MobileRailSheetController } from "./use-mobile-rail-sheet";
import type { RoutePlanningController } from "./use-route-planning-controller";

type LiveWorkspaceProps = {
  mobileRail: MobileRailSheetController;
  routePlanning: RoutePlanningController;
  leftRailData: LeftRailProps["data"];
  routeStatus: RouteRailStatus;
  hasActiveRoute: boolean;
  liveMap: LiveWorkspaceMapProps;
};

type LiveWorkspaceMapProps = {
  frameRef: RefObject<HTMLElement | null>;
  routeData: TransitRouteData | null;
  destCoords: { lat: number; lng: number } | null;
  onLocationUpdate: (coords: { lng: number; lat: number }) => void;
  onMapReady: (actions: MapActions) => void;
  onExpand: () => void;
  onRecenter: () => void;
};

export function LiveWorkspace({
  mobileRail, routePlanning, leftRailData, routeStatus, hasActiveRoute,
  liveMap,
}: LiveWorkspaceProps) {
  const [routeSearchFocused, setRouteSearchFocused] = useState(false);
  const {
    frameRef, routeData, destCoords, onLocationUpdate, onMapReady, onExpand, onRecenter,
  } = liveMap;
  const { syncMobileRailAppState } = mobileRail;

  const mobileRailAppState = routeSearchFocused
    ? "search"
    : routeStatus === "thinking"
      ? "loading"
      : routeStatus === "result"
        ? "result"
        : routeStatus === "error"
          ? "error"
          : "idle";

  useEffect(() => {
    syncMobileRailAppState(mobileRailAppState);
  }, [mobileRailAppState, syncMobileRailAppState]);

  const liveRailShellStyle = {
    position: "absolute",
    top: 14,
    left: 14,
    bottom: 14,
    width: 420,
    zIndex: 20,
    padding: 0,
    border: "none",
    background: "transparent",
    borderRadius: 22,
    overflow: "hidden",
    display: "flex",
    "--sr-mobile-sheet-height": mobileRail.mobileRailSheetHeight,
  } as CSSProperties;

  return (
    <div
      className="sr-live-console"
      data-mobile-sheet-state={mobileRail.mobileRailSheet}
      style={{ gridTemplateColumns: "minmax(0, 1fr)", position: "relative" }}
    >
      <aside
        className="sr-live-left-rail-shell"
        data-mobile-sheet-state={mobileRail.mobileRailSheet}
        data-mobile-sheet-dragging={
          mobileRail.isMobileRailDragging ? "true" : undefined
        }
        aria-label="SmartRoute Left Rail"
        style={liveRailShellStyle}
      >
        <button
          type="button"
          className="sr-mobile-rail-grip"
          aria-label="Resize route panel"
          aria-expanded={mobileRail.mobileRailSheet !== "small"}
          onPointerDown={mobileRail.handleMobileRailPointerDown}
          onPointerMove={mobileRail.handleMobileRailPointerMove}
          onPointerUp={mobileRail.handleMobileRailPointerUp}
          onPointerCancel={mobileRail.handleMobileRailPointerCancel}
          onKeyDown={mobileRail.handleMobileRailKeyDown}
        >
          <span aria-hidden="true" />
        </button>
        <div className="sr-mobile-rail-body">
          <LeftRail
            width={420}
            routeStatus={routeStatus}
            data={leftRailData}
            onSelectAlternative={routePlanning.handleSelectAlternative}
            search={{
              inputValue: routePlanning.inputValue,
              isLoading: routePlanning.isLoading,
              planningPhase: routePlanning.planningPhase,
              hasActiveRoute,
              onInputChange: routePlanning.handleDestinationInputChange,
              onSubmit: routePlanning.handleSearchSubmit,
              onCancelPlanning: routePlanning.handleCancelRoutePlanning,
              onClear: routePlanning.handleClearRoute,
            }}
            onRouteSearchFocusChange={setRouteSearchFocused}
            onRailInteraction={mobileRail.expandMobileRailSheet}
          />
        </div>
      </aside>

      <section
        ref={frameRef}
        className="sr-shell-canvas sr-shell-canvas--map sr-live-console__map"
      >
        <div className="absolute inset-0">
          <SmartRouteMap
            onLocationUpdate={onLocationUpdate}
            routeData={routeData}
            destCoords={destCoords}
            mobileSheetState={mobileRail.mobileRailSheet}
            onMapReady={onMapReady}
          />
        </div>

        <div className="sr-live-map-vignette" aria-hidden="true" />
        <MapMiniControls onExpand={onExpand} onRecenter={onRecenter} />
      </section>
    </div>
  );
}
