"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute — Left Rail FX primitives

   Ported from the prototype's `fx.jsx`. All exports are framework-agnostic
   (vanilla React + CSS) on purpose: the rail's motion is meant to feel
   sturdy and crystalline, not bouncy. We deliberately avoid Framer Motion
   here — the prototype's blur+fade reveals, conic-gradient beam, and
   grid-template-rows accordion are all expressed in pure CSS keyframes
   declared in `app/styles/smart-route-left-rail.css`.

   - `BorderBeam` — rotating conic-gradient stroke (ATLAS thinking state)
   - `MagicCard` — mouse-tracking radial spotlight overlay (rows, alerts)
   - `NumberTicker` — easeOutCubic integer interpolation
   - `BlurFade` — single-shot entry animation with delay support
   - `Stagger` — staggered animation-delay across children
   - `Accordion` — height-collapse via grid-rows trick
   - `PhraseReveal` — word-by-word blur-up text (thinking + result rationale)
   ════════════════════════════════════════════════════════════════════════ */

import {
  Children,
  type CSSProperties,
  type HTMLAttributes,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";

/* ── BorderBeam ──────────────────────────────────────────────────
   Rotating conic-gradient that traces the inside edge of its parent.
   Parent must be `position: relative`. Uses CSS `@property --sr-beam-angle`
   for GPU-accelerated rotation. Used on the ATLAS card during `thinking`. */
interface BorderBeamProps {
  color?: string;
  size?: number; // arc width as % of perimeter
  duration?: number; // seconds per full revolution
  delay?: number;
  inset?: number;
  className?: string;
}

function BorderBeam({
  color = "var(--sr-cyan)",
  size = 80,
  duration = 3,
  delay = 0,
  inset = 0,
  className,
}: BorderBeamProps) {
  return (
    <span
      aria-hidden="true"
      className={className}
      style={{
        position: "absolute",
        inset,
        pointerEvents: "none",
        borderRadius: "inherit",
        background: `conic-gradient(from var(--sr-beam-angle), transparent 0%, transparent ${
          100 - size
        }%, ${color} ${100 - size / 2}%, transparent 100%)`,
        WebkitMask:
          "linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0)",
        WebkitMaskComposite: "xor",
        maskComposite: "exclude",
        padding: 1,
        animation: `srBeamRotate ${duration}s linear infinite`,
        animationDelay: `${delay}s`,
      }}
    />
  );
}

/* ── MagicCard ──────────────────────────────────────────────────
   Wrap children with a mouse-tracking radial spotlight overlay. Used on
   arrival rows and alert rows. The hover signal is intentionally soft —
   `intensity` defaults to 8% so the row reads as alive without screaming. */
interface MagicCardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  intensity?: number;
  size?: number;
  color?: string; // an "r,g,b" triplet for the radial gradient
}

export function MagicCard({
  children,
  intensity = 0.08,
  size = 220,
  color = "216,155,43",
  style,
  ...rest
}: MagicCardProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x: -1000, y: -1000, on: false });

  const onMove = (event: React.MouseEvent<HTMLDivElement>) => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    setPos({ x: event.clientX - rect.left, y: event.clientY - rect.top, on: true });
  };
  const onLeave = () => setPos((p) => ({ ...p, on: false }));

  return (
    <div
      ref={ref}
      onMouseMove={onMove}
      onMouseLeave={onLeave}
      style={{ position: "relative", ...style }}
      {...rest}
    >
      <span
        aria-hidden="true"
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          background: `radial-gradient(${size}px circle at ${pos.x}px ${pos.y}px, rgba(${color},${intensity}), transparent 60%)`,
          opacity: pos.on ? 1 : 0,
          transition: "opacity 220ms",
          zIndex: 0,
        }}
      />
      <div style={{ position: "relative", zIndex: 1 }}>{children}</div>
    </div>
  );
}

/* ── NumberTicker ───────────────────────────────────────────────
   Eases an integer between value changes (default 600ms, easeOutCubic).
   Used for arrival minute counts. Render is synchronous when start === end. */
interface NumberTickerProps {
  value: number;
  dur?: number;
  format?: (n: number) => string;
}

export function NumberTicker({
  value,
  dur = 600,
  format = (n) => String(n),
}: NumberTickerProps) {
  const [display, setDisplay] = useState(value);
  const prev = useRef(value);

  useEffect(() => {
    const start = prev.current;
    const end = value;
    if (start === end) {
      setDisplay(end);
      return;
    }
    const t0 = performance.now();
    let raf: number;
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / dur);
      const e = 1 - Math.pow(1 - k, 3);
      setDisplay(Math.round(start + (end - start) * e));
      if (k < 1) raf = requestAnimationFrame(step);
      else prev.current = end;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value, dur]);

  return <>{format(display)}</>;
}

/* ── BlurFade ───────────────────────────────────────────────────
   Single-shot blur-up entry animation. Use `key` on the parent to re-trigger
   when content changes. */
interface BlurFadeProps {
  children: ReactNode;
  delay?: number;
  duration?: number;
  className?: string;
  style?: CSSProperties;
}

export function BlurFade({
  children,
  delay = 0,
  duration = 360,
  className,
  style,
}: BlurFadeProps) {
  return (
    <div
      className={className}
      style={{
        animation: `srBlurIn ${duration}ms var(--sr-ease) both`,
        animationDelay: `${delay}ms`,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/* ── Stagger ────────────────────────────────────────────────────
   Apply staggered animation-delay to an array of children. Defaults to the
   `srBlurIn` keyframe; pass `className="sr-slide-in"` for a tighter feel. */
interface StaggerProps {
  children: ReactNode;
  step?: number;
  baseDelay?: number;
  className?: string;
}

function Stagger({
  children,
  step = 40,
  baseDelay = 0,
  className = "sr-fade-in",
}: StaggerProps) {
  return (
    <>
      {Children.map(children, (child, i) => (
        <div
          className={className}
          style={{ animationDelay: `${baseDelay + i * step}ms` }}
        >
          {child}
        </div>
      ))}
    </>
  );
}

/* ── Accordion ──────────────────────────────────────────────────
   Smooth height collapse using the `grid-template-rows: 0fr → 1fr` trick.
   Inner content needs `overflow: hidden` + `min-height: 0`, both applied by
   the `.sr-accordion-inner` selector. */
interface AccordionProps {
  open: boolean;
  children: ReactNode;
  className?: string;
}

export function Accordion({ open, children, className }: AccordionProps) {
  return (
    <div className={`sr-accordion ${className ?? ""}`.trim()} data-open={open}>
      <div className="sr-accordion-inner">{children}</div>
    </div>
  );
}

/* ── PhraseReveal ───────────────────────────────────────────────
   Splits text on spaces, animates each word with a 55ms stagger. Used for
   ATLAS `thinking` phrases AND the `result` headline + rationale. The
   `key` prop on the wrapper element re-mounts on text change so the
   animation re-fires cleanly. */
interface PhraseRevealProps {
  text: string;
  stagger?: number;
  duration?: number;
}

export function PhraseReveal({
  text,
  stagger = 55,
  duration = 320,
}: PhraseRevealProps) {
  const words = text.split(" ");
  return (
    <span key={text}>
      {words.map((word, i) => (
        <span
          key={`${i}-${word}`}
          style={{
            opacity: 0,
            animation: `srBlurIn ${duration}ms var(--sr-ease) both`,
            animationDelay: `${i * stagger}ms`,
            display: "inline-block",
            marginRight: "0.3em",
          }}
        >
          {word}
        </span>
      ))}
    </span>
  );
}
