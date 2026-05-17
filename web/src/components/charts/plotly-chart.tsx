"use client";

import dynamic from "next/dynamic";
import * as React from "react";

import type { Data, Layout, Config } from "plotly.js";
import { PLOTLY_BASE_CONFIG, PLOTLY_BASE_LAYOUT } from "@/lib/plotly-config";

// Plot is large; lazy-loaded so it never ships in the server bundle.
const Plot = dynamic(() => import("react-plotly.js"), {
  ssr: false,
  loading: () => (
    <div
      className="w-full h-full min-h-[180px] animate-pulse bg-muted/40 rounded-md"
      role="status"
      aria-label="Loading chart"
    />
  ),
});

export interface PlotlyChartProps {
  data: Data[];
  layout?: Partial<Layout>;
  config?: Partial<Config>;
  className?: string;
  height?: number | string;
  ariaLabel?: string;
}

export function PlotlyChart({
  data,
  layout,
  config,
  className,
  height = 320,
  ariaLabel,
}: PlotlyChartProps) {
  const mergedLayout = React.useMemo<Partial<Layout>>(
    () => ({
      ...PLOTLY_BASE_LAYOUT,
      ...(layout ?? {}),
      xaxis: { ...PLOTLY_BASE_LAYOUT.xaxis, ...(layout?.xaxis ?? {}) },
      yaxis: { ...PLOTLY_BASE_LAYOUT.yaxis, ...(layout?.yaxis ?? {}) },
    }),
    [layout],
  );
  const mergedConfig = React.useMemo<Partial<Config>>(
    () => ({ ...PLOTLY_BASE_CONFIG, ...(config ?? {}) }),
    [config],
  );
  return (
    <div className={className} style={{ width: "100%", height }} role="figure" aria-label={ariaLabel}>
      <Plot
        data={data}
        layout={mergedLayout}
        config={mergedConfig}
        useResizeHandler
        style={{ width: "100%", height: "100%" }}
      />
    </div>
  );
}
