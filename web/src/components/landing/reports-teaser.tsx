import Link from "next/link";
import { ArrowRight, FileBadge2, FileBarChart2, FileText } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

interface Item {
  href: string;
  icon: typeof FileText;
  title: string;
  blurb: string;
  external?: boolean;
}

const ITEMS: Item[] = [
  {
    href: "/reports/FINAL_REPORT/",
    icon: FileBarChart2,
    title: "Final scorecards",
    blurb: "Top-10 survivors of the 102-variant search with per-gate evaluation.",
  },
  {
    href: "/reports/SCORECARD_rotation/",
    icon: FileBadge2,
    title: "Rotation scorecard",
    blurb: "Raw 12-gate scorecard for the dual-slope rotation winner.",
  },
  {
    href: "/reports/REPORT/",
    icon: FileText,
    title: "Methodology report",
    blurb: "Search design, cost model, walk-forward folds, diagnostics.",
  },
];

export function ReportsTeaser() {
  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="font-heading text-lg sm:text-xl">Archive</h2>
        <Link
          href="/reports/"
          className="inline-flex items-center gap-1 text-xs sm:text-sm text-[var(--color-brand-gold-700)] hover:text-[var(--color-brand-gold)]"
        >
          All reports <ArrowRight className="size-3.5" aria-hidden />
        </Link>
      </div>
      <div className="grid sm:grid-cols-3 gap-3">
        {ITEMS.map((it) => {
          const Icon = it.icon;
          return (
            <Link key={it.href} href={it.href} className="group focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-lg">
              <Card className="h-full transition-shadow group-hover:shadow-md">
                <CardContent className="py-4 space-y-2">
                  <div className="size-9 rounded-md flex items-center justify-center bg-[var(--color-brand-navy)] text-[var(--color-brand-gold)]">
                    <Icon className="size-4" aria-hidden />
                  </div>
                  <h3 className="font-medium text-sm text-foreground">{it.title}</h3>
                  <p className="text-xs text-muted-foreground">{it.blurb}</p>
                </CardContent>
              </Card>
            </Link>
          );
        })}
      </div>
    </section>
  );
}
