import { Card, CardContent } from "@/components/ui/card";
import { GlossaryTerm } from "@/components/common/glossary-term";

export const metadata = {
  title: "Research log",
  description: "Narrative journal — what we tried, what we learned, what's next.",
};

const ENTRIES = [
  {
    date: "2026-05-18",
    title: "Portal v1 — the dashboard you're reading",
    body: (
      <>
        Cleaned up the BursaHack research artifacts into a navigable portal. Two
        finalists make the headline (rotation rank-1 and Clenow rank-9). Every
        other variant is browseable through the search explorer. The investor PDF
        produced earlier this month was the static fallback; this portal is the
        interactive successor.
      </>
    ),
  },
  {
    date: "2026-05-17",
    title: "Holdout verdict written",
    body: (
      <>
        Touched the 2020-2022 holdout exactly once across all top-K survivors.
        The rotation winner&apos;s OOS Sharpe collapsed from a walk-forward 1.19 to
        0.29 — alpha is real but eaten by Bursa retail costs. Clenow rank-9 held
        almost all its WF Sharpe (0.93 → 0.92). That asymmetry is the most
        important finding of the programme so far.
      </>
    ),
  },
  {
    date: "2026-05-12",
    title: "Diagnostics harness landed",
    body: (
      <>
        Added <GlossaryTerm term="PBO" />, <GlossaryTerm term="DSR" />,{" "}
        <GlossaryTerm term="MinBTL" />, parameter neighbourhood, fold-Sharpe CoV,
        slippage drag, IS-OOS rank correlation, White&apos;s Reality Check, and SPA.
        With these together, the framework can actually say something about a
        variant rather than just &quot;the line goes up.&quot;
      </>
    ),
  },
  {
    date: "2026-05-05",
    title: "Expanded brute-force search (102 variants)",
    body: (
      <>
        Replaced the original 12-variant rotation grid with a six-family brute
        force across 186 unique parameter sets (102 survive minimum-trades / no-skip
        filters), each evaluated across 16 walk-forward folds — 2,965 backtests.
        Sparse parameter grid intentional: Bursa universe is small (~250-400 names),
        so finer grids over-search the same regions.
      </>
    ),
  },
  {
    date: "2026-04-22",
    title: "MPlus retail cost model — the binding constraint",
    body: (
      <>
        Built the cost model around MPlus Malacca Securities&apos; actual retail bracket
        (max(RM 8, 0.05%) per leg + Bursa clearing + stamp + sqrt-impact slippage).
        For our RM 350k portfolio, the RM 8 floor binds on 60-80% of legs and is the
        biggest single drag. This is why &quot;the search isn&apos;t overfit but no variant
        passes&quot; — retail costs eat the alpha. A larger portfolio (RM 1M+) would
        amortise the floor; this is the most likely path to deployment.
      </>
    ),
  },
  {
    date: "2026-04-03",
    title: "Holdout discipline locked",
    body: (
      <>
        Decided to wall off 2020-2022 as the holdout — touched exactly once at the
        very end. Walk-forward is informative but optimistic; OOS is the only true
        forward test. The trade-off: if the strategy fails OOS, the holdout is
        burned for this strategy iteration and a new untouched window must be cut.
      </>
    ),
  },
  {
    date: "2026-03-21",
    title: "First panel ingested",
    body: (
      <>
        Sentieo / FactSet XKLS CSV, 781 MB, 2007-01 → 2022-02. Survivorship-free
        (includes delisted names). Two passes: schema sanity (no NaN closes on
        named-trade days), then panel construction (raw + adjusted OHLCV per
        security, dividends embedded in <GlossaryTerm term="ADV">adjusted close</GlossaryTerm>).
      </>
    ),
  },
];

export default function ResearchLog() {
  return (
    <div className="space-y-6 fade-rise">
      <header className="space-y-2 max-w-3xl">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-brand-gold-700)]">
          Journal
        </p>
        <h1 className="font-heading text-3xl sm:text-4xl">Research log</h1>
        <p className="text-sm sm:text-base text-muted-foreground leading-relaxed">
          Most-recent first. What we tried, why, and what we learned. Forms the
          narrative thread behind the numbers on the rest of the portal.
        </p>
      </header>

      <ol className="space-y-4">
        {ENTRIES.map((e) => (
          <li key={e.date}>
            <Card>
              <CardContent className="py-5 space-y-2">
                <div className="flex items-baseline gap-3">
                  <time className="font-mono text-xs text-muted-foreground tabular shrink-0">
                    {e.date}
                  </time>
                  <h2 className="font-heading text-lg leading-tight">{e.title}</h2>
                </div>
                <div className="text-sm text-muted-foreground leading-relaxed">
                  {e.body}
                </div>
              </CardContent>
            </Card>
          </li>
        ))}
      </ol>
    </div>
  );
}
