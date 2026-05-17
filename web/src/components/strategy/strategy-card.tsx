import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Sparkline } from "@/components/charts/sparkline";
import { TierPill } from "@/components/scorecard/tier-pill";
import { Numeric } from "@/components/common/numeric";
import { PctChange } from "@/components/common/pct-change";
import { cn } from "@/lib/utils";
import { fmtSharpe, fmtRatio, strategyFamilyLabel } from "@/lib/format";
import type { Strategy, EquityCurve } from "@/lib/types";

interface Props {
  strategy: Strategy;
  equity: EquityCurve | null;
  href: string;
  emphasis?: "primary" | "secondary";
}

export function StrategyCard({ strategy, equity, href, emphasis = "secondary" }: Props) {
  const m = strategy.metrics;
  return (
    <Card
      className={cn(
        "group relative overflow-hidden border-border transition-shadow hover:shadow-md",
        emphasis === "primary" && "ring-1 ring-[var(--color-brand-gold)]/30",
      )}
    >
      <Link
        href={href}
        className="absolute inset-0 z-10 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-gold)] rounded-xl"
        aria-label={`Open ${strategy.label}`}
      />
      <CardContent className="relative space-y-5 py-5 sm:py-6">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              {strategyFamilyLabel(strategy.family)} · Rank #{strategy.rank}
            </p>
            <h3 className="font-heading text-xl sm:text-2xl leading-tight mt-1">
              {strategy.label}
            </h3>
          </div>
          <ArrowUpRight
            aria-hidden
            className="size-4 text-muted-foreground transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
          />
        </div>

        <div className="grid grid-cols-3 gap-3 sm:gap-5 items-end">
          <div>
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground">OOS Sharpe</p>
            <p className="font-heading text-3xl sm:text-[44px] leading-none mt-1 tabular">
              {fmtSharpe(m.oos_sharpe)}
            </p>
          </div>
          <div className="space-y-2">
            <div>
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">CAGR OOS</p>
              <p className="text-base sm:text-lg tabular">
                <PctChange value={m.cagr_oos} digits={1} withIcon={false} />
              </p>
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Max DD</p>
              <p className="text-base sm:text-lg tabular">
                <PctChange value={m.max_dd} digits={1} withIcon={false} />
              </p>
            </div>
          </div>
          <div className="space-y-2">
            <div>
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">WF Sharpe</p>
              <p className="text-base sm:text-lg">
                <Numeric>{fmtSharpe(m.wf_sharpe)}</Numeric>
              </p>
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">DSR-eff</p>
              <p className="text-base sm:text-lg">
                <Numeric>{fmtRatio(m.dsr_eff)}</Numeric>
              </p>
            </div>
          </div>
        </div>

        {equity ? (
          <div className="-mx-1">
            <Sparkline data={equity.equity} height={56} />
          </div>
        ) : (
          <div className="h-14 rounded-md bg-muted/40" aria-label="No equity data" />
        )}

        <div className="flex items-center justify-between">
          <TierPill tier={strategy.tier} recommendation={strategy.recommendation} size="sm" />
          <p className="text-[11px] text-muted-foreground tabular">
            {m.n_pass ?? "?"} / {m.n_eval ?? "?"} gates passed
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
