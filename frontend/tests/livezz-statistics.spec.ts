import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("administrator filters and downloads school aggregates without personal data", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("browser-admin");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  await page.goto("/admin/statistics");
  const panel = page.getByRole("region", { name: "学校使用统计", exact: true });
  await expect(panel.getByText("登录活跃账号", { exact: true })).toBeVisible();
  const report = (
    await (await page.request.get("/api/v1/admin/statistics")).json()
  ).data;
  await expect(
    panel
      .locator("article")
      .filter({ hasText: "实际消耗积分" })
      .locator("strong"),
  ).toHaveText(String(report.summary.consumedCredits));
  await expect(panel.locator("article").filter({ hasText: "创作产出" }).locator("strong"))
    .toHaveText(String(report.summary.resourceOutputs));
  await expect(panel.getByRole("heading", { name: "创作产出" })).toBeVisible();
  await expect(panel.getByRole("columnheader", { name: "资源产出" })).toBeVisible();
  await page.screenshot({ path: "test-results/live-statistics.png" });
  await panel.getByLabel("开始日期").fill("2020-01-01");
  await panel.getByLabel("结束日期").fill("2020-01-02");
  await panel.getByRole("button", { name: "查询统计" }).click();
  await expect(
    panel.getByRole("rowheader", { name: "2020-01-01" }),
  ).toBeVisible();
  await expect(panel.locator("tbody tr")).toHaveCount(2);
  const downloaded = page.waitForEvent("download");
  await panel.getByRole("link", { name: "导出汇总 CSV" }).click();
  const file = await downloaded;
  expect(file.suggestedFilename()).toBe(
    "school-usage-2020-01-01-2020-01-02.csv",
  );
  const content = await readFile((await file.path())!, "utf8");
  expect(content).toContain("2020-01-01");
  expect(content).toContain("PPT产出");
  expect(content).not.toContain("browser-admin");
  await page.setViewportSize({ width: 390, height: 844 });
  await panel.getByRole("heading", { name: "创作产出" }).scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/live-statistics-mobile.png",
    fullPage: true,
  });
});
