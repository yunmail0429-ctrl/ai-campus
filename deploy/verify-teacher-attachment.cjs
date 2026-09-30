const { chromium, expect } = require('../frontend/node_modules/@playwright/test');

const origin = 'http://localhost:3100';

(async () => {
  if (!process.env.CAMPUS_PASSWORD) throw new Error('Set CAMPUS_PASSWORD to the teacher password.');
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  let conversation;
  try {
    const login = await context.request.post(`${origin}/api/v1/auth/login`, {
      headers: { Origin: origin }, data: { username: 'teacher', password: process.env.CAMPUS_PASSWORD },
    });
    if (!login.ok()) throw new Error(`Teacher login HTTP ${login.status()}`);
    const page = await context.newPage();
    await page.goto(`${origin}/teacher/assistant`);
    await page.getByRole('button', { name: '新对话' }).click();
    await page.getByLabel('发送消息').fill('根据附件，蓝色水壶的编号是什么？只回答编号。');
    await page.locator('.chat-input input[type="file"]').setInputFiles({
      name: 'teacher-browser-attachment.txt', mimeType: 'text/plain',
      buffer: Buffer.from('校内科普角的蓝色水壶编号为 QH-7319。这个编号用于课堂观察记录。'),
    });
    const created = page.waitForResponse(response =>
      response.url().endsWith('/api/v1/conversations') && response.request().method() === 'POST');
    const uploaded = page.waitForResponse(response =>
      response.url().includes('/attachments') && response.request().method() === 'POST');
    await page.getByRole('button', { name: '发送 ↑', exact: true }).click();
    conversation = (await (await created).json()).data.id;
    const document = (await (await uploaded).json()).data.id;
    await expect(page.getByRole('main').getByRole('alert').first()).toContainText('资料已保存');
    await expect(page.getByLabel('发送消息')).toHaveValue('根据附件，蓝色水壶的编号是什么？只回答编号。');
    let ready = false;
    for (let attempt = 0; attempt < 90; attempt++) {
      const response = await context.request.get(`${origin}/api/v1/documents/${document}`);
      if (!response.ok()) throw new Error(`Document status HTTP ${response.status()}`);
      const status = (await response.json()).data.status;
      if (status === 'ready') { ready = true; break; }
      if (status === 'failed' || status === 'index_failed') throw new Error(`Document ${status}`);
      await page.waitForTimeout(2000);
    }
    if (!ready) throw new Error('Attachment indexing timed out');
    await page.getByRole('button', { name: '发送 ↑', exact: true }).click();
    await expect(page.locator('.chat-message.assistant')).toContainText('QH-7319', { timeout: 120000 });
    await expect(page.getByLabel('发送消息')).toHaveValue('');
    console.log('PASS: browser attachment retry displayed cited model answer after indexing');
  } finally {
    if (conversation) await context.request.delete(`${origin}/api/v1/conversations/${conversation}`, {
      headers: { Origin: origin },
    });
    await context.request.post(`${origin}/api/v1/auth/logout`, { headers: { Origin: origin } });
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
