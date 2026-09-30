import { expect, test, type Page } from "@playwright/test";
import { buildTeachingSkill } from "../src/lib/teaching-skill";
// Isolated HTTP fixtures validate React behavior only, never backend acceptance.
async function fixtures(page: Page) {
  let loggedIn = false;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const ok = (data: unknown) =>
      route.fulfill({
        json: { code: "OK", message: "success", data, requestId: "fixture" },
      });
    if (path === "/api/v1/auth/login") {
      const body = route.request().postDataJSON();
      if (body.password !== "valid-test-password")
        return route.fulfill({
          status: 401,
          json: {
            code: "INVALID_CREDENTIALS",
            message: "账号或密码错误",
            requestId: "fixture",
          },
        });
      loggedIn = true;
      return ok({});
    }
    if (!loggedIn)
      return route.fulfill({
        status: 401,
        json: {
          code: "UNAUTHENTICATED",
          message: "请先登录",
          requestId: "fixture",
        },
      });
    if (path === "/api/v1/auth/logout") {
      loggedIn = false;
      return ok({});
    }
    if (path === "/api/v1/me")
      return ok({
        id: "user-1",
        name: "李老师",
        username: "teacher",
        subject: "高中语文",
        grade: "高一",
        roles: ["teacher"],
      });
    if (path === "/api/v1/me/quota")
      return ok({
        balance: 8420,
        available: 8420,
        reserved: 0,
        frozen: false,
        totalAllocated: 8420,
        totalConsumed: 0,
        totalReclaimed: 0,
      });
    if (path === "/api/v1/me/quota/transactions")
      return ok({ items: [], nextCursor: null });
    if (path === "/api/v1/me/preferences")
      return ok({ skill: "", format: "PPT", pages: 10, style: "简洁教学" });
    if (path === "/api/v1/resource-tasks/capabilities")
      return ok([{ kind: "video", available: true, reason: null,
        video: { duration: 5, resolution: "720P", ratio: "16:9", estimatedCredits: 20 } }]);
    if (path === "/api/v1/skills")
      return ok({
        items: [
          {
            id: "skill-1",
            name: "课堂教学设计",
            description: "组织目标、活动与课堂评价",
            scope: "school",
            status: "published",
          },
        ],
        nextCursor: null,
      });
    if (path === "/api/v1/conversations" && route.request().method() === "POST")
      return ok({ id: "chat-1", title: "测试教学需求" });
    if (path === "/api/v1/conversations/chat-1/draft")
      return ok({ content: "", revision: 0 });
    if (
      path === "/api/v1/conversations/chat-1/messages" &&
      route.request().method() === "POST"
    )
      return route.fulfill({
        contentType: "text/event-stream",
        body: 'event: message.delta\ndata: {"delta":"课堂目标"}\n\nevent: message.completed\ndata: {"id":"message-1"}\n\n',
      });
    if (path === "/api/v1/conversations/chat-1/messages")
      return ok({
        items: [{ id: "message-1", role: "assistant", content: "课堂目标" }],
        nextCursor: null,
      });
    if (
      path === "/api/v1/resource-tasks" &&
      route.request().method() === "POST"
    )
      return route.fulfill({
        status: 503,
        json: {
          code: "MODEL_NOT_CONFIGURED",
          message: "尚未配置生成服务",
          requestId: "fixture",
        },
      });
    if (["/api/v1/conversations", "/api/v1/resource-tasks"].includes(path))
      return ok({ items: [], nextCursor: null });
    return route.fulfill({
      status: 404,
      json: {
        code: "NOT_FOUND",
        message: "测试未定义此接口",
        requestId: "fixture",
      },
    });
  });
}
async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("teacher");
  await page.getByLabel("登录密码").fill("valid-test-password");
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
}

test("sending shows teacher immediately and renders Markdown during generation and after reload", async ({ page }) => {
  await fixtures(page);
  const prompt = "请设计一节函数课";
  const markdown = "## 教学目标\n\n**理解单调性**\n\n- 观察图像\n- 讨论规律\n\n| 活动 | 时间 |\n| --- | --- |\n| 探究 | 10分钟 |\n\n```js\nconst x = 1;\n```\n\n<script>window.markdownUnsafe = true</script>";
  let releaseAnswer!: () => void;
  let releaseHistory!: () => void;
  const answerGate = new Promise<void>((resolve) => { releaseAnswer = resolve; });
  const historyGate = new Promise<void>((resolve) => { releaseHistory = resolve; });
  let answered = false;
  await page.route("**/api/v1/conversations/chat-1/messages", async (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON().content).toBe(prompt);
      await answerGate;
      answered = true;
      return route.fulfill({
        contentType: "text/event-stream",
        body: `event: message.delta\ndata: ${JSON.stringify({ delta: markdown })}\n\nevent: message.completed\ndata: {"id":"answer"}\n\n`,
      });
    }
    if (answered) await historyGate;
    return route.fulfill({ json: { code: "OK", data: { items: answered ? [
      { id: "question", role: "user", content: prompt, status: "completed" },
      { id: "answer", role: "assistant", content: markdown, status: "completed" },
    ] : [], nextCursor: null } } });
  });
  await login(page);
  await page.goto("/teacher/assistant");
  await page.getByLabel("发送消息").fill(prompt);
  await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
  await expect(page.getByLabel("发送消息")).toHaveValue("");
  await expect(page.locator(".chat-message.user .message-body")).toHaveText(prompt);
  await expect(page.getByText("正在思考…", { exact: true })).toBeVisible();
  releaseAnswer();
  await expect(page.locator(".message-markdown strong")).toHaveText("理解单调性");
  await expect(page.getByRole("button", { name: "生成中…", exact: true })).toBeVisible();
  releaseHistory();
  await expect(page.getByRole("button", { name: "发送 ↑", exact: true })).toBeVisible();
  await expect(page.locator(".chat-message.user")).toHaveCount(1);
  await expect(page.locator(".chat-message.assistant")).toHaveCount(1);
  await page.goto("/teacher/assistant?id=chat-1");
  await expect(page.locator(".chat-message.user .message-body")).toHaveText(prompt);
  await expect(page.locator(".message-markdown strong")).toHaveText("理解单调性");
  await expect(page.locator(".message-markdown h2")).toHaveText("教学目标");
  await expect(page.locator(".message-markdown li")).toHaveCount(2);
  await expect(page.locator(".message-markdown table")).toBeVisible();
  await expect(page.locator(".message-markdown pre code")).toHaveText("const x = 1;\n");
  await expect(page.locator(".message-markdown script")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("rejected message restores the composer and removes the optimistic bubble", async ({ page }) => {
  await fixtures(page);
  await page.route("**/api/v1/conversations/chat-1/messages", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    return route.fulfill({ status: 503, json: { code: "MODEL_NOT_CONFIGURED", message: "模型未配置" } });
  });
  await login(page);
  await page.goto("/teacher/assistant");
  await page.getByLabel("发送消息").fill("不能丢失的教学需求");
  await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toHaveText("模型未配置");
  await expect(page.getByLabel("发送消息")).toHaveValue("不能丢失的教学需求");
  await expect(page.locator(".chat-message.user")).toHaveCount(0);
});

test("selected assistant attachment is visible and can be removed", async ({ page }) => {
  await fixtures(page);
  await login(page);
  await page.goto("/teacher/assistant");
  const attachmentButton = page.locator(".attachment-button");
  const attachmentHeight = (await attachmentButton.boundingBox())!.height;
  const assistantHeight = (await page.locator(".chat-input-tools select").boundingBox())!.height;
  expect(Math.abs(attachmentHeight - assistantHeight)).toBeLessThan(1);
  const beforeHover = await attachmentButton.evaluate((element) => getComputedStyle(element).backgroundColor);
  await attachmentButton.hover();
  await expect.poll(() => attachmentButton.evaluate((element) => getComputedStyle(element).backgroundColor))
    .not.toBe(beforeHover);
  await page.locator('.chat-input input[type="file"]').setInputFiles({
    name: "课堂资料.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n"),
  });
  await expect(page.locator(".attachment-button")).toHaveClass(/is-selected/);
  await expect(page.locator(".attachment-chip-name")).toHaveText("课堂资料.pdf");
  await expect(page.getByRole("button", { name: "发送 ↑" })).toBeEnabled();
  await page.getByRole("button", { name: "移除附件 课堂资料.pdf" }).click();
  await expect(page.locator(".attachment-button")).not.toHaveClass(/is-selected/);
  await expect(page.locator(".attachment-chip")).toHaveCount(0);
});

test("available credits gives a brief press response without changing color", async ({ page }) => {
  await fixtures(page);
  await login(page);
  await page.goto("/teacher/create");
  const credit = page.getByRole("button", { name: /可用积分/ });
  const originalColor = await credit.evaluate((element) => getComputedStyle(element).backgroundColor);
  await credit.click();
  await expect(credit).toHaveClass(/is-pressed/);
  await expect(credit).not.toHaveClass(/is-pressed/);
  await expect(credit).toHaveCSS("background-color", originalColor);
});

test("workspace rows open their editors and prompt uses a single focus surface", async ({ page }) => {
  await fixtures(page);
  await page.route("**/api/v1/conversations?limit=5", (route) => route.fulfill({
    json: { code: "OK", data: { items: [
      { id: "chat-1", title: "函数课教学设计", updatedAt: "2026-09-23T01:00:00Z" },
    ], nextCursor: null } },
  }));
  await login(page);
  await page.getByLabel("教学需求").focus();
  await expect(page.getByLabel("教学需求")).toHaveCSS("outline-style", "none");
  await page.getByRole("link", { name: "继续编辑 函数课教学设计" }).click();
  await expect(page).toHaveURL(/\/teacher\/assistant\?id=chat-1/);
  await page.goto("/teacher/workspace");
  await page.getByRole("link", { name: "使用 课堂教学设计 开始编辑" }).click();
  await expect(page).toHaveURL(/\/teacher\/assistant\?skill=skill-1/);
});

test("workspace material button responds and selected file is visible", async ({ page }) => {
  await fixtures(page);
  await login(page);
  const upload = page.locator(".workspace-attachment-button");
  const initial = await upload.evaluate((el) => getComputedStyle(el).backgroundColor);
  await upload.hover();
  await expect.poll(() => upload.evaluate((el) => getComputedStyle(el).backgroundColor)).not.toBe(initial);
  await page.getByLabel("添加资料").setInputFiles({
    name: "课堂参考.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n"),
  });
  await expect(upload).toHaveClass(/is-selected/);
  await expect(page.locator(".workspace-file-chip")).toContainText("课堂参考.pdf");
  await page.getByRole("button", { name: "移除资料 课堂参考.pdf" }).click();
  await expect(upload).not.toHaveClass(/is-selected/);
});

test("knowledge library selection has hover and selected feedback", async ({ page }) => {
  await fixtures(page);
  await page.route("**/api/v1/knowledge-bases?limit=50", (route) => route.fulfill({
    json: { code: "OK", data: { items: [{ id: "base-1", name: "高一语文资料",
      scope: "private", canManage: true }], nextCursor: null } },
  }));
  await login(page);
  await page.goto("/teacher/knowledge");
  const library = page.getByRole("button", { name: /高一语文资料/ });
  const before = await library.evaluate((el) => getComputedStyle(el).backgroundColor);
  await library.hover();
  await expect.poll(() => library.evaluate((el) => getComputedStyle(el).backgroundColor)).not.toBe(before);
  await library.click();
  await expect(library).toHaveAttribute("aria-pressed", "true");
});

test("teachers find assistants by task, search and saved favorites", async ({ page }) => {
  await fixtures(page);
  await page.route(/\/api\/v1\/skills\?scope=school(?:&|$)/, (route) => {
    const next = new URL(route.request().url()).searchParams.has("cursor");
    return route.fulfill({ json: { code: "OK", data: {
    items: next ? [
      { id: "misc", name: "特色教学帮手", description: "支持个性化课堂想法", scope: "school", status: "published" },
    ] : [
      { id: "plan", name: "课堂教学设计", description: "帮助准备教案和课堂目标", scope: "school", status: "published" },
      { id: "quiz", name: "物理随堂测验", description: "生成课堂练习题", scope: "school", status: "published" },
    ], nextCursor: next ? null : "next",
  } } });
  });
  await login(page);
  await page.goto("/teacher/skills");
  await expect(page.locator(".skill-library-card")).toHaveCount(3);
  await page.getByRole("combobox", { name: "学科" }).selectOption("物理");
  await expect(page.locator(".skill-library-card")).toHaveCount(1);
  await expect(page.locator(".skill-library-card")).toContainText("物理随堂测验");
  await page.getByRole("combobox", { name: "学科" }).selectOption("all");
  await page.getByRole("combobox", { name: "学科" }).selectOption("综合");
  await expect(page.locator(".skill-library-card")).toHaveCount(2);
  await page.getByRole("combobox", { name: "学科" }).selectOption("all");
  await page.getByRole("textbox", { name: "搜索教学 Skill" }).fill("特色");
  await expect(page.locator(".skill-library-card")).toHaveCount(1);
  await expect(page.locator(".skill-library-card")).toContainText("特色教学帮手");
  await page.getByRole("textbox", { name: "搜索教学 Skill" }).fill("");
  if (process.env.SKILL_PREVIEW) await page.screenshot({ path: "local-data/skills-discovery-preview.png", fullPage: true });
  await page.getByRole("combobox", { name: "类别" }).selectOption("practice");
  await expect(page.locator(".skill-library-card")).toHaveCount(1);
  await expect(page.locator(".skill-library-card")).toContainText("物理随堂测验");
  await page.getByRole("combobox", { name: "类别" }).selectOption("all");
  await page.getByRole("textbox", { name: "搜索教学 Skill" }).fill("物理");
  await expect(page.locator(".skill-library-card")).toHaveCount(1);
  await page.getByRole("textbox", { name: "搜索教学 Skill" }).fill("");
  await page.getByRole("button", { name: "设为常用 物理随堂测验" }).click();
  await page.getByRole("button", { name: "只看常用" }).click();
  await expect(page.locator(".skill-library-card")).toHaveCount(1);
  await page.reload();
  await page.getByRole("button", { name: "只看常用" }).click();
  await expect(page.locator(".skill-library-card")).toHaveCount(1);
  await expect(page.locator(".skill-library-card")).toContainText("物理随堂测验");
  if (process.env.SKILL_PREVIEW) {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: "local-data/skills-discovery-mobile.png", fullPage: true });
  }
});

test("teachers create a Skill from classroom terms and preview its generated template", async ({ page }) => {
  await fixtures(page);
  let saved: Record<string, string> | undefined;
  await page.route("**/api/v1/skills", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    saved = route.request().postDataJSON();
    return route.fulfill({ json: { code: "OK", data: {
      id: "new-skill", ...saved, status: "draft", scope: "private", version: 1,
    } } });
  });
  await login(page);
  await page.goto("/teacher/skills");
  await page.getByRole("button", { name: "＋ 创建教学 Skill" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("给这个教学 Skill 起个名字").fill("议论文写作助手");
  const subjectSelect = dialog.locator(".skill-teacher-fields select").first();
  await subjectSelect.selectOption("语文");
  const subjectHeight = (await subjectSelect.boundingBox())!.height;
  const gradeHeight = (await dialog.getByLabel("适用年级").boundingBox())!.height;
  expect(Math.abs(subjectHeight - gradeHeight)).toBeLessThan(1);
  await dialog.getByLabel("教学主题").fill("议论文论证方法");
  await dialog.getByLabel("希望学生学会什么").fill("能区分事实论据与道理论据");
  await dialog.getByLabel("希望助手给你什么").fill("课堂练习与评价建议");
  if (process.env.SKILL_FORM_PREVIEW) {
    await page.screenshot({ path: "local-data/skill-form-desktop.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(dialog.getByRole("button", { name: "保存草稿" })).toBeInViewport();
    await page.screenshot({ path: "local-data/skill-form-mobile.png" });
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  await expect(dialog.getByRole("heading", { name: "议论文写作助手" })).toBeVisible();
  await expect(dialog.getByText("请围绕议论文论证方法设计一节课的导入活动。")).toBeVisible();
  await dialog.getByText("查看自动生成的详细设置").click();
  await expect(dialog.locator(".skill-template-preview pre")).toContainText("【教学主题】议论文论证方法");
  await dialog.getByRole("button", { name: "保存草稿" }).click();
  await expect.poll(() => saved?.prompt).toContain("【希望学生学会什么】能区分事实论据与道理论据");
  expect(saved?.description).toContain("高一语文");
});

test("older teaching assistant drafts remain editable", async ({ page }) => {
  await fixtures(page);
  let saved: Record<string, string> | undefined;
  await page.route("**/api/v1/skills?scope=mine", (route) => route.fulfill({ json: { code: "OK", data: {
    items: [{ id: "legacy", name: "旧助手", description: "旧用途", prompt: "旧教学要求", status: "draft", scope: "private" }], nextCursor: null,
  } } }));
  await page.route("**/api/v1/skills/legacy", (route) => {
    if (route.request().method() === "PATCH") {
      saved = route.request().postDataJSON();
      return route.fulfill({ json: { code: "OK", data: { id: "legacy", ...saved } } });
    }
    return route.fulfill({ json: { code: "OK", data: {
      id: "legacy", name: "旧助手", description: "旧用途", prompt: "旧教学要求", status: "draft", scope: "private",
    } } });
  });
  await login(page);
  await page.goto("/teacher/skills");
  await page.getByRole("button", { name: "我的助手" }).click();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "编辑教学 Skill" });
  await expect(dialog.getByLabel("已有教学要求")).toHaveValue("旧教学要求");
  await dialog.getByLabel("用途说明").fill("新的用途说明");
  await dialog.getByRole("button", { name: "保存草稿" }).click();
  await expect.poll(() => saved?.description).toBe("新的用途说明");
  expect(saved?.prompt).toBe("旧教学要求");
});

test("teachers can prepare five editable classroom examples before sharing a Skill", async ({ page }) => {
  await fixtures(page);
  const prompt = buildTeachingSkill({ subject: "语文", grade: "高一", topic: "议论文论证",
    goal: "能区分事实论据", result: "课堂练习", notes: "" });
  let readiness: { grades: string[]; tests: { input: string; expectedOutput: string }[] } | undefined;
  await page.route("**/api/v1/skills?scope=mine", (route) => route.fulfill({ json: { code: "OK", data: {
    items: [{ id: "teacher-skill", name: "议论文助手", description: "帮助备课",
      prompt, scope: "private", status: "draft", version: 1 }], nextCursor: null,
  } } }));
  await page.route("**/api/v1/skills/teacher-skill/readiness", (route) => {
    if (route.request().method() === "PUT") {
      readiness = route.request().postDataJSON();
      return route.fulfill({ json: { code: "OK", data: {} } });
    }
    return route.fulfill({ json: { code: "OK", data: {
      riskBoundary: "", grades: [], tests: [],
    } } });
  });
  await login(page);
  await page.goto("/teacher/skills");
  await page.getByRole("button", { name: "我的助手" }).click();
  await page.getByRole("button", { name: "分享前检查" }).click();
  const dialog = page.getByRole("dialog", { name: /分享前检查/ });
  await dialog.getByRole("button", { name: "生成课堂问题示例" }).click();
  await expect(dialog.locator(".sample-case")).toHaveCount(5);
  await expect(dialog.locator(".sample-case textarea").first()).toHaveValue(/议论文论证/);
  await expect(dialog.getByLabel("高一")).toBeChecked();
  await dialog.getByLabel("哪些情况需要你亲自核对").fill("教材原文需要核对");
  await dialog.getByRole("button", { name: "保存试用说明" }).click();
  await expect.poll(() => readiness?.tests.length).toBe(5);
  expect(readiness?.grades).toEqual(["高一"]);
});

test("school admins land on a visual overview and can read teacher credit changes", async ({ page }) => {
  await fixtures(page);
  await page.route("**/api/v1/me", (route) => route.fulfill({ json: { code: "OK", data: {
    id: "admin-1", name: "管理员", username: "admin", subject: "数学", roles: ["admin"],
  } } }));
  await page.route("**/api/v1/admin/statistics", (route) => route.fulfill({ json: { code: "OK", data: {
    start: "2026-09-01", end: "2026-09-23", resourceOutputs: {},
    summary: { activeAccounts: 3, calls: 5, completed: 4, failed: 1, cancelled: 0,
      running: 0, skillCalls: 2, inputTokens: 120, outputTokens: 80,
      consumedCredits: 25, embeddingCalls: 0, embeddingTokens: 0,
      embeddingUncertain: 0, feedbackCount: 0, feedbackAverage: null,
      resourceOutputs: 1, successRate: .8 },
    daily: [{ date: "2026-09-23", logins: 3, calls: 5, completed: 4, failed: 1,
      cancelled: 0, running: 0, skillCalls: 2, inputTokens: 120,
      outputTokens: 80, consumedCredits: 25, embeddingCalls: 0,
      embeddingTokens: 0, embeddingUncertain: 0, feedbackCount: 0,
      resourceOutputs: 1 }],
  } } }));
  await page.route("**/api/v1/admin/quota/summary", (route) => route.fulfill({
    json: { code: "OK", data: { available: 900, reserved: 30, consumed: 25, accounts: 3 } },
  }));
  await page.route("**/api/v1/admin/quota/transactions?**", (route) => route.fulfill({
    json: { code: "OK", data: { items: [{ id: "transaction-1", teacherName: "张老师",
      teacherUsername: "zhang", source: "AI 使用结算", kind: "settle",
      balanceDelta: -25, reservedDelta: -25, reason: "制作教学课件",
      createdAt: "2026-09-23T01:00:00Z" }], nextCursor: null } },
  }));
  await page.route("**/api/v1/admin/quota/accounts?**", (route) => route.fulfill({
    json: { code: "OK", data: { items: [], nextCursor: null } },
  }));
  await login(page);
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/admin\/statistics$/);
  await expect(page.getByLabel("每日 AI 使用趋势")).toContainText("5");
  await page.getByRole("link", { name: "积分管理" }).click();
  await expect(page.getByRole("region", { name: "最近积分变化明细" })).toContainText("张老师");
  await expect(page.getByRole("region", { name: "最近积分变化明细" })).toContainText("-25");
});

test("visible teacher actions communicate hover across core pages", async ({ page }) => {
  await fixtures(page);
  await login(page);
  const missing: string[] = [];
  for (const path of ["workspace", "assistant", "skills", "create", "knowledge", "settings"]) {
    await page.goto(`/teacher/${path}`);
    await expect(page.locator("#main-content")).toBeVisible();
    const actions = page.locator(".dashboard button:visible:not([disabled]), .dashboard .workspace-attachment-button:visible");
    const count = await actions.count();
    for (let i = 0; i < count; i++) {
      const action = actions.nth(i);
      if (!await action.isVisible()) continue;
      const label = (await action.getAttribute("aria-label")) || (await action.innerText()).trim().slice(0, 30);
      const style = () => action.evaluate((node) => {
        const css = getComputedStyle(node);
        return [css.backgroundColor, css.borderColor, css.boxShadow, css.transform, css.color, css.filter].join("|");
      });
      const before = await style();
      await action.hover();
      await page.waitForTimeout(190);
      if (await style() === before) missing.push(`${path}: ${label}`);
    }
  }
  expect(missing).toEqual([]);
});

test("visible admin actions communicate hover across management pages", async ({ page }) => {
  test.setTimeout(60_000);
  await fixtures(page);
  await page.route("**/api/v1/me", (route) => route.fulfill({ json: { code: "OK", data: {
    id: "admin-1", name: "管理员", username: "admin", subject: "数学", roles: ["admin"],
  } } }));
  await page.route("**/api/v1/admin/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    const data = path.endsWith("/quota/summary")
      ? { available: 900, reserved: 30, consumed: 25, accounts: 3 }
      : { items: [], nextCursor: null };
    return route.fulfill({ json: { code: "OK", data } });
  });
  await login(page);
  const missing: string[] = [];
  for (const path of ["statistics", "credits", "users", "skills", "knowledge", "models", "audit-logs"]) {
    await page.goto(`/admin/${path}`);
    await expect(page.locator("#main-content")).toBeVisible();
    const actions = page.locator("#main-content button:visible:not([disabled]), #main-content .admin-tabs a:visible");
    const count = await actions.count();
    for (let i = 0; i < count; i++) {
      const action = actions.nth(i);
      if (!await action.isVisible()) continue;
      const label = (await action.getAttribute("aria-label")) || (await action.innerText()).trim().slice(0, 30);
      const style = () => action.evaluate((node) => {
        const css = getComputedStyle(node);
        return [css.backgroundColor, css.borderColor, css.boxShadow, css.transform, css.color, css.filter].join("|");
      });
      const before = await style();
      await action.hover();
      await page.waitForTimeout(190);
      if (await style() === before) missing.push(`${path}: ${label}`);
    }
  }
  expect(missing).toEqual([]);
});

test("login actions communicate hover", async ({ page }) => {
  await fixtures(page);
  await page.goto("/login");
  for (const name of ["显示密码", "忘记密码？", "登 录 系 统"]) {
    const button = page.getByRole("button", { name });
    await expect(button).toBeEnabled();
    const style = () => button.evaluate((node) => {
      const css = getComputedStyle(node);
      return [css.color, css.backgroundColor, css.boxShadow, css.transform].join("|");
    });
    const before = await style();
    await button.hover();
    await page.waitForTimeout(200);
    expect(await style()).not.toBe(before);
  }
});

test("conversation history loads and opens older conversations", async ({
  page,
}) => {
  await fixtures(page);
  await page.route("**/api/v1/conversations**", async (route) => {
    const url = new URL(route.request().url());
    const ok = (data: unknown) =>
      route.fulfill({ json: { code: "OK", data, requestId: "history" } });
    if (url.pathname === "/api/v1/conversations") {
      return ok(
        url.searchParams.has("cursor")
          ? { items: [{ id: "old", title: "较早教学对话" }], nextCursor: null }
          : {
              items: [{ id: "recent", title: "最近教学对话" }],
              nextCursor: "older/+=",
            },
      );
    }
    if (url.pathname.endsWith("/old/draft"))
      return ok({ content: "旧草稿", revision: 1 });
    if (
      url.pathname.endsWith("/old/messages") &&
      url.searchParams.has("cursor")
    )
      return ok({
        items: [
          {
            id: "earliest",
            role: "user",
            status: "completed",
            content: "最早的教学问题",
          },
        ],
        nextCursor: null,
      });
    if (url.pathname.endsWith("/old/messages"))
      return ok({
        items: [
          {
            id: "old-answer",
            role: "assistant",
            status: "completed",
            content: "较早的教学回答",
          },
        ],
        nextCursor: "messages-before",
      });
    await route.fallback();
  });
  await login(page);
  await page.goto("/teacher/assistant");
  await expect(
    page.getByRole("button", { name: "最近教学对话", exact: true }),
  ).toBeVisible();
  const request = page.waitForRequest(
    (request) =>
      new URL(request.url()).searchParams.get("cursor") === "older/+=",
  );
  await page.getByRole("button", { name: "加载更早对话" }).click();
  await request;
  const recentRow = page.locator(".history-row", { hasText: "最近教学对话" });
  const oldRow = page.locator(".history-row", { hasText: "较早教学对话" });
  await expect(oldRow).toBeVisible();
  await page.mouse.move(500, 400);
  await page.waitForTimeout(200);
  expect(await page.locator(".chat-history").evaluate((node) => getComputedStyle(node).borderRightWidth)).toBe("0px");
  const idleBackground = await oldRow.evaluate((node) => getComputedStyle(node).backgroundColor);
  expect(idleBackground).toBe("rgba(0, 0, 0, 0)");
  expect((await oldRow.boundingBox())!.y - (await recentRow.boundingBox())!.y)
    .toBeGreaterThan((await recentRow.boundingBox())!.height);
  await oldRow.hover();
  await expect.poll(() => oldRow.evaluate((node) => getComputedStyle(node).backgroundColor)).not.toBe(idleBackground);
  await expect.poll(() => oldRow.getByRole("button", { name: "重命名 较早教学对话" }).evaluate((node) => getComputedStyle(node).opacity)).toBe("1");
  await page.getByRole("button", { name: "较早教学对话", exact: true }).click();
  await expect(page.getByRole("button", { name: "较早教学对话", exact: true })).toHaveAttribute("aria-current", "true");
  expect(await oldRow.evaluate((node) => getComputedStyle(node).backgroundColor)).not.toBe(idleBackground);
  await expect(page.getByRole("button", { name: "最近教学对话", exact: true })).not.toHaveAttribute("aria-current", "true");
  await expect(
    page.getByRole("heading", { name: "较早教学对话", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("较早的教学回答", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "加载更早消息" }).click();
  await expect(page.getByText("最早的教学问题", { exact: true })).toBeVisible();
  await expect(page.locator(".message-body")).toHaveText([
    "最早的教学问题",
    "较早的教学回答",
  ]);
  await expect(page.getByRole("button", { name: "加载更早消息" })).toHaveCount(
    0,
  );
  await expect(page.getByRole("button", { name: "加载更早对话" })).toHaveCount(
    0,
  );
});

test("knowledge preview pages through the entire extracted document", async ({
  page,
}) => {
  await fixtures(page);
  await page.route("**/api/v1/knowledge-bases**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const data = path.endsWith("/documents")
      ? {
          items: [
            {
              id: "doc-preview",
              filename: "完整资料.txt",
              sizeBytes: 25000,
              status: "ready",
              tags: [],
              chunkCount: 21,
              errorCode: null,
              errorMessage: null,
              updatedAt: "2026-09-22T01:00:00Z",
              canManage: true,
            },
          ],
          nextCursor: null,
        }
      : {
          items: [
            {
              id: "base-preview",
              name: "完整资料库",
              scope: "private",
              canManage: true,
            },
          ],
          nextCursor: null,
        };
    return route.fulfill({ json: { code: "OK", requestId: "fixture", data } });
  });
  await page.route("**/api/v1/documents/doc-preview/pages**", (route) => route.fulfill({
    json: { code: "OK", requestId: "fixture", data: { items: [], nextCursor: null } },
  }));
  await page.route(
    "**/api/v1/documents/doc-preview/chunks**",
    async (route) => {
      const offset = Number(
        new URL(route.request().url()).searchParams.get("offset") || "0",
      );
      expect([0, 20]).toContain(offset);
      return route.fulfill({
        json: {
          code: "OK",
          requestId: "fixture",
          data: {
            items: Array.from({ length: offset === 0 ? 20 : 1 }, (_, n) => ({
              id: "chunk-" + (offset + n),
              position: offset + n,
              location: "正文",
              content: "完整文档第" + (offset + n + 1) + "段",
            })),
            nextCursor: offset === 0 ? "20" : null,
          },
        },
      });
    },
  );
  await login(page);
  await page.goto("/teacher/knowledge");
  await page.getByRole("button", { name: "完整资料库 仅自己可见" }).click();
  await page.getByRole("button", { name: "查看解析内容" }).click();
  const preview = page.getByRole("region", { name: "完整资料.txt解析预览" });
  await expect(
    preview.getByText("完整文档第1段", { exact: true }),
  ).toBeVisible();
  await preview.getByRole("button", { name: "下一页分段" }).click();
  await expect(
    preview.getByText("完整文档第21段", { exact: true }),
  ).toBeVisible();
  await expect(preview.getByRole("button", { name: "下一页分段" })).toHaveCount(
    0,
  );
  await preview.getByRole("button", { name: "上一页分段" }).click();
  await expect(
    preview.getByText("完整文档第1段", { exact: true }),
  ).toBeVisible();
  await preview.getByRole("button", { name: "下一页分段" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await preview.scrollIntoViewIfNeeded();
  expect(
    await page
      .locator("#main-content")
      .evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
  ).toBe(true);
  await page.screenshot({ path: "test-results/knowledge-preview-mobile.png" });
});

test("creation history paginates without duplicates and refreshes credits when older work finishes", async ({
  page,
}) => {
  await fixtures(page);
  let finished = false;
  let quotaReads = 0;
  await page.route("**/api/v1/me/quota", async (route) => {
    quotaReads++;
    return route.fallback();
  });
  const shared = {
    kind: "ppt",
    payer: "teacher",
    credits: 0,
    maxCredits: 10,
    createdAt: "2026-09-22T01:00:00Z",
  };
  const newest = {
    ...shared,
    id: "new",
    prompt: "最新课件",
    status: "succeeded",
    credits: 7,
  };
  const cancelled = {
    ...shared,
    id: "cancelled",
    prompt: "已取消课件",
    status: "cancelled",
    cancelRequested: true,
  };
  await page.route("**/api/v1/resource-tasks**", async (route) => {
    const url = new URL(route.request().url());
    if (
      url.pathname !== "/api/v1/resource-tasks" ||
      route.request().method() !== "GET"
    )
      return route.fallback();
    const earlier = url.searchParams.has("cursor");
    if (earlier) expect(url.searchParams.get("cursor")).toBe("older+/=");
    return route.fulfill({
      json: {
        code: "OK",
        requestId: "fixture",
        data: {
          items: earlier
            ? [
                { ...newest, status: "running", credits: 0 },
                cancelled,
                {
                  ...shared,
                  id: "old",
                  prompt: "早前课件",
                  status: finished ? "succeeded" : "running",
                  progress: 50,
                  credits: finished ? 6 : 0,
                },
              ]
            : [newest, cancelled],
          nextCursor: earlier ? null : "older+/=",
        },
      },
    });
  });
  await login(page);
  await page.goto("/teacher/create");
  await expect(page.locator(".task-item")).toHaveCount(2);
  await expect(page.getByText(/实际消耗 7 积分/)).toBeVisible();
  await expect(page.getByText(/预占已释放/)).toBeVisible();
  await expect(page.getByText(/正在请求取消/)).toHaveCount(0);
  await page.getByRole("button", { name: "加载更早的任务" }).click();
  await expect(page.locator(".task-item")).toHaveCount(3);
  await expect(page.getByText(/实际消耗 7 积分/)).toBeVisible();
  await expect(page.getByText(/预占 10 积分/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "加载更早的任务" }),
  ).toHaveCount(0);
  const readsBeforeCompletion = quotaReads;
  finished = true;
  await expect(page.getByText(/实际消耗 6 积分/)).toBeVisible({
    timeout: 10000,
  });
  await expect.poll(() => quotaReads).toBeGreaterThan(readsBeforeCompletion);
  await expect(page.locator(".task-item")).toHaveCount(3);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByText("早前课件", { exact: true }).scrollIntoViewIfNeeded();
  expect(
    await page
      .locator("#main-content")
      .evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
  ).toBe(true);
  await page.screenshot({ path: "test-results/creation-history-mobile.png" });
});

test("uncertain media submission reuses its key and preserves creation options", async ({
  page,
}) => {
  await fixtures(page);
  const keys: string[] = [];
  await page.route("**/api/v1/resource-tasks", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    keys.push(route.request().headers()["idempotency-key"]);
    expect(route.request().postDataJSON().options).toEqual({
      pages: 10,
      style: "简洁教学",
    });
    if (keys.length === 1) return route.abort();
    return route.fulfill({
      status: 503,
      json: {
        code: "RESOURCE_NOT_CONFIGURED",
        message: "生成服务未配置",
        requestId: "fixture",
      },
    });
  });
  await login(page);
  await page.goto("/teacher/create?mode=PPT");
  await page.getByLabel("创作需求").fill("函数课堂 PPT");
  await page.getByRole("button", { name: "开始创作", exact: true }).click();
  await expect(
    page.getByText("网络连接失败，请检查网络后重试。", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "开始创作", exact: true }).click();
  await expect(page.getByText("生成服务未配置", { exact: true })).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
});

test("uncertain delivery reuses the request key until a definitive response", async ({
  page,
}) => {
  await fixtures(page);
  const keys: string[] = [];
  await page.route("**/api/v1/conversations/chat-1/messages", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    keys.push(route.request().headers()["idempotency-key"]);
    if (keys.length === 1)
      return route.fulfill({
        status: 503,
        json: {
          code: "SERVICE_UNAVAILABLE",
          message: "暂时无法确认发送结果",
          data: null,
          requestId: "fixture",
        },
      });
    return route.fallback();
  });
  await login(page);
  await page.goto("/teacher/assistant");
  await page.getByLabel("发送消息").fill("教学需求");
  await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "暂时无法确认发送结果",
  );
  await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
  await expect(page.getByLabel("发送消息")).toHaveValue("");
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
  await page.getByLabel("发送消息").fill("新的教学需求");
  await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
  await expect(page.getByLabel("发送消息")).toHaveValue("");
  expect(keys[2]).not.toBe(keys[0]);
});
test("login requires password and reports authentication failure", async ({
  page,
}) => {
  await fixtures(page);
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("teacher");
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("登录密码").fill("wrong-password");
  await page.getByRole("button", { name: "显示密码" }).click();
  await expect(page.getByLabel("登录密码")).toHaveAttribute("type", "text");
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toHaveText(
    "账号或密码错误",
  );
  await page.screenshot({
    path: "test-results/react-login.png",
    fullPage: true,
  });
});
test("React routes, manual sidebar, chat stream, creation failure and logout", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await fixtures(page);
  await login(page);
  await expect(page.locator(".tool-button")).toHaveCount(9);
  await expect(page.getByText("8,420")).toBeVisible();
  await page.screenshot({
    path: "test-results/react-workspace.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "收起导航" }).click();
  await expect(page.locator("#workspaceNav")).toHaveClass(/is-collapsed/);
  await page.locator("#workspaceNav").hover();
  await expect(page.locator("#workspaceNav")).toHaveClass(/is-collapsed/);
  await page.getByRole("button", { name: "展开导航" }).click();
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "AI 助手", exact: true })
    .click();
  await page.getByLabel("发送消息").fill("测试教学需求");
  await page.getByRole("button", { name: "发送 ↑" }).click();
  await expect(page.locator(".chat-message.assistant .message-body")).toHaveText("课堂目标");
  await page.getByRole("button", { name: "内容编辑" }).click();
  await page.getByLabel("教学内容编辑").fill("教学草稿");
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "教学 Skill", exact: true })
    .click();
  await page.getByRole("button", { name: "＋ 创建教学 Skill" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "AI 创作中心", exact: true })
    .click();
  await page.getByRole("button", { name: "视频", exact: true }).click();
  await page.getByLabel("创作需求").fill("函数课堂视频");
  await page.getByRole("button", { name: "开始创作" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toHaveText(
    "尚未配置生成服务",
  );
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "个人设置", exact: true })
    .click();
  await expect(page.getByLabel("姓名", { exact: true })).toHaveValue("李老师");
  await page.getByRole("button", { name: "打开账号菜单" }).click();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(errors).toEqual([]);
});
test("retrying video settings does not submit a filled creation form", async ({ page }) => {
  await fixtures(page);
  await login(page);
  let submissions = 0;
  await page.route("**/api/v1/resource-tasks", async route => {
    if (route.request().method() === "POST") submissions++;
    await route.fallback();
  });
  await page.route("**/api/v1/resource-tasks/capabilities", route => route.fulfill({
    status: 503,
    json: { code: "SERVICE_UNAVAILABLE", message: "配置读取失败", data: null, requestId: "fixture" },
  }));
  await page.goto("/teacher/create?mode=" + encodeURIComponent("视频"));
  await page.getByLabel("创作需求").fill("函数课堂视频");
  await expect(page.getByText("配置读取失败")).toBeVisible();
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect(page.getByText("配置读取失败")).toBeVisible();
  expect(submissions).toBe(0);
});

test("unconfigured video shows its reason and prevents submission", async ({ page }) => {
  await fixtures(page);
  await login(page);
  await page.route("**/api/v1/resource-tasks/capabilities", route => route.fulfill({
    json: { code: "OK", data: [{ kind: "video", available: false,
      reason: "视频服务未配置", video: null }], requestId: "fixture" },
  }));
  await page.goto("/teacher/create?mode=" + encodeURIComponent("视频"));
  await expect(page.getByText(/当前暂不能创建视频.*视频服务未配置/)).toBeVisible();
  await expect(page.getByRole("button", { name: "开始创作" })).toBeDisabled();
});

test("creative tools show unavailable services before teachers enter a request", async ({ page }) => {
  await fixtures(page);
  await login(page);
  await page.route("**/api/v1/resource-tasks/capabilities", (route) => route.fulfill({
    json: { code: "OK", data: [
      { kind: "image", available: false, reason: "绘图服务尚未配置", video: null },
      { kind: "ppt", available: true, reason: null, video: null },
    ] },
  }));
  await page.goto("/teacher/create?mode=" + encodeURIComponent("图片"));
  await expect(page.getByRole("button", { name: /图片.*暂不可用/ })).toBeVisible();
  await expect(page.locator(".work-status[role=status]")).toContainText("绘图服务尚未配置");
  await expect(page.getByRole("button", { name: "开始创作" })).toBeDisabled();
  await page.getByRole("button", { name: "PPT", exact: true }).click();
  await expect(page.getByRole("button", { name: "开始创作" })).toBeEnabled();
});

test("teachers download a completed creation in one click", async ({ page }) => {
  await fixtures(page);
  await login(page);
  const makeTask = (id: string, status: string, prompt: string) => ({
    id, status, prompt, kind: "ppt", payer: "teacher", maxCredits: 10,
    credits: status === "succeeded" ? 3 : 0,
    createdAt: "2026-09-23T01:00:00Z",
  });
  await page.route("**/api/v1/resource-tasks", (route) => route.fulfill({
    json: { code: "OK", data: { items: [
      makeTask("pending", "queued", "排队中的课件"),
      makeTask("ready", "succeeded", "已完成的课件"),
    ], nextCursor: null }, requestId: "fixture" },
  }));
  await page.route("**/api/v1/resource-tasks/ready/download", (route) => route.fulfill({
    status: 200,
    contentType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    headers: { "Content-Disposition": 'attachment; filename="lesson.pptx"' },
    body: "fixture",
  }));
  await page.goto("/teacher/create");
  await expect(page.getByRole("link", { name: "下载作品：排队中的课件" })).toHaveCount(0);
  const download = page.getByRole("link", { name: "下载作品：已完成的课件" });
  await expect(download).toHaveAttribute("href", "/api/v1/resource-tasks/ready/download");
  const event = page.waitForEvent("download");
  await download.click();
  expect((await event).suggestedFilename()).toBe("lesson.pptx");
});

test("mobile pages do not overflow horizontally", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixtures(page);
  await login(page);
  for (const title of [
    "AI 工作台",
    "AI 助手",
    "教学 Skill",
    "AI 创作中心",
    "个人设置",
  ]) {
    await page
      .getByRole("navigation", { name: "主导航" })
      .getByRole("link", { name: title, exact: true })
      .click();
    await expect(page.locator("#main-content")).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    expect(
      await page
        .locator("#main-content")
        .evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
    ).toBe(true);
  }
  await page.screenshot({
    path: "test-results/react-mobile.png",
    fullPage: true,
  });
});
test("workspace carries the teaching draft and file into the React assistant without putting text in URL", async ({
  page,
}) => {
  await fixtures(page);
  await login(page);
  await page.getByLabel("教学需求", { exact: true }).fill("需要整理的教学资料");
  await page.getByLabel("添加资料", { exact: true }).setInputFiles({
    name: "lesson.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("教学资料"),
  });
  await page.getByRole("button", { name: "开始准备 →" }).click();
  await expect(page).toHaveURL(/\/teacher\/assistant$/);
  await expect(page.getByLabel("发送消息")).toHaveValue("需要整理的教学资料");
  await expect(page.locator(".chat-input-note")).toContainText("lesson.txt");
});
test("unconfigured backend refuses login and protected pages", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("teacher");
  await page.getByLabel("登录密码").fill("not-a-real-password");
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "后台服务",
  );
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/teacher/workspace");
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "后台服务",
  );
  await expect(page.locator(".dashboard")).toHaveCount(0);
});


for (const status of [401, 503]) {
  test(`draft save failure ${status} preserves edits and permits retry`, async ({ page }) => {
    await fixtures(page);
    await login(page);
    await page.getByRole("navigation", { name: "主导航" })
      .getByRole("link", { name: "AI 助手", exact: true }).click();
    await page.getByLabel("发送消息").fill("测试教学需求");
    await page.getByRole("button", { name: "发送 ↑" }).click();
    await expect(page.locator(".chat-message.assistant .message-body")).toHaveText("课堂目标");
    let attempts = 0;
    await page.route("**/api/v1/conversations/chat-1/draft", async route => {
      if (route.request().method() !== "PUT") return route.fallback();
      attempts++;
      if (attempts === 1) return route.fulfill({ status, json: {
        code: status === 401 ? "UNAUTHENTICATED" : "SERVICE_UNAVAILABLE",
        message: "保存暂时失败，请重试", requestId: "draft-failure"
      }});
      expect(route.request().postDataJSON().content).toBe("未保存的重要教学修改");
      return route.fulfill({ json: { code: "OK", data: {
        content: "未保存的重要教学修改", revision: 1
      }}});
    });
    await page.getByRole("button", { name: "内容编辑" }).click();
    const editor = page.getByLabel("教学内容编辑");
    await editor.fill("未保存的重要教学修改");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(page.getByText("保存暂时失败，请重试", { exact: true })).toBeVisible();
    await expect(editor).toHaveValue("未保存的重要教学修改");
    expect(attempts).toBe(1);
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(page.getByText("草稿已保存为 v1。", { exact: true })).toBeVisible();
    await expect(editor).toHaveValue("未保存的重要教学修改");
    expect(attempts).toBe(2);
  });
}


test("RELEASE-017 unsaved draft survives leaving and reopening the assistant", async ({ page }) => {
  await fixtures(page);
  await page.route("**/api/v1/conversations", route => route.request().method() === "POST" ? route.fallback() : route.fulfill({ json: {
    code: "OK", data: { items: [{ id: "chat-1", title: "测试教学需求" }], nextCursor: null }
  }}));
  await login(page);
  const nav = page.getByRole("navigation", { name: "主导航" });
  await nav.getByRole("link", { name: "AI 助手", exact: true }).click();
  await page.getByLabel("发送消息").fill("测试教学需求");
  await page.getByRole("button", { name: "发送 ↑" }).click();
  await expect(page.locator(".chat-message.assistant .message-body")).toHaveText("课堂目标");
  await page.getByRole("button", { name: "内容编辑" }).click();
  await page.getByLabel("教学内容编辑").fill("需要保留的未保存教学草稿");
  await nav.getByRole("link", { name: "教学 Skill", exact: true }).click();
  await expect(page).toHaveURL(/\/teacher\/skills$/);
  await nav.getByRole("link", { name: "AI 助手", exact: true }).click();
  await page.getByRole("button", { name: "测试教学需求", exact: true }).click();
  await page.getByRole("button", { name: "内容编辑" }).click();
  await expect(page.getByLabel("教学内容编辑")).toHaveValue("需要保留的未保存教学草稿");
  const dialogPromise = page.waitForEvent("dialog");
  await page.evaluate(() => { setTimeout(() => location.reload(), 0); });
  const dialog = await dialogPromise;
  expect(dialog.type()).toBe("beforeunload");
  await dialog.dismiss();
  await expect(page.getByLabel("教学内容编辑")).toHaveValue("需要保留的未保存教学草稿");
});

test("parsed attachment without indexing explains why model call is blocked", async ({ page }) => {
  await fixtures(page);
  let modelCalls = 0;
  await page.route("**/api/v1/conversations/chat-1/attachments", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    return route.fulfill({
      status: 201,
      json: { code: "OK", message: "success", requestId: "fixture", data: {
        id: "document-1", filename: "课堂资料.pdf", status: "parsed", chunkCount: 1,
        errorCode: null, errorMessage: null,
      } },
    });
  });
  await page.route("**/api/v1/conversations/chat-1/messages", async (route) => {
    if (route.request().method() === "POST") modelCalls += 1;
    return route.fallback();
  });
  await login(page);
  await page.goto("/teacher/assistant");
  await page.getByLabel("发送消息").fill("根据附件回答");
  await page.locator('.chat-input input[type="file"]').setInputFiles({
    name: "课堂资料.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n"),
  });
  await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("索引尚不可用");
  await expect(page.getByLabel("发送消息")).toHaveValue("根据附件回答");
  expect(modelCalls).toBe(0);
});
test("login never exposes credentials before hydration", async ({ page }) => {
  await page.route(/\/_next\/static\/.*\.js/, route => route.abort());
  await page.goto("/login");
  await expect(page.locator("form.login")).toHaveAttribute("method", "post");
  await expect(page.getByLabel("账号", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("登录密码")).toBeDisabled();
  await expect(page.getByRole("button", { name: "登 录 系 统" })).toBeDisabled();
  expect(page.url()).not.toContain("password=");
});

test("RELEASE-017 unsaved draft survives logout and same-account login", async ({ page }) => {
  await fixtures(page);
  await page.route("**/api/v1/conversations", route => route.request().method() === "POST" ? route.fallback() : route.fulfill({ json: {
    code: "OK", data: { items: [{ id: "chat-1", title: "测试教学需求" }], nextCursor: null }
  }}));
  await login(page);
  await page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "AI 助手", exact: true }).click();
  await page.getByRole("button", { name: "测试教学需求", exact: true }).click();
  await page.getByRole("button", { name: "内容编辑", exact: true }).click();
  await page.getByLabel("教学内容编辑").fill("退出前尚未保存的教学草稿");
  await page.getByRole("button", { name: "打开账号菜单" }).click();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await login(page);
  await page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "AI 助手", exact: true }).click();
  await page.getByRole("button", { name: "测试教学需求", exact: true }).click();
  await page.getByRole("button", { name: "内容编辑", exact: true }).click();
  await expect(page.getByLabel("教学内容编辑")).toHaveValue("退出前尚未保存的教学草稿");
  await expect(page.getByText("已恢复本次打开应用期间的未保存修改，请保存草稿。")).toBeVisible();
});
