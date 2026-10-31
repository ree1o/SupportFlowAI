import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

async function postJson(page: Page, path: string, body: unknown) {
  const csrf = await page.request.get("/api/auth/csrf");
  const token = (await csrf.json()).csrf_token as string;
  return page.request.post("/api" + path, {
    data: body,
    headers: { Origin: "http://localhost:5173", "X-CSRF-Token": token },
  });
}

test("staff reviews deterministic evidence and confirms a manual incident", async ({ page }) => {
  const id = randomUUID().slice(0, 8);
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Radar Owner " + id);
  await page.getByLabel("Email", { exact: true }).fill("radar-" + id + "@example.com");
  await page.getByLabel("Password", { exact: true }).fill("Test-" + randomUUID());
  await page.getByLabel("Organization name", { exact: true }).fill("Radar demo " + id);
  await page.getByLabel("Workspace address").fill("radar-" + id);
  await page.getByRole("button", { name: "Create account & workspace" }).click();
  await expect(page).toHaveURL(/\/app\/tickets$/);
  const org = await page.getByLabel("Select organization").inputValue();
  const contacts = [];
  for (const name of ["Radar Customer One", "Radar Customer Two", "Unrelated Reporter"]) {
    const response = await postJson(page, "/organizations/" + org + "/contacts", {
      name,
      email: name.toLowerCase().replaceAll(" ", ".") + "-" + id + "@example.com",
    });
    expect(response.status()).toBe(201);
    contacts.push((await response.json()).id as string);
  }
  const titles = [
    ["Payment gateway times out", "Card payment times out during checkout."],
    ["Checkout payment timeout", "Card authorization times out while paying."],
    ["Password reset email delayed", "Login link never arrives for this account."],
  ];
  for (let index = 0; index < titles.length; index++) {
    const response = await postJson(page, "/organizations/" + org + "/tickets", {
      requester_contact_id: contacts[index],
      title: titles[index][0],
      initial_message: titles[index][1],
      priority: "normal",
      category_id: null,
      assigned_membership_id: null,
    });
    expect(response.status()).toBe(201);
  }

  await page.getByRole("link", { name: "Incident Radar", exact: true }).click();
  await page.getByRole("button", { name: "Run detection", exact: true }).click();
  await expect(page.getByText("Detection run queued.", { exact: true })).toBeVisible();
  await expect.poll(async () => {
    const response = await page.request.get("/api/organizations/" + org + "/radar/proposals?status=pending");
    return response.status() === 200 ? (await response.json()).total as number : 0;
  }).toBe(1);
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByRole("heading", { name: /Pending proposals/ })).toContainText("1");
  const proposalCard = page.locator(".proposal-card");
  await expect(proposalCard.getByRole("link", { name: titles[0][0] })).toBeVisible();
  await expect(proposalCard.getByRole("link", { name: titles[1][0] })).toBeVisible();
  await expect(proposalCard.getByRole("link", { name: titles[2][0] })).toHaveCount(0);
  await proposalCard.getByLabel("Incident title").fill("Payment gateway incident");
  await proposalCard.getByLabel("Severity").selectOption("sev2");
  page.once("dialog", (dialog) => dialog.accept());
  await proposalCard.getByRole("button", { name: "Confirm incident" }).click();
  await expect(page.getByText("Proposal confirmed and incident created.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Payment gateway incident/ }).click();
  await expect(page.getByRole("heading", { name: /Linked tickets/ })).toContainText("2");
  await page.getByLabel("Add internal investigation note").fill("Payment processor reports elevated timeout rates.");
  await page.getByRole("button", { name: "Add note", exact: true }).click();
  await expect(page.getByText("Payment processor reports elevated timeout rates.", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
