import { expect, test } from "@playwright/test";

test("indexed documents ground chat with persistent citations and no retrieval credit charge", async ({
  page,
  browser,
}) => {
  test.skip(
    process.env.XFLJ_TEST_KNOWLEDGE_SERVICES !== "1",
    "Use backend/tests/run_knowledge_services.py --browser",
  );
  test.setTimeout(90_000);
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("browser-admin");
  await page.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
  await page.getByRole("button", { name: "登 录 系 统" }).click();
  await expect(page).toHaveURL(/\/teacher\/workspace$/);
  const headers = { Origin: "http://127.0.0.1:3218" };
  const username = `rag-teacher-${Date.now()}`;
  const created = await page.request.post("/api/v1/admin/users", {
    headers,
    data: {
      username,
      password: process.env.XFLJ_E2E_PASSWORD!,
      name: "检索测试教师",
      subject: "数学",
      grade: "高一",
      role: "teacher",
    },
  });
  expect(created.ok()).toBe(true);
  const accounts = await (
    await page.request.get("/api/v1/admin/quota/accounts")
  ).json();
  const account = accounts.data.items.find(
    (a: { username: string }) => a.username === username,
  );
  const grant = await page.request.post(
    `/api/v1/admin/quota/accounts/${account.id}/adjustments`,
    {
      headers: { ...headers, "Idempotency-Key": username },
      data: { amount: 100, reason: "检索链路测试" },
    },
  );
  expect(grant.ok()).toBe(true);
  const configured = await page.request.post("/api/v1/admin/models", {
    headers,
    data: {
      name: "检索协议模型",
      provider: "protocol-test",
      model: "rag-chat",
      active: true,
      contextTokens: 16384,
      maxOutputTokens: 1024,
      timeoutSeconds: 30,
      inputPrice: 1000,
      outputPrice: 2000,
    },
  });
  const model = (await configured.json()).data;
  expect(
    (
      await page.request.post(`/api/v1/admin/models/${model.id}/default`, {
        headers,
      })
    ).ok(),
  ).toBe(true);
  await page.goto("/admin/models");
  const embedding = page.getByRole("region", { name: "知识库向量模型" });
  await embedding
    .getByRole("combobox", { name: "向量服务", exact: true })
    .selectOption("protocol-test");
  await embedding.getByLabel("向量模型标识").fill("test-embedding");
  await embedding.getByLabel("向量维度", { exact: true }).fill("3");
  await embedding.getByLabel("请求指定维度（服务支持时启用）").check();
  await embedding.getByLabel("启用知识库向量检索").check();
  await embedding.getByRole("button", { name: "保存向量配置" }).click();
  await expect(
    embedding.getByText(
      "向量模型配置已保存，后台会为已解析资料建立或更新索引。",
    ),
  ).toBeVisible();
  await embedding.getByRole("button", { name: "检查向量连接" }).click();
  await expect(
    embedding.getByText("向量服务连接和返回维度检查通过。"),
  ).toBeVisible();
  await page.screenshot({ path: "test-results/live-embedding-settings.png" });

  const teacherContext = await browser.newContext();
  const teacher = await teacherContext.newPage();
  try {
    await teacher.goto("/login");
    await teacher.getByLabel("账号", { exact: true }).fill(username);
    await teacher.getByLabel("登录密码").fill(process.env.XFLJ_E2E_PASSWORD!);
    await teacher.getByRole("button", { name: "登 录 系 统" }).click();
    await expect(teacher).toHaveURL(/\/teacher\/workspace$/);
    await teacher.goto("/teacher/knowledge");
    await teacher.getByLabel("个人知识库名称").fill("单调性课堂活动");
    await teacher.getByRole("button", { name: "新建知识库" }).click();
    await expect(
      teacher.getByRole("heading", { name: "单调性课堂活动" }),
    ).toBeVisible();
    await teacher
      .getByLabel("上传教学资料")
      .setInputFiles({
        name: "函数课堂.txt",
        mimeType: "text/plain",
        buffer: Buffer.from(
          "函数单调性课堂活动：先观察图像，再用定义证明，最后分组讨论。",
        ),
      });
    await teacher
      .getByRole("button", { name: "上传资料", exact: true })
      .click();
    await expect(teacher.getByText("可用于检索", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    await teacher
      .getByLabel("教学问题", { exact: true })
      .fill("函数单调性的课堂活动");
    await teacher.getByRole("button", { name: "查找相关段落" }).click();
    await expect(
      teacher.getByRole("region", { name: "回答参考资料" }),
    ).toContainText("函数课堂.txt");
    const quota = await (await teacher.request.get("/api/v1/me/quota")).json();
    expect(quota.data.totalConsumed).toBe(0);
    expect(quota.data.balance).toBe(100);
    await teacher.goto("/teacher/assistant");
    await teacher
      .getByLabel("发送消息", { exact: true })
      .fill("如何开展函数单调性的课堂活动？");
    await teacher.getByRole("button", { name: "发送 ↑", exact: true }).click();
    await expect(
      teacher.getByText("本次消耗 1 积分，可在“我的积分”查看记录。", {
        exact: true,
      }),
    ).toBeVisible({ timeout: 30_000 });
    const citation = teacher
      .locator(".chat-message.assistant")
      .getByRole("region", { name: "回答参考资料" });
    await expect(citation).toContainText("函数课堂.txt");
    await citation
      .locator("summary")
      .filter({ hasText: "函数课堂.txt" })
      .click();
    await expect(citation).toContainText("先观察图像，再用定义证明");
    await teacher.screenshot({ path: "test-results/live-rag-citations.png" });
    const chats = await (
      await teacher.request.get("/api/v1/conversations")
    ).json();
    await teacher.goto(`/teacher/assistant?id=${chats.data.items[0].id}`);
    await expect(
      teacher
        .locator(".chat-message.assistant")
        .getByRole("region", { name: "回答参考资料" }),
    ).toContainText("函数课堂.txt");
    await teacher.setViewportSize({ width: 390, height: 844 });
    expect(
      await teacher.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await teacher.screenshot({ path: "test-results/live-rag-mobile.png" });
    const after = await (await teacher.request.get("/api/v1/me/quota")).json();
    expect(after.data.totalConsumed).toBe(1);
    expect(after.data.reserved).toBe(0);
  } finally {
    await teacherContext.close();
  }
});
