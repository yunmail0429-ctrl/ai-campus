import { expect, test } from "@playwright/test";

test("school administrator allocates credits and teacher sees sources and lifetime totals", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("browser-admin");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  await page.goto("/admin/credits");
  await page.getByLabel("查找教师").fill("browser-teacher");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  const account = page
    .locator("article")
    .filter({ hasText: "browser-teacher" });
  await account.getByRole("button", { name: "分配积分", exact: true }).click();
  await page.getByLabel("积分数量").fill("200");
  await page.getByLabel("变更原因").fill("九月教学额度");
  await page.getByRole("button", { name: "确认分配积分" }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(account).toContainText("余额 200");
  await account.getByRole("button", { name: "收回积分", exact: true }).click();
  await page.getByLabel("积分数量").fill("30");
  await page.getByLabel("变更原因").fill("调整剩余额度");
  await page.getByRole("button", { name: "确认收回积分" }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(account).toContainText("余额 170");
  await account.getByRole("button", { name: "积分明细", exact: true }).click();
  await expect(
    page.getByRole("dialog").getByText("九月教学额度", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "关闭明细" }).click();
  await account.getByRole("button", { name: "冻结账户" }).click();
  await page.getByLabel("变更原因").fill("检查冻结状态");
  await page.getByRole("button", { name: "确认冻结积分账户" }).click();
  await expect(account.getByText("积分已冻结", { exact: true })).toBeVisible();
  await account.getByRole("button", { name: "恢复账户" }).click();
  await page.getByLabel("变更原因").fill("检查完成");
  await page.getByRole("button", { name: "确认恢复积分账户" }).click();
  await expect(account.getByText("积分正常", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/live-admin-credits.png" });
  await page.getByRole("button", { name: "打开账号菜单" }).click();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("账号", { exact: true }).fill("browser-teacher");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  await page.goto("/teacher/settings");
  await page.getByRole("button", { name: "我的积分", exact: true }).click();
  const overview = page.getByRole("region", { name: "我的积分总览" });
  for (const [label, value] of [
    ["可用积分", "170"],
    ["累计获得", "200"],
    ["历史总消耗", "0"],
    ["学校累计收回", "30"],
  ]) {
    await expect(
      overview
        .locator(".quota-stats > div")
        .filter({ hasText: label })
        .locator("dd"),
    ).toHaveText(value);
  }
  await expect(overview.getByText("学校分配", { exact: true })).toBeVisible();
  await expect(overview.getByText("学校收回", { exact: true })).toBeVisible();
  await expect(
    overview.getByText("九月教学额度", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "我的积分", exact: true }).click();
  await expect(page.getByText("九月教学额度", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/live-personal-credits.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByText("学校收回", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/live-personal-credits-mobile.png",
  });
});

test("teacher submission, admin publishing, account creation and audit use real backend", async ({
  page,
}) => {
  test.setTimeout(60_000);
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("browser-teacher");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  await page.goto("/admin/users");
  await expect(
    page.getByRole("heading", { name: "无权访问管理员后台" }),
  ).toBeVisible();
  await page.goto("/teacher/skills");
  await page.getByRole("button", { name: "＋ 创建教学 Skill" }).click();
  await page.getByLabel("给这个教学 Skill 起个名字").fill("可发布的教学方法");
  await page.getByLabel("学科", { exact: true }).selectOption("数学");
  await page.getByLabel("适用年级").selectOption("高一");
  await page.getByLabel("教学主题").fill("函数单调性");
  await page.getByLabel("希望学生学会什么").fill("理解函数单调性并能分析简单图像");
  await page.getByLabel("希望助手给你什么").fill("教学目标和学习活动");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  const card = page
    .locator("article")
    .filter({ has: page.getByRole("link", { name: "应用教学 Skill：可发布的教学方法" }) });
  await card.getByRole("button", { name: "分享前检查", exact: true }).click();
  await page
    .getByLabel("哪些情况需要你亲自核对，或不适合让助手直接回答？")
    .fill("仅用于教师备课，所有内容须由教师复核。");
  await page.getByLabel("高一", { exact: true }).check();
  for (let i = 1; i <= 5; i++) {
    const caseGroup = page.getByRole("group", { name: `试用问题 ${i}` });
    await caseGroup.getByLabel("老师会怎样提问？").fill(`测试课题 ${i}`);
    await caseGroup
      .getByLabel("你希望助手回答到什么程度？")
      .fill("教学目标、活动步骤、评价方式");
  }
  await page.getByRole("button", { name: "保存试用说明", exact: true }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await card.getByRole("button", { name: "提交学校审核" }).click();
  await expect(card.getByText("审核中", { exact: true })).toHaveText("审核中");
  await page.getByRole("button", { name: "打开账号菜单" }).click();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("账号", { exact: true }).fill("browser-admin");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "学校管理" })
    .click();
  await page.getByRole("link", { name: "账号管理", exact: true }).click();
  await page.getByRole("button", { name: "＋ 创建账号" }).click();
  await page.getByLabel("登录账号", { exact: true }).fill("new-teacher");
  await page.getByLabel("初始密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByLabel("姓名", { exact: true }).fill("新教师");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "创建账号", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "新教师", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "教学 Skill 审核", exact: true }).click();
  const review = page
    .locator("article")
    .filter({ has: page.getByRole("heading", { name: "可发布的教学方法" }) });
  await review.getByRole("button", { name: "查看审核资料" }).click();
  await expect(
    review.getByText("样例 5：测试课题 5", { exact: true }),
  ).toBeVisible();
  await review
    .getByLabel("审核意见")
    .fill("已人工核查五个样例、适用年级与风险边界。");
  await review.getByRole("button", { name: "审核通过并发布" }).click();
  await expect(review.getByText("已发布", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "操作记录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "共享教学 Skill", exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: "test-results/live-admin-audit.png" });
});

test("real login, profile persistence, personal Skill, draft and session revocation", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("browser-teacher");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  const loginResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/auth/login"),
  );
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  const loginResult = await loginResponse;
  expect(loginResult.status(), await loginResult.text()).toBe(200);
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  await expect(
    page.getByText("还没有教学任务，从上方输入需求开始。"),
  ).toBeVisible();
  const cookies = await page.context().cookies();
  const session = cookies.find((cookie) => cookie.name === "xflj_session");
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe("Lax");

  const nav = page.getByRole("navigation", { name: "主导航" });
  await nav.getByRole("link", { name: "个人设置", exact: true }).click();
  await page.getByLabel("姓名", { exact: true }).fill("真实持久化教师");
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByText("已保存。", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("姓名", { exact: true })).toHaveValue(
    "真实持久化教师",
  );

  await nav.getByRole("link", { name: "教学 Skill", exact: true }).click();
  await page.getByRole("button", { name: "＋ 创建教学 Skill" }).click();
  await page.getByLabel("给这个教学 Skill 起个名字").fill("我的函数教学");
  await page.getByLabel("学科", { exact: true }).selectOption("数学");
  await page.getByLabel("教学主题").fill("函数单调性");
  await page.getByLabel("希望学生学会什么").fill("理解单调性并分析图像");
  await page.getByLabel("希望助手给你什么").fill("教学目标和练习");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByRole("link", { name: "应用教学 Skill：我的函数教学" })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "我的助手", exact: true }).click();
  await expect(page.getByRole("link", { name: "应用教学 Skill：我的函数教学" })).toBeVisible();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByLabel("教学主题")).toHaveValue("函数单调性");
  await page.getByRole("button", { name: "关闭", exact: true }).click();

  await nav.getByRole("link", { name: "AI 助手", exact: true }).click();
  await page.getByLabel("发送消息").fill("函数单调性教案");
  await page.getByRole("button", { name: "发送 ↑" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "尚未配置对话模型",
  );
  await page.getByRole("button", { name: "内容编辑", exact: true }).click();
  await page.getByLabel("教学内容编辑").fill("可独立保存的真实草稿");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(
    page.getByText("草稿已保存为 v1。", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await page
    .getByRole("button", { name: "函数单调性教案", exact: true })
    .click();
  await page.getByRole("button", { name: "内容编辑", exact: true }).click();
  await expect(page.getByLabel("教学内容编辑")).toHaveValue(
    "可独立保存的真实草稿",
  );
  await page.screenshot({ path: "test-results/live-conversation.png" });

  await page.getByRole("button", { name: "打开账号菜单" }).click();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.context().addCookies([session!]);
  await page.goto("/teacher/workspace");
  await expect(page).toHaveURL(/\/login$/);
  expect(errors).toEqual([]);
});

test("model administration and streamed credit settlement use the local supplier protocol fixture", async ({
  page,
}) => {
  test.setTimeout(60_000);
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("browser-admin");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  await page.goto("/admin/models");
  await page.getByRole("button", { name: "＋ 添加模型" }).click();
  await page.getByLabel("显示名称", { exact: true }).fill("教学协议联调");
  await page
    .getByRole("combobox", { name: "供应商", exact: true })
    .selectOption("protocol-test");
  await page.getByLabel("模型标识", { exact: true }).fill("test-chat");
  await page.getByLabel("上下文上限（Token）", { exact: true }).fill("8192");
  await page.getByLabel("单次最大输出（Token）", { exact: true }).fill("1024");
  await page
    .getByLabel("每百万输入 Token 的积分", { exact: true })
    .fill("1000");
  await page
    .getByLabel("每百万输出 Token 的积分", { exact: true })
    .fill("2000");
  await page.getByLabel("启用模型", { exact: true }).check();
  await page.getByRole("button", { name: "保存模型", exact: true }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  const model = page
    .locator("article")
    .filter({ has: page.getByRole("heading", { name: "教学协议联调" }) });
  await model.getByRole("button", { name: "检查连接", exact: true }).click();
  await expect(model.getByRole("status")).toContainText("连接可用");
  await model.getByRole("button", { name: "设为默认", exact: true }).click();
  await expect(model.getByText("默认对话模型", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/live-model-settings.png" });
  await page.getByRole("button", { name: "打开账号菜单" }).click();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("账号", { exact: true }).fill("browser-teacher");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  await page.goto("/teacher/assistant");
  await page.getByLabel("发送消息", { exact: true }).fill("流式计量回归");
  await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
  await expect(
    page.getByText("教学目标：理解函数单调性。", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("本次消耗 1 积分，可在“我的积分”查看记录。", {
      exact: true,
    }),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "流式计量回归", exact: true }).click();
  await expect(
    page.getByText("教学目标：理解函数单调性。", { exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: "test-results/live-chat-usage.png" });
  await page.getByText("评价这条回答", { exact: true }).click();
  await page.getByRole("combobox", { name: "回答评分" }).selectOption("4");
  await page.getByLabel("反馈说明（选填）").fill("课堂例子很有帮助");
  await page.getByRole("button", { name: "保存评价" }).click();
  await expect(page.getByText("我的评价：4 分", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "流式计量回归", exact: true }).click();
  await page.getByText("我的评价：4 分", { exact: true }).click();
  await expect(page.getByLabel("反馈说明（选填）")).toHaveValue(
    "课堂例子很有帮助",
  );
  await page.screenshot({ path: "test-results/live-answer-feedback.png" });
  await page.getByRole("combobox", { name: "回答评分" }).selectOption("5");
  await page.getByRole("button", { name: "保存评价" }).click();
  await expect(page.getByText("我的评价：5 分", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "撤回评价" }).click();
  await expect(page.getByText("评价已撤回。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "内容编辑", exact: true }).click();
  await page.getByLabel("教学内容编辑").fill("教师自己的备注");
  await page.getByRole("button", { name: "加入教学草稿", exact: true }).click();
  await expect(page.getByLabel("教学内容编辑")).toHaveValue(
    "教师自己的备注\n\n教学目标：理解函数单调性。",
  );
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(
    page.getByText("草稿已保存为 v1。", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("导出格式", { exact: true }).selectOption("md");
  const draftDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出文件", exact: true }).click();
  expect((await draftDownload).suggestedFilename()).toBe("教学草稿.md");
  await page.goto("/teacher/settings");
  await page.getByRole("button", { name: "我的积分", exact: true }).click();
  await expect(
    page
      .locator(".quota-stats > div")
      .filter({ hasText: "历史总消耗" })
      .locator("dd"),
  ).toHaveText("1");
  await expect(page.getByText("AI 使用结算", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "打开账号菜单" }).click();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("账号", { exact: true }).fill("browser-admin");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  await page.goto("/admin/models");
  await page.getByRole("button", { name: "编辑配置", exact: true }).click();
  await page.getByLabel("模型标识", { exact: true }).fill("slow-chat");
  await page.getByRole("button", { name: "保存模型", exact: true }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByRole("button", { name: "打开账号菜单" }).click();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("账号", { exact: true }).fill("browser-teacher");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  await page.goto("/teacher/assistant");
  await page.getByLabel("发送消息", { exact: true }).fill("取消计量回归");
  await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
  await expect(page.getByText("教学目标：", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "停止生成", exact: true }).click();
  await expect(
    page.getByText("本次生成未完成，预占积分已释放。以上为中断前内容。", {
      exact: true,
    }),
  ).toBeVisible();
  await page.goto("/teacher/settings");
  await page.getByRole("button", { name: "我的积分", exact: true }).click();
  await expect(
    page
      .locator(".quota-stats > div")
      .filter({ hasText: "任务预占" })
      .locator("dd"),
  ).toHaveText("0");
  await expect(
    page
      .locator(".quota-stats > div")
      .filter({ hasText: "历史总消耗" })
      .locator("dd"),
  ).toHaveText("1");
  await expect(
    page.getByText("任务取消或失败释放", { exact: true }),
  ).toBeVisible();
});
