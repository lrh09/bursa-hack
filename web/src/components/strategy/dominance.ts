import type { VariantInline } from "@/lib/types";

// Frozen dominance gate set per spec §6.1. DSR_eff is not in VariantInline yet;
// re-add when it's available.
const GATES: Array<{
  key: keyof VariantInline;
  better: "higher" | "lower";
}> = [
  { key: "oos_sharpe", better: "higher" },
  { key: "max_dd",     better: "higher" },   // less negative = better
  { key: "cov",        better: "lower" },
];

function ge(a: number, b: number, better: "higher" | "lower"): boolean {
  return better === "higher" ? a >= b : a <= b;
}
function gt(a: number, b: number, better: "higher" | "lower"): boolean {
  return better === "higher" ? a > b : a < b;
}

export function isDominated(candidate: VariantInline, others: VariantInline[]): boolean {
  for (const other of others) {
    if (other.params_hash === candidate.params_hash) continue;
    let allBetterOrEqual = true;
    let strictlyBetterOnOne = false;
    for (const g of GATES) {
      const a = candidate[g.key] as number | null;
      const b = other[g.key] as number | null;
      if (a == null || b == null) { allBetterOrEqual = false; break; }
      // "other >= candidate" on every gate means candidate is NOT strictly better
      if (!ge(b, a, g.better)) { allBetterOrEqual = false; break; }
      if (gt(b, a, g.better)) strictlyBetterOnOne = true;
    }
    if (allBetterOrEqual && strictlyBetterOnOne) return true;
  }
  return false;
}

export function nonDominated(variants: VariantInline[]): VariantInline[] {
  return variants.filter((v) => !isDominated(v, variants));
}
