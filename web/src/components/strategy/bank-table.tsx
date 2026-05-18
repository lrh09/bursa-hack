"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import type { ManifestStrategy } from "@/lib/types";

type SortKey =
  | "best_oos_sharpe"
  | "variant_count"
  | "best_tier"
  | "headline_gates_passed";
type Dir = "asc" | "desc";

const COLS: Array<{
  key: SortKey | "display_name";
  label: string;
  align?: "right";
}> = [
  { key: "display_name", label: "Strategy" },
  { key: "variant_count", label: "Variants", align: "right" },
  { key: "best_oos_sharpe", label: "Best OOS Sharpe", align: "right" },
  { key: "best_tier", label: "Best tier", align: "right" },
  { key: "headline_gates_passed", label: "Gates", align: "right" },
];

const TIER_RANK: Record<string, number> = {
  A: 0,
  B: 1,
  C: 2,
  D: 3,
  E: 4,
  F: 5,
};

export function BankTable({ entries }: { entries: ManifestStrategy[] }) {
  const [sort, setSort] = React.useState<{ key: SortKey; dir: Dir }>({
    key: "best_oos_sharpe",
    dir: "desc",
  });
  const [familyFilter, setFamilyFilter] = React.useState<string | null>(null);

  const families = Array.from(new Set(entries.map((e) => e.family))).sort();
  const filtered = familyFilter
    ? entries.filter((e) => e.family === familyFilter)
    : entries;

  const sorted = React.useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      const k = sort.key;
      let av = 0;
      let bv = 0;
      if (k === "best_tier") {
        av = TIER_RANK[a.best_tier ?? "F"] ?? 99;
        bv = TIER_RANK[b.best_tier ?? "F"] ?? 99;
      } else {
        av = (a[k] as number | null) ?? -Infinity;
        bv = (b[k] as number | null) ?? -Infinity;
      }
      const cmp = av > bv ? 1 : av < bv ? -1 : 0;
      return sort.dir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [filtered, sort]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted-foreground">Family:</span>
        <button
          type="button"
          onClick={() => setFamilyFilter(null)}
          className={`px-2 py-0.5 rounded border ${
            familyFilter === null
              ? "border-foreground bg-accent"
              : "border-border hover:bg-muted"
          }`}
        >
          All
        </button>
        {families.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFamilyFilter(f)}
            className={`px-2 py-0.5 rounded border ${
              familyFilter === f
                ? "border-foreground bg-accent"
                : "border-border hover:bg-muted"
            }`}
          >
            {f}
          </button>
        ))}
      </div>
      <div className="rounded-md border border-border overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              {COLS.map((c) => (
                <TableHead
                  key={c.key}
                  className={c.align === "right" ? "text-right" : undefined}
                >
                  {c.key === "display_name" ? (
                    c.label
                  ) : (
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 hover:text-foreground"
                      onClick={() =>
                        setSort((s) => ({
                          key: c.key as SortKey,
                          dir:
                            s.key === c.key && s.dir === "desc" ? "asc" : "desc",
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
                  )}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((e) => (
              <TableRow key={e.strategy_id ?? e.slug}>
                <TableCell>
                  <Link
                    href={`/strategies/${e.strategy_id ?? e.slug}/`}
                    className="hover:underline font-medium"
                  >
                    {e.display_name ?? e.label}
                  </Link>
                  <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    {e.family}
                  </div>
                </TableCell>
                <TableCell className="text-right tabular">
                  {e.variant_count ?? "—"}
                </TableCell>
                <TableCell className="text-right tabular">
                  {e.best_oos_sharpe != null
                    ? e.best_oos_sharpe.toFixed(2)
                    : "—"}
                </TableCell>
                <TableCell className="text-right">
                  <Badge variant="outline">{e.best_tier ?? "—"}</Badge>
                </TableCell>
                <TableCell className="text-right tabular">
                  {e.headline_gates_passed ?? "—"} /{" "}
                  {e.headline_gates_evaluated ?? "—"}
                </TableCell>
              </TableRow>
            ))}
            {!sorted.length && (
              <TableRow>
                <TableCell
                  colSpan={COLS.length}
                  className="text-center py-6 text-muted-foreground"
                >
                  No strategies match.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
