"use client";

import * as React from "react";
import { useRouter, usePathname } from "next/navigation";
import { ArrowUpDown, ArrowUp, ArrowDown } from "lucide-react";

import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { nonDominated } from "./dominance";
import type { StrategyBundleV2 } from "@/lib/types";

type NumKey =
  | "wf_sharpe" | "oos_sharpe" | "cov" | "max_dd"
  | "cagr_oos" | "slip_drag" | "order_mult" | "n_pass";

const COLS: Array<{ key: NumKey | "params" | "tier"; label: string; align?: "right" }> = [
  { key: "params", label: "Params" },
  { key: "wf_sharpe",  label: "WF Sharpe",  align: "right" },
  { key: "oos_sharpe", label: "OOS Sharpe", align: "right" },
  { key: "cov",        label: "fold CoV",   align: "right" },
  { key: "max_dd",     label: "Max DD",     align: "right" },
  { key: "cagr_oos",   label: "CAGR OOS",   align: "right" },
  { key: "slip_drag",  label: "Drag",       align: "right" },
  { key: "order_mult", label: "Order x",    align: "right" },
  { key: "n_pass",     label: "Gates ✓",   align: "right" },
  { key: "tier",       label: "Tier",       align: "right" },
];

const TIER_RANK: Record<string, number> = { A: 0, B: 1, C: 2, D: 3, E: 4, F: 5 };

export function VariantExplorer({
  bundle,
  selectedHash,
}: {
  bundle: StrategyBundleV2;
  selectedHash: string | null;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [sort, setSort] = React.useState<{ key: NumKey | "tier"; dir: "asc" | "desc" }>({
    key: "wf_sharpe", dir: "desc",
  });
  const [hideDominated, setHideDominated] = React.useState(false);

  const rows = React.useMemo(() => {
    const base = hideDominated ? nonDominated(bundle.variants_inline) : bundle.variants_inline;
    const arr = [...base];
    arr.sort((a, b) => {
      let av = 0, bv = 0;
      if (sort.key === "tier") {
        av = TIER_RANK[a.tier ?? "F"] ?? 99;
        bv = TIER_RANK[b.tier ?? "F"] ?? 99;
      } else {
        av = (a[sort.key] as number | null) ?? -Infinity;
        bv = (b[sort.key] as number | null) ?? -Infinity;
      }
      const cmp = av > bv ? 1 : av < bv ? -1 : 0;
      return sort.dir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [bundle, sort, hideDominated]);

  const openVariant = (hash: string) => {
    router.push(`${pathname}?v=${hash}`);
  };

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-heading text-lg">Variants ({bundle.variant_count})</h2>
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={hideDominated}
            onChange={(e) => setHideDominated(e.target.checked)}
          />
          Hide dominated (OOS Sharpe / Max DD / fold CoV)
        </label>
      </div>
      <div className="rounded-md border border-border overflow-x-auto">
        <Table className="min-w-[820px]">
          <TableHeader>
            <TableRow>
              {COLS.map((c) => (
                <TableHead key={c.key} className={c.align === "right" ? "text-right" : undefined}>
                  {c.key === "params" ? c.label : (
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 hover:text-foreground"
                      onClick={() => setSort((s) => ({
                        key: c.key as NumKey | "tier",
                        dir: s.key === c.key && s.dir === "desc" ? "asc" : "desc",
                      }))}
                    >
                      {c.label}
                      {sort.key === c.key
                        ? (sort.dir === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)
                        : <ArrowUpDown className="size-3 opacity-30" />}
                    </button>
                  )}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((v) => (
              <TableRow
                key={v.params_hash}
                className={`cursor-pointer ${v.params_hash === selectedHash ? "bg-accent" : "hover:bg-muted"} ${v.headline ? "border-l-4 border-l-[var(--color-brand-gold)]" : ""}`}
                onClick={() => openVariant(v.params_hash)}
              >
                <TableCell className="font-mono text-[10px]">
                  {Object.entries(v.params).slice(0, 4).map(([k, val]) => (
                    <span key={k} className="mr-2">{k}={String(val)}</span>
                  ))}
                </TableCell>
                <TableCell className="text-right tabular text-xs">{fmt(v.wf_sharpe)}</TableCell>
                <TableCell className="text-right tabular text-xs">{fmt(v.oos_sharpe)}</TableCell>
                <TableCell className="text-right tabular text-xs">{fmt(v.cov)}</TableCell>
                <TableCell className="text-right tabular text-xs">{fmtPct(v.max_dd)}</TableCell>
                <TableCell className="text-right tabular text-xs">{fmtPct(v.cagr_oos)}</TableCell>
                <TableCell className="text-right tabular text-xs">{fmtPct(v.slip_drag)}</TableCell>
                <TableCell className="text-right tabular text-xs">{fmt(v.order_mult)}</TableCell>
                <TableCell className="text-right tabular text-xs">
                  {v.n_pass ?? "—"} / {v.n_eval ?? "—"}
                </TableCell>
                <TableCell className="text-right">
                  <Badge variant="outline">{v.tier ?? "—"}</Badge>
                </TableCell>
              </TableRow>
            ))}
            {!rows.length && (
              <TableRow>
                <TableCell colSpan={COLS.length} className="text-center py-6 text-muted-foreground">
                  No variants after filter.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

function fmt(v: number | null | undefined) {
  return v == null ? "—" : v.toFixed(2);
}
function fmtPct(v: number | null | undefined) {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}
