"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu } from "lucide-react";

import { NAV, SITE } from "@/lib/site";
import { cn } from "@/lib/utils";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
  SheetDescription,
} from "@/components/ui/sheet";

export function MobileNav() {
  const pathname = usePathname();
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => setOpen(false), [pathname]);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        aria-label="Open navigation"
        className="lg:hidden inline-flex h-9 w-9 items-center justify-center rounded-md text-white/85 hover:text-white hover:bg-white/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-gold)]"
      >
        <Menu className="size-5" aria-hidden />
      </SheetTrigger>
      <SheetContent
        side="left"
        className="w-[300px] p-0 bg-sidebar text-sidebar-foreground border-sidebar-border"
      >
        <SheetHeader className="px-5 pt-6 pb-5 border-b border-sidebar-border text-left">
          <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-sidebar-primary">
            {SITE.brand}
          </p>
          <SheetTitle className="font-heading text-2xl text-sidebar-foreground">
            BursaHack
          </SheetTitle>
          <SheetDescription className="text-sidebar-foreground/60 text-xs">
            {SITE.subtitle}
          </SheetDescription>
        </SheetHeader>
        <nav className="px-3 py-4 space-y-0.5" aria-label="Mobile">
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
                  "flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors",
                  "hover:bg-sidebar-accent",
                  isActive
                    ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                    : "text-sidebar-foreground/85",
                )}
              >
                <Icon className="size-4 shrink-0" aria-hidden />
                <div className="flex-1 min-w-0">
                  <p className="truncate">{item.label}</p>
                  {item.description ? (
                    <p className="text-[11px] text-sidebar-foreground/55 mt-0.5 truncate">
                      {item.description}
                    </p>
                  ) : null}
                </div>
              </Link>
            );
          })}
        </nav>
      </SheetContent>
    </Sheet>
  );
}
