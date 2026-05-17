import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmtSharpe } from "@/lib/format";
import { PctChange } from "@/components/common/pct-change";
import type { FoldRow } from "@/lib/types";

interface Props {
  folds: FoldRow[];
}

export function FoldTable({ folds }: Props) {
  const sorted = [...folds].sort((a, b) => a.fold_idx - b.fold_idx);
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-12 text-right">#</TableHead>
          <TableHead>Train</TableHead>
          <TableHead>Validate</TableHead>
          <TableHead className="text-right">Sharpe</TableHead>
          <TableHead className="text-right">CAGR</TableHead>
          <TableHead className="text-right">Max DD</TableHead>
          <TableHead className="text-right">Trades</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sorted.map((f) => (
          <TableRow key={f.fold_idx}>
            <TableCell className="text-right tabular text-xs text-muted-foreground">
              {f.fold_idx}
            </TableCell>
            <TableCell className="tabular text-xs">
              {f.train_start} → {f.validate_start}
            </TableCell>
            <TableCell className="tabular text-xs">
              {f.validate_start} → {f.validate_end}
            </TableCell>
            <TableCell className="text-right tabular">
              {fmtSharpe(f.sharpe)}
            </TableCell>
            <TableCell className="text-right tabular">
              <PctChange value={f.cagr} digits={1} withIcon={false} />
            </TableCell>
            <TableCell className="text-right tabular">
              <PctChange value={f.max_drawdown} digits={1} withIcon={false} />
            </TableCell>
            <TableCell className="text-right tabular text-xs">
              {f.n_trades ?? "—"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
