import { expect, test } from "@playwright/test";

test("private and school knowledge uploads use real storage, scanner and worker", async ({
  page,
  browser,
}) => {
  test.skip(
    process.env.XFLJ_TEST_KNOWLEDGE_SERVICES !== "1",
    "Use backend/tests/run_knowledge_services.py --browser",
  );
  test.setTimeout(90_000);
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("browser-teacher");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  await page.getByRole("link", { name: "知识库", exact: true }).click();
  await page.getByLabel("个人知识库名称").fill("我的单调性备课资料");
  await page.getByRole("button", { name: "新建知识库" }).click();
  await expect(
    page.getByRole("heading", { name: "我的单调性备课资料" }),
  ).toBeVisible();
  await page
    .getByLabel("上传教学资料")
    .setInputFiles({
      name: "函数教学.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(
        "函数的单调性：通过定义和函数图像设计课堂活动。",
        "utf8",
      ),
    });
  await page.getByRole("button", { name: "上传资料", exact: true }).click();
  await expect(
    page.getByText("解析完成，待建立索引", { exact: true }),
  ).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "查看解析内容" }).click();
  await expect(
    page.getByText("函数的单调性：通过定义和函数图像设计课堂活动。", {
      exact: true,
    }),
  ).toBeVisible();
  await page.getByText("编辑标签", { exact: true }).click();
  await page.getByLabel("资料标签").fill("数学，教案");
  await page.getByRole("button", { name: "保存标签" }).click();
  await expect(page.getByText("标签：数学、教案")).toBeVisible();
  await page.reload();
  await page
    .getByRole("button", { name: "我的单调性备课资料 仅自己可见" })
    .click();
  await expect(page.getByText("函数教学.txt", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/live-knowledge-teacher.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: "test-results/live-knowledge-mobile.png" });
  await page.setViewportSize({ width: 1440, height: 1000 });

  const adminContext = await browser.newContext();
  const admin = await adminContext.newPage();
  try {
    await admin.goto("/login");
    await admin.getByLabel("账号", { exact: true }).fill("browser-admin");
    await admin.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
    await admin.getByRole("button", { name: "登 录 系 统" }).click();
    await expect(admin).toHaveURL(/\/teacher\/workspace$/);
    await admin.goto("/admin/knowledge");
    await expect(admin.getByText("我的单调性备课资料")).toHaveCount(0);
    await admin.getByLabel("学校知识库名称").fill("全校教研共享");
    await admin.getByRole("button", { name: "新建知识库" }).click();
    await expect(
      admin.getByRole("heading", { name: "全校教研共享" }),
    ).toBeVisible();
    await admin
      .getByLabel("上传教学资料")
      .setInputFiles({
        name: "教研要求.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("教研活动每月一次，参与教师提交教学设计。", "utf8"),
      });
    await admin.getByRole("button", { name: "上传资料", exact: true }).click();
    await expect(
      admin.getByText("解析完成，待建立索引", { exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await admin.screenshot({ path: "test-results/live-knowledge-admin.png" });
    await page.reload();
    await page.getByRole("button", { name: "全校教研共享 学校共享" }).click();
    await expect(page.getByText("教研要求.txt", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "删除资料" })).toHaveCount(0);
    await expect(page.getByLabel("上传教学资料")).toHaveCount(0);
    await page
      .getByRole("button", { name: "我的单调性备课资料 仅自己可见" })
      .click();
    await page.getByRole("button", { name: "删除资料" }).click();
    await page.getByRole("button", { name: "确认删除" }).click();
    await expect(page.getByText("函数教学.txt", { exact: true })).toHaveCount(
      0,
    );
  } finally {
    await adminContext.close();
  }
});
