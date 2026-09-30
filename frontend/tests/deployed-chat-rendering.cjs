const { chromium, expect } = require('@playwright/test');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  let conversationId;
  try {
    await page.goto('http://localhost:3100/login');
    await page.getByLabel('账号', { exact: true }).fill('teacher');
    await page.getByLabel('登录密码').fill(process.env.DEPLOY_TEST_PASSWORD);
    await page.getByRole('button', { name: '登 录 系 统' }).click();
    await page.waitForURL('**/teacher/workspace');
    await page.goto('http://localhost:3100/teacher/assistant');
    const prompt = '这是界面验证。请只输出 Markdown 二级标题“教学目标”，下一段用粗体写“理解单调性”，再列两条简短课堂活动。';
    await page.getByLabel('发送消息').fill(prompt);
    const creation = page.waitForResponse(response => response.url().endsWith('/api/v1/conversations') && response.request().method() === 'POST');
    await page.getByRole('button', { name: '发送 ↑', exact: true }).click();
    conversationId = (await (await creation).json()).data.id;
    await expect(page.getByLabel('发送消息')).toHaveValue('');
    await expect(page.locator('.chat-message.user .message-body')).toHaveText(prompt);
    await expect(page.locator('.chat-message.assistant .message-markdown strong')).toHaveText('理解单调性', { timeout: 150000 });
    await expect(page.getByRole('button', { name: '发送 ↑', exact: true })).toBeVisible({ timeout: 150000 });
    await expect(page.locator('.chat-message.user')).toHaveCount(1);
    await expect(page.locator('.chat-message.assistant')).toHaveCount(1);
    await page.goto(`http://localhost:3100/teacher/assistant?id=${conversationId}`);
    await expect(page.locator('.chat-message.user .message-body')).toHaveText(prompt);
    await expect(page.locator('.message-markdown h2')).toHaveText('教学目标');
    await expect(page.locator('.message-markdown strong')).toHaveText('理解单调性');
    await expect(page.locator('.message-markdown li')).toHaveCount(2);
    fs.mkdirSync('test-results', { recursive: true });
    await page.screenshot({ path: 'test-results/deployed-chat-rendering.png', fullPage: true });
    console.log(JSON.stringify({ immediateUserMessage: true, clearedComposer: true, realModelMarkdown: true, noDuplicates: true, historyAfterReload: true }));
  } catch (error) {
    fs.mkdirSync('test-results', { recursive: true });
    await page.screenshot({ path: 'test-results/deployed-chat-rendering-failure.png', fullPage: true });
    throw error;
  } finally {
    if (conversationId) {
      const response = await page.request.delete(`http://localhost:3100/api/v1/conversations/${conversationId}`, { headers: { Origin: 'http://localhost:3100' } });
      if (!response.ok()) console.error('Verification cleanup status:', response.status());
    }
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
