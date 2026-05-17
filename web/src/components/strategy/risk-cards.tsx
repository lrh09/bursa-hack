import { Card, CardContent } from "@/components/ui/card";
import {
  ShieldAlert,
  TrendingDown,
  Banknote,
  Cpu,
  Droplet,
  Server,
  type LucideIcon,
} from "lucide-react";

interface RiskRow {
  id: string;
  icon: LucideIcon;
  label: string;
  body: string;
}

const RISKS: RiskRow[] = [
  {
    id: "market",
    icon: ShieldAlert,
    label: "Market risk",
    body:
      "The strategy is fully invested in a 20-30 name basket of Bursa equities. Bursa-wide drawdowns translate ~1:1. The 2008 GFC and 2020 COVID stress prints in equity show the strategy does not have an intrinsic crash hedge.",
  },
  {
    id: "regime",
    icon: TrendingDown,
    label: "Regime risk",
    body:
      "Trend-following loses money when the cross-section is choppy (no persistent winners). The 2014-2017 sideways KLCI is the canonical scarring period in our backtest.",
  },
  {
    id: "concentration",
    icon: Banknote,
    label: "Concentration risk",
    body:
      "Top-N constraints keep us to 20-30 names but the universe is small (eligible Bursa equities ≈ 250-400 names depending on the date), so concentration in a single sector (banks, plantations) can occur. The 10% single-name cap is the only formal control.",
  },
  {
    id: "model",
    icon: Cpu,
    label: "Model risk",
    body:
      "Parameters were tuned on 2008-2019 walk-forward data, then frozen. The OOS holdout 2020-2022 is the only true forward test. Param-neighbourhood fragility is captured by gate 4 (param stability).",
  },
  {
    id: "liquidity",
    icon: Droplet,
    label: "Liquidity risk",
    body:
      "ADV floor of RM 500k filters out illiquid names but doesn't immunise against intra-day spread widening. Slippage model assumes 10% of ADV; abrupt regime shifts can blow past it.",
  },
  {
    id: "operational",
    icon: Server,
    label: "Operational risk",
    body:
      "MPlus broker API + monthly rebal cadence + 20-30 line orders. Manual execution is feasible but error-prone. Kill triggers K5/K6 capture the operational failure modes.",
  },
];

export function RiskCards() {
  return (
    <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
      {RISKS.map((r) => {
        const Icon = r.icon;
        return (
          <Card key={r.id}>
            <CardContent className="py-4 space-y-2">
              <div className="flex items-center gap-2 text-foreground">
                <div className="rounded-md size-7 flex items-center justify-center bg-[color:color-mix(in_srgb,var(--color-brand-amber)_12%,transparent)] text-[var(--color-brand-amber)]">
                  <Icon className="size-3.5" aria-hidden />
                </div>
                <h3 className="font-medium text-sm">{r.label}</h3>
              </div>
              <p className="text-xs leading-relaxed text-muted-foreground">{r.body}</p>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
