import { AlertTriangle } from "lucide-react";

export function ResearchBadge() {
  return (
    <div
      className="w-full bg-[color:color-mix(in_srgb,var(--color-brand-amber)_8%,transparent)] border-y border-[color:color-mix(in_srgb,var(--color-brand-amber)_25%,transparent)] text-foreground"
      role="region"
      aria-label="Research stage notice"
      data-print="hide"
    >
      <div className="mx-auto max-w-[1400px] flex items-start gap-3 px-4 py-2 sm:px-6">
        <AlertTriangle
          aria-hidden
          className="size-4 mt-[2px] shrink-0 text-[var(--color-brand-amber)]"
        />
        <p className="text-xs sm:text-[13px] leading-relaxed">
          <span className="font-semibold tracking-wider uppercase text-[var(--color-brand-amber)]">
            Research stage
          </span>
          <span className="mx-2 text-muted-foreground">·</span>
          Pre-deployment. No strategy is yet recommended for live capital. The
          rotation winner is tier F; Clenow #9 is tier F but the closest to
          passing. See the{" "}
          <a className="underline underline-offset-2" href="/methodology/">
            scorecard framework
          </a>{" "}
          for the full assessment.
        </p>
      </div>
    </div>
  );
}
