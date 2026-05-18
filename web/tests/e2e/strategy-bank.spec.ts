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
