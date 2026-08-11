"use client";

import {
  AnimatePresence,
  animate,
  motion,
  useMotionValue,
  useReducedMotion,
} from "motion/react";
import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";

const PAGE_SLIVER_WIDTH = 52;
const DRAG_ACTIVATION_DISTANCE = 10;
const CLOSE_DISTANCE_RATIO = 0.28;
const CLOSE_VELOCITY = -460;

const STAGE_SPRING = {
  type: "spring" as const,
  stiffness: 420,
  damping: 38,
  mass: 0.9,
};

export function MobileStage({
  navigationOpen,
  onDismissNavigation,
  children,
}: {
  navigationOpen: boolean;
  onDismissNavigation: () => void;
  children: ReactNode;
}) {
  const [travelDistance, setTravelDistance] = useState(0);
  const stageX = useMotionValue(0);
  const reduceMotion = useReducedMotion() ?? false;
  const stageAnimationRef = useRef<ReturnType<typeof animate> | null>(null);
  const releaseVelocityRef = useRef<number | undefined>(undefined);
  const dragOriginRef = useRef(0);
  const dragCommittedRef = useRef(false);
  const activePointerRef = useRef<number | null>(null);
  const pointerStartXRef = useRef(0);
  const previousPointerXRef = useRef(0);
  const previousPointerTimeRef = useRef(0);
  const pointerVelocityRef = useRef(0);
  const suppressNextClickRef = useRef(false);

  useEffect(() => {
    function measureTravel() {
      setTravelDistance(Math.max(0, window.innerWidth - PAGE_SLIVER_WIDTH));
    }

    measureTravel();
    window.addEventListener("resize", measureTravel);
    return () => window.removeEventListener("resize", measureTravel);
  }, []);

  useEffect(() => {
    const target = navigationOpen ? travelDistance : 0;
    stageAnimationRef.current?.stop();
    if (reduceMotion) {
      stageX.jump(target);
      stageAnimationRef.current = null;
      return;
    }

    const playback = animate(stageX, target, {
      ...STAGE_SPRING,
      velocity: releaseVelocityRef.current ?? stageX.getVelocity(),
    });
    stageAnimationRef.current = playback;
    releaseVelocityRef.current = undefined;
    return () => {
      playback.stop();
      if (stageAnimationRef.current === playback) {
        stageAnimationRef.current = null;
      }
    };
  }, [navigationOpen, reduceMotion, stageX, travelDistance]);

  function startDismissDrag(event: ReactPointerEvent<HTMLDivElement>) {
    stageAnimationRef.current?.stop();
    stageAnimationRef.current = null;
    dragOriginRef.current = stageX.get();
    dragCommittedRef.current = false;
    activePointerRef.current = event.pointerId;
    pointerStartXRef.current = event.clientX;
    previousPointerXRef.current = event.clientX;
    previousPointerTimeRef.current = event.timeStamp;
    pointerVelocityRef.current = 0;
    suppressNextClickRef.current = false;
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handleDismissPointerMove(
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    if (activePointerRef.current !== event.pointerId) {
      return;
    }

    const offsetX = event.clientX - pointerStartXRef.current;
    if (!dragCommittedRef.current && Math.abs(offsetX) < DRAG_ACTIVATION_DISTANCE) {
      return;
    }

    const elapsed = event.timeStamp - previousPointerTimeRef.current;
    if (elapsed > 0) {
      pointerVelocityRef.current =
        ((event.clientX - previousPointerXRef.current) / elapsed) * 1000;
    }
    previousPointerXRef.current = event.clientX;
    previousPointerTimeRef.current = event.timeStamp;
    dragCommittedRef.current = true;
    suppressNextClickRef.current = true;
    stageX.set(
      Math.min(
        travelDistance,
        Math.max(0, dragOriginRef.current + offsetX),
      ),
    );
  }

  function settleDismissDrag(velocityX: number) {
    const projectedX = stageX.get() + velocityX * 0.16;
    const closeThreshold = travelDistance * (1 - CLOSE_DISTANCE_RATIO);
    const shouldClose =
      velocityX <= CLOSE_VELOCITY || projectedX <= closeThreshold;

    releaseVelocityRef.current = velocityX;
    if (shouldClose) {
      onDismissNavigation();
      return;
    }

    if (reduceMotion) {
      stageX.jump(travelDistance);
      return;
    }

    stageAnimationRef.current = animate(stageX, travelDistance, {
      ...STAGE_SPRING,
      velocity: velocityX,
    });
  }

  function handleDismissPointerUp(
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    if (activePointerRef.current !== event.pointerId) return;
    activePointerRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (dragCommittedRef.current) {
      settleDismissDrag(pointerVelocityRef.current);
    }
  }

  function handleDismissPointerCancel(
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    if (activePointerRef.current !== event.pointerId) return;
    activePointerRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    suppressNextClickRef.current = false;
    if (dragCommittedRef.current) {
      if (reduceMotion) {
        stageX.jump(travelDistance);
      } else {
        stageAnimationRef.current = animate(
          stageX,
          travelDistance,
          STAGE_SPRING,
        );
      }
    }
  }

  function handleDismissClick() {
    if (suppressNextClickRef.current) {
      suppressNextClickRef.current = false;
      return;
    }
    onDismissNavigation();
  }

  return (
    <>
      <motion.div
        className="sr-mobile-stage"
        data-navigation-open={navigationOpen ? "true" : "false"}
        aria-hidden={navigationOpen ? true : undefined}
        inert={navigationOpen ? true : undefined}
        style={{ x: stageX }}
      >
        {children}
      </motion.div>

      <AnimatePresence initial={false}>
        {navigationOpen ? (
          <motion.div
            className="sr-mobile-stage-dismiss"
            aria-hidden="true"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.14, ease: "easeOut" }}
            onPointerDown={startDismissDrag}
            onPointerMove={handleDismissPointerMove}
            onPointerUp={handleDismissPointerUp}
            onPointerCancel={handleDismissPointerCancel}
            onClick={handleDismissClick}
          />
        ) : null}
      </AnimatePresence>
    </>
  );
}
