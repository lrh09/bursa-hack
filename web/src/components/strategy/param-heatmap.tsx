"use client";

import * as React from "react";

import { PlotlyChart } from "../charts/plotly-chart";
import { PALETTE } from "@/lib/theme";
import type { VariantInline } from "@/lib/types";

interface Props {
  variants: VariantInline[];
  contKeys: string[];  // candidate axes (continuous numeric params present in this strategy)
}

export function ParamHeatmap({ variants, contKeys }: Props) {
  // Only consider keys that actually vary across the strategy's variants
  const varyingKeys = React.useMemo(() => {
    return contKeys.filter((k) => {
      const values = new Set(variants.map((v) => v.params[k]));
      return values.size >= 2;
    });
  }, [variants, contKeys]);

  const [xKey, setXKey] = React.useState<string>(varyingKeys[0] ?? "");
  const [yKey, setYKey] = React.useState<string>(varyingKeys[1] ?? varyingKeys[0] ?? "");

  if (varyingKeys.length < 2) {
    return (
      <div className="rounded-md border border-border p-4 text-xs text-muted-foreground">
        Heatmap needs at least two continuous parameters to vary across this strategy&apos;s variants; this one doesn&apos;t have that. Use the table.
      </div>
    );
  }

  const xVals = Array.from(new Set(variants.map((v) => v.params[xKey]))).sort(numOrStr);
  const yVals = Array.from(new Set(variants.map((v) => v.params[yKey]))).sort(numOrStr);

  // Cell = best variant per (x, y) by wf_sharpe; +N badge if >1 variant collapses
  const cellMap = new Map<string, { best: VariantInline; count: number }>();
  for (const v of variants) {
    const xv = v.params[xKey];
    const yv = v.params[yKey];
    if (xv == null || yv == null) continue;
    const key = `${xv}|${yv}`;
    const slot = cellMap.get(key);
    if (!slot) {
      cellMap.set(key, { best: v, count: 1 });
    } else {
      slot.count += 1;
      if ((v.wf_sharpe ?? -Infinity) > (slot.best.wf_sharpe ?? -Infinity)) {
        slot.best = v;
      }
    }
  }

  const z: (number | null)[][] = yVals.map((yv) =>
    xVals.map((xv) => {
      const slot = cellMap.get(`${xv}|${yv}`);
      return slot ? (slot.best.wf_sharpe ?? null) : null;
    }),
  );
  const text: string[][] = yVals.map((yv) =>
    xVals.map((xv) => {
      const slot = cellMap.get(`${xv}|${yv}`);
      if (!slot) return "";
      const sharpe = slot.best.wf_sharpe;
      const head = sharpe == null ? "—" : sharpe.toFixed(2);
      return slot.count > 1 ? `${head} +${slot.count - 1}` : head;
    }),
  );

  const flat = z.flat().filter((v): v is number => v != null);
  const maxAbs = Math.max(0.1, ...flat.map(Math.abs));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <label className="flex items-center gap-1">
          X:
          <select
            value={xKey}
            onChange={(e) => setXKey(e.target.value)}
            className="border border-border rounded px-1 py-0.5 bg-background"
          >
            {varyingKeys.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1">
          Y:
          <select
            value={yKey}
            onChange={(e) => setYKey(e.target.value)}
            className="border border-border rounded px-1 py-0.5 bg-background"
          >
            {varyingKeys.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
      </div>
      <div className="-mx-3 sm:mx-0 overflow-x-auto">
        <div className="min-w-[480px] sm:min-w-0 px-3 sm:px-0">
          <PlotlyChart
            data={[
              {
                type: "heatmap",
                x: xVals.map(String),
                y: yVals.map(String),
                z,
                text: text as unknown as string[],
                texttemplate: "%{text}",
                textfont: { size: 10, family: "var(--font-inter)", color: PALETTE.ink },
                colorscale: PALETTE.rdbu,
                zmin: -maxAbs,
                zmax: maxAbs,
                hovertemplate: `<b>${xKey}=%{x}, ${yKey}=%{y}</b><br>WF Sharpe: %{z:.2f}<extra></extra>`,
                xgap: 1, ygap: 1,
                colorbar: {
                  title: { text: "WF Sharpe", side: "right", font: { size: 10 } },
                  tickfont: { size: 10 }, thickness: 10, len: 0.9,
                },
              } as unknown as import("plotly.js").Data,
            ]}
            layout={{
              margin: { l: 56, r: 32, t: 24, b: 36 },
              xaxis: { title: { text: xKey }, showgrid: false, fixedrange: true, type: "category" },
              yaxis: { title: { text: yKey }, showgrid: false, fixedrange: true, type: "category" },
            }}
            height={320}
            ariaLabel={`WF Sharpe heatmap over ${xKey} and ${yKey}`}
          />
        </div>
      </div>
      <p className="text-[10px] text-muted-foreground">
        Cell colour = best variant&apos;s WF Sharpe at that (x, y). &quot;+N&quot; badge means N other variants exist at the same cell (collapsed continuous dims).
      </p>
    </div>
  );
}

function numOrStr(a: unknown, b: unknown) {
  const an = Number(a), bn = Number(b);
  if (!isNaN(an) && !isNaN(bn)) return an - bn;
  return String(a).localeCompare(String(b));
}
