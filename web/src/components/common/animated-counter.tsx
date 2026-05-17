"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

interface Props {
  /** Final value the counter rolls up to */
  value: number;
  /** Total duration in ms */
  duration?: number;
  /** Number of decimal places */
  digits?: number;
  /** Prefix (e.g. "RM ") */
  prefix?: string;
  /** Suffix (e.g. "%") */
  suffix?: string;
  /** If true, render value × 100 and append % */
  asPct?: boolean;
  /** Use compact formatting (1.2k, 3.4M) */
  compact?: boolean;
  className?: string;
}

const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);

export function AnimatedCounter({
  value,
  duration = 900,
  digits = 0,
  prefix = "",
  suffix = "",
  asPct = false,
  compact = false,
  className,
}: Props) {
  const [display, setDisplay] = React.useState(0);
  const reduceMotion = React.useRef(false);

  React.useEffect(() => {
    if (typeof window !== "undefined" && "matchMedia" in window) {
      reduceMotion.current = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    }
    if (reduceMotion.current) {
      setDisplay(value);
      return;
    }
    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      setDisplay(value * easeOutCubic(t));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);

  const v = asPct ? display * 100 : display;
  const formatter = compact
    ? new Intl.NumberFormat("en-MY", { notation: "compact", maximumFractionDigits: 1 })
    : new Intl.NumberFormat("en-MY", { minimumFractionDigits: digits, maximumFractionDigits: digits });

  return (
    <span className={cn("tabular", className)}>
      {prefix}
      {formatter.format(v)}
      {asPct ? "%" : suffix}
    </span>
  );
}
