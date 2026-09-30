const { chromium, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge' });
  const results = [];
  fs.mkdirSync('test-results/page-matrix', { recursive: true });
  try {
    for (const role of ['admin', 'teacher']) {
      const context = await browser.newContext();
      const page = await context.newPage();
      let errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto('http://localhost:3100/login');
      await page.getByLabel('账号', { exact: true }).fill(role);
      await page.getByLabel('登录密码').fill(process.env[role === 'admin' ? 'DEPLOY_ADMIN_PASSWORD' : 'DEPLOY_TEACHER_PASSWORD']);
      await page.getByRole('button', { name: '登 录 系 统' }).click();
      await page.waitForURL('**/teacher/workspace');
      const pages = role === 'admin'
        ? ['users', 'credits', 'models', 'statistics', 'knowledge', 'audit-logs', 'skills'].map(name => '/admin/' + name)
        : ['workspace', 'assistant', 'skills', 'create', 'settings', 'knowledge'].map(name => '/teacher/' + name);
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
        for (const route of pages) {
          errors = [];
          const response = await page.goto('http://localhost:3100' + route);
          await expect(page.getByRole('main')).toBeVisible();
          await page.waitForLoadState('networkidle');
          const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
          const actualPath = new URL(page.url()).pathname;
          const row = { role, route, width, status: response.status(), actualPath, overflow, errors: [...errors] };
          row.passed = row.status === 200 && actualPath === route && !overflow && errors.length === 0;
          results.push(row);
          await page.screenshot({ path: `test-results/page-matrix/${role}-${route.split('/').pop()}-${width}.png`, fullPage: true });
        }
      }
      await context.close();
    }
  } finally {
    await browser.close();
    const report = { checks: results.length, failed: results.filter(row => !row.passed).length, results };
    fs.writeFileSync(path.resolve('../docs/DEPLOYED-PAGE-MATRIX.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ checks: report.checks, failed: report.failed }));
    if (report.failed || results.length !== 26) process.exitCode = 1;
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
