import Link from "next/link";
import { notFound } from "next/navigation";

import {
  getEquity,
  getManifest,
  getStrategy,
} from "@/lib/data";
import { EquityOverlay } from "@/components/charts/equity-overlay";
import { ScorecardTable } from "@/components/scorecard/scorecard-table";
import { TierPill } from "@/components/scorecard/tier-pill";
import { Card, CardContent } from "@/components/ui/card";
import { Numeric } from "@/components/common/numeric";
import { PctChange } from "@/components/common/pct-change";
import { PALETTE } from "@/lib/theme";
import { fmtBps, fmtMultiplier, fmtRatio, fmtSharpe, strategyFamilyLabel } from "@/lib/format";

export async function generateStaticParams() {
  const manifest = await getManifest();
  // Generate ordered pairs (a,b) where a != b for the two headline strategies.
  const slugs = manifest.strategies.map((s) => s.slug);
  const params: Array<{ a: string; b: string }> = [];
  for (const a of slugs) for (const b of slugs) if (a !== b) params.push({ a, b });
  return params;
}

interface PageProps {
  params: Promise<{ a: string; b: string }>;
}

export default async function ComparePage({ params }: PageProps) {
  const { a, b } = await params;
  let strategyA, strategyB;
  try {
    [strategyA, strategyB] = await Promise.all([getStrategy(a), getStrategy(b)]);
  } catch {
    notFound();
  }
  const [eqA, eqB] = await Promise.all([
    getEquity(a, "350k"),
    getEquity(b, "350k"),
  ]);

  return (
    <div className="space-y-8 fade-rise">
      <header className="space-y-3 max-w-3xl">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-brand-gold-700)]">
          Side-by-side
        </p>
        <h1 className="font-heading text-3xl sm:text-4xl">
          {strategyA.label}{" "}
          <span className="text-muted-foreground">vs</span>{" "}
          {strategyB.label}
        </h1>
        <p className="text-sm text-muted-foreground leading-relaxed">
          Both strategies, RM 350k starting capital, normalised to 100 at panel start so they share a y-axis.
          Holdout window 2020-2022 shaded gold.
        </p>
      </header>

      <Card>
        <CardContent className="py-3">
          {eqA && eqB ? (
            <EquityOverlay
              series={[
                { label: strategyA.label, equity: eqA, color: PALETTE.navy },
                { label: strategyB.label, equity: eqB, color: PALETTE.gold },
              ]}
              normalise
              height={420}
            />
          ) : (
            <p className="py-8 text-center text-muted-foreground">
              Equity curves unavailable for at least one strategy.
            </p>
          )}
        </CardContent>
      </Card>

      <section className="grid sm:grid-cols-2 gap-4">
        {[strategyA, strategyB].map((s) => (
          <Card key={s.slug}>
            <CardContent className="py-4 space-y-3">
              <div className="flex items-baseline justify-between">
                <div className="min-w-0">
                  <p className="text-[10px] uppercase tracking-[0.18em] text-[var(--color-brand-gold-700)]">
                    {strategyFamilyLabel(s.family)} · Rank #{s.rank}
                  </p>
                  <Link
                    href={`/strategies/${s.slug}/`}
                    className="font-heading text-lg hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
                  >
                    {s.label}
                  </Link>
                </div>
                <TierPill tier={s.tier} size="sm" />
              </div>
              <dl className="grid grid-cols-3 gap-3 pt-2 border-t border-border">
                <Pair label="WF Sharpe" value={fmtSharpe(s.metrics.wf_sharpe)} />
                <Pair label="OOS Sharpe" value={fmtSharpe(s.metrics.oos_sharpe)} />
                <Pair
                  label="CAGR OOS"
                  value={<PctChange value={s.metrics.cagr_oos} digits={1} withIcon={false} />}
                />
                <Pair
                  label="Max DD"
                  value={<PctChange value={s.metrics.max_dd} digits={1} withIcon={false} />}
                />
                <Pair label="DSR-eff" value={fmtRatio(s.metrics.dsr_eff)} />
                <Pair label="Cost / leg" value={fmtBps(s.metrics.cost_bps_mean)} />
                <Pair label="Order × floor" value={fmtMultiplier(s.metrics.order_mult)} />
                <Pair label="Hit rate" value={s.metrics.monthly_hit !== null ? `${(s.metrics.monthly_hit * 100).toFixed(0)}%` : "—"} />
                <Pair
                  label="Gates passed"
                  value={`${s.metrics.n_pass ?? "?"}/${s.metrics.n_eval ?? "?"}`}
                />
              </dl>
            </CardContent>
          </Card>
        ))}
      </section>

      <section className="grid sm:grid-cols-2 gap-4">
        {[strategyA, strategyB].map((s) => (
          <Card key={s.slug}>
            <CardContent className="py-3 space-y-2">
              <h2 className="font-heading text-base">{s.label} — scorecard</h2>
              <ScorecardTable gates={s.gates} />
            </CardContent>
          </Card>
        ))}
      </section>

      <section className="space-y-2 max-w-3xl">
        <h2 className="font-heading text-xl">What the comparison says</h2>
        <p className="text-sm text-muted-foreground leading-relaxed">
          The rotation winner has the higher walk-forward Sharpe but loses
          dramatically out-of-sample (1.19 → 0.29). Clenow #9 holds almost all of
          its WF Sharpe (0.93 → 0.92) into the holdout — its alpha survives. Both
          are tier F because they fail on cost / order-size gates at RM 350k retail;
          the deployment story for either of them is the same: scale capital to
          RM 1M+ so the broker floor stops dominating.
        </p>
      </section>
    </div>
  );
}

function Pair({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium text-foreground">
        {typeof value === "string" ? <Numeric>{value}</Numeric> : value}
      </dd>
    </div>
  );
}
