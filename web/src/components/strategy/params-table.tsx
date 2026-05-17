import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface Props {
  params: Record<string, unknown>;
}

function fmt(v: unknown): string {
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") {
    if (Number.isInteger(v)) return v.toString();
    return v.toFixed(2);
  }
  if (v === null || v === undefined) return "—";
  return String(v);
}

export function ParamsTable({ params }: Props) {
  const entries = Object.entries(params);
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Parameter</TableHead>
          <TableHead className="text-right">Value</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {entries.map(([k, v]) => (
          <TableRow key={k}>
            <TableCell className="font-mono text-xs text-muted-foreground">{k}</TableCell>
            <TableCell className="text-right tabular text-foreground">{fmt(v)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
