// Server-side data loaders. Read from ../data/*.json at build/request time.
// All consumers must be server components or route handlers.

import { promises as fs } from "node:fs";
import path from "node:path";
import { cache } from "react";

import type {
  EquityCurve,
  FoldRow,
  Manifest,
  Strategy,
  Trade,
  VariantDetail,
  VariantSummary,
} from "./types";

const DATA_ROOT = path.join(process.cwd(), "data");

async function readJson<T>(rel: string): Promise<T> {
  const p = path.join(DATA_ROOT, rel);
  const raw = await fs.readFile(p, "utf-8");
  return JSON.parse(raw) as T;
}

export const getManifest = cache(async (): Promise<Manifest> => {
  return readJson<Manifest>("manifest.json");
});

export const getStrategy = cache(async (slug: string): Promise<Strategy> => {
  return readJson<Strategy>(`strategies/${slug}.json`);
});

export const getAllStrategies = cache(async (): Promise<Strategy[]> => {
  const m = await getManifest();
  return Promise.all(m.strategies.map((s) => getStrategy(s.slug)));
});

export const getEquity = cache(
  async (slug: string, capital: string): Promise<EquityCurve | null> => {
    try {
      return await readJson<EquityCurve>(`equity/${slug}_${capital}.json`);
    } catch {
      return null;
    }
  },
);

export const getTrades = cache(async (slug: string): Promise<Trade[] | null> => {
  try {
    return await readJson<Trade[]>(`trades/${slug}.json`);
  } catch {
    return null;
  }
});

export const getAllVariants = cache(async (): Promise<VariantSummary[]> => {
  return readJson<VariantSummary[]>("search_summary.json");
});

export const getVariantDetail = cache(
  async (hash: string): Promise<VariantDetail | null> => {
    try {
      return await readJson<VariantDetail>(`variants/${hash}.json`);
    } catch {
      return null;
    }
  },
);

export const getAllVariantHashes = cache(async (): Promise<string[]> => {
  const v = await getAllVariants();
  return v.map((x) => x.params_hash);
});

export const getFolds = cache(async (strategy?: string): Promise<FoldRow[]> => {
  if (strategy) {
    try {
      return await readJson<FoldRow[]>(`folds/${strategy}.json`);
    } catch {
      // fallthrough
    }
  }
  return readJson<FoldRow[]>("folds/all.json");
});

export const getFinalScorecards = cache(
  async (): Promise<Array<Record<string, string>>> => {
    return readJson<Array<Record<string, string>>>("final_scorecards.json");
  },
);

export const getReport = cache(async (slug: string): Promise<string | null> => {
  try {
    const p = path.join(DATA_ROOT, "reports", `${slug}.md`);
    return await fs.readFile(p, "utf-8");
  } catch {
    return null;
  }
});

export const listReports = cache(async (): Promise<string[]> => {
  const dir = path.join(DATA_ROOT, "reports");
  try {
    const entries = await fs.readdir(dir);
    return entries.filter((e) => e.endsWith(".md")).map((e) => e.replace(/\.md$/, ""));
  } catch {
    return [];
  }
});
