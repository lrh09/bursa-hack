import Link from "next/link";
import { CalendarClock, FileBarChart2 } from "lucide-react";

import { SITE } from "@/lib/site";
import { ThemeToggle } from "./theme-toggle";
import { MobileNav } from "./mobile-nav";
import { CommandPaletteTrigger } from "./command-palette";
import { getManifest } from "@/lib/data";

export async function BrandHeader() {
  const manifest = await getManifest();
  const generatedDate = new Date(manifest.generated_at).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });

  return (
    <header
      className="sticky top-0 z-20 bg-[var(--color-brand-navy)] text-white border-b border-[color:color-mix(in_srgb,white_10%,transparent)]"
      data-print="hide"
    >
      <div className="mx-auto max-w-[1400px] flex items-center gap-4 px-4 py-3 sm:px-6">
        <MobileNav />
        <Link
          href="/"
          className="lg:hidden focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-gold)] rounded-sm"
        >
          <p className="text-[9px] font-semibold uppercase tracking-[0.22em] text-[var(--color-brand-gold)]">
            {SITE.brand}
          </p>
          <p className="font-heading text-lg leading-none -mt-0.5">BursaHack</p>
        </Link>

        <div className="hidden lg:flex items-baseline gap-3 ml-2">
          <p className="font-heading text-lg leading-none text-white">
            Bursa Malaysia Quant Research
          </p>
          <p className="text-xs text-white/55">
            {manifest.variants_count} variants · {manifest.folds_count.toLocaleString()} backtests · holdout 2020-2022
          </p>
        </div>

        <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
          <CommandPaletteTrigger />
          <span
            className="hidden md:inline-flex items-center gap-1.5 text-[11px] text-white/60 px-2"
            title="Data freshness"
          >
            <CalendarClock className="size-3.5" aria-hidden />
            <span className="tabular">{generatedDate}</span>
          </span>
          <a
            href="/reports/"
            className="hidden md:inline-flex items-center gap-1.5 text-[12px] px-2.5 py-1.5 rounded-md text-white/85 hover:text-white hover:bg-white/8 transition-colors"
          >
            <FileBarChart2 className="size-3.5" aria-hidden />
            Reports
          </a>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
