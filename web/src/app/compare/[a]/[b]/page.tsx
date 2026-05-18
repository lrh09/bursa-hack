import Link from "next/link";
import { notFound } from "next/navigation";

import {
  getEquity,
  getManifest,
  getStrategy,
  getStrategyAliases,
  getStrategyV2,
} from "@/lib/data";
import { EquityOverlay } from "@/components/charts/equity-overlay";
import { ScorecardTable } from "@/components/scorecard/scorecard-table";
import { TierPill } from "@/components/scorecard/tier-pill";
import { Card, CardContent } from "@/components/ui/card";
import { Numeric } from "@/components/common/numeric";
import { PctChange } from "@/components/common/pct-change";
import { PALETTE } from "@/lib/theme";
import { fmtBps, fmtMultiplier, fmtRatio, fmtSharpe, strategyFamilyLabel } from "@/lib/format";
import type {
  Gate,
  Strategy as LegacyStrategy,
  StrategyBundleV2,
  VariantInline,
} from "@/lib/types";

// T14: each route param can now be a legacy slug, a strategy_id (V2 bank),
// or a variant params_hash. resolveRef normalises that.
type ResolvedRef =
  | { kind: "legacy"; slug: string; strategy: LegacyStrategy }
  | {
      kind: "strategyV2";
      strategy_id: string;
      bundle: StrategyBundleV2;
      headlineHash: string;
    }
  | {
      kind: "variantInBank";
      params_hash: string;
      sid: string;
      variant: VariantInline;
      bundle: StrategyBundleV2;
    };

async function resolveRef(token: string): Promise<ResolvedRef | null> {
  // (a) Legacy slug — if a hand-coded legacy bundle exists, prefer it so the
  // pre-T14 rendering (full gates / diagnostics / equity-by-slug) keeps working.
  // This covers the canonical /compare/rotation_rank_1/clenow_som_rank_9/ URL.
  try {
    const strategy = await getStrategy(token);
    // getStrategy returns a parsed JSON; legacy bundles have `gates` and `slug`.
    if (strategy && Array.isArray((strategy as LegacyStrategy).gates)) {
      return { kind: "legacy", slug: token, strategy };
    }
  } catch {
    // not a legacy bundle, fall through
  }

  const manifest = await getManifest();

  // (b) strategy_id direct match in the V2 bank manifest
  const knownSids = new Set<string>(
    manifest.strategies
      .map((s) => s.strategy_id)
      .filter((sid): sid is string => typeof sid === "string"),
  );
  if (knownSids.has(token)) {
    const bundle = await getStrategyV2(token);
    return {
      kind: "strategyV2",
      strategy_id: token,
      bundle,
      headlineHash: bundle.headline_variant_hash,
    };
  }

  // (c) params_hash -> bank variant
  const sid = manifest.hash_to_strategy_id?.[token];
  if (sid) {
    const bundle = await getStrategyV2(sid);
    const variant = bundle.variants_inline.find((v) => v.params_hash === token);
    if (variant) {
      return { kind: "variantInBank", params_hash: token, sid, variant, bundle };
    }
  }

  // (d) Legacy alias fallback (rotation_rank_1 -> rotation__rebal-M, etc.)
  const aliases = await getStrategyAliases();
  if (aliases[token]) {
    return resolveRef(aliases[token]);
  }

  return null;
}

// Card-display shape used by the existing rendering. Build from any ResolvedRef.
interface DisplayCard {
  href: string;
  label: string;
  family: string;
  rank: number | string;
  tier: string | null;
  gates: Gate[];
  metrics: {
    wf_sharpe: number | null;
    oos_sharpe: number | null;
    cagr_oos: number | null;
    max_dd: number | null;
    dsr_eff: number | null;
    cost_bps_mean: number | null;
    order_mult: number | null;
    monthly_hit: number | null;
    n_pass: number | null;
    n_eval: number | null;
  };
}

function aggMedian(
  bundle: StrategyBundleV2,
  key: string,
): number | null {
  const five = bundle.aggregate_metrics?.[key];
  return five?.median ?? null;
}

function toDisplay(ref: ResolvedRef): DisplayCard {
  if (ref.kind === "legacy") {
    const s = ref.strategy;
    return {
      href: `/strategies/${s.slug}/`,
      label: s.label,
      family: s.family,
      rank: s.rank,
      tier: s.tier,
      gates: s.gates,
      metrics: {
        wf_sharpe: s.metrics.wf_sharpe,
        oos_sharpe: s.metrics.oos_sharpe,
        cagr_oos: s.metrics.cagr_oos,
        max_dd: s.metrics.max_dd,
        dsr_eff: s.metrics.dsr_eff,
        cost_bps_mean: s.metrics.cost_bps_mean ?? null,
        order_mult: s.metrics.order_mult,
        monthly_hit: s.metrics.monthly_hit,
        n_pass: s.metrics.n_pass,
        n_eval: s.metrics.n_eval,
      },
    };
  }
  if (ref.kind === "strategyV2") {
    const b = ref.bundle;
    const headline = b.variants_inline.find((v) => v.params_hash === b.headline_variant_hash);
    return {
      href: `/strategies/${b.strategy_id}/`,
      label: b.display_name,
      family: b.family,
      rank: "—",
      tier: headline?.tier ?? null,
      gates: [],
      metrics: {
        wf_sharpe: headline?.wf_sharpe ?? aggMedian(b, "wf_sharpe"),
        oos_sharpe: headline?.oos_sharpe ?? aggMedian(b, "oos_sharpe"),
        cagr_oos: headline?.cagr_oos ?? aggMedian(b, "cagr_oos"),
        max_dd: headline?.max_dd ?? aggMedian(b, "max_dd"),
        dsr_eff: aggMedian(b, "dsr_eff"),
        cost_bps_mean: aggMedian(b, "cost_bps_mean"),
        order_mult: headline?.order_mult ?? aggMedian(b, "order_mult"),
        monthly_hit: headline?.monthly_hit ?? aggMedian(b, "monthly_hit"),
        n_pass: headline?.n_pass ?? null,
        n_eval: headline?.n_eval ?? null,
      },
    };
  }
  // variantInBank
  const v = ref.variant;
  const b = ref.bundle;
  return {
    href: `/strategies/${b.strategy_id}/`,
    label: `${b.display_name} — ${v.params_hash.slice(0, 8)}`,
    family: b.family,
    rank: "—",
    tier: v.tier,
    gates: [],
    metrics: {
      wf_sharpe: v.wf_sharpe,
      oos_sharpe: v.oos_sharpe,
      cagr_oos: v.cagr_oos,
      max_dd: v.max_dd,
      dsr_eff: aggMedian(b, "dsr_eff"),
      cost_bps_mean: aggMedian(b, "cost_bps_mean"),
      order_mult: v.order_mult,
      monthly_hit: v.monthly_hit,
      n_pass: v.n_pass,
      n_eval: v.n_eval,
    },
  };
}

function equityKey(ref: ResolvedRef): string {
  if (ref.kind === "legacy") return ref.slug;
  if (ref.kind === "variantInBank") return ref.params_hash;
  return ref.headlineHash;
}

export async function generateStaticParams() {
  const manifest = await getManifest();
  // Generate ordered pairs (a,b) where a != b for the two headline strategies.
  // See /strategies/[slug] for why we union manifest slugs with alias keys.
  const manifestSlugs = manifest.strategies
    .map((s) => s.slug)
    .filter((s): s is string => typeof s === "string");
  const aliasSlugs = Object.keys(await getStrategyAliases());
  const slugs = Array.from(new Set([...manifestSlugs, ...aliasSlugs]));
  const params: Array<{ a: string; b: string }> = [];
  for (const a of slugs) for (const b of slugs) if (a !== b) params.push({ a, b });
  return params;
}

interface PageProps {
  params: Promise<{ a: string; b: string }>;
}

export default async function ComparePage({ params }: PageProps) {
  const { a, b } = await params;
  const [aRef, bRef] = await Promise.all([resolveRef(a), resolveRef(b)]);
  if (!aRef || !bRef) notFound();

  const cardA = toDisplay(aRef);
  const cardB = toDisplay(bRef);

  const [eqA, eqB] = await Promise.all([
    getEquity(equityKey(aRef), "350k"),
    getEquity(equityKey(bRef), "350k"),
  ]);

  return (
    <div className="space-y-8 fade-rise">
      <header className="space-y-3 max-w-3xl">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-brand-gold-700)]">
          Side-by-side
        </p>
        <h1 className="font-heading text-3xl sm:text-4xl">
          {cardA.label}{" "}
          <span className="text-muted-foreground">vs</span>{" "}
          {cardB.label}
        </h1>
        <p className="text-sm text-muted-foreground leading-relaxed">
          Both strategies, RM 350k starting capital, normalised to 100 at panel start so they share a y-axis.
          Holdout window 2020-2022 shaded gold.
        </p>
      </header>

      <Card>
        <CardContent className="py-3">
          {eqA && eqB ? (
            <EquityOverlay
              series={[
                { label: cardA.label, equity: eqA, color: PALETTE.navy },
                { label: cardB.label, equity: eqB, color: PALETTE.gold },
              ]}
              normalise
              height={420}
            />
          ) : (
            <p className="py-8 text-center text-muted-foreground">
              Equity curves unavailable for at least one strategy.
            </p>
          )}
        </CardContent>
      </Card>

      <section className="grid sm:grid-cols-2 gap-4">
        {[cardA, cardB].map((s, i) => (
          <Card key={`${s.href}-${i}`}>
            <CardContent className="py-4 space-y-3">
              <div className="flex items-baseline justify-between">
                <div className="min-w-0">
                  <p className="text-[10px] uppercase tracking-[0.18em] text-[var(--color-brand-gold-700)]">
                    {strategyFamilyLabel(s.family)} · Rank #{s.rank}
                  </p>
                  <Link
                    href={s.href}
                    className="font-heading text-lg hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
                  >
                    {s.label}
                  </Link>
                </div>
                <TierPill tier={s.tier} size="sm" />
              </div>
              <dl className="grid grid-cols-3 gap-3 pt-2 border-t border-border">
                <Pair label="WF Sharpe" value={fmtSharpe(s.metrics.wf_sharpe)} />
                <Pair label="OOS Sharpe" value={fmtSharpe(s.metrics.oos_sharpe)} />
                <Pair
                  label="CAGR OOS"
                  value={<PctChange value={s.metrics.cagr_oos} digits={1} withIcon={false} />}
                />
                <Pair
                  label="Max DD"
                  value={<PctChange value={s.metrics.max_dd} digits={1} withIcon={false} />}
                />
                <Pair label="DSR-eff" value={fmtRatio(s.metrics.dsr_eff)} />
                <Pair label="Cost / leg" value={fmtBps(s.metrics.cost_bps_mean)} />
                <Pair label="Order × floor" value={fmtMultiplier(s.metrics.order_mult)} />
                <Pair label="Hit rate" value={s.metrics.monthly_hit !== null ? `${(s.metrics.monthly_hit * 100).toFixed(0)}%` : "—"} />
                <Pair
                  label="Gates passed"
                  value={`${s.metrics.n_pass ?? "?"}/${s.metrics.n_eval ?? "?"}`}
                />
              </dl>
            </CardContent>
          </Card>
        ))}
      </section>

      <section className="grid sm:grid-cols-2 gap-4">
        {[cardA, cardB].map((s, i) => (
          <Card key={`scorecard-${s.href}-${i}`}>
            <CardContent className="py-3 space-y-2">
              <h2 className="font-heading text-base">{s.label} — scorecard</h2>
              {s.gates.length > 0 ? (
                <ScorecardTable gates={s.gates} />
              ) : (
                <p className="text-sm text-muted-foreground">
                  Per-gate breakdown lives on the strategy page.{" "}
                  <Link href={s.href} className="underline">
                    Open {s.label}
                  </Link>
                  .
                </p>
              )}
            </CardContent>
          </Card>
        ))}
      </section>

      <section className="space-y-2 max-w-3xl">
        <h2 className="font-heading text-xl">What the comparison says</h2>
        <p className="text-sm text-muted-foreground leading-relaxed">
          The rotation winner has the higher walk-forward Sharpe but loses
          dramatically out-of-sample (1.19 → 0.29). Clenow #9 holds almost all of
          its WF Sharpe (0.93 → 0.92) into the holdout — its alpha survives. Both
          are tier F because they fail on cost / order-size gates at RM 350k retail;
          the deployment story for either of them is the same: scale capital to
          RM 1M+ so the broker floor stops dominating.
        </p>
      </section>
    </div>
  );
}

function Pair({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium text-foreground">
        {typeof value === "string" ? <Numeric>{value}</Numeric> : value}
      </dd>
    </div>
  );
}
