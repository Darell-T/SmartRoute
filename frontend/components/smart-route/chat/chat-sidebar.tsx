"use client";

import Image from "next/image";
import { motion, useReducedMotion } from "motion/react";
import { useState, useSyncExternalStore, type ComponentType, type SVGProps } from "react";
import { Map as MapIcon } from "iconoir-react";
import {
  MessageCircle,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  SquarePen,
  Sun,
} from "lucide-react";
import type { AppTab } from "@/app/page-parts";
import type { ChatTheme } from "@/lib/hooks/use-chat-theme";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

type SidebarIcon = ComponentType<SVGProps<SVGSVGElement>>;
type IconMotion = "lift" | "rotate" | "open";
type SidebarTransform = { x: number; y: number; rotate: number; scale: number };

const SIDEBAR_ICON_REST: SidebarTransform = { x: 0, y: 0, rotate: 0, scale: 1 };
const SIDEBAR_ICON_MOTION = {
  lift: { x: 0, y: -1, rotate: 0, scale: 1.025 },
  rotate: { x: 0, y: 0, rotate: 9, scale: 1.025 },
  open: { x: 0.8, y: 0, rotate: 0, scale: 1.025 },
} satisfies Record<IconMotion, SidebarTransform>;

function subscribeToHydration() {
  return () => undefined;
}

function useHydrated(): boolean {
  return useSyncExternalStore(subscribeToHydration, () => true, () => false);
}

type SidebarItemProps = {
  label: string;
  icon: SidebarIcon;
  motionEffect?: IconMotion;
  active?: boolean;
  description?: string;
  onClick?: () => void;
};

function AnimatedSidebarIcon({
  icon: Icon,
  active,
  engaged,
  effect = "lift",
}: {
  icon: SidebarIcon;
  active?: boolean;
  engaged?: boolean;
  effect?: IconMotion;
}) {
  const hydrated = useHydrated();
  const prefersReducedMotion = useReducedMotion() ?? false;
  // Motion's media-query value is client-only. Keep the server's initial
  // attribute stable until hydration completes, then honor the preference.
  const reduceMotion = hydrated && prefersReducedMotion;
  const transform = reduceMotion || !engaged
    ? SIDEBAR_ICON_REST
    : (SIDEBAR_ICON_MOTION[effect] ?? SIDEBAR_ICON_MOTION.lift);
  const iconState = active ? "active" : engaged ? "engaged" : "rest";

  return (
    <motion.span
      className="sr-app-sidebar__animated-icon"
      data-state={iconState}
      data-reduced-motion={reduceMotion ? "true" : "false"}
      animate={transform}
      transition={{
        duration: reduceMotion ? 0 : 0.19,
        ease: [0.22, 1, 0.36, 1],
      }}
    >
      <Icon width={20} height={20} strokeWidth={1.85} aria-hidden="true" />
    </motion.span>
  );
}

function SidebarItem({
  label,
  icon,
  motionEffect = "lift",
  active = false,
  description,
  onClick,
}: SidebarItemProps) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const engaged = hovered || focused;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          className="sr-app-sidebar__item"
          data-active={active ? "true" : "false"}
          aria-current={active ? "page" : undefined}
          aria-label={label}
          onPointerEnter={() => setHovered(true)}
          onPointerLeave={() => setHovered(false)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onClick={onClick}
        >
          <span className="sr-app-sidebar__item-icon" aria-hidden="true">
            <AnimatedSidebarIcon
              icon={icon}
              active={active}
              engaged={engaged}
              effect={motionEffect}
            />
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
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

export function ChatSidebar({
  activeTab,
  collapsed,
  theme,
  onOpenChat,
  onOpenLiveMap,
  onNewTrip,
  onToggleCollapsed,
  onToggleTheme,
}: {
  activeTab: AppTab;
  collapsed: boolean;
  theme: ChatTheme;
  onOpenChat: () => void;
  onOpenLiveMap: () => void;
  onNewTrip: () => void;
  onToggleCollapsed: () => void;
  onToggleTheme: () => void;
}) {
  const [collapseEngaged, setCollapseEngaged] = useState(false);
  const [themeEngaged, setThemeEngaged] = useState(false);

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
          icon={MessageCircle}
          active={activeTab === "chat"}
          onClick={onOpenChat}
        />
        <SidebarItem
          label="Transit Map"
          description="Live feed · Alerts · Routes"
          icon={MapIcon}
          motionEffect="open"
          active={activeTab === "livemap"}
          onClick={onOpenLiveMap}
        />
        <SidebarItem
          label="New Trip"
          icon={SquarePen}
          motionEffect="open"
          onClick={onNewTrip}
        />
      </nav>

      <div className="sr-app-sidebar__footer">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              className="sr-app-sidebar__control sr-app-sidebar__collapse-control"
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              onPointerEnter={() => setCollapseEngaged(true)}
              onPointerLeave={() => setCollapseEngaged(false)}
              onFocus={() => setCollapseEngaged(true)}
              onBlur={() => setCollapseEngaged(false)}
              onClick={onToggleCollapsed}
            >
              <span className="sr-app-sidebar__control-icon" aria-hidden="true">
                <AnimatedSidebarIcon
                  icon={collapsed ? PanelLeftOpen : PanelLeftClose}
                  engaged={collapseEngaged}
                  effect="open"
                />
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
              onPointerEnter={() => setThemeEngaged(true)}
              onPointerLeave={() => setThemeEngaged(false)}
              onFocus={() => setThemeEngaged(true)}
              onBlur={() => setThemeEngaged(false)}
              onClick={onToggleTheme}
            >
              <span className="sr-app-sidebar__theme-icon" aria-hidden="true">
                <AnimatedSidebarIcon
                  icon={theme === "dark" ? Sun : Moon}
                  engaged={themeEngaged}
                  effect="rotate"
                />
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
