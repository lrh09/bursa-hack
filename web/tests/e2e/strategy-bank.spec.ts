import { test, expect } from "@playwright/test";

test("bank index page loads and lists strategies", async ({ page }) => {
  await page.goto("/strategies/");
  await expect(
    page.getByRole("heading", {
      name: "Strategies in the BursaHack research",
    }),
  ).toBeVisible();
  // At least one row link to a strategy detail page
  const firstLink = page.locator("table a").first();
  await expect(firstLink).toBeVisible();
});

test("bank index row links to a strategy detail page", async ({ page }) => {
  await page.goto("/strategies/");
  const firstLink = page.locator("table a").first();
  const href = await firstLink.getAttribute("href");
  expect(href).toMatch(/^\/strategies\/.+\/?$/);
});

test("clicking a variant row opens slide-over with ?v=hash", async ({ page }) => {
  await page.goto("/strategies/");
  // Click the first strategy row to get into a detail page
  await page.locator("table a").first().click();
  await page.waitForLoadState("networkidle");

  // Target the explorer table specifically: it has the "Hide dominated" label nearby.
  // Find the first variant row INSIDE the section that has the Hide-dominated checkbox.
  const explorerSection = page.locator("section", { hasText: "Hide dominated" });
  const firstRow = explorerSection.locator("table tbody tr").first();
  await expect(firstRow).toBeVisible();
  await firstRow.click();

  // URL should now have ?v=
  await page.waitForURL(/\?v=[a-f0-9]+/, { timeout: 10_000 });

  // Slide-over should show a "Scorecard" heading inside the Sheet
  await expect(page.getByText(/^Scorecard$/i)).toBeVisible({ timeout: 5_000 });
});
