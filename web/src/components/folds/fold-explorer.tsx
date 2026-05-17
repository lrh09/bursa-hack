"use client";

import * as React from "react";

import { PlotlyChart } from "@/components/charts/plotly-chart";
import { FoldTimeline } from "@/components/charts/fold-timeline";
import { FoldTable } from "@/components/strategy/fold-table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Card, CardContent } from "@/components/ui/card";
import { PALETTE } from "@/lib/theme";
import { strategyFamilyLabel } from "@/lib/format";
import type { FoldRow } from "@/lib/types";

interface Props {
  byStrategy: Record<string, FoldRow[]>;
  variantLabel: Record<string, string>;
}

export function FoldExplorer({ byStrategy, variantLabel }: Props) {
  const strategies = Object.keys(byStrategy);
  const [strategy, setStrategy] = React.useState<string>(strategies[0] ?? "rotation");
  const folds = byStrategy[strategy] ?? [];

  // Plot WF sharpe by params_hash (one point per fold) faceted by params_hash colour
  // Aggregate to show fold-by-fold sharpe distribution across the top-K variants.
  const byHash = new Map<string, FoldRow[]>();
  for (const f of folds) {
    if (!byHash.has(f.params_hash)) byHash.set(f.params_hash, []);
    byHash.get(f.params_hash)!.push(f);
  }
  // Take the top 8 variants by mean Sharpe so the chart stays readable.
  const variantStats = Array.from(byHash.entries())
    .map(([h, rows]) => ({
      hash: h,
      mean:
        rows.reduce((acc, r) => acc + (r.sharpe ?? 0), 0) /
        Math.max(1, rows.filter((r) => r.sharpe !== null).length),
      rows,
    }))
    .sort((a, b) => b.mean - a.mean)
    .slice(0, 8);

  const data = variantStats.map((vs, i) => ({
    x: vs.rows.map((r) => r.validate_start),
    y: vs.rows.map((r) => r.sharpe ?? 0),
    name: `${variantLabel[vs.hash] ?? vs.hash.slice(0, 8)}`,
    mode: "lines+markers" as const,
    type: "scatter" as const,
    line: { width: 1.2 },
    marker: {
      size: 7,
      color: i === 0 ? PALETTE.gold : i === 1 ? PALETTE.navy : undefined,
      opacity: i < 2 ? 1 : 0.55,
    },
    opacity: i < 2 ? 1 : 0.6,
    hovertemplate: "<b>%{x}</b><br>Sharpe: %{y:.2f}<extra>" + vs.hash.slice(0, 8) + "</extra>",
  }));

  // Headline variant for the timeline:
  const headlineHash = variantStats[0]?.hash;
  const headlineFolds = headlineHash ? byHash.get(headlineHash) ?? [] : [];

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <label className="text-sm text-muted-foreground">Strategy family:</label>
        <Select value={strategy} onValueChange={(v) => v && setStrategy(v)}>
          <SelectTrigger className="w-[260px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {strategies.map((s) => (
              <SelectItem key={s} value={s}>
                {strategyFamilyLabel(s)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Card>
        <CardContent className="py-3 space-y-2">
          <h2 className="font-heading text-base">
            Top-variant fold timeline (headline variant)
          </h2>
          <FoldTimeline folds={headlineFolds} />
        </CardContent>
      </Card>

      <Card>
        <CardContent className="py-3 space-y-2">
          <h2 className="font-heading text-base">
            Per-fold Sharpe — top 8 variants in family
          </h2>
          <PlotlyChart
            data={data}
            layout={{
              showlegend: true,
              legend: { orientation: "h", y: -0.18, font: { size: 10 } },
              yaxis: { zeroline: true, zerolinecolor: PALETTE.grid, title: { text: "Validation Sharpe" } },
              margin: { l: 56, r: 16, t: 24, b: 120 },
            }}
            height={420}
            ariaLabel="Fold sharpe scatter"
          />
        </CardContent>
      </Card>

      <Card>
        <CardContent className="py-3 space-y-2">
          <h2 className="font-heading text-base">Headline variant — per-fold table</h2>
          <FoldTable folds={headlineFolds} />
        </CardContent>
      </Card>
    </div>
  );
}
