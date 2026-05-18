import Link from "next/link";
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
        <h1 className="font-heading text-3xl sm:text-4xl">All variants — flat view</h1>
        <p className="text-sm sm:text-base text-muted-foreground leading-relaxed">
          Cross-strategy variant table — every backtest in the search, ungrouped. Use the{" "}
          <Link href="/strategies/" className="underline text-[var(--color-brand-gold-700)] hover:text-[var(--color-brand-gold)]">Strategy Bank</Link>{" "}
          if you want the curated view. {manifest.variants_count} variants × {manifest.folds_count.toLocaleString()} fold backtests.
        </p>
      </header>
      <VariantExplorer
        variants={variants}
        families={manifest.families}
        hashToSid={manifest.hash_to_strategy_id ?? {}}
      />
    </div>
  );
}
