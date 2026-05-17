import Link from "next/link";

import { Card, CardContent } from "@/components/ui/card";
import { GLOSSARY } from "@/lib/glossary";
import { ScorecardTable } from "@/components/scorecard/scorecard-table";
import { getStrategy } from "@/lib/data";
import { SITE } from "@/lib/site";

export const metadata = {
  title: "Methodology",
  description: "Search design, walk-forward folds, gate framework, glossary.",
};

const STEPS = [
  {
    n: 1,
    title: "Universe construction",
    body:
      "Daily survivorship-free panel of Bursa Malaysia equities (Sentieo / FactSet, 2007-01 → 2022-02). Filtered to price ≥ RM 0.20 and 20-day ADV ≥ RM 500k. Suspended names exit at next rebal.",
  },
  {
    n: 2,
    title: "Strategy families (6)",
    body:
      "Momentum (lookback × skip), Reversal (short-horizon mean-revert), Clenow Stocks-on-the-Move (regime-gated), Dual-slope rotation, Time-series momentum, Donchian breakout.",
  },
  {
    n: 3,
    title: "Cost model",
    body:
      "MPlus Malacca retail (0.05% incl. 1.08% SST, max RM 8/leg) + Bursa clearing (0.03% capped RM 1,000) + stamp (0.10% capped RM 200) + sqrt-impact slippage. T+1 open fill.",
  },
  {
    n: 4,
    title: "Walk-forward validation",
    body:
      "16 rolling folds inside the dev window (3-year train, 1-year validate, 6-month step, 21-day embargo). Selection happens fold-by-fold; the holdout is never seen.",
  },
  {
    n: 5,
    title: "Selection & ranking",
    body:
      "Per-family brute-force grids reduced to top-K by walk-forward Sharpe. Deflated Sharpe Ratio (effective-N) used to penalise multiple testing.",
  },
  {
    n: 6,
    title: "Holdout verdict",
    body:
      "Each top-K variant is run exactly once against the 2020-01 → 2022-02 holdout window at three capital levels. No re-tuning permitted.",
  },
  {
    n: 7,
    title: "12-gate scorecard",
    body:
      "The framework that turns research findings into a deployment recommendation. Gates cover overfitting, robustness, cost, capacity, drawdown, and operational fitness.",
  },
];

export default async function MethodologyPage() {
  const rotation = await getStrategy(SITE.defaultStrategy);
  return (
    <div className="space-y-10 fade-rise">
      <header className="space-y-2 max-w-3xl">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-brand-gold-700)]">
          Framework
        </p>
        <h1 className="font-heading text-3xl sm:text-4xl">Methodology</h1>
        <p className="text-sm sm:text-base text-muted-foreground leading-relaxed">
          From raw panel to deployment recommendation. Every step is intended to
          reduce the chance that a strong-looking backtest is actually noise; the
          12-gate scorecard quantifies what&apos;s left.
        </p>
      </header>

      <section className="space-y-3">
        <h2 className="font-heading text-xl">The pipeline</h2>
        <ol className="space-y-3">
          {STEPS.map((s) => (
            <li
              key={s.n}
              className="grid grid-cols-[36px_1fr] sm:grid-cols-[48px_1fr] items-baseline gap-3 sm:gap-4 rounded-lg border border-border bg-card p-4"
            >
              <span className="font-heading text-2xl tabular text-[var(--color-brand-gold-700)] leading-none">
                {s.n.toString().padStart(2, "0")}
              </span>
              <div>
                <h3 className="font-medium text-foreground">{s.title}</h3>
                <p className="text-sm text-muted-foreground leading-relaxed mt-1">{s.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="space-y-3">
        <h2 className="font-heading text-xl">The 12-gate framework</h2>
        <p className="text-sm text-muted-foreground max-w-2xl">
          Each gate is binary (pass / fail / pending). A strategy is deployable
          when it clears all 12. The values shown are evaluated against the rotation
          winner — read this table as the gate-by-gate worked example.
        </p>
        <Card>
          <CardContent className="py-3">
            <ScorecardTable gates={rotation.gates} />
          </CardContent>
        </Card>
        <p className="text-xs text-muted-foreground">
          Per-variant scorecards live on the{" "}
          <Link className="underline text-[var(--color-brand-gold-700)]" href="/search/">
            search explorer
          </Link>
          .
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="font-heading text-xl">Glossary</h2>
        <p className="text-sm text-muted-foreground max-w-2xl">
          Quick definitions for the recurring technical terms across the portal.
          Most appear with a dotted underline in copy — hover for a tooltip.
        </p>
        <dl className="grid sm:grid-cols-2 gap-3">
          {Object.entries(GLOSSARY).map(([term, entry]) => (
            <div key={term} className="rounded-lg border border-border bg-card p-4">
              <dt className="font-heading text-base text-foreground">{term}</dt>
              <p className="text-xs uppercase tracking-wider text-muted-foreground mt-0.5">
                {entry.short}
              </p>
              <dd className="text-sm text-muted-foreground mt-2 leading-relaxed">
                {entry.long}
              </dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}
