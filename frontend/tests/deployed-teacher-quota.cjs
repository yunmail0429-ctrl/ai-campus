const { chromium, expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await page.goto('http://localhost:3100/login');
    await page.getByLabel('账号', { exact: true }).fill('teacher');
    await page.getByLabel('登录密码').fill(process.env.DEPLOY_TEST_PASSWORD);
    await page.getByRole('button', { name: '登 录 系 统' }).click();
    await page.waitForURL('**/teacher/workspace');

    const quotaResponse = await page.request.get('http://localhost:3100/api/v1/me/quota');
    const historyResponse = await page.request.get('http://localhost:3100/api/v1/me/quota/transactions?limit=20');
    if (!quotaResponse.ok() || !historyResponse.ok()) throw new Error('Teacher quota API unavailable');
    const quota = (await quotaResponse.json()).data;
    const history = (await historyResponse.json()).data;
    await page.goto('http://localhost:3100/teacher/settings');
    await page.getByRole('button', { name: '我的积分' }).click();
    const summary = page.getByRole('region', { name: '我的积分总览' });
    await expect(summary.locator('dt', { hasText: '历史总消耗' }).locator('..').locator('dd'))
      .toHaveText(quota.totalConsumed.toLocaleString('zh-CN'));
    await expect(summary.locator('dt', { hasText: '当前余额' }).locator('..').locator('dd'))
      .toHaveText(quota.balance.toLocaleString('zh-CN'));
    const records = page.getByRole('region', { name: '积分记录' }).locator('.quota-record');
    await expect(records).toHaveCount(history.items.length);
    for (let index = 0; index < history.items.length; index++) {
      const row = history.items[index];
      const record = records.nth(index);
      await expect(record.locator('strong')).toHaveText(row.source);
      await expect(record.locator('code')).toHaveText(row.id);
      await expect(record.locator('.quota-record-meta')).toContainText(
        `变更后余额 ${row.balanceAfter.toLocaleString('zh-CN')}`,
      );
    }
    await page.reload();
    await page.getByRole('button', { name: '我的积分' }).click();
    await expect(summary.locator('dt', { hasText: '历史总消耗' }).locator('..').locator('dd'))
      .toHaveText(quota.totalConsumed.toLocaleString('zh-CN'));
    await expect(records).toHaveCount(history.items.length);
    console.log(JSON.stringify({ totalConsumed: quota.totalConsumed, balance: quota.balance, visibleRecords: history.items.length, persistsAfterReload: true }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
