const { chromium, expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge' });
  const context = await browser.newContext();
  const page = await context.newPage();
  let conversationId;
  const reference = 'test-invocation-reference-123';
  try {
    await page.goto('http://localhost:3100/login');
    await page.getByLabel('账号', { exact: true }).fill('teacher');
    await page.getByLabel('登录密码').fill(process.env.DEPLOY_TEST_PASSWORD);
    await page.getByRole('button', { name: '登 录 系 统' }).click();
    await page.waitForURL('**/teacher/workspace');
    const quotaBefore = (await (await context.request.get('http://localhost:3100/api/v1/me/quota')).json()).data;
    await page.goto('http://localhost:3100/teacher/assistant');
    await page.route('**/api/v1/conversations/*/messages', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `event: error\ndata: ${JSON.stringify({
          code: 'MODEL_UNAVAILABLE',
          message: '模型服务暂时不可用，请联系管理员。',
          invocationId: reference,
        })}\n\n`,
      });
    });
    await page.getByLabel('发送消息').fill('错误编号界面验证');
    const creation = page.waitForResponse((response) => response.url().endsWith('/api/v1/conversations') && response.request().method() === 'POST');
    await page.getByRole('button', { name: '发送 ↑', exact: true }).click();
    conversationId = (await (await creation).json()).data.id;
    await expect(page.getByText(`模型服务暂时不可用，请联系管理员。（请求编号：${reference}）`)).toBeVisible();
    const quotaAfter = (await (await context.request.get('http://localhost:3100/api/v1/me/quota')).json()).data;
    if (JSON.stringify(quotaBefore) !== JSON.stringify(quotaAfter)) throw new Error('Simulated model error changed quota');
    console.log(JSON.stringify({ errorReferenceVisible: true, quotaUnchanged: true }));
  } finally {
    if (conversationId) await context.request.delete(`http://localhost:3100/api/v1/conversations/${conversationId}`, { headers: { Origin: 'http://localhost:3100' } });
    await browser.close();
  }
})().catch((error) => { console.error(error.message); process.exitCode = 1; });
