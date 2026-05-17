"use client";

import { PlotlyChart } from "./plotly-chart";
import { PALETTE } from "@/lib/theme";
import type { MonthlyGrid } from "@/lib/types";

interface Props {
  grid: MonthlyGrid;
  height?: number;
  className?: string;
}

const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function MonthlyHeatmap({ grid, height = 320, className }: Props) {
  // Build z[year_idx][month_idx]
  const years = grid.years;
  const z: (number | null)[][] = years.map(() => Array(12).fill(null));
  const text: (string)[][] = years.map(() => Array(12).fill(""));
  for (const c of grid.cells) {
    const y = years.indexOf(c.year);
    const m = c.month - 1;
    if (y >= 0 && m >= 0 && m < 12 && z[y]) {
      const row = z[y]!;
      const trow = text[y]!;
      row[m] = c.ret * 100;
      trow[m] = `${(c.ret * 100).toFixed(1)}%`;
    }
  }

  // Symmetric colour range so 0% is neutral
  const flat = grid.cells.map((c) => c.ret * 100);
  const maxAbs = Math.max(0.1, ...flat.map((v) => Math.abs(v)));

  return (
    <>
      <PlotlyChart
        data={[
          {
            type: "heatmap",
            x: MONTH_LABELS,
            y: years.map(String),
            z,
            text: text as unknown as string[],
            texttemplate: "%{text}",
            textfont: { size: 10, family: "var(--font-inter)", color: PALETTE.ink },
            colorscale: PALETTE.rdbu,
            zmin: -maxAbs,
            zmax: maxAbs,
            hovertemplate: "<b>%{x} %{y}</b><br>Return: %{z:.2f}%<extra></extra>",
            colorbar: {
              title: { text: "Return %", side: "right", font: { size: 10 } },
              tickfont: { size: 10, color: PALETTE.muted },
              thickness: 12,
              len: 0.9,
            },
            xgap: 1,
            ygap: 1,
          },
        ]}
        layout={{
          margin: { l: 48, r: 32, t: 12, b: 24 },
          xaxis: { side: "top", showgrid: false, fixedrange: true },
          yaxis: { autorange: "reversed", showgrid: false, fixedrange: true, type: "category" },
        }}
        height={height}
        className={className}
        ariaLabel="Monthly returns heatmap"
      />
      <details className="mt-3 text-xs text-muted-foreground">
        <summary className="cursor-pointer hover:text-foreground">View as table (accessibility fallback)</summary>
        <div className="mt-2 overflow-x-auto">
          <table className="text-[11px] tabular border-collapse">
            <thead>
              <tr>
                <th className="px-2 py-1 text-left">Year</th>
                {MONTH_LABELS.map((m) => (
                  <th key={m} className="px-2 py-1">{m}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {years.map((y, yi) => (
                <tr key={y}>
                  <td className="px-2 py-1 font-medium">{y}</td>
                  {MONTH_LABELS.map((_, mi) => {
                    const row = z[yi];
                    const v = row ? row[mi] : null;
                    const valid = v !== null && v !== undefined;
                    return (
                      <td
                        key={mi}
                        className="px-2 py-1 text-right"
                        aria-label={valid ? `${v.toFixed(2)}%` : "no data"}
                      >
                        {valid ? `${v.toFixed(1)}%` : "—"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}
