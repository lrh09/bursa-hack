import type { Metadata } from "next";
import { Inter, Playfair_Display, JetBrains_Mono } from "next/font/google";

import "./globals.css";
import { ThemeProvider } from "@/components/theme-provider";
import { BrandHeader } from "@/components/shell/brand-header";
import { ResearchBadge } from "@/components/shell/research-badge";
import { Sidebar } from "@/components/shell/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { SITE } from "@/lib/site";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});
const playfair = Playfair_Display({
  subsets: ["latin"],
  variable: "--font-playfair",
  display: "swap",
});
const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: `${SITE.name} — Bursa Malaysia Quant Research`,
    template: `%s · ${SITE.name}`,
  },
  description:
    "BursaHack — a research portal for Bursa Malaysia momentum & trend-following strategies. 102 variants, 16 walk-forward folds, 2007-2022 panel.",
  keywords: ["Bursa Malaysia", "KLSE", "quant", "momentum", "Clenow", "walk-forward", "backtest"],
  authors: [{ name: SITE.brand }],
  openGraph: {
    title: SITE.name,
    description: "Bursa Malaysia quantitative research portal.",
    type: "website",
    siteName: SITE.brand,
  },
  twitter: {
    card: "summary_large_image",
    title: SITE.name,
  },
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${inter.variable} ${playfair.variable} ${jetbrains.variable}`}
    >
      <body className="antialiased min-h-screen bg-background text-foreground">
        <ThemeProvider
          attribute="class"
          defaultTheme="light"
          enableSystem
          disableTransitionOnChange
        >
          <TooltipProvider delay={200}>
            <a
              href="#main"
              className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:px-3 focus:py-2 focus:rounded-md focus:bg-foreground focus:text-background"
            >
              Skip to content
            </a>
            <Sidebar />
            <div className="lg:pl-[260px]">
              <BrandHeader />
              <ResearchBadge />
              <main id="main" className="mx-auto max-w-[1400px] px-4 sm:px-6 py-6 sm:py-8">
                {children}
              </main>
              <footer className="border-t border-border mt-12 py-6 px-4 sm:px-6 text-xs text-muted-foreground" data-print="hide">
                <div className="mx-auto max-w-[1400px] flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4">
                  <p>
                    © {new Date().getFullYear()} {SITE.brand}. Research artifact, not a registered product.
                  </p>
                  <p className="ml-auto">
                    Panel: 2007-01 → 2022-02 ·
                    Holdout touched <span className="tabular">1</span> time ·
                    Cost model: MPlus retail
                  </p>
                </div>
              </footer>
            </div>
            <Toaster richColors closeButton position="top-right" />
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
