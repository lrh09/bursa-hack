import Link from "next/link";
import { ArrowRight } from "lucide-react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { TierPill } from "@/components/scorecard/tier-pill";
import { Numeric } from "@/components/common/numeric";
import { PctChange } from "@/components/common/pct-change";
import { fmtSharpe, strategyFamilyLabel } from "@/lib/format";

interface ScorecardRow {
  rank: string;
  strategy: string;
  params_hash: string;
  tier: string;
  rec: string;
  wf_sharpe: string;
  oos_sharpe: string;
  cagr_oos: string;
  max_dd: string;
  pbo: string;
  dsr_eff: string;
}

interface Props {
  rows: ScorecardRow[];
}

export function LeaderboardTeaser({ rows }: Props) {
  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="font-heading text-lg sm:text-xl">
          Top variants from the brute-force search
        </h2>
        <Link
          href="/search/"
          className="inline-flex items-center gap-1 text-xs sm:text-sm text-[var(--color-brand-gold-700)] hover:text-[var(--color-brand-gold)] focus-visible:ring-2 focus-visible:ring-ring rounded-sm focus:outline-none"
        >
          View all <ArrowRight className="size-3.5" aria-hidden />
        </Link>
      </div>
      <div className="rounded-md border border-border overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-12 text-right">#</TableHead>
              <TableHead>Strategy</TableHead>
              <TableHead className="text-right">WF Sharpe</TableHead>
              <TableHead className="text-right">OOS Sharpe</TableHead>
              <TableHead className="text-right">CAGR OOS</TableHead>
              <TableHead className="text-right">Max DD</TableHead>
              <TableHead className="text-right">Tier</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.slice(0, 10).map((r) => (
              <TableRow key={r.params_hash}>
                <TableCell className="text-right tabular text-muted-foreground text-xs">
                  {r.rank}
                </TableCell>
                <TableCell className="font-medium">
                  <Link
                    href={`/search/${r.params_hash}/`}
                    className="hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
                  >
                    {strategyFamilyLabel(r.strategy)}
                  </Link>
                  <span className="ml-2 text-[10px] text-muted-foreground font-mono">
                    {r.params_hash.slice(0, 8)}
                  </span>
                </TableCell>
                <TableCell className="text-right">
                  <Numeric>{fmtSharpe(Number(r.wf_sharpe))}</Numeric>
                </TableCell>
                <TableCell className="text-right">
                  <Numeric>{fmtSharpe(Number(r.oos_sharpe))}</Numeric>
                </TableCell>
                <TableCell className="text-right">
                  <PctChange value={Number(r.cagr_oos)} digits={1} withIcon={false} />
                </TableCell>
                <TableCell className="text-right">
                  <PctChange value={Number(r.max_dd)} digits={1} withIcon={false} />
                </TableCell>
                <TableCell className="text-right">
                  <TierPill tier={r.tier} size="sm" />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}
