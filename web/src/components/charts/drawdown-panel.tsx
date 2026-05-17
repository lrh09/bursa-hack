"use client";

import { PlotlyChart } from "./plotly-chart";
import { PALETTE } from "@/lib/theme";
import type { EquityCurve as EquityCurveData } from "@/lib/types";

interface Props {
  equity: EquityCurveData;
  height?: number;
  className?: string;
  holdoutStart?: string;
  holdoutEnd?: string;
}

export function DrawdownPanel({
  equity,
  height = 200,
  className,
  holdoutStart = "2020-01-01",
  holdoutEnd = "2022-02-15",
}: Props) {
  return (
    <PlotlyChart
      data={[
        {
          x: equity.dates,
          y: equity.drawdown.map((d) => d * 100),
          type: "scatter",
          mode: "lines",
          line: { color: PALETTE.amber, width: 1.2 },
          fill: "tozeroy",
          fillcolor: "rgba(180, 83, 9, 0.18)",
          hovertemplate: "<b>%{x|%d %b %Y}</b><br>DD: %{y:.1f}%<extra></extra>",
        },
      ]}
      layout={{
        shapes: [
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
        ],
        yaxis: { ticksuffix: "%", title: { text: "Drawdown" } },
        margin: { l: 56, r: 24, t: 16, b: 32 },
      }}
      height={height}
      className={className}
      ariaLabel="Drawdown panel"
    />
  );
}
