import { TierPill } from "@/components/scorecard/tier-pill";
import { Numeric } from "@/components/common/numeric";
import { PctChange } from "@/components/common/pct-change";
import { fmtSharpe, fmtRatio, fmtMultiplier, strategyFamilyLabel, fmtBps } from "@/lib/format";
import type { Strategy } from "@/lib/types";

interface Props {
  strategy: Strategy;
}

export function StrategyHeader({ strategy }: Props) {
  const m = strategy.metrics;
  return (
    <header className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-brand-gold-700)]">
            {strategyFamilyLabel(strategy.family)} · Rank #{strategy.rank}
          </p>
          <h1 className="font-heading text-3xl sm:text-4xl leading-tight mt-1">
            {strategy.label}
          </h1>
          <p className="mt-2 max-w-2xl text-sm sm:text-base text-muted-foreground leading-relaxed">
            {strategy.narrative}
          </p>
        </div>
        <TierPill tier={strategy.tier} recommendation={strategy.recommendation} size="lg" />
      </div>
      <dl className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3 sm:gap-4 border-y border-border py-4">
        <Tile label="WF Sharpe" value={fmtSharpe(m.wf_sharpe)} />
        <Tile label="OOS Sharpe" value={fmtSharpe(m.oos_sharpe)} highlight />
        <Tile label="CAGR (OOS)" value={<PctChange value={m.cagr_oos} digits={2} withIcon={false} />} />
        <Tile label="Max DD" value={<PctChange value={m.max_dd} digits={2} withIcon={false} />} />
        <Tile label="DSR-eff" value={fmtRatio(m.dsr_eff)} />
        <Tile label="PBO" value={fmtRatio(m.pbo)} />
        <Tile label="Cost / leg" value={fmtBps(m.cost_bps_mean)} />
        <Tile label="Order × floor" value={fmtMultiplier(m.order_mult)} />
      </dl>
    </header>
  );
}

function Tile({
  label,
  value,
  highlight = false,
}: {
  label: string;
  value: React.ReactNode;
  highlight?: boolean;
}) {
  return (
    <div className="space-y-1">
      <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</dt>
      <dd
        className={
          highlight
            ? "font-heading text-2xl tabular text-[var(--color-brand-navy)]"
            : "font-heading text-xl tabular text-foreground"
        }
      >
        {typeof value === "string" || typeof value === "number" ? <Numeric>{value}</Numeric> : value}
      </dd>
    </div>
  );
}
