import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import {
  getEquity,
  getFolds,
  getManifest,
  getStrategy,
  getStrategyAliases,
  getStrategyV2,
  getTrades,
} from "@/lib/data";
import { StrategyHeader } from "@/components/strategy/strategy-header";
import { CapitalToggle } from "@/components/strategy/capital-toggle";
import { EquityCurveChart } from "@/components/charts/equity-curve";
import { DrawdownPanel } from "@/components/charts/drawdown-panel";
import { MonthlyHeatmap } from "@/components/charts/monthly-heatmap";
import { CalendarBar } from "@/components/charts/calendar-bar";
import { RollingSharpeChart } from "@/components/charts/rolling-sharpe";
import { FoldTimeline } from "@/components/charts/fold-timeline";
import { CostWaterfall } from "@/components/charts/cost-waterfall";
import { ParamsTable } from "@/components/strategy/params-table";
import { FoldTable } from "@/components/strategy/fold-table";
import { RiskCards } from "@/components/strategy/risk-cards";
import { TradeExplorer } from "@/components/strategy/trade-explorer";
import { VariantExplorer } from "@/components/strategy/variant-explorer";
import { VariantSlideOverWrapper } from "@/components/strategy/variant-slide-over-wrapper";
import { ParamHeatmap } from "@/components/strategy/param-heatmap";
import { ScorecardTable } from "@/components/scorecard/scorecard-table";
import { DiagnosticsList } from "@/components/scorecard/diagnostics-list";
import { KillTriggers } from "@/components/scorecard/kill-triggers";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent } from "@/components/ui/card";
import type { Strategy as LegacyStrategy, StrategyBundleV2 } from "@/lib/types";

export async function generateStaticParams() {
  const manifest = await getManifest();
  const aliases = await getStrategyAliases();

  const sids = manifest.strategies
    .map((s) => s.strategy_id)
    .filter((sid): sid is string => typeof sid === "string");
  const legacySlugs = Object.keys(aliases); // rotation_rank_1, clenow_som_rank_9

  // Dedupe just in case.
  const all = Array.from(new Set([...sids, ...legacySlugs]));
  return all.map((slug) => ({ slug }));
}

interface PageProps {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}

export default async function StrategyPage({ params, searchParams }: PageProps) {
  const { slug } = await params;
  const sp = await searchParams;

  // (1) Alias redirect: rotation_rank_1 -> rotation__rebal-M
  const aliases = await getStrategyAliases();
  if (aliases[slug]) {
    redirect(`/strategies/${aliases[slug]}/`);
  }

  // (2) New bank bundle?
  let bundle: StrategyBundleV2 | null = null;
  try {
    bundle = await getStrategyV2(slug);
  } catch {
    // Falls through
  }

  if (bundle) {
    return <StrategyBankPage bundle={bundle} selectedHash={sp.v ?? null} />;
  }

  // (3) Legacy fallback
  let strategy;
  try {
    strategy = await getStrategy(slug);
  } catch {
    notFound();
  }
  return <StrategyLegacyPage strategy={strategy} sp={sp} />;
}

/* ------------------------------------------------------------------ */
/* NEW: bank-style page for the auto-derived strategy bundles         */
/* ------------------------------------------------------------------ */

function StrategyBankPage({
  bundle,
  selectedHash,
}: {
  bundle: StrategyBundleV2;
  selectedHash: string | null;
}) {
  const head = bundle.variants_inline.find((v) => v.headline) ?? bundle.variants_inline[0];
  const contKeys = Array.from(new Set(
    bundle.variants_inline.flatMap((v) => Object.keys(v.params)),
  )).filter((k) => {
    const values = new Set(bundle.variants_inline.map((v) => v.params[k]));
    return values.size >= 2 && [...values].every((x) => typeof x === "number");
  });
  return (
    <div className="space-y-8 fade-rise">
      <header className="space-y-2 max-w-3xl">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-brand-gold-700)]">
          Strategy Bank
        </p>
        <h1 className="font-heading text-3xl sm:text-4xl">{bundle.display_name}</h1>
        <p className="text-sm text-muted-foreground">{bundle.short_blurb}</p>
        <p className="text-xs text-muted-foreground tabular">{bundle.one_liner}</p>
      </header>

      <section className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
        <Kpi label="Variants" value={String(bundle.variant_count)} />
        <Kpi label="Best OOS Sharpe" value={fmtNum(bundle.aggregate_metrics.oos_sharpe?.max)} />
        <Kpi label="Median WF Sharpe" value={fmtNum(bundle.aggregate_metrics.wf_sharpe?.median)} />
        <Kpi label="Headline tier" value={head?.tier ?? "—"} />
      </section>

      <div className="grid lg:grid-cols-[3fr_2fr] gap-5 items-start">
        <VariantExplorer bundle={bundle} selectedHash={selectedHash} />
        <ParamHeatmap variants={bundle.variants_inline} contKeys={contKeys} />
      </div>

      <section className="prose prose-sm max-w-none">
        <pre className="whitespace-pre-wrap text-xs">{bundle.definition_md}</pre>
      </section>

      <VariantSlideOverWrapper bundle={bundle} />
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border p-3">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="font-heading text-xl tabular">{value}</div>
    </div>
  );
}

function fmtNum(v: number | null | undefined) {
  return v == null ? "—" : v.toFixed(2);
}

/* ------------------------------------------------------------------ */
/* LEGACY: existing single-variant page (rotation_rank_1, clenow_…)   */
/* ------------------------------------------------------------------ */

async function StrategyLegacyPage({
  strategy,
  sp,
}: {
  strategy: LegacyStrategy;
  sp: Record<string, string | undefined>;
}) {
  const requestedCap = sp.capital;
  const capital = requestedCap && strategy.equity_capitals.includes(requestedCap)
    ? requestedCap
    : strategy.equity_capitals.includes("350k")
      ? "350k"
      : strategy.equity_capitals[0] ?? "350k";

  const [equity, folds, trades] = await Promise.all([
    getEquity(strategy.slug, capital),
    getFolds(strategy.family),
    getTrades(strategy.slug),
  ]);
  const variantFolds = folds.filter((f) => f.params_hash === strategy.params_hash);

  return (
    <div className="space-y-8 fade-rise">
      <StrategyHeader strategy={strategy} />

      <Tabs defaultValue="performance" className="space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <TabsList className="overflow-x-auto">
            <TabsTrigger value="performance">Performance</TabsTrigger>
            <TabsTrigger value="scorecard">Scorecard</TabsTrigger>
            <TabsTrigger value="methodology">Methodology</TabsTrigger>
            <TabsTrigger value="costs">Costs</TabsTrigger>
            <TabsTrigger value="trades">Trades</TabsTrigger>
            <TabsTrigger value="risks">Risks</TabsTrigger>
          </TabsList>
          {strategy.equity_capitals.length > 1 && (
            <CapitalToggle available={strategy.equity_capitals} current={capital} />
          )}
        </div>

        <TabsContent value="performance" className="space-y-6">
          {equity ? (
            <>
              <Card>
                <CardContent className="py-3">
                  <EquityCurveChart equity={equity} height={420} />
                </CardContent>
              </Card>
              <div className="grid lg:grid-cols-[1fr_300px] gap-5 items-start">
                <Card>
                  <CardContent className="py-3">
                    <DrawdownPanel equity={equity} />
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="py-4 space-y-3 text-sm">
                    <div>
                      <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Window</p>
                      <p className="tabular">{equity.dates[0]} → {equity.dates[equity.dates.length - 1]}</p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Starting equity</p>
                      <p className="tabular">RM {equity.equity[0].toLocaleString("en-MY", { maximumFractionDigits: 0 })}</p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Final equity</p>
                      <p className="tabular text-[var(--color-brand-navy)] font-semibold">
                        RM {equity.equity[equity.equity.length - 1].toLocaleString("en-MY", { maximumFractionDigits: 0 })}
                      </p>
                    </div>
                    <p className="text-[11px] text-muted-foreground border-t border-border pt-3">
                      Holdout window (2020-2022) shaded. Hover the curve for date / value / drawdown.
                    </p>
                  </CardContent>
                </Card>
              </div>

              {strategy.calendar_year_returns && (
                <Card>
                  <CardContent className="py-3 space-y-2">
                    <h2 className="font-heading text-base">Calendar-year returns</h2>
                    <CalendarBar data={strategy.calendar_year_returns} />
                  </CardContent>
                </Card>
              )}

              {strategy.monthly_grid && (
                <Card>
                  <CardContent className="py-3 space-y-2">
                    <h2 className="font-heading text-base">Monthly returns heatmap</h2>
                    <MonthlyHeatmap grid={strategy.monthly_grid} />
                  </CardContent>
                </Card>
              )}

              {strategy.rolling_sharpe && (
                <Card>
                  <CardContent className="py-3 space-y-2">
                    <h2 className="font-heading text-base">Rolling 6-month Sharpe</h2>
                    <RollingSharpeChart equity={equity} rolling={strategy.rolling_sharpe} />
                  </CardContent>
                </Card>
              )}
            </>
          ) : (
            <Card>
              <CardContent className="py-8 text-center text-muted-foreground">
                No equity curve available for this strategy.
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="scorecard" className="space-y-5">
          <Card>
            <CardContent className="py-3">
              <ScorecardTable gates={strategy.gates} />
            </CardContent>
          </Card>
          {strategy.diagnostics.length > 0 && (
            <Card>
              <CardContent className="py-4">
                <DiagnosticsList diagnostics={strategy.diagnostics} />
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="methodology" className="space-y-5">
          <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
            <Card>
              <CardContent className="py-3 space-y-2">
                <h2 className="font-heading text-base">Walk-forward folds</h2>
                <FoldTimeline folds={variantFolds} />
              </CardContent>
            </Card>
            <Card>
              <CardContent className="py-3 space-y-2">
                <h2 className="font-heading text-base">Parameters</h2>
                <ParamsTable params={strategy.params} />
              </CardContent>
            </Card>
          </div>
          {variantFolds.length > 0 && (
            <Card>
              <CardContent className="py-3 space-y-2">
                <h2 className="font-heading text-base">Per-fold detail</h2>
                <FoldTable folds={variantFolds} />
              </CardContent>
            </Card>
          )}
          {strategy.is_stability && (
            <Card>
              <CardContent className="py-4 space-y-3">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <h2 className="font-heading text-base">In-sample param stability</h2>
                  <span className="text-[11px] uppercase tracking-wider text-muted-foreground">
                    {strategy.is_stability.method}
                  </span>
                </div>
                <p className="text-sm text-muted-foreground">
                  Iron rule honoured: <span className="tabular">{strategy.is_stability.iron_rule}</span>.
                  Each numeric param perturbed ±15% independently; Sharpe recomputed on the IS slice.
                  Threshold: max %-drop &gt; −25% to pass.
                </p>
                <div className="grid gap-3 md:grid-cols-2">
                  {Object.entries(strategy.is_stability.variants).map(([key, v]) => (
                    <div key={key} className="rounded-md border border-border p-3 space-y-2">
                      <div className="flex items-baseline justify-between gap-2">
                        <p className="font-medium text-sm">{v.label}</p>
                        <span
                          className={
                            "text-[11px] uppercase tracking-wider tabular font-semibold " +
                            (v.passed ? "text-emerald-700" : "text-red-700")
                          }
                        >
                          {v.passed ? "PASS" : "FAIL"}
                        </span>
                      </div>
                      <div className="grid grid-cols-2 gap-x-3 text-xs">
                        <div>
                          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Base Sharpe (IS)</p>
                          <p className="tabular text-sm font-medium">{v.base_sharpe.toFixed(3)}</p>
                        </div>
                        <div>
                          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Worst %-drop</p>
                          <p className="tabular text-sm font-medium">
                            {(v.max_pct_drop * 100).toFixed(2)}%
                          </p>
                        </div>
                      </div>
                      <details className="text-xs">
                        <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                          {v.perturbations.length} perturbations
                        </summary>
                        <table className="w-full mt-2 tabular">
                          <thead>
                            <tr className="text-[10px] uppercase tracking-wider text-muted-foreground">
                              <th className="text-left font-medium pb-1">Param</th>
                              <th className="text-right font-medium pb-1">Value</th>
                              <th className="text-right font-medium pb-1">Sharpe</th>
                              <th className="text-right font-medium pb-1">Δ%</th>
                            </tr>
                          </thead>
                          <tbody>
                            {v.perturbations.map((p, i) => (
                              <tr key={i} className="border-t border-border/40">
                                <td className="py-1">{p.param}</td>
                                <td className="text-right py-1">{p.value}</td>
                                <td className="text-right py-1">{p.sharpe.toFixed(4)}</td>
                                <td
                                  className={
                                    "text-right py-1 " +
                                    (p.delta_pct < 0 ? "text-red-700" : "text-emerald-700")
                                  }
                                >
                                  {(p.delta_pct * 100).toFixed(2)}%
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </details>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="costs" className="space-y-5">
          <Card>
            <CardContent className="py-4 space-y-3">
              <h2 className="font-heading text-base">Cost decomposition (per leg)</h2>
              <p className="text-sm text-muted-foreground">
                The strategy averages <span className="tabular font-medium text-foreground">
                  {strategy.metrics.cost_bps_mean?.toFixed(1) ?? "—"} bps
                </span>{" "}
                per leg on MPlus retail brackets at RM 350k. Below: the fee mix that produces that number.
              </p>
              {strategy.metrics.cost_bps_mean ? (
                <CostWaterfall costBps={strategy.metrics.cost_bps_mean} />
              ) : null}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="trades" className="space-y-5">
          {trades ? (
            <TradeExplorer trades={trades} />
          ) : (
            <Card>
              <CardContent className="py-8 text-center text-muted-foreground">
                No trade-level audit log available for this strategy.
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="risks" className="space-y-5">
          <RiskCards />
          <div className="space-y-3">
            <h2 className="font-heading text-base">Kill triggers</h2>
            <p className="text-sm text-muted-foreground">
              If we deploy live capital, these are the bright lines that stop trading and force a diagnostic.
            </p>
            <KillTriggers triggers={strategy.kill_triggers} />
          </div>
        </TabsContent>
      </Tabs>

      <footer className="border-t border-border pt-6 text-sm text-muted-foreground flex flex-wrap gap-4 justify-between">
        <p>
          Compare with{" "}
          <Link
            href={`/compare/${strategy.slug}/${strategy.slug === "rotation_rank_1" ? "clenow_som_rank_9" : "rotation_rank_1"}/`}
            className="underline text-[var(--color-brand-gold-700)] hover:text-[var(--color-brand-gold)]"
          >
            the other finalist →
          </Link>
        </p>
        <p className="text-xs">
          params_hash {strategy.params_hash} · variant rank #{strategy.rank}
        </p>
      </footer>
    </div>
  );
}
