"use client";

import { motion, useReducedMotion, type HTMLMotionProps } from "motion/react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface BlurFadeProps extends HTMLMotionProps<"div"> {
  children: ReactNode;
  delay?: number;
}

export function BlurFade({
  children,
  className,
  delay = 0,
  ...props
}: BlurFadeProps) {
  const reduceMotion = useReducedMotion();

  return (
    <motion.div
      initial={reduceMotion ? { opacity: 1 } : { opacity: 0, y: 6, filter: "blur(6px)" }}
      animate={reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0, filter: "blur(0px)" }}
      exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -4, filter: "blur(4px)" }}
      transition={{
        duration: reduceMotion ? 0.01 : 0.34,
        delay: reduceMotion ? 0 : delay,
        ease: [0.16, 1, 0.3, 1],
      }}
      className={cn(className)}
      {...props}
    >
      {children}
    </motion.div>
  );
}
