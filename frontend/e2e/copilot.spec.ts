import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";

test("ticket Copilot shows grounded citations and inserts a human-reviewed draft", async ({ page }) => {
  const id = randomUUID().slice(0, 8);
  const articleId = randomUUID();
  await page.route("**/api/organizations/*/ai/status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ enabled: true, state: "available", model: "local-test-model", digest: "fixture" }),
    }),
  );
  await page.route("**/api/organizations/*/ai/tickets/*/runs", async (route) => {
    const action = (route.request().postDataJSON() as { action: string }).action;
    return route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        id: randomUUID(),
        action,
        status: "completed",
        ticket_version: 1,
        result: action === "reply"
          ? "Please open account recovery and verify the email address before signing in again."
          : "Confirm the reset link is current, then follow the published recovery instructions.",
        insufficient_evidence: false,
        citations: action === "reply" ? [{
          article_id: articleId,
          revision: 2,
          chunk_index: 0,
          title: "Account recovery steps",
          slug: "account-recovery",
          excerpt: "Verify the account email before signing in with a new password.",
          relevance: 0.86,
        }] : [],
        tools_used: action === "agentic" ? ["get_sla_status"] : [],
        warnings: [],
        error_code: null,
        latency_ms: 740,
      }),
    });
  });

  await page.goto("/register");
  await page.getByLabel("Your name").fill(`Copilot Owner ${id}`);
  await page.getByLabel("Email", { exact: true }).fill(`copilot-${id}@example.com`);
  await page.getByLabel("Password", { exact: true }).fill(`Test-${randomUUID()}`);
  await page.getByLabel("Organization name", { exact: true }).fill(`Copilot desk ${id}`);
  await page.getByLabel("Workspace address").fill(`copilot-${id}`);
  await page.getByRole("button", { name: "Create account & workspace" }).click();
  await expect(page).toHaveURL(/\/app\/tickets$/);

  await page.getByRole("link", { name: "Contacts", exact: true }).click();
  await page.getByRole("button", { name: "New contact", exact: true }).click();
  await page.getByLabel("Contact name", { exact: true }).fill("Copilot Customer");
  await page.getByLabel("Contact email", { exact: true }).fill(`customer-${id}@example.com`);
  await page.getByRole("button", { name: "Save contact" }).click();
  await page.getByRole("link", { name: "Tickets", exact: true }).click();
  await page.getByRole("link", { name: "New ticket", exact: true }).click();
  await page.getByRole("button", { name: /Copilot Customer/ }).click();
  await page.getByLabel("Ticket title", { exact: true }).fill("Cannot sign in after password reset");
  await page.getByLabel("Initial customer request").fill("The new password is rejected at sign in.");
  await page.getByRole("button", { name: "Create ticket", exact: true }).click();
  await expect(page.getByRole("heading", { name: "AI Copilot" })).toBeVisible();
  await page.getByRole("button", { name: "Generate grounded reply" }).click();
  await expect(page.getByText("Account recovery steps · revision 2")).toBeVisible();
  await expect(page.getByText("Nothing is sent to the customer automatically.")).toBeVisible();
  await page.getByRole("button", { name: "Insert draft into public reply" }).click();
  await expect(page.getByLabel("Public reply body")).toHaveValue(
    "Please open account recovery and verify the email address before signing in again.",
  );
  await page.getByRole("button", { name: "Analyze with read-only tools" }).click();
  await expect(page.getByText("Read-only tools used: get_sla_status")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
