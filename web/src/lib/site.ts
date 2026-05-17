// Site config. Imported by the shell + the command palette.

import {
  Home,
  LineChart,
  Search,
  Layers,
  BookOpen,
  FileText,
  GitCompare,
  type LucideIcon,
  Notebook,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  description?: string;
}

export const SITE = {
  name: "BursaHack",
  subtitle: "Bursa Malaysia quantitative research",
  brand: "FatFyre Research",
  url: "https://bursahack.fatfyre.com",
  defaultStrategy: "rotation_rank_1",
  alternateStrategy: "clenow_som_rank_9",
  defaultCapital: "350k",
};

export const NAV: NavItem[] = [
  { href: "/", label: "Overview", icon: Home, description: "Programme summary + headline strategies" },
  {
    href: "/strategies/rotation_rank_1",
    label: "Bursa Rotation",
    icon: LineChart,
    description: "Dual-slope cross-sectional momentum — tier F",
  },
  {
    href: "/strategies/clenow_som_rank_9",
    label: "Clenow #9",
    icon: LineChart,
    description: "Stocks on the Move (regime-on, lookback 60)",
  },
  {
    href: "/compare/rotation_rank_1/clenow_som_rank_9",
    label: "Compare",
    icon: GitCompare,
    description: "Side-by-side rotation vs Clenow #9",
  },
  {
    href: "/search",
    label: "Search explorer",
    icon: Search,
    description: "All 102 variants of the brute-force search",
  },
  {
    href: "/folds",
    label: "Walk-forward folds",
    icon: Layers,
    description: "16 train/validate windows across 2008-2019",
  },
  {
    href: "/methodology",
    label: "Methodology",
    icon: BookOpen,
    description: "Framework, gates, glossary",
  },
  {
    href: "/research-log",
    label: "Research log",
    icon: Notebook,
    description: "Narrative journal of the research programme",
  },
  {
    href: "/reports",
    label: "Reports & archive",
    icon: FileText,
    description: "Investor PDF + raw scorecard markdown",
  },
];
