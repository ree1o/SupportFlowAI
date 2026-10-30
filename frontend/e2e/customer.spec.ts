import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

const password = `Customer-${randomUUID()}-secure`;

async function registerStaff(page: Page) {
  const id = randomUUID().slice(0, 8);
  const email = `portal-owner-${id}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Your name").fill(`Portal Owner ${id}`);
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByLabel("Organization name", { exact: true }).fill(`Portal Org ${id}`);
  await page.getByLabel("Workspace address", { exact: true }).fill(`portal-${id}`);
  await page.getByRole("button", { name: "Create account & workspace" }).click();
  await expect(page).toHaveURL(/\/app\/tickets$/);
  await page.getByRole("link", { name: "Contacts", exact: true }).click();
  await page.getByRole("button", { name: "New contact", exact: true }).click();
  const customerEmail = `portal-customer-${id}@example.com`;
  await page.getByLabel("Contact name", { exact: true }).fill(`Portal Customer ${id}`);
  await page.getByLabel("Contact email", { exact: true }).fill(customerEmail);
  await page.getByRole("button", { name: "Save contact" }).click();
  await page.getByRole("button", { name: "View contact" }).click();
  await page.getByRole("button", { name: "Invite to customer portal" }).click();
  await expect(page.getByRole("status")).toContainText("queued for delivery");
  return { id, email, customerEmail };
}

test("customer invitation, portal ticket, staff-only note, and two-way public conversation", async ({
  page,
  browser,
  request,
}) => {
  test.setTimeout(90000);
  const account = await registerStaff(page);
  let invitationUrl = "";
  await expect
    .poll(
      async () => {
        const response = await request.get("http://localhost:8025/api/v1/messages?limit=50");
        if (!response.ok()) return false;
        const data = (await response.json()) as {
          messages?: { ID?: string; To?: { Address?: string }[] }[];
        };
        const mail = data.messages?.find((item) =>
          item.To?.some((recipient) => recipient.Address === account.customerEmail),
        );
        if (!mail?.ID) return false;
        const detail = await request.get(`http://localhost:8025/api/v1/message/${mail.ID}`);
        if (!detail.ok()) return false;
        const body = JSON.stringify(await detail.json());
        const token = body.match(/customer\/invitation\?token=([A-Za-z0-9_=-]+)/)?.[1];
        if (!token) return false;
        invitationUrl = `http://localhost:5173/customer/invitation?token=${token}`;
        return true;
      },
      { timeout: 60000, intervals: [500, 1000, 2000] },
    )
    .toBe(true);

  const customerContext = await browser.newContext({ baseURL: new URL(page.url()).origin });
  const customer = await customerContext.newPage();
  await customer.goto(invitationUrl);
  await customer.getByLabel("Your name", { exact: true }).fill(`Portal Customer ${account.id}`);
  await customer.getByLabel("Password", { exact: true }).fill(password);
  await customer.getByRole("button", { name: "Accept invitation" }).click();
  await expect(customer).toHaveURL(/\/customer$/);
  await expect(customer.getByRole("heading", { name: "Your support requests" })).toBeVisible();

  const title = `Portal issue ${account.id}`;
  await customer.getByRole("link", { name: "Create a request" }).click();
  await customer.getByLabel("Subject", { exact: true }).fill(title);
  await customer.getByLabel("How can we help?", { exact: true }).fill("My order payment timed out.");
  const createResponse = customer.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().includes("/api/customer/organizations/") &&
      response.url().endsWith("/tickets"),
  );
  await customer.getByRole("button", { name: "Submit request" }).click();
  const created = await (await createResponse).json() as { id: string };
  await expect(customer.getByText("My order payment timed out.", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Tickets", exact: true }).click();
  await page.getByRole("link").filter({ hasText: title }).click();
  await page.getByRole("button", { name: "Internal note", exact: true }).click();
  await page.getByLabel("Internal note body", { exact: true }).fill("STAFF ONLY: payment provider trace.");
  await page.getByRole("button", { name: "Add internal note", exact: true }).click();
  await expect(page.getByText("STAFF ONLY: payment provider trace.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Public reply", exact: true }).click();
  await page.getByLabel("Public reply body", { exact: true }).fill("We are checking the transaction now.");
  await page.getByRole("button", { name: "Post public reply", exact: true }).click();
  await expect(page.getByText("We are checking the transaction now.", { exact: true })).toBeVisible();

  await customer.reload();
  await expect(customer.getByText("We are checking the transaction now.", { exact: true })).toBeVisible();
  await expect(customer.getByText("STAFF ONLY: payment provider trace.", { exact: true })).toHaveCount(0);
  await customer.getByLabel("Reply to support", { exact: true }).fill("Thank you; I can retry now.");
  await customer.getByRole("button", { name: "Send reply", exact: true }).click();
  await expect(customer.getByText("Thank you; I can retry now.", { exact: true })).toBeVisible();

  await page.reload();
  await expect(page.getByText("Thank you; I can retry now.", { exact: true })).toBeVisible();
  expect(created.id).toMatch(/^[0-9a-f-]{36}$/i);
  await customerContext.close();
});
