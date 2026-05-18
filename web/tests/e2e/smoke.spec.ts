import { test, expect } from "@playwright/test";

const ROUTES: Array<{ path: string; expectText: RegExp }> = [
  { path: "/", expectText: /Bursa Malaysia momentum/i },
  { path: "/strategies/", expectText: /Strategies in the BursaHack research/i },
  { path: "/strategies/rotation_rank_1/", expectText: /Bursa Momentum Rotation/i },
  { path: "/strategies/rotation__rebal-M/", expectText: /Bursa Momentum Rotation/i },
  { path: "/strategies/clenow_som_rank_9/", expectText: /Clenow Stocks on the Move/i },
  { path: "/strategies/clenow_som__regime-on__rebal-M/", expectText: /Clenow Stocks on the Move/i },
  { path: "/search/", expectText: /Search explorer/i },
  { path: "/folds/", expectText: /Walk-forward folds/i },
  { path: "/methodology/", expectText: /Methodology/i },
  { path: "/reports/", expectText: /Reports/i },
  { path: "/research-log/", expectText: /Research log/i },
  { path: "/compare/rotation_rank_1/clenow_som_rank_9/", expectText: /Side-by-side/i },
];

for (const r of ROUTES) {
  test(`route ${r.path} loads, has research-stage banner, no console errors`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });

    await page.goto(r.path, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("main")).toBeVisible();
    await expect(page.getByText(r.expectText).first()).toBeVisible();
    await expect(page.getByText(/RESEARCH STAGE/i).first()).toBeVisible();

    // ignore plotly/recharts warning text from ssr only
    const real = errors.filter(
      (e) => !/ResizeObserver|Recharts|loadable|chunk|Hydration/.test(e),
    );
    expect(real).toEqual([]);
  });
}

test("command palette opens via trigger button and navigates", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Open command palette/i }).click();
  const input = page.getByPlaceholder(/Search routes/i);
  await expect(input).toBeVisible();
  await input.fill("rotation");
  await expect(page.getByText(/Bursa Rotation/i).first()).toBeVisible();
});

