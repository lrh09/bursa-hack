import { getAllStrategies, getFolds, getManifest } from "@/lib/data";
import { FoldExplorer } from "@/components/folds/fold-explorer";

export const metadata = {
  title: "Walk-forward folds",
  description: "Per-fold validation Sharpe across all strategies in the search.",
};

export default async function FoldsPage() {
  const [manifest, allStrategies] = await Promise.all([getManifest(), getAllStrategies()]);
  const variantLabel: Record<string, string> = {};
  for (const s of allStrategies) variantLabel[s.params_hash] = s.label;

  // Group folds per family for the picker.
  const byStrategy: Record<string, Awaited<ReturnType<typeof getFolds>>> = {};
  for (const family of manifest.families) {
    byStrategy[family] = await getFolds(family);
  }

  return (
    <div className="space-y-6 fade-rise">
      <header className="space-y-2 max-w-3xl">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-brand-gold-700)]">
          Validation discipline
        </p>
        <h1 className="font-heading text-3xl sm:text-4xl">Walk-forward folds</h1>
        <p className="text-sm sm:text-base text-muted-foreground leading-relaxed">
          The 2008-2019 dev set is split into 16 rolling train/validate windows
          (3-year train + 1-year validate, step every 6 months, 21-day embargo).
          Every variant is evaluated across all 16 folds; aggregate stats feed the
          search ranking and the per-variant scorecard.
        </p>
      </header>
      <FoldExplorer byStrategy={byStrategy} variantLabel={variantLabel} />
    </div>
  );
}
