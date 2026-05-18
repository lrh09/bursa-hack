"use client";

import { PlotlyChart } from "./plotly-chart";
import { PALETTE } from "@/lib/theme";
import type { FoldRow } from "@/lib/types";

interface Props {
  folds: FoldRow[];
  height?: number;
}

export function FoldTimeline({ folds, height = 280 }: Props) {
  // Distinct folds by index, ordered chronologically
  const byIdx = new Map<number, FoldRow>();
  for (const f of folds) {
    if (!byIdx.has(f.fold_idx)) byIdx.set(f.fold_idx, f);
  }
  const rows = Array.from(byIdx.values()).sort((a, b) => a.fold_idx - b.fold_idx);

  const trainShapes = rows.map((f) => ({
    type: "rect" as const,
    xref: "x" as const,
    yref: "y" as const,
    x0: f.train_start,
    x1: f.validate_start,
    y0: f.fold_idx - 0.4,
    y1: f.fold_idx + 0.4,
    fillcolor: "rgba(11, 35, 73, 0.18)",
    line: { width: 0 },
  }));
  const validateShapes = rows.map((f) => ({
    type: "rect" as const,
    xref: "x" as const,
    yref: "y" as const,
    x0: f.validate_start,
    x1: f.validate_end,
    y0: f.fold_idx - 0.4,
    y1: f.fold_idx + 0.4,
    fillcolor: "rgba(196, 154, 42, 0.45)",
    line: { width: 0 },
  }));

  return (
    <div className="-mx-3 sm:mx-0 overflow-x-auto">
    <div className="min-w-[560px] sm:min-w-0 px-3 sm:px-0">
    <PlotlyChart
      data={[
        // Invisible scatter for tooltips
        {
          type: "scatter",
          mode: "markers",
          x: rows.map((f) => f.validate_start),
          y: rows.map((f) => f.fold_idx),
          marker: { size: 8, color: PALETTE.gold },
          hovertemplate:
            "<b>Fold %{y}</b><br>Train: %{customdata[0]} → %{customdata[1]}<br>Validate: %{customdata[1]} → %{customdata[2]}<br>Sharpe: %{customdata[3]:.2f}<extra></extra>",
          customdata: rows.map((f) => [
            f.train_start,
            f.validate_start,
            f.validate_end,
            f.sharpe ?? 0,
          ]),
        },
      ]}
      layout={{
        shapes: [...trainShapes, ...validateShapes],
        yaxis: {
          title: { text: "Fold" },
          dtick: 1,
          autorange: "reversed",
        },
        xaxis: { type: "date" },
        margin: { l: 48, r: 24, t: 16, b: 36 },
      }}
      height={height}
      ariaLabel="Walk-forward folds timeline"
    />
    </div>
    </div>
  );
}
