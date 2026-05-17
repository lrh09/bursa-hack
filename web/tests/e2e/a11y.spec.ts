import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const PAGES = [
  "/",
  "/strategies/rotation_rank_1/",
  "/search/",
  "/methodology/",
  "/compare/rotation_rank_1/clenow_som_rank_9/",
];

for (const path of PAGES) {
  test(`a11y: ${path} — no critical violations`, async ({ page }) => {
    await page.goto(path);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();
    const critical = results.violations.filter(
      (v) => v.impact === "critical" || v.impact === "serious",
    );
    if (critical.length) {
      console.log(JSON.stringify(critical.map((v) => ({ id: v.id, nodes: v.nodes.length, help: v.help })), null, 2));
    }
    expect(critical).toEqual([]);
  });
}
