"use client";

import Image from "next/image";
import { AnimatePresence, motion } from "motion/react";
import { useState, type ComponentType, type SVGProps } from "react";
import {
  Bookmark,
  ChatBubble,
  HalfMoon,
  HelpCircle,
  Map,
  MessageText,
  NavArrowDown,
  NavArrowLeft,
  NavArrowRight,
  Plus,
  Settings,
  SunLight,
  Train,
} from "iconoir-react";
import type { AppTab } from "@/app/page-parts";
import type { ChatTheme } from "@/lib/use-chat-theme";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { LineBadge } from "./line-badge";

type SidebarItemProps = {
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  active?: boolean;
  description?: string;
  disabled?: boolean;
  appearance?: "primary" | "secondary";
  onClick?: () => void;
};

function SidebarItem({
  label,
  icon: Icon,
  active = false,
  description,
  disabled = false,
  appearance = "primary",
  onClick,
}: SidebarItemProps) {
  const tooltipLabel = disabled ? `${label}, coming soon` : label;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          className="sr-app-sidebar__item"
          data-active={active ? "true" : "false"}
          data-disabled={disabled ? "true" : "false"}
          data-appearance={appearance}
          aria-current={active ? "page" : undefined}
          aria-label={tooltipLabel}
          disabled={disabled}
          onClick={onClick}
        >
          <span className="sr-app-sidebar__item-icon" aria-hidden="true">
            <Icon width={14} height={14} strokeWidth={1.65} />
          </span>
          <span className="sr-app-sidebar__item-copy">
            <span className="sr-app-sidebar__item-label">{label}</span>
            {description ? (
              <span className="sr-app-sidebar__item-description">{description}</span>
            ) : null}
          </span>
        </Button>
      </TooltipTrigger>
      <TooltipContent side="right" sideOffset={10} className="sr-app-sidebar__tooltip">
        {tooltipLabel}
      </TooltipContent>
    </Tooltip>
  );
}

function NearbyLinesAccordion({
  collapsed,
  routeIds,
  onRequestExpand,
  onSelectLine,
}: {
  collapsed: boolean;
  routeIds: string[];
  onRequestExpand: () => void;
  onSelectLine: (routeId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const visibleRouteIds = routeIds.slice(0, 10);

  function toggleOpen() {
    if (collapsed) {
      setOpen(true);
      onRequestExpand();
      return;
    }
    setOpen((value) => !value);
  }

  return (
    <div className="sr-app-sidebar__nearby" data-open={open ? "true" : "false"}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            className="sr-app-sidebar__item sr-app-sidebar__nearby-trigger"
            data-active={open ? "true" : "false"}
            aria-expanded={open && !collapsed}
            aria-controls="sr-nearby-lines-grid"
            aria-label="Nearby Lines"
            onClick={toggleOpen}
          >
            <span className="sr-app-sidebar__item-icon" aria-hidden="true">
              <Train width={14} height={14} strokeWidth={1.65} />
            </span>
            <span className="sr-app-sidebar__item-copy">
              <span className="sr-app-sidebar__item-label">Nearby Lines</span>
            </span>
            <NavArrowDown
              className="sr-app-sidebar__nearby-chevron"
              width={13}
              height={13}
              strokeWidth={1.7}
              aria-hidden="true"
            />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="right" sideOffset={10} className="sr-app-sidebar__tooltip">
          Nearby Lines
        </TooltipContent>
      </Tooltip>

      <AnimatePresence initial={false}>
        {open && !collapsed ? (
          <motion.div
            id="sr-nearby-lines-grid"
            className="sr-app-sidebar__nearby-panel"
            initial={{ height: 0, opacity: 0, y: -4 }}
            animate={{ height: "auto", opacity: 1, y: 0 }}
            exit={{ height: 0, opacity: 0, y: -4 }}
            transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
          >
            {visibleRouteIds.length > 0 ? (
              <div className="sr-app-sidebar__nearby-grid" aria-label="Subway lines near you">
                {visibleRouteIds.map((routeId) => (
                  <motion.button
                    key={routeId}
                    type="button"
                    className="sr-app-sidebar__line-button"
                    aria-label={`Show ${routeId} train arrivals`}
                    whileHover={{ scale: 1.24, y: -2 }}
                    whileTap={{ scale: 0.94 }}
                    transition={{ type: "spring", stiffness: 430, damping: 24 }}
                    onClick={() => onSelectLine(routeId)}
                  >
                    <LineBadge line={routeId} size={20} />
                  </motion.button>
                ))}
              </div>
            ) : (
              <p className="sr-app-sidebar__nearby-empty">Locating nearby service…</p>
            )}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

export function ChatSidebar({
  activeTab,
  collapsed,
  theme,
  nearbyRouteIds,
  onOpenChat,
  onOpenLiveMap,
  onNewTrip,
  onSelectNearbyLine,
  onToggleCollapsed,
  onToggleTheme,
}: {
  activeTab: AppTab;
  collapsed: boolean;
  theme: ChatTheme;
  nearbyRouteIds: string[];
  onOpenChat: () => void;
  onOpenLiveMap: () => void;
  onNewTrip: () => void;
  onSelectNearbyLine: (routeId: string) => void;
  onToggleCollapsed: () => void;
  onToggleTheme: () => void;
}) {
  return (
    <aside
      className="sr-app-sidebar"
      data-collapsed={collapsed ? "true" : "false"}
      data-theme={theme}
      aria-label="SmartRoute navigation"
    >
      <button
        type="button"
        className="sr-app-sidebar__brand"
        aria-label="Start a new SmartRoute trip"
        onClick={onNewTrip}
      >
        <Image
          src="/smart-route-mark-512.png"
          alt=""
          width={34}
          height={34}
          priority
          aria-hidden="true"
        />
        <span className="sr-app-sidebar__brand-name">SmartRoute</span>
      </button>

      <nav className="sr-app-sidebar__nav" aria-label="Primary">
        <SidebarItem
          label="Chat"
          description="Plan and explore trips"
          icon={ChatBubble}
          active={activeTab === "chat"}
          onClick={onOpenChat}
        />
        <SidebarItem
          label="Transit Map"
          description="Live feed · Alerts · Routes"
          icon={Map}
          active={activeTab === "livemap"}
          onClick={onOpenLiveMap}
        />
        <SidebarItem label="New Trip" icon={Plus} onClick={onNewTrip} />
        <SidebarItem label="Favorites" icon={Bookmark} disabled />
        <NearbyLinesAccordion
          collapsed={collapsed}
          routeIds={nearbyRouteIds}
          onRequestExpand={onToggleCollapsed}
          onSelectLine={onSelectNearbyLine}
        />

        <div className="sr-app-sidebar__nav-rule" aria-hidden="true" />

        <SidebarItem
          label="Feedback"
          icon={MessageText}
          appearance="secondary"
          disabled
        />
        <SidebarItem label="Help" icon={HelpCircle} appearance="secondary" disabled />
        <SidebarItem label="Settings" icon={Settings} appearance="secondary" disabled />
      </nav>

      <div className="sr-app-sidebar__footer">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              className="sr-app-sidebar__control sr-app-sidebar__collapse-control"
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              onClick={onToggleCollapsed}
            >
              <span className="sr-app-sidebar__control-icon" aria-hidden="true">
                {collapsed ? (
                  <NavArrowRight width={14} height={14} strokeWidth={1.7} />
                ) : (
                  <NavArrowLeft width={14} height={14} strokeWidth={1.7} />
                )}
              </span>
              <span className="sr-app-sidebar__control-label">
                {collapsed ? "Expand" : "Collapse"}
              </span>
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={10} className="sr-app-sidebar__tooltip">
            {collapsed ? "Expand sidebar" : "Collapse sidebar"}
          </TooltipContent>
        </Tooltip>

        <div className="sr-app-sidebar__footer-rule" aria-hidden="true" />

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              className="sr-app-sidebar__control sr-app-sidebar__theme"
              aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              onClick={onToggleTheme}
            >
              <span className="sr-app-sidebar__theme-icon" aria-hidden="true">
                {theme === "dark" ? (
                  <SunLight width={14} height={14} strokeWidth={1.5} />
                ) : (
                  <HalfMoon width={14} height={14} strokeWidth={1.5} />
                )}
              </span>
              <span className="sr-app-sidebar__control-label">
                {theme === "dark" ? "Light mode" : "Dark mode"}
              </span>
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={10} className="sr-app-sidebar__tooltip">
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </TooltipContent>
        </Tooltip>
      </div>
    </aside>
  );
}
