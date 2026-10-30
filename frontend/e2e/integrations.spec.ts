import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

async function setup(page: Page) {
  const id = randomUUID().slice(0, 8);
  await page.goto("/register");
  await page.getByLabel("Your name").fill(`Integration Owner ${id}`);
  await page.getByLabel("Email", { exact: true }).fill(`integration-${id}@example.com`);
  await page.getByLabel("Password", { exact: true }).fill(`Test-${randomUUID()}`);
  await page.getByLabel("Organization name", { exact: true }).fill(`Integration ${id}`);
  await page.getByLabel("Workspace address").fill(`integration-${id}`);
  await page.getByRole("button", { name: "Create account & workspace" }).click();
  await expect(page).toHaveURL(/\/app\/tickets$/);
  await page.getByRole("link", { name: "Contacts", exact: true }).click();
  await page.getByRole("button", { name: "New contact", exact: true }).click();
  await page.getByLabel("Contact name", { exact: true }).fill("Mapped Integration Customer");
  await page.getByLabel("Contact email", { exact: true }).fill(`mapped-${id}@example.com`);
  await page.getByRole("button", { name: "Save contact" }).click();
  await expect(page.getByRole("cell", { name: "Mapped Integration Customer" })).toBeVisible();
  return id;
}

test("owner creates a contact-bound source and receives one idempotent ticket", async ({ page }) => {
  const id = await setup(page);
  await page.getByRole("link", { name: "Integrations", exact: true }).click();
  await page.getByLabel("Source name").fill("Payment service");
  await page.getByLabel("Authorized contact").selectOption({
    label: `Mapped Integration Customer · mapped-${id}@example.com`,
  });
  const create = page.waitForResponse((response) =>
    response.request().method() === "POST" && response.url().includes("/integrations"),
  );
  await page.getByRole("button", { name: "Create source" }).click();
  const sourceResponse = await create;
  expect(sourceResponse.status()).toBe(201);
  const { source, credential } = await sourceResponse.json();
  await expect(page.getByRole("heading", { name: "Save this credential now" })).toBeVisible();
  await expect(page.getByLabel("New signing credential")).toHaveValue(credential);

  const sent = await page.evaluate(async ({ sourceId, key, eventId }) => {
    const timestamp = String(Math.floor(Date.now() / 1000));
    const body = JSON.stringify({
      type: "ticket.created",
      title: "Payment provider returned an error",
      message: "Checkout could not complete the card payment.",
      priority: "high",
    });
    const encoder = new TextEncoder();
    const cryptoKey = await crypto.subtle.importKey(
      "raw",
      encoder.encode(key),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
    const signed = `${timestamp}.${eventId}.${body}`;
    const signature = Array.from(
      new Uint8Array(await crypto.subtle.sign("HMAC", cryptoKey, encoder.encode(signed))),
      (byte) => byte.toString(16).padStart(2, "0"),
    ).join("");
    const send = () => fetch(`/api/integrations/${sourceId}/events`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-SupportFlow-Event-Id": eventId,
        "X-SupportFlow-Timestamp": timestamp,
        "X-SupportFlow-Signature": `v1=${signature}`,
      },
      body,
    });
    const first = await send();
    const firstResult = await first.json();
    const duplicate = await send();
    return { firstStatus: first.status, duplicateStatus: duplicate.status, firstResult };
  }, { sourceId: source.id, key: credential, eventId: `pw-${randomUUID()}` });
  expect(sent.firstStatus).toBe(201);
  expect(sent.duplicateStatus).toBe(201);

  await page.getByRole("link", { name: "Tickets", exact: true }).click();
  await expect(page.getByRole("link", { name: "Payment provider returned an error" })).toBeVisible();
  await page.getByRole("link", { name: "Integrations", exact: true }).click();
  await expect(page.getByText("Processed").first()).toBeVisible();
  await expect(page.getByText("Payment service").first()).toBeVisible();
});
