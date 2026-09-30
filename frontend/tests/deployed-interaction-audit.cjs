const { chromium } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const routes = {
  teacher: ['workspace', 'assistant', 'skills', 'create', 'plugins', 'knowledge', 'settings'].map(x => `/teacher/${x}`),
  admin: ['users', 'credits', 'models', 'statistics', 'knowledge', 'audit-logs', 'skills'].map(x => `/admin/${x}`),
};

(async () => {
  const browser = await chromium.launch({ channel: 'msedge' });
  const results = [];
  try {
    for (const role of ['teacher', 'admin']) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto('http://localhost:3100/login');
      await page.getByLabel('账号', { exact: true }).fill(role);
      await page.getByLabel('登录密码').fill(process.env.DEPLOY_TEST_PASSWORD);
      await page.getByRole('button', { name: '登 录 系 统' }).click();
      await page.waitForURL('**/teacher/workspace');
      for (const route of routes[role]) {
        const start = errors.length;
        const response = await page.goto(`http://localhost:3100${route}`);
        await page.waitForLoadState('networkidle');
        const controls = await page.locator('button:visible, input[type="button"]:visible, input[type="submit"]:visible, summary:visible').evaluateAll(nodes => nodes.map(node => ({
          name: node.getAttribute('aria-label') || node.textContent.trim().replace(/\s+/g, ' ').slice(0, 80),
          disabled: node.disabled || node.getAttribute('aria-disabled') === 'true',
          tag: node.tagName.toLowerCase(),
        })));
        results.push({ role, route, status: response.status(), controls, errors: errors.slice(start) });
      }
      await context.close();
    }
  } finally {
    await browser.close();
  }
  const report = { pages: results.length, controls: results.reduce((n, x) => n + x.controls.length, 0), results };
  const destination = path.resolve('../test-results/deployed-interaction-inventory.json');
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.writeFileSync(destination, JSON.stringify(report, null, 2));
  for (const row of results) console.log(`${row.role} ${row.route} HTTP ${row.status} controls=${row.controls.length} errors=${row.errors.length}: ${row.controls.map(x => x.name).join(' | ')}`);
  console.log(`TOTAL pages=${report.pages} controls=${report.controls} report=${destination}`);
  if (results.some(x => x.status !== 200 || x.errors.length)) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
