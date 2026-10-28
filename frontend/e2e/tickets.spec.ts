import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

async function desk(page: Page) {
  const id = randomUUID().slice(0, 8);
  await page.goto("/register");
  await page.getByLabel("Your name").fill(`Support Owner ${id}`);
  await page
    .getByLabel("Email", { exact: true })
    .fill(`desk-${id}@example.com`);
  await page
    .getByLabel("Password", { exact: true })
    .fill(`Test-${randomUUID()}`);
  await page
    .getByLabel("Organization name", { exact: true })
    .fill(`Support desk ${id}`);
  await page.getByLabel("Workspace address").fill(`desk-${id}`);
  await page
    .getByRole("button", { name: "Create account & workspace" })
    .click();
  await expect(page).toHaveURL(/\/app\/tickets$/);
  await expect(
    page.getByRole("heading", { name: "Ticket queue", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Contacts", exact: true }).click();
  await page.getByRole("button", { name: "New contact", exact: true }).click();
  await page.getByLabel("Contact name", { exact: true }).fill("Jamie Customer");
  await page
    .getByLabel("Contact email", { exact: true })
    .fill(`customer-${id}@example.com`);
  await page.getByRole("button", { name: "Save contact" }).click();
  await expect(
    page.getByRole("cell", { name: "Jamie Customer", exact: true }),
  ).toBeVisible();
  return id;
}

async function ticket(page: Page, title: string) {
  await page.getByRole("link", { name: "Tickets", exact: true }).click();
  await page.getByRole("link", { name: "New ticket", exact: true }).click();
  await page.getByRole("button", { name: /Jamie Customer/ }).click();
  await page.getByLabel("Ticket title", { exact: true }).fill(title);
  await page
    .getByLabel("Initial customer request")
    .fill("Checkout fails after entering a card.");
  await page
    .getByRole("button", { name: "Create ticket", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: title, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Checkout fails after entering a card.", { exact: true }),
  ).toBeVisible();
}

test("staff intake, assignment, distinct conversations, audit, filtering, and tenant switching", async ({
  page,
}) => {
  await desk(page);
  await page.getByRole("link", { name: "Categories", exact: true }).click();
  await page.getByLabel("Category name", { exact: true }).fill("Payments");
  await page
    .getByRole("button", { name: "Create category", exact: true })
    .click();
  await expect(page.getByLabel("Category name for Payments")).toBeVisible();
  await ticket(page, "Customer cannot complete checkout");
  await page.getByLabel("Ticket assignee").selectOption({ index: 1 });
  await expect(
    page.getByText("assignment changed", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("Ticket priority").selectOption("urgent");
  await expect(
    page.getByText("priority changed", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("Ticket category").selectOption({ label: "Payments" });
  await expect(
    page.getByText("category changed", { exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("Public reply body")
    .fill("We are investigating your checkout issue.");
  await page.getByRole("button", { name: "Post public reply" }).click();
  await expect(
    page.getByText("We are investigating your checkout issue.", {
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Internal note", exact: true })
    .click();
  await page
    .getByLabel("Internal note body")
    .fill("PRIVATE: payment provider reference 42.");
  await page
    .getByRole("button", { name: "Add internal note", exact: true })
    .click();
  await expect(page.locator(".internal-note")).toContainText(
    "PRIVATE: payment provider reference 42.",
  );
  await expect(page.getByText("note added", { exact: true })).toBeVisible();
  const ticketURL = page.url();
  const org = await page.getByLabel("Select organization").inputValue();
  const ticketId = ticketURL.split("/").pop();
  const publicResponse = await page.request.get(
    `/api/organizations/${org}/tickets/${ticketId}/public-conversation`,
  );
  expect(publicResponse.status()).toBe(200);
  const publicData = await publicResponse.json();
  expect(JSON.stringify(publicData)).not.toContain("PRIVATE");
  expect(publicData.items).toHaveLength(2);
  await page.getByLabel("Ticket status").selectOption("resolved");
  await expect(
    page.getByRole("heading", { name: "Activity history" }),
  ).toBeVisible();
  await expect(page.locator(".ticket-heading .status-pill")).toHaveText(
    "resolved",
  );
  await page.getByLabel("Ticket status").selectOption("closed");
  await expect(page.getByLabel("Ticket priority")).toBeDisabled();
  await page.getByLabel("Ticket status").selectOption("open");
  await expect(page.getByLabel("Ticket priority")).toBeEnabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: test.info().outputPath("ticket-detail.png"),
    fullPage: true,
  });
  await page.getByRole("link", { name: "Tickets", exact: true }).click();
  await page.getByLabel("Search tickets", { exact: true }).fill("checkout");
  await page
    .getByRole("combobox", { name: "Priority", exact: true })
    .selectOption("urgent");
  await page
    .getByRole("button", { name: "Apply filters", exact: true })
    .click();
  await expect(page.locator(".ticket-row")).toHaveCount(1);
  await page
    .getByRole("combobox", { name: "Status", exact: true })
    .selectOption("closed");
  await page
    .getByRole("button", { name: "Apply filters", exact: true })
    .click();
  await expect(page.getByText("No tickets here yet")).toBeVisible();
  await page.getByRole("button", { name: "+ New workspace" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Isolated workspace");
  await page
    .getByLabel("Workspace address", { exact: true })
    .fill(`isolated-${randomUUID().slice(0, 8)}`);
  await page
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Isolated workspace", exact: true }),
  ).toBeVisible();
  const otherOrg = await page.getByLabel("Select organization").inputValue();
  const hidden = await page.request.get(
    `/api/organizations/${otherOrg}/tickets/${ticketId}`,
  );
  expect(hidden.status()).toBe(404);
  await page.getByRole("link", { name: "Tickets", exact: true }).click();
  await expect(page.getByText("No tickets here yet")).toBeVisible();
  await page.getByLabel("Select organization").selectOption(org);
  await expect(page.locator(".ticket-row")).toHaveCount(1);
});

test("two staff tabs reject stale changes and preserve reply drafts", async ({
  page,
}) => {
  await desk(page);
  await ticket(page, "Concurrent staff update");
  const second = await page.context().newPage();
  await second.goto(page.url());
  await expect(second.getByLabel("Public reply body")).toBeVisible();
  await second
    .getByLabel("Public reply body")
    .fill("Draft written before the other change.");
  await page.getByLabel("Ticket priority").selectOption("high");
  await expect(
    page.getByText("priority changed", { exact: true }),
  ).toBeVisible();
  const rejected = second.waitForResponse(
    (response) =>
      response.url().endsWith("/replies") &&
      response.request().method() === "POST",
  );
  await second.getByRole("button", { name: "Post public reply" }).click();
  expect((await rejected).status()).toBe(409);
  await expect(
    second.getByText("Another staff member updated this ticket."),
  ).toBeVisible();
  await expect(second.getByLabel("Public reply body")).toHaveValue(
    "Draft written before the other change.",
  );
  await second
    .getByRole("button", { name: "Reload latest ticket", exact: true })
    .click();
  await expect(second.getByLabel("Ticket priority")).toHaveValue("high");
  await second.getByRole("button", { name: "Post public reply" }).click();
  await expect(
    second.getByText("Draft written before the other change.", { exact: true }),
  ).toBeVisible();
  await expect(second.getByLabel("Public reply body")).toHaveValue("");
  await second.close();
});
