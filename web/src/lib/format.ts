// Display formatters. Always tabular-numerals (callers apply the class).

const rmFormatter = new Intl.NumberFormat("en-MY", {
  style: "currency",
  currency: "MYR",
  maximumFractionDigits: 0,
});

const rmCompactFormatter = new Intl.NumberFormat("en-MY", {
  style: "currency",
  currency: "MYR",
  notation: "compact",
  maximumFractionDigits: 1,
});

const pctFormatter = (digits: number) =>
  new Intl.NumberFormat("en-MY", {
    style: "percent",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });

export function fmtRM(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return rmFormatter.format(n);
}

export function fmtRMCompact(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return rmCompactFormatter.format(n);
}

export function fmtPct(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return pctFormatter(digits).format(n);
}

export function fmtBps(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${n.toFixed(digits)} bps`;
}

export function fmtSharpe(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toFixed(2);
}

export function fmtRatio(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toFixed(digits);
}

export function fmtMultiplier(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${n.toFixed(digits)}×`;
}

export function fmtDate(s: string | undefined): string {
  if (!s) return "—";
  return new Date(s).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function fmtDateShort(s: string | undefined): string {
  if (!s) return "—";
  return new Date(s).toLocaleDateString("en-GB", {
    month: "short",
    year: "2-digit",
  });
}

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("en-MY", { maximumFractionDigits: 0 }).format(n);
}

export function capitalLabel(cap: string): string {
  if (cap === "100k") return "RM 100,000";
  if (cap === "350k") return "RM 350,000";
  if (cap === "1M") return "RM 1,000,000";
  return cap;
}

export function capitalToNumber(cap: string): number {
  return cap === "100k" ? 100_000 : cap === "1M" ? 1_000_000 : 350_000;
}

export function tierColor(tier: string | null | undefined): string {
  switch (tier) {
    case "A":
      return "text-[var(--color-brand-forest)] bg-[color:color-mix(in_srgb,var(--color-brand-forest)_12%,transparent)]";
    case "B":
      return "text-[var(--color-brand-gold-700)] bg-[color:color-mix(in_srgb,var(--color-brand-gold)_15%,transparent)]";
    case "C":
      return "text-foreground bg-muted";
    case "D":
      return "text-[var(--color-brand-amber)] bg-[color:color-mix(in_srgb,var(--color-brand-amber)_12%,transparent)]";
    case "F":
      return "text-[var(--color-brand-amber)] bg-[color:color-mix(in_srgb,var(--color-brand-amber)_14%,transparent)]";
    default:
      return "text-muted-foreground bg-muted";
  }
}

export function strategyFamilyLabel(family: string): string {
  switch (family) {
    case "rotation":
      return "Cross-sectional rotation";
    case "clenow_som":
      return "Clenow Stocks on the Move";
    case "momentum":
      return "Time-series momentum";
    case "reversal":
      return "Short-horizon reversal";
    case "breakout":
      return "Donchian breakout";
    case "tsmom":
      return "TSMOM";
    default:
      return family;
  }
}
