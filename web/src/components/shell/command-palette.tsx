"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Search, ArrowRight } from "lucide-react";

import { NAV } from "@/lib/site";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";

interface VariantOption {
  hash: string;
  strategy: string;
  oosSharpe: number | null;
  tier: string | null;
}

export function CommandPaletteTrigger() {
  const [open, setOpen] = React.useState(false);
  const router = useRouter();
  const [variants, setVariants] = React.useState<VariantOption[]>([]);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === "/" && document.activeElement === document.body) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  React.useEffect(() => {
    if (!open || variants.length) return;
    fetch("/api/variants")
      .then((r) => r.json())
      .then((rows: VariantOption[]) => setVariants(rows))
      .catch(() => undefined);
  }, [open, variants.length]);

  const go = (href: string) => {
    setOpen(false);
    router.push(href);
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-2 rounded-md border border-white/15 bg-white/5 px-2.5 py-1.5 text-[12px] text-white/75 hover:text-white hover:bg-white/10 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-gold)]"
        aria-label="Open command palette"
      >
        <Search className="size-3.5" aria-hidden />
        <span className="hidden sm:inline">Jump anywhere</span>
        <span className="hidden md:inline-flex items-center gap-1 ml-2 text-[10px] text-white/55">
          <kbd className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[10px]">⌘K</kbd>
        </span>
      </button>

      <CommandDialog open={open} onOpenChange={setOpen} title="Quick navigation" description="Search routes, variants, and folds">
        <CommandInput placeholder="Search routes, variants, folds..." />
        <CommandList>
          <CommandEmpty>Nothing matches.</CommandEmpty>
          <CommandGroup heading="Pages">
            {NAV.map((n) => {
              const Icon = n.icon;
              return (
                <CommandItem
                  key={n.href}
                  value={n.label + " " + (n.description ?? "")}
                  onSelect={() => go(n.href)}
                >
                  <Icon className="size-4" aria-hidden />
                  <span>{n.label}</span>
                  {n.description ? (
                    <span className="ml-auto text-xs text-muted-foreground truncate">
                      {n.description}
                    </span>
                  ) : null}
                </CommandItem>
              );
            })}
          </CommandGroup>
          {variants.length > 0 && (
            <>
              <CommandSeparator />
              <CommandGroup heading={`Variants (${variants.length})`}>
                {variants.slice(0, 80).map((v) => (
                  <CommandItem
                    key={v.hash}
                    value={`${v.hash} ${v.strategy} ${v.tier ?? ""}`}
                    onSelect={() => go(`/search/${v.hash}/`)}
                  >
                    <ArrowRight className="size-3.5" aria-hidden />
                    <span className="font-mono text-xs">{v.hash.slice(0, 8)}</span>
                    <span className="text-muted-foreground">— {v.strategy}</span>
                    {v.oosSharpe !== null && (
                      <span className="ml-auto text-xs tabular text-muted-foreground">
                        OOS {v.oosSharpe.toFixed(2)}
                      </span>
                    )}
                  </CommandItem>
                ))}
              </CommandGroup>
            </>
          )}
        </CommandList>
      </CommandDialog>
    </>
  );
}
