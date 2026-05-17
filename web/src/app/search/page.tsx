import { getAllVariants, getManifest } from "@/lib/data";
import { VariantExplorer } from "@/components/search/variant-explorer";

export const metadata = {
  title: "Search explorer",
  description: "Browse every variant in the BursaHack brute-force search.",
};

export default async function SearchPage() {
  const [variants, manifest] = await Promise.all([getAllVariants(), getManifest()]);
  return (
    <div className="space-y-6 fade-rise">
      <header className="space-y-2 max-w-3xl">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-brand-gold-700)]">
          Brute-force search
        </p>
        <h1 className="font-heading text-3xl sm:text-4xl">Search explorer</h1>
        <p className="text-sm sm:text-base text-muted-foreground leading-relaxed">
          Every {manifest.variants_count} variant of the parameter sweep, evaluated across {manifest.folds_count.toLocaleString()} walk-forward fold backtests. Filter by family, sort by metric, click into any row for the full per-variant scorecard.
        </p>
      </header>
      <VariantExplorer variants={variants} families={manifest.families} />
    </div>
  );
}
