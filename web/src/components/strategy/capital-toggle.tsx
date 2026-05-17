"use client";

import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";

interface Props {
  available: string[];
  current: string;
}

const LABELS: Record<string, string> = {
  "100k": "RM 100k",
  "350k": "RM 350k",
  "1M": "RM 1M",
};

export function CapitalToggle({ available, current }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const choose = (c: string) => {
    const sp = new URLSearchParams(params);
    sp.set("capital", c);
    router.replace(`${pathname}?${sp.toString()}`, { scroll: false });
  };

  return (
    <div
      role="radiogroup"
      aria-label="Portfolio capital"
      className="inline-flex items-center rounded-md border border-border bg-card p-0.5 tabular text-xs"
    >
      {(["100k", "350k", "1M"] as const).map((c) => {
        const isAvailable = available.includes(c);
        const active = c === current;
        return (
          <button
            key={c}
            role="radio"
            aria-checked={active}
            aria-disabled={!isAvailable}
            disabled={!isAvailable}
            onClick={() => isAvailable && choose(c)}
            className={cn(
              "px-2.5 py-1.5 rounded-[5px] transition-colors",
              active
                ? "bg-[var(--color-brand-navy)] text-white"
                : isAvailable
                  ? "text-foreground hover:bg-muted"
                  : "text-muted-foreground opacity-50 cursor-not-allowed",
            )}
          >
            {LABELS[c]}
          </button>
        );
      })}
    </div>
  );
}
