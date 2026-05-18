"use client";

import { useRouter, usePathname, useSearchParams } from "next/navigation";

import { VariantSlideOver } from "./variant-slide-over";
import type { StrategyBundleV2 } from "@/lib/types";

export function VariantSlideOverWrapper({ bundle }: { bundle: StrategyBundleV2 }) {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const hash = sp.get("v");
  const variant = hash
    ? bundle.variants_inline.find((v) => v.params_hash === hash) ?? null
    : null;

  return (
    <VariantSlideOver
      variant={variant}
      onClose={() => router.push(pathname)}
    />
  );
}
