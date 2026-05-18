import { test, devices } from "@playwright/test";
import path from "node:path";

const OUT = path.join(__dirname, "..", "..", ".tmp", "mobile-shots");
const BASE = process.env.LIVE_URL ?? "https://bursahack-portal-production.up.railway.app";

const SHOTS = [
  { path: "/", name: "01-landing" },
  { path: "/strategies/rotation_rank_1/", name: "02-strategy-rotation" },
  { path: "/strategies/clenow_som_rank_9/", name: "03-strategy-clenow" },
  { path: "/compare/rotation_rank_1/clenow_som_rank_9/", name: "04-compare" },
  { path: "/search/", name: "05-search" },
  { path: "/folds/", name: "06-folds" },
  { path: "/methodology/", name: "07-methodology" },
  { path: "/strategies/", name: "08-bank-index" },
  { path: "/strategies/clenow_som__regime-on__rebal-M/", name: "09-bank-strategy" },
];

test.use({ ...devices["iPhone 13"] });
test.describe.configure({ mode: "serial" });

for (const s of SHOTS) {
  test(`mobile shot ${s.name}`, async ({ page }) => {
    await page.goto(BASE + s.path, { waitUntil: "networkidle", timeout: 30_000 });
    await page.waitForTimeout(2500);
    await page.screenshot({
      path: path.join(OUT, `${s.name}.png`),
      fullPage: true,
    });
  });
}
