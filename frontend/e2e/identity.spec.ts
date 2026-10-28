import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

const password = `Test-${randomUUID()}`;

async function register(page: Page, prefix: string) {
  const id = randomUUID().slice(0, 8);
  const account = {
    email: `${prefix}-${id}@example.com`,
    name: `${prefix} ${id}`,
    slug: `${prefix}-${id}`,
  };
  await page.goto("/register");
  await page.getByLabel("Your name").fill(account.name);
  await page.getByLabel("Email", { exact: true }).fill(account.email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page
    .getByLabel("Organization name", { exact: true })
    .fill(account.name);
  await page
    .getByLabel("Workspace address", { exact: true })
    .fill(account.slug);
  await page
    .getByRole("button", { name: "Create account & workspace" })
    .click();
  await expect(page).toHaveURL(/\/app$/);
  await expect(
    page.getByRole("heading", { name: account.name, exact: true }),
  ).toBeVisible();
  return account;
}

test("onboarding, protected routes, organization switching, renewal, and logout", async ({
  page,
}) => {
  await page.goto("/app");
  await expect(page).toHaveURL(/\/login$/);
  const account = await register(page, "owner");
  await expect(
    page.getByText(account.email, { exact: true }).first(),
  ).toBeVisible();
  const original = await page.getByLabel("Select organization").inputValue();
  await page.getByRole("button", { name: "+ New workspace" }).click();
  const second = `second-${randomUUID().slice(0, 8)}`;
  await page.getByLabel("Name", { exact: true }).fill("Second workspace");
  await page.getByLabel("Workspace address", { exact: true }).fill(second);
  await page
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Second workspace", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Select organization").selectOption(original);
  await expect(
    page.getByRole("heading", { name: account.name, exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("Organization name", { exact: true })
    .fill("Renamed workspace");
  await page.getByRole("button", { name: "Save name" }).click();
  await expect(
    page.getByRole("heading", { name: "Renamed workspace", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel(`Role for ${account.email}`)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("workspace.png"), fullPage: true });
  await page.getByLabel(`Role for ${account.email}`).selectOption("agent");
  await expect(page.getByRole("alert")).toContainText("retain an active owner");
  const before = (await page.context().cookies()).find(
    (cookie) => cookie.name === "supportflow_session",
  );
  await page.getByRole("button", { name: "Renew session" }).click();
  await expect
    .poll(
      async () =>
        (await page.context().cookies()).find(
          (cookie) => cookie.name === "supportflow_session",
        )?.value,
    )
    .not.toBe(before?.value);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Email", { exact: true }).fill(account.email);
  await page
    .getByLabel("Password", { exact: true })
    .fill("incorrect long password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Email or password is incorrect",
  );
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/app$/);
  await page.goto("/status");
  await expect(page.getByText("available", { exact: true })).toHaveCount(2);
});

test("membership access and expired browser session", async ({
  page,
  browser,
}) => {
  const owner = await register(page, "manager");
  const otherContext = await browser.newContext({
    baseURL: new URL(page.url()).origin,
  });
  const otherPage = await otherContext.newPage();
  const agent = await register(otherPage, "colleague");
  await page.getByLabel("Existing account email").fill(agent.email);
  await page.getByRole("button", { name: "Add member" }).click();
  await expect(page.getByText(agent.email, { exact: true })).toBeVisible();
  await otherPage.reload();
  await otherPage
    .getByLabel("Select organization")
    .selectOption({ label: owner.name });
  await expect(
    otherPage.getByRole("heading", { name: owner.name, exact: true }),
  ).toBeVisible();
  await expect(
    otherPage.getByRole("button", { name: "Add member" }),
  ).toHaveCount(0);
  const row = page.getByRole("row").filter({ hasText: agent.email });
  await row.getByRole("button", { name: "Disable", exact: true }).click();
  await expect(row.getByText("Disabled", { exact: true })).toBeVisible();
  await otherPage.reload();
  await expect(
    otherPage.getByRole("heading", { name: "No workspace selected" }),
  ).toBeVisible();
  await otherContext.clearCookies();
  await otherPage.reload();
  await expect(otherPage).toHaveURL(/\/login$/);
  await otherContext.close();
});
