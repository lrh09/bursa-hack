import { cn } from "@/lib/utils";
import { tierColor } from "@/lib/format";

interface Props {
  tier: string | null | undefined;
  recommendation?: string | null;
  className?: string;
  size?: "sm" | "md" | "lg";
}

export function TierPill({ tier, recommendation, className, size = "md" }: Props) {
  const display = tier ?? "—";
  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 rounded-md px-2.5 py-1",
        tierColor(tier),
        className,
      )}
      title={recommendation ?? undefined}
    >
      <span
        className={cn(
          "font-heading font-semibold leading-none",
          size === "lg" ? "text-2xl" : size === "md" ? "text-base" : "text-sm",
        )}
      >
        Tier {display}
      </span>
      {recommendation ? (
        <span className="text-[11px] uppercase tracking-wider opacity-80">
          {recommendation}
        </span>
      ) : null}
    </div>
  );
}
