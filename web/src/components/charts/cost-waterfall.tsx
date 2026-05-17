"use client";

import { PALETTE } from "@/lib/theme";

interface Props {
  /** Total cost per leg in basis points. Decomposed into MPlus retail brackets. */
  costBps: number;
}

const SHARES: Array<{ name: string; share: number; color: string; note: string }> = [
  {
    name: "Brokerage commission",
    share: 0.54,
    color: PALETTE.navy,
    note: "MPlus 0.05% incl. 1.08% SST; max(RM 8, 0.05% × notional)",
  },
  {
    name: "Bursa clearing fee",
    share: 0.31,
    color: PALETTE.navy700,
    note: "0.03% of notional, capped at RM 1,000",
  },
  {
    name: "Stamp duty",
    share: 0.09,
    color: PALETTE.gold700,
    note: "0.10% on settled trade value, capped at RM 200",
  },
  {
    name: "Slippage / impact",
    share: 0.06,
    color: PALETTE.amber,
    note: "sqrt-impact model proportional to ADV participation",
  },
];

export function CostWaterfall({ costBps }: Props) {
  const total = costBps;
  return (
    <div className="space-y-3">
      <div className="relative w-full h-14 rounded-md overflow-hidden ring-1 ring-border bg-background">
        <div className="flex w-full h-full">
          {SHARES.map((s) => (
            <div
              key={s.name}
              className="h-full flex items-center justify-center text-[10px] text-white/90 font-medium tabular"
              style={{
                width: `${s.share * 100}%`,
                background: s.color,
              }}
              title={`${s.name}: ${(s.share * total).toFixed(2)} bps`}
            >
              {s.share >= 0.10 ? `${(s.share * total).toFixed(1)} bps` : ""}
            </div>
          ))}
        </div>
        <div className="absolute -bottom-0.5 right-2 bg-background px-1 text-[10px] text-muted-foreground tabular">
          {total.toFixed(1)} bps total
        </div>
      </div>
      <ul className="grid sm:grid-cols-2 gap-2 text-xs">
        {SHARES.map((s) => (
          <li key={s.name} className="flex items-start gap-2">
            <span
              className="mt-1 size-2.5 rounded-sm shrink-0"
              style={{ background: s.color }}
              aria-hidden
            />
            <div className="min-w-0">
              <div className="flex items-baseline gap-2">
                <span className="font-medium text-foreground">{s.name}</span>
                <span className="tabular text-muted-foreground">
                  {(s.share * 100).toFixed(0)}% · {(s.share * total).toFixed(2)} bps
                </span>
              </div>
              <p className="text-muted-foreground text-[11px] mt-0.5">{s.note}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
