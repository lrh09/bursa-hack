"use client";

import { PlotlyChart } from "./plotly-chart";
import { PALETTE } from "@/lib/theme";
import type { EquityCurve as EquityCurveData, RollingSharpePoint } from "@/lib/types";

interface Props {
  equity: EquityCurveData;
  rolling: RollingSharpePoint[];
  height?: number;
}

export function RollingSharpeChart({ equity, rolling, height = 220 }: Props) {
  const dates = rolling.map((r) => equity.dates[r.i]).filter(Boolean) as string[];
  const sharpe = rolling.map((r) => r.sharpe);
  return (
    <PlotlyChart
      data={[
        {
          x: dates,
          y: sharpe,
          type: "scatter",
          mode: "lines",
          line: { color: PALETTE.gold700, width: 1.6 },
          fill: "tozeroy",
          fillcolor: "rgba(196, 154, 42, 0.10)",
          hovertemplate: "<b>%{x|%d %b %Y}</b><br>Sharpe (trail 6m): %{y:.2f}<extra></extra>",
        },
      ]}
      layout={{
        shapes: [
          {
            type: "line",
            xref: "paper",
            yref: "y",
            x0: 0,
            x1: 1,
            y0: 1,
            y1: 1,
            line: { color: PALETTE.muted, width: 0.8, dash: "dash" },
          },
          {
            type: "line",
            xref: "paper",
            yref: "y",
            x0: 0,
            x1: 1,
            y0: 0,
            y1: 0,
            line: { color: PALETTE.grid, width: 1 },
          },
        ],
        yaxis: { title: { text: "Rolling Sharpe (6m)" } },
        margin: { l: 56, r: 16, t: 12, b: 32 },
      }}
      height={height}
      ariaLabel="Trailing 6-month Sharpe"
    />
  );
}
