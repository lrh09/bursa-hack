import { TrendingDown, TrendingUp, Minus } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtPct } from "@/lib/format";

interface Props {
  value: number | null | undefined;
  digits?: number;
  withIcon?: boolean;
  withSign?: boolean;
  className?: string;
}

export function PctChange({ value, digits = 2, withIcon = true, withSign = true, className }: Props) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className={cn("tabular text-muted-foreground", className)}>—</span>;
  }
  const tone =
    value > 0
      ? "text-[var(--color-brand-forest)]"
      : value < 0
        ? "text-[var(--color-brand-amber)]"
        : "text-muted-foreground";
  const Icon = value > 0 ? TrendingUp : value < 0 ? TrendingDown : Minus;
  const formatted = fmtPct(Math.abs(value), digits);
  return (
    <span className={cn("inline-flex items-center gap-1 tabular", tone, className)}>
      {withIcon ? <Icon className="size-3.5" aria-hidden /> : null}
      {withSign && value !== 0 ? (value > 0 ? "+" : "−") : ""}
      {formatted}
    </span>
  );
}
