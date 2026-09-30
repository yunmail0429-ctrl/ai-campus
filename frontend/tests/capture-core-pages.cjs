const { chromium } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const origin = 'http://localhost:3100';
const destination = path.resolve('../artifacts/core-pages/screenshots');
const pages = [
  ['teacher', 'workspace', '/teacher/workspace'],
  ['teacher', 'assistant', '/teacher/assistant'],
  ['teacher', 'creative', '/teacher/create'],
  ['teacher', 'knowledge', '/teacher/knowledge'],
  ['teacher', 'skills', '/teacher/skills'],
  ['teacher', 'plugins', '/teacher/plugins'],
  ['teacher', 'settings', '/teacher/settings'],
  ['admin', 'users', '/admin/users'],
  ['admin', 'credits', '/admin/credits'],
  ['admin', 'admin-knowledge', '/admin/knowledge'],
  ['admin', 'admin-skills', '/admin/skills'],
  ['admin', 'models', '/admin/models'],
  ['admin', 'statistics', '/admin/statistics'],
  ['admin', 'audit-logs', '/admin/audit-logs'],
];

(async () => {
  fs.mkdirSync(destination, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge' });
  try {
    for (const role of ['teacher', 'admin']) {
      const context = await browser.newContext({ viewport: { width: 1600, height: 800 }, deviceScaleFactor: 1 });
      const page = await context.newPage();
      await page.goto(`${origin}/login`);
      await page.getByLabel('账号', { exact: true }).fill(role);
      await page.getByLabel('登录密码').fill(process.env.DEPLOY_TEST_PASSWORD);
      await page.getByRole('button', { name: '登 录 系 统' }).click();
      await page.waitForURL('**/teacher/workspace');
      for (const [, name, route] of pages.filter(item => item[0] === role)) {
        const response = await page.goto(`${origin}${route}`);
        if (response.status() !== 200) throw new Error(`${route}: HTTP ${response.status()}`);
        await page.waitForLoadState('networkidle');
        const output = path.join(destination, `${name}.png`);
        await page.screenshot({ path: output });
        console.log(`${name}: ${output}`);
      }
      await context.close();
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
