"use client"

import React, {
  useEffect,
  useMemo,
  useState,
  type ComponentPropsWithoutRef,
} from "react"
import { AnimatePresence, motion, useReducedMotion, type MotionProps } from "motion/react"

import { cn } from "@/lib/utils"

function AnimatedListItem({ children }: { children: React.ReactNode }) {
  const reduceMotion = useReducedMotion()
  const animations: MotionProps = {
    initial: reduceMotion ? { opacity: 1 } : { opacity: 0, y: 9 },
    animate: { opacity: 1, y: 0, originY: 0 },
    exit: reduceMotion ? { opacity: 0 } : { opacity: 0, y: -7 },
    transition: reduceMotion
      ? { duration: 0.01 }
      : { duration: 0.32, ease: [0.16, 1, 0.3, 1] },
  }

  return (
    <motion.div {...animations} layout className="mx-auto w-full">
      {children}
    </motion.div>
  )
}

export interface AnimatedListProps extends ComponentPropsWithoutRef<"div"> {
  children: React.ReactNode
  delay?: number
  reverseOrder?: boolean
}

export const AnimatedList = React.memo(
  ({ children, className, delay = 1000, reverseOrder = true, ...props }: AnimatedListProps) => {
    const [index, setIndex] = useState(0)
    const childrenArray = useMemo(
      () => React.Children.toArray(children),
      [children]
    )

    useEffect(() => {
      setIndex((current) =>
        Math.min(current, Math.max(childrenArray.length - 1, 0))
      )
    }, [childrenArray.length])

    useEffect(() => {
      let timeout: ReturnType<typeof setTimeout> | null = null

      if (index < childrenArray.length - 1) {
        timeout = setTimeout(() => {
          setIndex((prevIndex) => (prevIndex + 1) % childrenArray.length)
        }, delay)
      }

      return () => {
        if (timeout !== null) {
          clearTimeout(timeout)
        }
      }
    }, [index, delay, childrenArray.length])

    const itemsToShow = useMemo(() => {
      const result = childrenArray.slice(0, index + 1)
      if (reverseOrder) return [...result].reverse()
      return result
    }, [index, childrenArray, reverseOrder])

    return (
      <div
        className={cn(`flex flex-col items-center gap-4`, className)}
        {...props}
      >
        <AnimatePresence>
          {itemsToShow.map((item) => (
            <AnimatedListItem key={(item as React.ReactElement).key}>
              {item}
            </AnimatedListItem>
          ))}
        </AnimatePresence>
      </div>
    )
  }
)

AnimatedList.displayName = "AnimatedList"
