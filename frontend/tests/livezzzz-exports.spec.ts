import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("teacher downloads the current edited draft in Word PDF and Markdown without extra credits", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("browser-teacher");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  const response = await page.request.post("/api/v1/conversations", {
    headers: { Origin: "http://127.0.0.1:3218" },
    data: { title: "函数课堂导出验证" },
  });
  expect(response.ok()).toBe(true);
  const identity = (await response.json()).data.id;
  const before = (await (await page.request.get("/api/v1/me/quota")).json())
    .data;
  await page.goto(`/teacher/assistant?id=${identity}`);
  await page.getByRole("button", { name: "内容编辑", exact: true }).click();
  const editor = page.getByLabel("教学内容编辑");
  await expect(editor).toHaveValue("");
  const content =
    "# 函数教学目标\n\n理解函数的单调性，使用图像进行解释。\n\n|环节|活动|\n|---|---|\n|导入|观察图像|";
  await editor.fill(content);
  for (const format of ["docx", "pdf", "md"]) {
    await page.getByLabel("导出格式", { exact: true }).selectOption(format);
    const pending = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出文件", exact: true }).click();
    const file = await pending;
    expect(file.suggestedFilename()).toBe(`教学草稿.${format}`);
    const bytes = await readFile((await file.path())!);
    if (format === "docx") expect(bytes.subarray(0, 2).toString()).toBe("PK");
    else if (format === "pdf")
      expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
    else expect(bytes.toString("utf8")).toBe(content);
  }
  expect(
    (await (await page.request.get("/api/v1/me/quota")).json()).data,
  ).toEqual(before);
  expect(
    (
      await (
        await page.request.get(`/api/v1/conversations/${identity}/draft`)
      ).json()
    ).data.content,
  ).toBe("");
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "导出文件", exact: true })
    .scrollIntoViewIfNeeded();
  expect(
    await page
      .locator("#main-content")
      .evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
  ).toBe(true);
  await page.screenshot({ path: "test-results/exports-mobile.png" });
});
