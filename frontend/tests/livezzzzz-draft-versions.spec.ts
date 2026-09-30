import { expect, test } from "@playwright/test";

test("teacher saves previews restores and resolves draft conflicts without losing local edits", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("browser-teacher");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  const response = await page.request.post("/api/v1/conversations", {
    headers: { Origin: "http://127.0.0.1:3218" },
    data: { title: "草稿版本验收" },
  });
  expect(response.ok()).toBe(true);
  const identity = (await response.json()).data.id;
  const secondResponse = await page.request.post("/api/v1/conversations", {
    headers: { Origin: "http://127.0.0.1:3218" }, data: { title: "草稿隔离验收" },
  });
  expect(secondResponse.ok()).toBe(true);
  const before = (await (await page.request.get("/api/v1/me/quota")).json())
    .data;
  await page.goto("/teacher/assistant?id=" + identity);
  await page.getByRole("button", { name: "内容编辑", exact: true }).click();
  const editor = page.getByLabel("教学内容编辑");
  await editor.fill("第一版教学目标");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(
    page.getByText("草稿已保存为 v1。", { exact: true }),
  ).toBeVisible();
  await editor.fill("第二版增加探究活动");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(
    page.getByText("草稿已保存为 v2。", { exact: true }),
  ).toBeVisible();
  await page.locator(".draft-history summary").click();
  await page.getByRole("button", { name: "查看 v1", exact: true }).click();
  await expect(page.getByLabel("历史版本内容")).toHaveValue("第一版教学目标");
  await editor.fill("未保存的课堂修改");
  await expect(
    page.getByRole("button", { name: "恢复为新版本", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(
    page.getByText("草稿已保存为 v3。", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "恢复为新版本", exact: true }).click();
  await expect(page.getByText("已恢复为 v4。", { exact: true })).toBeVisible();
  await expect(editor).toHaveValue("第一版教学目标");
  await page.getByRole("button", { name: "查看 v3", exact: true }).click();
  await expect(page.getByLabel("历史版本内容")).toHaveValue("未保存的课堂修改");
  // Another session saves first. The stale editor must retain its text after 409.
  await editor.fill("本页合并后的教学草稿");
  const external = await page.request.put(
    "/api/v1/conversations/" + identity + "/draft",
    {
      headers: { Origin: "http://127.0.0.1:3218" },
      data: { content: "另一页面的第五版", revision: 4 },
    },
  );
  expect(external.ok()).toBe(true);
  const nav = page.getByRole("navigation", { name: "主导航" });
  await nav.getByRole("link", { name: "教学 Skill", exact: true }).click();
  await expect(page).toHaveURL(/\/teacher\/skills$/);
  await nav.getByRole("link", { name: "AI 助手", exact: true }).click();
  await page.getByRole("button", { name: "草稿版本验收", exact: true }).click();
  await page.getByRole("button", { name: "内容编辑", exact: true }).click();
  await expect(editor).toHaveValue("本页合并后的教学草稿");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText(/草稿已在其他页面更新/)).toBeVisible();
  await expect(editor).toHaveValue("本页合并后的教学草稿");
  await expect(page.getByText(/最新保存稿为 v5/)).toBeVisible();
  await page
    .getByRole("button", { name: "将我的修改保存为新版本", exact: true })
    .click();
  await expect(
    page.getByText("草稿已保存为 v6。", { exact: true }),
  ).toBeVisible();
  await page.locator(".draft-history summary").click();
  await page.getByRole("button", { name: "查看 v5", exact: true }).click();
  await expect(page.getByLabel("历史版本内容")).toHaveValue("另一页面的第五版");
  const current = (
    await (
      await page.request.get("/api/v1/conversations/" + identity + "/draft")
    ).json()
  ).data;
  expect(current).toEqual({ content: "本页合并后的教学草稿", revision: 6 });
  expect(
    (await (await page.request.get("/api/v1/me/quota")).json()).data,
  ).toEqual(before);
  await page.getByRole("button", { name: "草稿隔离验收", exact: true }).click();
  await expect(editor).toHaveValue("");
  await editor.fill("另一个对话的未保存内容");
  await page.getByRole("button", { name: "草稿版本验收", exact: true }).click();
  await expect(editor).toHaveValue("本页合并后的教学草稿");
  await expect(page.getByText(/已恢复本次打开应用期间/)).toHaveCount(0);
  await page.getByRole("button", { name: "草稿隔离验收", exact: true }).click();
  await expect(editor).toHaveValue("另一个对话的未保存内容");
  await page.getByRole("button", { name: "草稿版本验收", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".draft-history summary").scrollIntoViewIfNeeded();
  expect(
    await page
      .locator("#main-content")
      .evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
  ).toBe(true);
  await page.screenshot({ path: "test-results/draft-versions-mobile.png" });
});
