"use client";

import { PlotlyChart } from "./plotly-chart";
import { PALETTE } from "@/lib/theme";
import type { CalendarYearReturn } from "@/lib/types";

interface Props {
  data: CalendarYearReturn[];
  height?: number;
  className?: string;
  holdoutFromYear?: number;
}

export function CalendarBar({ data, height = 220, className, holdoutFromYear = 2020 }: Props) {
  return (
    <PlotlyChart
      data={[
        {
          type: "bar",
          x: data.map((d) => d.year),
          y: data.map((d) => d.ret * 100),
          marker: {
            color: data.map((d) =>
              d.ret >= 0
                ? d.year >= holdoutFromYear
                  ? PALETTE.gold
                  : PALETTE.forest
                : d.year >= holdoutFromYear
                  ? PALETTE.amber
                  : PALETTE.amber,
            ),
            line: { width: 0 },
          },
          text: data.map((d) => `${(d.ret * 100).toFixed(1)}%`),
          textposition: "outside",
          textfont: { size: 10, color: PALETTE.muted, family: "var(--font-inter)" },
          hovertemplate: "<b>%{x}</b><br>Return: %{y:.2f}%<extra></extra>",
        },
      ]}
      layout={{
        yaxis: { ticksuffix: "%", zeroline: true, zerolinecolor: PALETTE.grid },
        xaxis: { type: "category", showgrid: false },
        margin: { l: 56, r: 16, t: 32, b: 32 },
      }}
      height={height}
      className={className}
      ariaLabel="Calendar-year returns"
    />
  );
}
