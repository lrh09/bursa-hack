"use client";

import * as React from "react";
import type { Data, Layout } from "plotly.js";

import { PlotlyChart } from "./plotly-chart";
import { PALETTE } from "@/lib/theme";
import { KEY_EVENTS } from "@/lib/events";
import type { EquityCurve as EquityCurveData } from "@/lib/types";

interface Props {
  equity: EquityCurveData;
  holdoutStart?: string;
  holdoutEnd?: string;
  withEvents?: boolean;
  withBenchmark?: number[];
  benchmarkLabel?: string;
  height?: number;
  className?: string;
  ariaLabel?: string;
}

export function EquityCurveChart({
  equity,
  holdoutStart = "2020-01-01",
  holdoutEnd = "2022-02-15",
  withEvents = true,
  withBenchmark,
  benchmarkLabel = "Benchmark",
  height = 360,
  className,
  ariaLabel = "Equity curve",
}: Props) {
  const data: Data[] = [
    {
      x: equity.dates,
      y: equity.equity,
      type: "scatter",
      mode: "lines",
      line: { color: PALETTE.navy, width: 2 },
      fill: "tozeroy",
      fillcolor: "rgba(11, 35, 73, 0.06)",
      name: "Equity",
      hovertemplate: "<b>%{x|%d %b %Y}</b><br>Equity: RM %{y:,.0f}<extra></extra>",
    },
  ];

  if (withBenchmark) {
    data.push({
      x: equity.dates,
      y: withBenchmark,
      type: "scatter",
      mode: "lines",
      line: { color: PALETTE.muted, width: 1.4, dash: "dot" },
      name: benchmarkLabel,
      hovertemplate: `<b>%{x|%d %b %Y}</b><br>${benchmarkLabel}: RM %{y:,.0f}<extra></extra>`,
    });
  }

  const shapes: NonNullable<Layout["shapes"]> = [
    // Holdout shading
    {
      type: "rect",
      xref: "x",
      yref: "paper",
      x0: holdoutStart,
      x1: holdoutEnd,
      y0: 0,
      y1: 1,
      fillcolor: "rgba(196, 154, 42, 0.10)",
      line: { width: 0 },
      layer: "below",
    },
    // Holdout left boundary
    {
      type: "line",
      xref: "x",
      yref: "paper",
      x0: holdoutStart,
      x1: holdoutStart,
      y0: 0,
      y1: 1,
      line: { color: PALETTE.gold700, width: 1, dash: "dot" },
    },
  ];

  const annotations: NonNullable<Layout["annotations"]> = [
    {
      x: holdoutStart,
      y: 1.02,
      xref: "x",
      yref: "paper",
      text: "OOS holdout begins",
      showarrow: false,
      font: { size: 10, color: PALETTE.gold700, family: "var(--font-inter)" },
      xanchor: "left",
      yanchor: "bottom",
    },
  ];

  if (withEvents) {
    for (const e of KEY_EVENTS) {
      shapes.push({
        type: "line",
        xref: "x",
        yref: "paper",
        x0: e.date,
        x1: e.date,
        y0: 0,
        y1: 0.92,
        line: {
          color: e.category === "crisis" ? PALETTE.amber : PALETTE.muted,
          width: 0.6,
          dash: "dot",
        },
      });
      annotations.push({
        x: e.date,
        y: 0.92,
        xref: "x",
        yref: "paper",
        text: e.label,
        showarrow: false,
        font: {
          size: 9,
          color: e.category === "crisis" ? PALETTE.amber : PALETTE.muted,
          family: "var(--font-inter)",
        },
        xanchor: "left",
        yanchor: "bottom",
        textangle: "-30",
        captureevents: true,
      });
    }
  }

  return (
    <PlotlyChart
      data={data}
      layout={{
        shapes,
        annotations,
        yaxis: { tickformat: ",.0s", tickprefix: "RM " },
        margin: { l: 64, r: 24, t: 36, b: 40 },
      }}
      height={height}
      className={className}
      ariaLabel={ariaLabel}
    />
  );
}
