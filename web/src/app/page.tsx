import {
  getAllStrategies,
  getEquity,
  getFinalScorecards,
  getManifest,
} from "@/lib/data";
import { StrategyCard } from "@/components/strategy/strategy-card";
import { ProgrammeStats } from "@/components/strategy/programme-stats";
import { LeaderboardTeaser } from "@/components/strategy/leaderboard-teaser";
import { HowToRead } from "@/components/landing/how-to-read";
import { ReportsTeaser } from "@/components/landing/reports-teaser";
import { SITE } from "@/lib/site";

export default async function Home() {
  const [manifest, strategies, scorecards] = await Promise.all([
    getManifest(),
    getAllStrategies(),
    getFinalScorecards(),
  ]);

  const rotation = strategies.find((s) => s.slug === SITE.defaultStrategy)!;
  const clenow = strategies.find((s) => s.slug === SITE.alternateStrategy)!;
  const [eqRotation, eqClenow] = await Promise.all([
    getEquity(rotation.slug, SITE.defaultCapital),
    getEquity(clenow.slug, SITE.defaultCapital),
  ]);

  const yearStart = manifest.iron.data_panel[0].slice(0, 4);
  const yearEnd = manifest.iron.data_panel[1].slice(0, 4);

  return (
    <div className="space-y-10 fade-rise">
      <section className="space-y-5 sm:space-y-6">
        <div className="space-y-3 max-w-3xl">
          <p className="text-[11px] sm:text-xs font-semibold uppercase tracking-[0.22em] text-[var(--color-brand-gold-700)]">
            Research findings · {yearStart}-{yearEnd}
          </p>
          <h1 className="font-heading text-3xl sm:text-5xl leading-[1.05]">
            Bursa Malaysia momentum &amp; trend-following.
            <span className="block text-muted-foreground text-2xl sm:text-3xl mt-2">
              {manifest.variants_count} variants searched. Two finalists. Neither cleared for live capital — yet.
            </span>
          </h1>
        </div>

        <div className="grid gap-4 sm:gap-5 lg:grid-cols-2">
          <StrategyCard
            strategy={rotation}
            equity={eqRotation}
            href={`/strategies/${rotation.slug}/`}
            emphasis="primary"
          />
          <StrategyCard
            strategy={clenow}
            equity={eqClenow}
            href={`/strategies/${clenow.slug}/`}
            emphasis="primary"
          />
        </div>

        <div className="text-sm text-muted-foreground">
          <a
            href={`/compare/${rotation.slug}/${clenow.slug}/`}
            className="inline-flex items-center gap-1 text-[var(--color-brand-gold-700)] hover:text-[var(--color-brand-gold)] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
          >
            → See both side-by-side with overlaid equity curves
          </a>
        </div>
      </section>

      <ProgrammeStats manifest={manifest} />

      <HowToRead />

      <LeaderboardTeaser rows={scorecards as never} />

      <ReportsTeaser />
    </div>
  );
}
