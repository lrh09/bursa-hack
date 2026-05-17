"use client";

import * as React from "react";
import { ArrowUpDown, ArrowDown, ArrowUp } from "lucide-react";

import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmtBps, fmtInt, fmtRM } from "@/lib/format";
import type { Trade } from "@/lib/types";

type SortKey = "date" | "sec_id" | "qty" | "raw_px" | "notional_raw" | "fees" | "cost_bps";
type Direction = "asc" | "desc";

const COLS: Array<{ key: SortKey; label: string; align?: "right" }> = [
  { key: "date", label: "Date" },
  { key: "sec_id", label: "Security" },
  { key: "qty", label: "Qty", align: "right" },
  { key: "raw_px", label: "Price", align: "right" },
  { key: "notional_raw", label: "Notional", align: "right" },
  { key: "fees", label: "Fees", align: "right" },
  { key: "cost_bps", label: "Cost bps", align: "right" },
];

export function TradeExplorer({ trades }: { trades: Trade[] }) {
  const [query, setQuery] = React.useState("");
  const [sort, setSort] = React.useState<{ key: SortKey; dir: Direction }>({ key: "date", dir: "asc" });
  const [page, setPage] = React.useState(0);
  const PAGE = 50;

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    let rows = q ? trades.filter((t) => t.sec_id.toLowerCase().includes(q) || t.date.includes(q)) : trades;
    rows = [...rows].sort((a, b) => {
      const va = a[sort.key];
      const vb = b[sort.key];
      // dates and security id are strings; everything else numeric
      const isNum = sort.key !== "date" && sort.key !== "sec_id";
      const aa = isNum ? Number(va) : va;
      const bb = isNum ? Number(vb) : vb;
      const cmp = aa > bb ? 1 : aa < bb ? -1 : 0;
      return sort.dir === "asc" ? cmp : -cmp;
    });
    return rows;
  }, [trades, query, sort]);

  React.useEffect(() => setPage(0), [query, sort]);

  const pageRows = filtered.slice(page * PAGE, (page + 1) * PAGE);
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE));

  return (
    <section className="space-y-3">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground tabular">
          {fmtInt(filtered.length)} of {fmtInt(trades.length)} trades
          {query ? ` matching "${query}"` : ""}
        </p>
        <Input
          type="search"
          placeholder="Filter by ticker or date YYYY-MM..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="max-w-xs"
          aria-label="Filter trades"
        />
      </div>
      <div className="rounded-md border border-border overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              {COLS.map((c) => (
                <TableHead key={c.key} className={c.align === "right" ? "text-right" : undefined}>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 hover:text-foreground"
                    onClick={() =>
                      setSort((s) => ({
                        key: c.key,
                        dir: s.key === c.key && s.dir === "asc" ? "desc" : "asc",
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
            </TableRow>
          </TableHeader>
          <TableBody>
            {pageRows.map((t, i) => {
              const notional = Number(t.notional_raw);
              const fees = Number(t.fees);
              const bps = Number(t.cost_bps);
              return (
                <TableRow key={`${t.date}-${t.sec_id}-${i}`}>
                  <TableCell className="tabular text-xs">{t.date}</TableCell>
                  <TableCell className="font-mono text-xs">{t.sec_id}</TableCell>
                  <TableCell className="text-right tabular text-xs">{fmtInt(Number(t.qty))}</TableCell>
                  <TableCell className="text-right tabular text-xs">{Number(t.raw_px).toFixed(2)}</TableCell>
                  <TableCell className="text-right tabular text-xs">{fmtRM(notional)}</TableCell>
                  <TableCell className="text-right tabular text-xs">{fmtRM(fees)}</TableCell>
                  <TableCell className="text-right tabular text-xs">{fmtBps(bps)}</TableCell>
                </TableRow>
              );
            })}
            {!pageRows.length && (
              <TableRow>
                <TableCell colSpan={COLS.length} className="text-center text-muted-foreground py-6">
                  No trades match this filter.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-end gap-2 text-xs">
          <button
            type="button"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            className="px-2 py-1 rounded border border-border text-xs hover:bg-muted disabled:opacity-40"
          >
            ←
          </button>
          <span className="tabular text-muted-foreground">
            page {page + 1} of {pages}
          </span>
          <button
            type="button"
            disabled={page + 1 >= pages}
            onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}
            className="px-2 py-1 rounded border border-border text-xs hover:bg-muted disabled:opacity-40"
          >
            →
          </button>
        </div>
      )}
    </section>
  );
}
