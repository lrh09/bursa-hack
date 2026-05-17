import { GlossaryTerm } from "@/components/common/glossary-term";

export function HowToRead() {
  return (
    <details className="rounded-lg border border-border bg-card" open>
      <summary className="cursor-pointer px-5 py-3 font-heading text-base text-foreground select-none">
        How to read this portal
        <span className="ml-2 text-xs text-muted-foreground font-sans">click to collapse</span>
      </summary>
      <div className="px-5 pb-5 space-y-3 text-sm leading-relaxed text-muted-foreground">
        <p>
          The programme tested 102 distinct momentum / trend-following recipes against a 15-year,
          survivorship-free panel of Bursa Malaysia equities (2007-01 to 2022-02). Each recipe was
          evaluated through <GlossaryTerm term="Walk-forward">walk-forward validation</GlossaryTerm>
          {" "}(16 train/validate splits) and then once against an untouched
          {" "}<GlossaryTerm term="Holdout">2020-2022 holdout</GlossaryTerm>.
        </p>
        <p>
          Two finalists are the headline story: a dual-slope cross-sectional rotation (the
          search&apos;s best in-sample Sharpe) and a Clenow Stocks-on-the-Move with a 60-day
          lookback and a market-regime filter (the strongest out-of-sample Sharpe). Both
          are <strong className="text-foreground">tier F</strong> against our 12-gate framework — neither has cleared all gates for live capital.
        </p>
        <p>
          On every page, the <span className="rounded-sm bg-amber-500/10 text-[var(--color-brand-amber)] px-1 font-semibold">Research stage</span>
          {" "}banner reminds you that figures shown are historical research, not live performance.
          Numbers reconcile back to the artifacts in <code className="text-xs bg-muted px-1.5 py-0.5 rounded">results/</code>.
        </p>
      </div>
    </details>
  );
}
