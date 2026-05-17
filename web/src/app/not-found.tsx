import Link from "next/link";
import { Compass } from "lucide-react";

export default function NotFound() {
  return (
    <div className="min-h-[40vh] flex flex-col items-center justify-center text-center gap-4 py-12 fade-rise">
      <Compass className="size-10 text-muted-foreground" aria-hidden />
      <h1 className="font-heading text-3xl sm:text-4xl">Off the chart</h1>
      <p className="text-muted-foreground max-w-md">
        The page you&apos;re looking for is past the holdout. Try the search explorer or head back to the overview.
      </p>
      <div className="flex gap-3 text-sm">
        <Link
          href="/"
          className="px-3 py-2 rounded-md bg-[var(--color-brand-navy)] text-white hover:opacity-90"
        >
          Overview
        </Link>
        <Link
          href="/search/"
          className="px-3 py-2 rounded-md border border-border hover:bg-muted"
        >
          Search explorer
        </Link>
      </div>
    </div>
  );
}
