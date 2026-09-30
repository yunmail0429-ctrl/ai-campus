import { expect, test } from "@playwright/test";

test("saved preferences drive Skill selection and creation options while explicit choices win", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("browser-teacher");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  const response = await page.request.post("/api/v1/skills", {
    headers: { Origin: "http://127.0.0.1:3218" },
    data: {
      name: "默认探究活动",
      description: "偏好联动测试",
      prompt: "设计课堂探究活动",
    },
  });
  expect(response.ok()).toBe(true);
  const skill = (await response.json()).data;
  await page.goto("/teacher/settings");
  await page.getByRole("button", { name: "使用偏好", exact: true }).click();
  await page
    .getByRole("combobox", { name: "默认教学 Skill", exact: true })
    .selectOption(skill.id);
  await page
    .getByRole("combobox", { name: "默认创作类型", exact: true })
    .selectOption("PPT");
  await page.getByLabel("PPT 默认页数").fill("16");
  await page.getByLabel("默认创作风格").fill("课堂探究");
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByText("已保存。", { exact: true })).toBeVisible();
  await page.goto("/teacher/workspace");
  await expect(page.getByRole("combobox", { name: "选择 Skill" })).toHaveValue(
    skill.id,
  );
  await page.getByRole("combobox", { name: "选择 Skill" }).selectOption("");
  await page.getByLabel("教学需求", { exact: true }).fill("通用助手临时选择");
  await page.getByRole("button", { name: "开始准备 →" }).click();
  await expect(page.getByRole("combobox", { name: "选择 Skill" })).toHaveValue(
    "",
  );
  await page.goto("/teacher/assistant");
  await expect(page.getByRole("combobox", { name: "选择 Skill" })).toHaveValue(
    skill.id,
  );
  await page.goto("/teacher/assistant?skill=");
  await expect(page.getByRole("combobox", { name: "选择 Skill" })).toHaveValue(
    "",
  );
  await page.goto("/teacher/create");
  await expect(
    page.locator(".mode-tabs").getByRole("button", { name: /^PPT/ }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("PPT 页数")).toHaveValue("16");
  await expect(page.getByLabel("创作风格", { exact: true })).toHaveValue(
    "课堂探究",
  );
  await page.getByLabel("PPT 页数").fill("12");
  await page.getByLabel("创作需求").fill("函数教学演示");
  await page.route("**/api/v1/resource-tasks/capabilities", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const ppt = body.data.find((item: { kind: string }) => item.kind === "ppt");
    ppt.available = true;
    ppt.reason = "";
    await route.fulfill({ response, json: body });
  });
  await page.reload();
  await expect(page.getByRole("button", { name: "开始创作", exact: true })).toBeEnabled();
  await page.getByLabel("PPT 页数").fill("12");
  await page.getByLabel("创作需求").fill("函数教学演示");
  const submitted = page.waitForRequest(
    (request) =>
      request.url().endsWith("/resource-tasks") && request.method() === "POST",
  );
  await page.getByRole("button", { name: "开始创作", exact: true }).click();
  expect((await submitted).postDataJSON().options).toEqual({
    pages: 12,
    style: "课堂探究",
  });
  await page.unroute("**/api/v1/resource-tasks/capabilities");
  // The media backend is still unavailable: this checks the real submitted options, not generation.
  await page.goto("/teacher/create?mode=图片");
  await expect(
    page.locator(".mode-tabs").getByRole("button", { name: /^图片/ }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("PPT 页数")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.goto("/teacher/assistant?skill=unavailable-skill");
  await expect(
    page.getByText("所选 Skill 已不可用，请重新选择。", { exact: true }),
  ).toBeVisible();
  await page.getByRole("combobox", { name: "选择 Skill" }).selectOption("");
  await page.getByLabel("发送消息", { exact: true }).fill("重新选择通用助手");
  await expect(
    page.getByRole("button", { name: "发送 ↑", exact: true }),
  ).toBeEnabled();
  let resume!: () => void;
  const delayed = new Promise<void>((resolve) => {
    resume = resolve;
  });
  await page.route("**/api/v1/me/preferences", async (route) => {
    await delayed;
    await route.continue();
  });
  try {
    await page.goto("/teacher/workspace");
    await page
      .getByRole("combobox", { name: "选择 Skill" })
      .selectOption(skill.id);
    await page.getByRole("combobox", { name: "选择 Skill" }).selectOption("");
    resume();
    await page
      .getByLabel("教学需求", { exact: true })
      .fill("等待偏好期间的临时选择");
    await expect(
      page.getByRole("button", { name: "开始准备 →" }),
    ).toBeEnabled();
    await expect(
      page.getByRole("combobox", { name: "选择 Skill" }),
    ).toHaveValue("");
  } finally {
    resume();
  }
});
