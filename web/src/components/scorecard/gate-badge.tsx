import { CheckCircle2, XCircle, Circle } from "lucide-react";
import { cn } from "@/lib/utils";

import type { GateStatus } from "@/lib/types";

interface Props {
  status: GateStatus;
  className?: string;
  label?: string;
  size?: "sm" | "md";
}

export function GateBadge({ status, className, label, size = "sm" }: Props) {
  const icon =
    status === "PASS" ? (
      <CheckCircle2 className={size === "sm" ? "size-3.5" : "size-4"} aria-hidden />
    ) : status === "FAIL" ? (
      <XCircle className={size === "sm" ? "size-3.5" : "size-4"} aria-hidden />
    ) : (
      <Circle className={size === "sm" ? "size-3.5" : "size-4"} aria-hidden />
    );
  const tone =
    status === "PASS"
      ? "text-[var(--color-brand-forest)] bg-[color:color-mix(in_srgb,var(--color-brand-forest)_14%,transparent)]"
      : status === "FAIL"
        ? "text-[var(--color-brand-amber)] bg-[color:color-mix(in_srgb,var(--color-brand-amber)_14%,transparent)]"
        : "text-muted-foreground bg-muted";
  const text = label ?? (status === "PENDING" ? "Pending" : status);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium tracking-wide",
        tone,
        size === "md" && "px-2.5 py-1 text-xs",
        className,
      )}
      role="status"
      aria-label={`Gate ${text}`}
    >
      {icon}
      {text}
    </span>
  );
}
