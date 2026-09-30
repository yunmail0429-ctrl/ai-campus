const { chromium, expect } = require('@playwright/test');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const output = path.resolve('test-results/scanned-ui.pdf');
  fs.mkdirSync(path.dirname(output), { recursive: true });
  const code = `from pathlib import Path\nfrom PIL import Image\nimport sys\nsource=next((Path(sys.argv[1])/'local-data/mimo-ppt-task-review').glob('*1.PNG'))\nwith Image.open(source) as image: image.convert('RGB').save(sys.argv[2], format='PDF')`;
  const generated = spawnSync('py', ['-3', '-c', code, path.resolve('..'), output], { encoding: 'utf8' });
  if (generated.status !== 0) throw new Error('Could not create scanned PDF fixture');
  const browser = await chromium.launch({ channel: 'msedge' });
  let conversation;
  let page;
  try {
    page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await page.goto('http://localhost:3100/login');
    await page.getByLabel('账号', { exact: true }).fill('teacher');
    await page.getByLabel('登录密码').fill(process.env.DEPLOY_TEST_PASSWORD);
    await page.getByRole('button', { name: '登 录 系 统' }).click();
    await page.waitForURL('**/teacher/workspace');
    const origin = 'http://localhost:3100';
    const headers = { Origin: origin };
    const before = (await (await page.request.get(`${origin}/api/v1/me/quota`)).json()).data;
    const created = await page.request.post(`${origin}/api/v1/conversations`, {
      headers, data: { title: 'Scanned PDF UI acceptance' },
    });
    if (!created.ok()) throw new Error(`Create conversation HTTP ${created.status()}`);
    conversation = (await created.json()).data.id;
    const upload = await page.request.post(`${origin}/api/v1/conversations/${conversation}/attachments`, {
      headers: { ...headers, 'Idempotency-Key': crypto.randomUUID() },
      multipart: { file: { name: 'scanned-ui.pdf', mimeType: 'application/pdf', buffer: fs.readFileSync(output) } },
    });
    if (!upload.ok()) throw new Error(`Upload HTTP ${upload.status()}`);
    await page.goto(`${origin}/teacher/assistant?id=${conversation}`);
    await page.getByRole('button', { name: '调用详情' }).click();
    const card = page.locator('.knowledge-document', { hasText: 'scanned-ui.pdf' });
    await expect(card.locator('.knowledge-status')).toHaveText('处理失败', { timeout: 120000 });
    await expect(card.getByRole('status')).toHaveText('这份资料需要 OCR 识别，当前尚未配置 OCR 服务。');
    await expect(card.getByRole('button', { name: '重试处理' })).toHaveCount(0);
    const documentId = (await upload.json()).data.id;
    const refused = await page.request.post(`${origin}/api/v1/documents/${documentId}/retry`, { headers });
    if (refused.status() !== 409 || (await refused.json()).code !== 'DOCUMENT_NOT_RETRYABLE') {
      throw new Error('OCR-required document could be retried without an OCR service');
    }
    const after = (await (await page.request.get(`${origin}/api/v1/me/quota`)).json()).data;
    if (JSON.stringify(after) !== JSON.stringify(before)) throw new Error('Scanned PDF changed teacher quota');
    console.log('PASS: scanned PDF OCR failure is visible in teacher UI; no quota change');
  } finally {
    if (conversation && page) {
      await page.request.delete(`http://localhost:3100/api/v1/conversations/${conversation}`, {
        headers: { Origin: 'http://localhost:3100' },
      });
    }
    await browser.close();
    fs.unlinkSync(output);
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
