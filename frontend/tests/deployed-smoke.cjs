const { chromium, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const password = process.env.DEPLOY_ADMIN_PASSWORD || fs.readFileSync(path.resolve('../deploy/admin.env'), 'utf8').trim().split('=')[1];
  const browser = await chromium.launch({ channel: 'msedge' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await page.goto('http://localhost:3100/login');
    await page.getByLabel('账号', { exact: true }).fill('admin');
    await page.getByLabel('登录密码').fill(password);
    await page.getByRole('button', { name: '登 录 系 统' }).click();
    await page.waitForURL('**/teacher/workspace');
    await page.goto('http://localhost:3100/admin/users');
    await page.getByRole('heading', { name: /账号/ }).first().waitFor();
    fs.mkdirSync('test-results', { recursive: true });
    await page.screenshot({ path: 'test-results/deployed-admin.png', fullPage: true });
    await page.goto('http://localhost:3100/admin/models');
    await page.getByRole('heading', { name: 'AI 动态视频 · 通义万相' }).waitFor();
    const video = await page.request.get('http://localhost:3100/api/v1/admin/video');
    if (!video.ok()) throw new Error('Video configuration endpoint unavailable');
    await page.screenshot({ path: 'test-results/deployed-video-settings.png', fullPage: true });
    await page.goto('http://localhost:3100/teacher/create?mode=' + encodeURIComponent('视频'));
    const capabilities = await page.request.get('http://localhost:3100/api/v1/resource-tasks/capabilities');
    if (!capabilities.ok()) throw new Error('Resource capabilities unavailable');
    const service = (await capabilities.json()).data.find(item => item.kind === 'video');
    if (!service.available) {
      await expect(page.getByText(service.reason + ' 请联系学校管理员。')).toBeVisible();
      await expect(page.getByRole('button', { name: '开始创作' })).toBeDisabled();
    }
    if (service.video) {
      await expect(page.getByText(new RegExp('预计 ' + service.video.estimatedCredits + ' 积分'))).toBeVisible();
    }
    await page.screenshot({ path: 'test-results/deployed-video-creation.png', fullPage: true });
    let unintendedSubmissions = 0;
    await page.route('**/api/v1/resource-tasks', async route => {
      if (route.request().method() === 'POST') {
        unintendedSubmissions++;
        await route.abort();
      } else await route.continue();
    });
    await page.route('**/api/v1/resource-tasks/capabilities', route => route.fulfill({
      status: 503, json: { code: 'SERVICE_UNAVAILABLE', message: '故障注入：配置读取失败', data: null },
    }));
    await page.reload();
    await page.getByLabel('创作需求').fill('配置重试安全检查');
    await expect(page.getByText('故障注入：配置读取失败')).toBeVisible();
    const retry = page.getByRole('button', { name: '重新加载', exact: true });
    await expect(retry).toHaveAttribute('type', 'button');
    await retry.click();
    await expect(page.getByText('故障注入：配置读取失败')).toBeVisible();
    if (unintendedSubmissions) throw new Error('Retry unexpectedly submitted a creation task');
    await page.unroute('**/api/v1/resource-tasks');
    await page.unroute('**/api/v1/resource-tasks/capabilities');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('http://localhost:3100/teacher/workspace');
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) {
      throw new Error('Mobile horizontal overflow');
    }
    await page.screenshot({ path: 'test-results/deployed-mobile.png', fullPage: true });
    console.log('PASS: deployed browser login, administration and mobile workspace');
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
