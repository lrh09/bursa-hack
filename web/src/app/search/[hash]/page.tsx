import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";

import {
  getAllVariantHashes,
  getFolds,
  getVariantDetail,
} from "@/lib/data";
import { ScorecardTable } from "@/components/scorecard/scorecard-table";
import { ParamsTable } from "@/components/strategy/params-table";
import { FoldTable } from "@/components/strategy/fold-table";
import { FoldTimeline } from "@/components/charts/fold-timeline";
import { TierPill } from "@/components/scorecard/tier-pill";
import { Numeric } from "@/components/common/numeric";
import { PctChange } from "@/components/common/pct-change";
import { Card, CardContent } from "@/components/ui/card";
import { fmtSharpe, fmtRatio, strategyFamilyLabel } from "@/lib/format";

export async function generateStaticParams() {
  const hashes = await getAllVariantHashes();
  return hashes.map((hash) => ({ hash }));
}

interface PageProps {
  params: Promise<{ hash: string }>;
}

export default async function VariantPage({ params }: PageProps) {
  const { hash } = await params;
  const detail = await getVariantDetail(hash);
  if (!detail) notFound();

  const allFolds = await getFolds(detail.summary.strategy);
  const folds = allFolds.filter((f) => f.params_hash === hash);

  const s = detail.summary;
  return (
    <div className="space-y-6 fade-rise">
      <Link
        href="/search/"
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3" aria-hidden /> Back to search
      </Link>

      <header className="space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-brand-gold-700)]">
              {strategyFamilyLabel(s.strategy)}
              {s.rank ? ` · Rank #${s.rank}` : ""}
            </p>
            <h1 className="font-heading text-2xl sm:text-3xl mt-1 break-all sm:break-normal">
              <span className="font-mono">{hash}</span>
            </h1>
          </div>
          {s.tier ? <TierPill tier={s.tier} size="md" /> : null}
        </div>

        <dl className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3 sm:gap-4 border-y border-border py-4">
          <Tile label="WF Sharpe" value={fmtSharpe(s.sharpe_mean)} />
          <Tile label="OOS Sharpe" value={s.oos_sharpe !== null ? fmtSharpe(s.oos_sharpe) : "—"} />
          <Tile label="CAGR" value={<PctChange value={s.cagr_mean} digits={1} withIcon={false} />} />
          <Tile label="Max DD" value={<PctChange value={s.max_dd_mean} digits={1} withIcon={false} />} />
          <Tile label="DSR" value={fmtRatio(s.dsr)} />
          <Tile label="Cost / leg" value={`${s.cost_bps_mean.toFixed(1)} bps`} />
        </dl>
      </header>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        {folds.length > 0 ? (
          <Card>
            <CardContent className="py-3 space-y-3">
              <h2 className="font-heading text-base">Walk-forward folds</h2>
              <FoldTimeline folds={folds} />
            </CardContent>
          </Card>
        ) : null}
        <Card>
          <CardContent className="py-3 space-y-3">
            <h2 className="font-heading text-base">Parameters</h2>
            <ParamsTable params={s.params} />
          </CardContent>
        </Card>
      </div>

      {detail.gates.length > 0 ? (
        <Card>
          <CardContent className="py-3 space-y-3">
            <h2 className="font-heading text-base">12-gate scorecard</h2>
            <ScorecardTable gates={detail.gates} />
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="py-6 text-center text-sm text-muted-foreground">
            This variant did not enter the top-K scorecard pass. Its per-fold metrics are shown above.
          </CardContent>
        </Card>
      )}

      {folds.length > 0 ? (
        <Card>
          <CardContent className="py-3 space-y-3">
            <h2 className="font-heading text-base">Per-fold detail</h2>
            <FoldTable folds={folds} />
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function Tile({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</dt>
      <dd className="font-heading text-xl tabular text-foreground">
        {typeof value === "string" ? <Numeric>{value}</Numeric> : value}
      </dd>
    </div>
  );
}
