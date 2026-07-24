"use client";

import Image from "next/image";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useState, type ComponentType, type SVGProps } from "react";
import {
  Bookmark,
  BookmarkSolid,
  ChatBubble,
  ChatBubbleSolid,
  HalfMoon,
  HelpCircle,
  HelpCircleSolid,
  Map,
  MessageText,
  MessageTextSolid,
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
  filledIcon?: ComponentType<SVGProps<SVGSVGElement>>;
  motionEffect?: "lift" | "rotate" | "open";
  active?: boolean;
  description?: string;
  disabled?: boolean;
  appearance?: "primary" | "secondary";
  onClick?: () => void;
};

function SidebarItem({
  label,
  icon: Icon,
  filledIcon,
  motionEffect = "lift",
  active = false,
  description,
  disabled = false,
  appearance = "primary",
  onClick,
}: SidebarItemProps) {
  const tooltipLabel = disabled ? `${label}, coming soon` : label;
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const engaged = !disabled && (hovered || focused);

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
          onPointerEnter={() => setHovered(true)}
          onPointerLeave={() => setHovered(false)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onClick={onClick}
        >
          <span className="sr-app-sidebar__item-icon" aria-hidden="true">
            <AnimatedSidebarIcon
              icon={Icon}
              filledIcon={filledIcon}
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
        {tooltipLabel}
      </TooltipContent>
    </Tooltip>
  );
}

function AnimatedSidebarIcon({
  icon: OutlineIcon,
  filledIcon: FilledIcon,
  active = false,
  engaged = false,
  effect = "lift",
}: {
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  filledIcon?: ComponentType<SVGProps<SVGSVGElement>>;
  active?: boolean;
  engaged?: boolean;
  effect?: "lift" | "rotate" | "open";
}) {
  const reduceMotion = useReducedMotion() ?? false;
  const showFilled = active || engaged;
  const transform = reduceMotion
    ? { x: 0, y: 0, rotate: 0, scale: 1 }
    : effect === "rotate"
      ? { x: 0, y: 0, rotate: engaged ? 7 : 0, scale: engaged ? 1.02 : 1 }
      : effect === "open"
        ? { x: engaged ? 0.7 : 0, y: 0, rotate: engaged ? -1.5 : 0, scale: engaged ? 1.015 : 1 }
        : { x: 0, y: engaged ? -1 : 0, rotate: 0, scale: engaged ? 1.02 : 1 };
  const transition = {
    duration: reduceMotion ? 0 : 0.19,
    ease: [0.22, 1, 0.36, 1] as const,
  };
  const ActiveIcon = FilledIcon ?? OutlineIcon;

  return (
    <motion.span
      className="sr-app-sidebar__animated-icon"
      data-state={active ? "active" : engaged ? "engaged" : "rest"}
      data-reduced-motion={reduceMotion ? "true" : "false"}
      animate={transform}
      transition={transition}
    >
      <motion.span
        className="sr-app-sidebar__animated-icon-layer sr-app-sidebar__animated-icon-layer--outline"
        animate={{ opacity: showFilled ? 0 : 1 }}
        transition={transition}
      >
        <OutlineIcon width={20} height={20} strokeWidth={1.65} />
      </motion.span>
      <motion.span
        className="sr-app-sidebar__animated-icon-layer sr-app-sidebar__animated-icon-layer--filled"
        animate={{ opacity: showFilled ? 1 : 0 }}
        transition={transition}
      >
        <ActiveIcon
          width={20}
          height={20}
          strokeWidth={FilledIcon ? 0 : 1.5}
          fill="currentColor"
        />
      </motion.span>
    </motion.span>
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
  const [engaged, setEngaged] = useState(false);
  const reduceMotion = useReducedMotion() ?? false;
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
            onPointerEnter={() => setEngaged(true)}
            onPointerLeave={() => setEngaged(false)}
            onFocus={() => setEngaged(true)}
            onBlur={() => setEngaged(false)}
            onClick={toggleOpen}
          >
            <span className="sr-app-sidebar__item-icon" aria-hidden="true">
              <AnimatedSidebarIcon
                icon={Train}
                active={open}
                engaged={engaged}
                effect="lift"
              />
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
                    whileHover={reduceMotion ? undefined : { scale: 1.03, y: -1 }}
                    whileTap={reduceMotion ? undefined : { scale: 0.97 }}
                    transition={{ duration: reduceMotion ? 0 : 0.18, ease: [0.22, 1, 0.36, 1] }}
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
          icon={ChatBubble}
          filledIcon={ChatBubbleSolid}
          active={activeTab === "chat"}
          onClick={onOpenChat}
        />
        <SidebarItem
          label="Transit Map"
          description="Live feed · Alerts · Routes"
          icon={Map}
          motionEffect="open"
          active={activeTab === "livemap"}
          onClick={onOpenLiveMap}
        />
        <SidebarItem
          label="New Trip"
          icon={Plus}
          motionEffect="rotate"
          onClick={onNewTrip}
        />
        <SidebarItem label="Favorites" icon={Bookmark} filledIcon={BookmarkSolid} disabled />
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
          filledIcon={MessageTextSolid}
          appearance="secondary"
          disabled
        />
        <SidebarItem
          label="Help"
          icon={HelpCircle}
          filledIcon={HelpCircleSolid}
          appearance="secondary"
          disabled
        />
        <SidebarItem
          label="Settings"
          icon={Settings}
          motionEffect="rotate"
          appearance="secondary"
          disabled
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
                  icon={collapsed ? NavArrowRight : NavArrowLeft}
                  engaged={collapseEngaged}
                  effect="lift"
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
                  icon={theme === "dark" ? SunLight : HalfMoon}
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
