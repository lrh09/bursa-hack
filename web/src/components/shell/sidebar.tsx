"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronRight } from "lucide-react";

import { NAV, SITE } from "@/lib/site";
import { cn } from "@/lib/utils";

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside
      className="hidden lg:flex fixed left-0 top-0 h-screen w-[260px] flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground z-30"
      data-print="hide"
    >
      <div className="px-5 pt-6 pb-5 border-b border-sidebar-border">
        <Link href="/" className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring rounded-sm">
          <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-sidebar-primary">
            {SITE.brand}
          </p>
          <h1 className="font-heading text-2xl text-sidebar-foreground leading-none mt-1">
            BursaHack
          </h1>
          <p className="text-[11px] text-sidebar-foreground/60 mt-2">
            {SITE.subtitle}
          </p>
        </Link>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-0.5" aria-label="Primary">
        {NAV.map((item) => {
          const Icon = item.icon;
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname === item.href || pathname.startsWith(item.href + "/");
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "group flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
                "hover:bg-sidebar-accent",
                isActive
                  ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-sidebar-primary -ml-px pl-[10px] font-medium"
                  : "text-sidebar-foreground/80",
              )}
            >
              <Icon className="size-4 shrink-0" aria-hidden />
              <span className="truncate">{item.label}</span>
              <ChevronRight
                aria-hidden
                className={cn(
                  "size-3 ml-auto opacity-0 group-hover:opacity-50 transition-opacity",
                  isActive && "opacity-60",
                )}
              />
            </Link>
          );
        })}
      </nav>

      <div className="px-5 py-4 border-t border-sidebar-border text-[11px] text-sidebar-foreground/60 leading-relaxed">
        <p>
          <kbd className="px-1.5 py-0.5 rounded bg-sidebar-accent text-sidebar-foreground border border-sidebar-border font-mono text-[10px]">
            ⌘K
          </kbd>{" "}
          to jump anywhere
        </p>
        <p className="mt-2">
          Data window 2007-01 → 2022-02
          <br />
          Holdout touched once
        </p>
      </div>
    </aside>
  );
}
