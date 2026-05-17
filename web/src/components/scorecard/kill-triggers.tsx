import { Siren } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import type { KillTrigger } from "@/lib/types";

interface Props {
  triggers: KillTrigger[];
}

export function KillTriggers({ triggers }: Props) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {triggers.map((t) => (
        <Card key={t.id} className="border-[color:color-mix(in_srgb,var(--color-brand-amber)_25%,transparent)]">
          <CardContent className="py-4">
            <div className="flex items-start gap-3">
              <div className="rounded-md p-1.5 bg-[color:color-mix(in_srgb,var(--color-brand-amber)_12%,transparent)] text-[var(--color-brand-amber)]">
                <Siren className="size-4" aria-hidden />
              </div>
              <div className="min-w-0">
                <p className="text-[11px] uppercase tracking-wider text-[var(--color-brand-amber)] font-semibold">
                  {t.id} — {t.name}
                </p>
                <p className="text-sm text-foreground mt-1">{t.trigger}</p>
                <p className="text-xs text-muted-foreground mt-2">
                  <span className="font-medium text-foreground">Response:</span> {t.action}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
