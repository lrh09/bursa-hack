"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowDown, ArrowUp, ArrowUpDown, Filter } from "lucide-react";

import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { TierPill } from "@/components/scorecard/tier-pill";
import { PctChange } from "@/components/common/pct-change";
import { Numeric } from "@/components/common/numeric";
import { cn } from "@/lib/utils";
import { fmtSharpe, fmtRatio, strategyFamilyLabel } from "@/lib/format";
import type { VariantSummary } from "@/lib/types";

type SortKey = "sharpe_mean" | "cagr_mean" | "max_dd_mean" | "dsr" | "oos_sharpe" | "cost_bps_mean";
type Direction = "asc" | "desc";

const COLS: Array<{ key: SortKey; label: string }> = [
  { key: "sharpe_mean", label: "WF Sharpe" },
  { key: "oos_sharpe", label: "OOS Sharpe" },
  { key: "cagr_mean", label: "CAGR (WF)" },
  { key: "max_dd_mean", label: "Max DD" },
  { key: "dsr", label: "DSR" },
  { key: "cost_bps_mean", label: "Cost bps" },
];

interface Props {
  variants: VariantSummary[];
  families: string[];
  hashToSid: Record<string, string>;
}

export function VariantExplorer({ variants, families, hashToSid }: Props) {
  const [query, setQuery] = React.useState("");
  const [activeFamilies, setActiveFamilies] = React.useState<Set<string>>(new Set());
  const [onlyTopK, setOnlyTopK] = React.useState(false);
  const [sort, setSort] = React.useState<{ key: SortKey; dir: Direction }>({
    key: "sharpe_mean",
    dir: "desc",
  });

  const toggleFamily = (f: string) =>
    setActiveFamilies((s) => {
      const n = new Set(s);
      if (n.has(f)) n.delete(f);
      else n.add(f);
      return n;
    });

  const filtered = React.useMemo(() => {
    let rows = variants;
    if (activeFamilies.size > 0) rows = rows.filter((v) => activeFamilies.has(v.strategy));
    if (onlyTopK) rows = rows.filter((v) => v.in_top_k);
    if (query.trim()) {
      const q = query.toLowerCase();
      rows = rows.filter(
        (v) => v.params_hash.startsWith(q) || v.strategy.includes(q),
      );
    }
    return [...rows].sort((a, b) => {
      const av = (a as unknown as Record<string, unknown>)[sort.key];
      const bv = (b as unknown as Record<string, unknown>)[sort.key];
      const aa = av === null || av === undefined ? -Infinity : Number(av);
      const bb = bv === null || bv === undefined ? -Infinity : Number(bv);
      const cmp = aa > bb ? 1 : aa < bb ? -1 : 0;
      return sort.dir === "asc" ? cmp : -cmp;
    });
  }, [variants, activeFamilies, onlyTopK, query, sort]);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2 flex-wrap">
          <Filter className="size-3.5 text-muted-foreground" aria-hidden />
          {families.map((f) => (
            <button
              type="button"
              key={f}
              onClick={() => toggleFamily(f)}
              className={cn(
                "rounded-full px-2.5 py-1 text-xs border transition-colors tabular",
                activeFamilies.has(f)
                  ? "bg-[var(--color-brand-navy)] text-white border-[var(--color-brand-navy)]"
                  : "bg-card text-foreground border-border hover:bg-muted",
              )}
              aria-pressed={activeFamilies.has(f)}
            >
              {strategyFamilyLabel(f)}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setOnlyTopK((v) => !v)}
            className={cn(
              "rounded-full px-2.5 py-1 text-xs border transition-colors",
              onlyTopK
                ? "bg-[var(--color-brand-gold)] text-[var(--color-brand-navy)] border-[var(--color-brand-gold)] font-medium"
                : "bg-card text-muted-foreground border-border hover:text-foreground",
            )}
            aria-pressed={onlyTopK}
          >
            Top-10 only
          </button>
        </div>
        <Input
          type="search"
          placeholder="Search by hash prefix or family..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="max-w-xs"
        />
      </div>

      <p className="text-xs text-muted-foreground tabular">
        Showing {filtered.length} of {variants.length} variants
      </p>

      <div className="rounded-md border border-border overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Variant</TableHead>
              <TableHead>Family</TableHead>
              {COLS.map((c) => (
                <TableHead key={c.key} className="text-right">
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 hover:text-foreground"
                    onClick={() =>
                      setSort((s) => ({
                        key: c.key,
                        dir: s.key === c.key && s.dir === "desc" ? "asc" : "desc",
                      }))
                    }
                  >
                    {c.label}
                    {sort.key === c.key ? (
                      sort.dir === "asc" ? (
                        <ArrowUp className="size-3" aria-hidden />
                      ) : (
                        <ArrowDown className="size-3" aria-hidden />
                      )
                    ) : (
                      <ArrowUpDown className="size-3 opacity-30" aria-hidden />
                    )}
                  </button>
                </TableHead>
              ))}
              <TableHead className="text-right">Tier</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((v) => {
              const targetHref = hashToSid[v.params_hash]
                ? `/strategies/${hashToSid[v.params_hash]}/?v=${v.params_hash}`
                : `/search/${v.params_hash}/`;
              return (
              <TableRow key={v.params_hash}>
                <TableCell>
                  <Link
                    href={targetHref}
                    className="font-mono text-xs text-foreground hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
                  >
                    {v.params_hash.slice(0, 10)}
                  </Link>
                  {v.in_top_k && (
                    <Badge variant="secondary" className="ml-2 text-[9px] uppercase tracking-wider">
                      Top {v.rank}
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="text-xs">{strategyFamilyLabel(v.strategy)}</TableCell>
                <TableCell className="text-right">
                  <Numeric>{fmtSharpe(v.sharpe_mean)}</Numeric>
                </TableCell>
                <TableCell className="text-right">
                  <Numeric>{v.oos_sharpe !== null ? fmtSharpe(v.oos_sharpe) : "—"}</Numeric>
                </TableCell>
                <TableCell className="text-right">
                  <PctChange value={v.cagr_mean} digits={1} withIcon={false} />
                </TableCell>
                <TableCell className="text-right">
                  <PctChange value={v.max_dd_mean} digits={1} withIcon={false} />
                </TableCell>
                <TableCell className="text-right">
                  <Numeric>{fmtRatio(v.dsr)}</Numeric>
                </TableCell>
                <TableCell className="text-right text-xs">
                  <Numeric>{v.cost_bps_mean.toFixed(1)} bps</Numeric>
                </TableCell>
                <TableCell className="text-right">
                  {v.tier ? <TierPill tier={v.tier} size="sm" /> : <span className="text-muted-foreground text-xs">—</span>}
                </TableCell>
              </TableRow>
              );
            })}
            {!filtered.length && (
              <TableRow>
                <TableCell colSpan={COLS.length + 3} className="text-center text-muted-foreground py-8">
                  No variants match these filters.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
