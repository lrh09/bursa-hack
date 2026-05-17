"use client";

import { PlotlyChart } from "./plotly-chart";
import { PALETTE } from "@/lib/theme";
import { KEY_EVENTS } from "@/lib/events";
import type { EquityCurve } from "@/lib/types";

interface Series {
  label: string;
  equity: EquityCurve;
  color: string;
}

interface Props {
  series: Series[];
  normalise?: boolean;
  height?: number;
  showEvents?: boolean;
  holdoutStart?: string;
  holdoutEnd?: string;
}

export function EquityOverlay({
  series,
  normalise = true,
  height = 380,
  showEvents = false,
  holdoutStart = "2020-01-01",
  holdoutEnd = "2022-02-15",
}: Props) {
  const data = series.map((s) => {
    const base = s.equity.equity[0] ?? 1;
    const y = normalise ? s.equity.equity.map((v) => (v / base) * 100) : s.equity.equity;
    return {
      x: s.equity.dates,
      y,
      type: "scatter" as const,
      mode: "lines" as const,
      name: s.label,
      line: { color: s.color, width: 2 },
      hovertemplate: `<b>%{x|%d %b %Y}</b><br>${s.label}: ${normalise ? "%{y:.1f}" : "RM %{y:,.0f}"}<extra></extra>`,
    };
  });

  const shapes = [
    {
      type: "rect" as const,
      xref: "x" as const,
      yref: "paper" as const,
      x0: holdoutStart,
      x1: holdoutEnd,
      y0: 0,
      y1: 1,
      fillcolor: "rgba(196, 154, 42, 0.08)",
      line: { width: 0 },
      layer: "below" as const,
    },
  ];
  const annotations: Array<Record<string, unknown>> = [];
  if (showEvents) {
    for (const e of KEY_EVENTS) {
      shapes.push({
        type: "rect" as const,
        xref: "x" as const,
        yref: "paper" as const,
        x0: e.date,
        x1: e.date,
        y0: 0,
        y1: 0.92,
        fillcolor: "rgba(0,0,0,0)",
        line: { width: 0 },
        layer: "below" as const,
      });
      annotations.push({
        x: e.date,
        y: 0.95,
        xref: "x",
        yref: "paper",
        text: e.label,
        showarrow: false,
        font: { size: 9, color: PALETTE.muted, family: "var(--font-inter)" },
        xanchor: "left",
        textangle: "-30",
      });
    }
  }

  return (
    <PlotlyChart
      data={data}
      layout={{
        showlegend: true,
        legend: { orientation: "h", y: -0.18, font: { size: 11 } },
        yaxis: normalise
          ? { title: { text: "Equity (=100 at start)" } }
          : { tickformat: ",.0s", tickprefix: "RM " },
        shapes,
        annotations,
        margin: { l: 64, r: 24, t: 24, b: 80 },
      }}
      height={height}
      ariaLabel="Equity curves overlay"
    />
  );
}
