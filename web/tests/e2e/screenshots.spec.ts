import { test } from "@playwright/test";
import path from "node:path";

const OUT = path.join(__dirname, "..", "..", ".tmp", "screenshots");
const SHOTS = [
  { path: "/", name: "01-landing" },
  { path: "/strategies/rotation_rank_1/", name: "02-strategy-rotation" },
  { path: "/strategies/clenow_som_rank_9/", name: "03-strategy-clenow" },
  { path: "/compare/rotation_rank_1/clenow_som_rank_9/", name: "04-compare" },
  { path: "/search/", name: "05-search" },
  { path: "/folds/", name: "06-folds" },
  { path: "/methodology/", name: "07-methodology" },
  { path: "/research-log/", name: "08-research-log" },
  { path: "/reports/", name: "09-reports" },
  { path: "/reports/SCORECARD_rotation/", name: "10-report-scorecard" },
];

test.use({ viewport: { width: 1440, height: 1800 } });

for (const s of SHOTS) {
  test(`screenshot ${s.name}`, async ({ page }) => {
    await page.goto(s.path, { waitUntil: "domcontentloaded" });
    // Wait for plotly charts to settle
    await page.waitForTimeout(2500);
    await page.screenshot({
      path: path.join(OUT, `${s.name}.png`),
      fullPage: true,
    });
  });
}
