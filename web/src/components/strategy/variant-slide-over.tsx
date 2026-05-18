"use client";

import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription,
} from "@/components/ui/sheet";
import type { VariantInline } from "@/lib/types";

interface Props {
  variant: VariantInline | null;
  onClose: () => void;
}

export function VariantSlideOver({ variant, onClose }: Props) {
  return (
    <Sheet open={variant != null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent side="right" className="w-full sm:max-w-md overflow-y-auto">
        {variant && (
          <>
            <SheetHeader>
              <SheetTitle className="font-heading text-xl">
                Variant <span className="font-mono text-base">{variant.params_hash.slice(0, 12)}</span>
              </SheetTitle>
              <SheetDescription>
                {variant.headline && (
                  <span className="inline-block mb-2 text-[10px] uppercase tracking-wider text-[var(--color-brand-gold-700)]">
                    Headline variant
                  </span>
                )}
              </SheetDescription>
            </SheetHeader>
            <div className="space-y-4 py-4 text-sm">
              <section>
                <h3 className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Params</h3>
                <table className="text-xs tabular w-full">
                  <tbody>
                    {Object.entries(variant.params).map(([k, v]) => (
                      <tr key={k} className="border-b border-border/40">
                        <td className="py-1 pr-2 text-muted-foreground">{k}</td>
                        <td className="py-1 text-right font-mono">{String(v)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
              <section>
                <h3 className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Scorecard</h3>
                <table className="text-xs tabular w-full">
                  <tbody>
                    <Row label="WF Sharpe" value={fmt(variant.wf_sharpe)} />
                    <Row label="OOS Sharpe" value={fmt(variant.oos_sharpe)} />
                    <Row label="CAGR OOS" value={fmtPct(variant.cagr_oos)} />
                    <Row label="Max DD" value={fmtPct(variant.max_dd)} />
                    <Row label="fold CoV" value={fmt(variant.cov)} />
                    <Row label="Drag" value={fmtPct(variant.slip_drag)} />
                    <Row label="Order x" value={fmt(variant.order_mult)} />
                    <Row label="Gates" value={`${variant.n_pass ?? "—"} / ${variant.n_eval ?? "—"}`} />
                    <Row label="Tier" value={variant.tier ?? "—"} />
                  </tbody>
                </table>
              </section>
              <p className="text-[10px] text-muted-foreground border-t border-border pt-3">
                Full per-fold deep-dive available at <code>/search/{variant.params_hash}</code>.
              </p>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <tr className="border-b border-border/40">
      <td className="py-1 pr-2 text-muted-foreground">{label}</td>
      <td className="py-1 text-right font-mono">{value}</td>
    </tr>
  );
}

function fmt(v: number | null | undefined) { return v == null ? "—" : v.toFixed(2); }
function fmtPct(v: number | null | undefined) { return v == null ? "—" : `${(v * 100).toFixed(1)}%`; }
