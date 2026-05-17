import { cn } from "@/lib/utils";

interface Props {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  className?: string;
  tone?: "default" | "success" | "danger" | "neutral";
}

const toneClasses: Record<NonNullable<Props["tone"]>, string> = {
  default: "text-foreground",
  success: "text-[var(--color-brand-forest)]",
  danger: "text-[var(--color-brand-amber)]",
  neutral: "text-muted-foreground",
};

export function MetricTile({ label, value, hint, className, tone = "default" }: Props) {
  return (
    <div className={cn("space-y-1", className)}>
      <p className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <div className={cn("font-heading text-2xl sm:text-3xl tabular leading-none", toneClasses[tone])}>
        {value}
      </div>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}
