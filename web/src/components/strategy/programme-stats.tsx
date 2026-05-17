import { AnimatedCounter } from "@/components/common/animated-counter";
import type { Manifest } from "@/lib/types";

interface Props {
  manifest: Manifest;
}

export function ProgrammeStats({ manifest }: Props) {
  const items: Array<{ label: string; value: number; suffix?: string; compact?: boolean; digits?: number }> = [
    { label: "Variants tested", value: manifest.variants_count },
    { label: "Backtests run", value: manifest.folds_count, compact: true },
    { label: "Walk-forward folds", value: 16 },
    { label: "Years of panel", value: 15 },
    { label: "Holdout touches", value: 1 },
  ];
  return (
    <dl className="grid grid-cols-2 sm:grid-cols-5 gap-4 sm:gap-6 border-y border-border py-6">
      {items.map((it) => (
        <div key={it.label} className="space-y-1">
          <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{it.label}</dt>
          <dd className="font-heading text-3xl sm:text-4xl leading-none tabular text-foreground">
            <AnimatedCounter value={it.value} digits={it.digits ?? 0} compact={it.compact} suffix={it.suffix} />
          </dd>
        </div>
      ))}
    </dl>
  );
}
