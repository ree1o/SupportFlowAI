import { expect, type Page } from "@playwright/test";

async function clickDrawerTarget(page: Page, target: ReturnType<Page["getByRole"]>) {
  const sidebar = page.locator(".sidebar");
  const viewport = page.viewportSize();
  if (!viewport || viewport.width > 820) {
    await target.scrollIntoViewIfNeeded();
    await target.click();
    return;
  }
  await target.evaluate((element) => element.scrollIntoView({ block: "nearest", inline: "nearest" }));
  await target.evaluate((element) => {
    const drawer = element.closest<HTMLElement>(".sidebar");
    if (!drawer) return;
    const targetRect = element.getBoundingClientRect();
    const drawerRect = drawer.getBoundingClientRect();
    if (targetRect.top < drawerRect.top) drawer.scrollTop -= drawerRect.top - targetRect.top;
    if (targetRect.bottom > drawerRect.bottom) drawer.scrollTop += targetRect.bottom - drawerRect.bottom;
  });
  await expect.poll(async () => target.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const drawer = element.closest(".sidebar")?.getBoundingClientRect();
    return Boolean(drawer && rect.top >= drawer.top && rect.bottom <= drawer.bottom);
  })).toBe(true);
  await target.click();
  await expect.poll(async () => {
    if (await sidebar.count() === 0) return true;
    return !(await sidebar.getAttribute("class") ?? "").split(" ").includes("is-open");
  }).toBe(true);
}

export async function clickStaffNavigation(page: Page, name: string) {
  const openButton = page.getByRole("button", { name: "Open navigation" });
  if (await openButton.count()) {
    await openButton.click();
    await expect(page.locator(".sidebar")).toHaveClass(/is-open/);
    await expect.poll(() => page.locator(".sidebar").evaluate((element) => element.getBoundingClientRect().left)).toBe(0);
  }
  await clickDrawerTarget(page, page.getByRole("link", { name, exact: true }));
}

export async function clickNewWorkspace(page: Page) {
  const openButton = page.getByRole("button", { name: "Open navigation" });
  if (await openButton.count()) {
    await openButton.click();
    await expect(page.locator(".sidebar")).toHaveClass(/is-open/);
    await expect.poll(() => page.locator(".sidebar").evaluate((element) => element.getBoundingClientRect().left)).toBe(0);
  }
  await clickDrawerTarget(page, page.getByRole("button", { name: "+ New workspace" }));
}

export async function clickSidebarAction(page: Page, name: string) {
  const openButton = page.getByRole("button", { name: "Open navigation" });
  if (await openButton.count()) {
    await openButton.click();
    await expect(page.locator(".sidebar")).toHaveClass(/is-open/);
    await expect.poll(() => page.locator(".sidebar").evaluate((element) => element.getBoundingClientRect().left)).toBe(0);
  }
  await clickDrawerTarget(page, page.getByRole("button", { name, exact: true }));
}
