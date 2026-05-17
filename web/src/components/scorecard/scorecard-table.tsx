import { Info } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { GateBadge } from "./gate-badge";
import type { Gate } from "@/lib/types";

interface Props {
  gates: Gate[];
}

function formatValue(g: Gate): string {
  if (g.value === null) return "—";
  // Heuristic formatting per gate index
  if (g.n === 3 || g.n === 2 || g.n === 1 || g.n === 5) return g.value.toFixed(2);
  if (g.n === 4) return `${(g.value * 100).toFixed(1)}%`;
  if (g.n === 6 || g.n === 8 || g.n === 9 || g.n === 10) return `${(g.value * 100).toFixed(2)}%`;
  if (g.n === 7) return `${g.value.toFixed(2)}×`;
  return String(g.value);
}

export function ScorecardTable({ gates }: Props) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-12 text-right">#</TableHead>
          <TableHead>Gate</TableHead>
          <TableHead className="text-right">Value</TableHead>
          <TableHead className="text-right">Threshold</TableHead>
          <TableHead className="text-right">Status</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {gates.map((g) => (
          <TableRow key={g.n}>
            <TableCell className="text-right tabular text-muted-foreground text-xs">
              {g.n}
            </TableCell>
            <TableCell>
              <div className="flex items-center gap-2">
                <span className="font-medium text-foreground">{g.name}</span>
                {g.blurb ? (
                  <Tooltip>
                    <TooltipTrigger
                      className="text-muted-foreground hover:text-foreground"
                      aria-label={`What is ${g.name}?`}
                    >
                      <Info className="size-3.5" />
                    </TooltipTrigger>
                    <TooltipContent className="max-w-xs text-xs leading-relaxed">
                      <p className="font-semibold mb-1">{g.fullname}</p>
                      <p>{g.blurb}</p>
                    </TooltipContent>
                  </Tooltip>
                ) : null}
              </div>
            </TableCell>
            <TableCell className="text-right tabular text-foreground">
              {formatValue(g)}
            </TableCell>
            <TableCell className="text-right tabular text-muted-foreground text-xs">
              {g.threshold_text ?? "—"}
            </TableCell>
            <TableCell className="text-right">
              <GateBadge status={g.status} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
