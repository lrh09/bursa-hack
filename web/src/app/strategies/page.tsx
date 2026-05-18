import { getManifest } from "@/lib/data";
import { BankTable } from "@/components/strategy/bank-table";

export const metadata = {
  title: "Strategy Bank",
  description:
    "All strategy concepts in the BursaHack research, grouped by family x broad-shape switches.",
};

export default async function StrategiesIndex() {
  const manifest = await getManifest();
  const strategies = manifest.strategies;
  const totalVariants = strategies.reduce(
    (s, e) => s + (e.variant_count ?? 0),
    0,
  );

  return (
    <div className="space-y-6 fade-rise">
      <header className="space-y-2 max-w-3xl">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-brand-gold-700)]">
          Strategy Bank
        </p>
        <h1 className="font-heading text-3xl sm:text-4xl">
          Strategies in the BursaHack research
        </h1>
        <p className="text-sm sm:text-base text-muted-foreground leading-relaxed">
          {strategies.length} strategies, {totalVariants} parameter variants
          searched. Each row is one strategy concept; click in to see how the
          variants under it behaved.
        </p>
      </header>
      <BankTable entries={strategies} />
    </div>
  );
}
