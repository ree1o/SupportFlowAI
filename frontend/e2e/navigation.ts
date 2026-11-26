import { expect, type Page } from "@playwright/test";

async function openMobileNavigation(page: Page) {
  const viewport = page.viewportSize();
  if (!viewport || viewport.width > 820) return;
  const opener = page.getByRole("button", { name: "Open navigation" });
  await expect(opener).toBeVisible();
  await opener.click();
  const sidebar = page.locator(".sidebar");
  await expect(sidebar).toHaveClass(/is-open/);
  await expect.poll(() => sidebar.evaluate((element) => element.getBoundingClientRect().left)).toBe(0);
}

async function clickDrawerTarget(page: Page, target: ReturnType<Page["getByRole"]>) {
  const sidebar = page.locator(".sidebar");
  const viewport = page.viewportSize();
  if (!viewport || viewport.width > 820) {
    await target.scrollIntoViewIfNeeded();
    await target.click();
    return;
  }
  await target.evaluate((element) => {
    const drawer = element.closest<HTMLElement>(".sidebar");
    if (!drawer) return;
    const targetRect = element.getBoundingClientRect();
    const drawerRect = drawer.getBoundingClientRect();
    const visualViewport = window.visualViewport;
    const viewportTop = visualViewport?.offsetTop ?? 0;
    const viewportBottom = viewportTop + (visualViewport?.height ?? window.innerHeight);
    const visibleTop = Math.max(drawerRect.top, viewportTop);
    const visibleBottom = Math.min(drawerRect.bottom, viewportBottom);
    const targetCenter = (targetRect.top + targetRect.bottom) / 2;
    drawer.scrollTop += targetCenter - (visibleTop + visibleBottom) / 2;
  });
  await expect.poll(async () => target.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const drawer = element.closest(".sidebar")?.getBoundingClientRect();
    const visualViewport = window.visualViewport;
    const viewportTop = visualViewport?.offsetTop ?? 0;
    const viewportBottom = viewportTop + (visualViewport?.height ?? window.innerHeight);
    const viewportLeft = visualViewport?.offsetLeft ?? 0;
    const viewportRight = viewportLeft + (visualViewport?.width ?? window.innerWidth);
    return Boolean(
      drawer &&
      rect.top >= Math.max(drawer.top, viewportTop) &&
      rect.bottom <= Math.min(drawer.bottom, viewportBottom) &&
      rect.left >= Math.max(drawer.left, viewportLeft) &&
      rect.right <= Math.min(drawer.right, viewportRight),
    );
  })).toBe(true);
  await target.click();
  await expect.poll(async () => {
    if (await sidebar.count() === 0) return true;
    return !(await sidebar.getAttribute("class") ?? "").split(" ").includes("is-open");
  }).toBe(true);
}

export async function clickStaffNavigation(page: Page, name: string) {
  await openMobileNavigation(page);
  await clickDrawerTarget(page, page.getByRole("link", { name, exact: true }));
}

export async function clickNewWorkspace(page: Page) {
  await openMobileNavigation(page);
  await clickDrawerTarget(page, page.getByRole("button", { name: "+ New workspace" }));
}

export async function clickSidebarAction(page: Page, name: string) {
  await openMobileNavigation(page);
  await clickDrawerTarget(page, page.getByRole("button", { name, exact: true }));
}
