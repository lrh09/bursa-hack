import Link from "next/link";
import { notFound } from "next/navigation";

import {
  getEquity,
  getFolds,
  getManifest,
  getStrategy,
  getStrategyAliases,
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
import { ScorecardTable } from "@/components/scorecard/scorecard-table";
import { DiagnosticsList } from "@/components/scorecard/diagnostics-list";
import { KillTriggers } from "@/components/scorecard/kill-triggers";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent } from "@/components/ui/card";

export async function generateStaticParams() {
  // Manifest entries dropped the legacy `slug` field once the bank rewrite
  // landed (T6). The legacy rank-N slugs are the keys of strategy_aliases.json
  // and have on-disk strategy JSONs — include them here so SSG covers them
  // until T8 refactors this route to consume strategy_id directly.
  const manifest = await getManifest();
  const manifestSlugs = manifest.strategies
    .map((s) => s.slug)
    .filter((slug): slug is string => typeof slug === "string");
  const aliasSlugs = Object.keys(await getStrategyAliases());
  const all = Array.from(new Set([...manifestSlugs, ...aliasSlugs]));
  return all.map((slug) => ({ slug }));
}

interface PageProps {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}

export default async function StrategyPage({ params, searchParams }: PageProps) {
  const { slug } = await params;
  const sp = await searchParams;

  let strategy;
  try {
    strategy = await getStrategy(slug);
  } catch {
    notFound();
  }

  const requestedCap = sp.capital;
  const capital = requestedCap && strategy.equity_capitals.includes(requestedCap)
    ? requestedCap
    : strategy.equity_capitals.includes("350k")
      ? "350k"
      : strategy.equity_capitals[0] ?? "350k";

  const [equity, folds, trades] = await Promise.all([
    getEquity(slug, capital),
    getFolds(strategy.family),
    getTrades(slug),
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
            href={`/compare/${slug}/${slug === "rotation_rank_1" ? "clenow_som_rank_9" : "rotation_rank_1"}/`}
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
