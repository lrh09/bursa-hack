import { GateBadge } from "./gate-badge";
import type { Diagnostic } from "@/lib/types";

interface Props {
  diagnostics: Diagnostic[];
}

export function DiagnosticsList({ diagnostics }: Props) {
  if (!diagnostics.length) return null;
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Supplementary diagnostics
      </h3>
      <ul className="divide-y divide-border border-y border-border">
        {diagnostics.map((d) => (
          <li key={d.name} className="flex items-baseline gap-3 py-2">
            <span className="flex-1 text-sm text-foreground">{d.name}</span>
            <span className="tabular text-xs text-muted-foreground">
              {d.value !== null ? d.value.toFixed(3) : "—"}
              {d.threshold !== null ? (
                <span className="ml-2 text-[10px] uppercase tracking-wider">
                  vs {d.threshold.toFixed(3)}
                </span>
              ) : null}
            </span>
            <GateBadge status={d.status} />
          </li>
        ))}
      </ul>
    </div>
  );
}
