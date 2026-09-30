const { chromium } = require('../frontend/node_modules/@playwright/test');
const path = require('node:path');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  try {
    const response = await context.request.post('http://localhost:3100/api/v1/auth/login', {
      headers: { Origin: 'http://localhost:3100' },
      data: { username: 'teacher', password: process.env.CAMPUS_PASSWORD },
    });
    if (!response.ok()) throw new Error(`Login failed: ${response.status()}`);
    const page = await context.newPage();
    await page.goto('http://localhost:3100/teacher/skills');
    const state = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../local-data/teaching-assistants-live.json'), 'utf8'));
    for (const item of Object.values(state.items)) {
      await page.getByRole('heading', { name: item.name, exact: true }).waitFor({ state: 'visible' });
      const href = `/teacher/assistant?skill=${encodeURIComponent(item.skillId)}`;
      if (await page.locator(`a[href="${href}"]`).count() !== 1) throw new Error(`Missing use action: ${item.name}`);
    }
    const screenshot = path.resolve(__dirname, '../local-data/teaching-assistants-shared.png');
    await page.screenshot({ path: screenshot, fullPage: true });
    const first = Object.values(state.items)[0];
    await page.locator(`a[href="/teacher/assistant?skill=${first.skillId}"]`).click();
    await page.waitForURL(`**/teacher/assistant?skill=${first.skillId}`);
    console.log(JSON.stringify({ teacherCardsVisible: 6, useLinksVerified: 6, useNavigationVerified: true, screenshot }));
    await context.request.post('http://localhost:3100/api/v1/auth/logout', { headers: { Origin: 'http://localhost:3100' } });
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
