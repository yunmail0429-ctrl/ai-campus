const { chromium } = require('@playwright/test');
const { randomBytes, randomUUID } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const origin = 'http://localhost:3100';
const headers = { Origin: origin };
const username = `audit${Date.now().toString(36)}`;
const password = randomBytes(18).toString('hex');
const report = { username, tasks: [], cleanup: null };

async function login(page, user, secret) {
  await page.goto(`${origin}/login`);
  await page.getByLabel('账号', { exact: true }).fill(user);
  await page.getByLabel('登录密码').fill(secret);
  await page.getByRole('button', { name: '登 录 系 统' }).click();
  await page.waitForURL('**/teacher/workspace');
}

async function taskState(page, id, accepted, timeout = 180000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const response = await page.request.get(`${origin}/api/v1/resource-tasks/${id}`);
    const task = (await response.json()).data;
    if (accepted.includes(task.status) || ['failed', 'cancelled'].includes(task.status)) return task;
    await page.waitForTimeout(3000);
  }
  throw new Error(`Task ${id} did not settle within ${timeout}ms`);
}

(async () => {
  const browser = await chromium.launch({ channel: 'msedge' });
  let adminContext, teacherContext, userId;
  try {
    adminContext = await browser.newContext();
    const admin = await adminContext.newPage();
    await login(admin, 'admin', process.env.DEPLOY_TEST_PASSWORD);
    const created = await admin.request.post(`${origin}/api/v1/admin/users`, { headers, data: {
      username, password, name: '创作验收临时账号', subject: '语文', grade: '高一', role: 'teacher',
    } });
    if (created.status() !== 201) throw new Error(`Create test user: ${created.status()} ${await created.text()}`);
    userId = (await created.json()).data.id;
    const accounts = await admin.request.get(`${origin}/api/v1/admin/quota/accounts?q=${username}`);
    const account = (await accounts.json()).data.items.find(x => x.username === username);
    if (!account) throw new Error('Test quota account missing');
    const grant = await admin.request.post(`${origin}/api/v1/admin/quota/accounts/${account.id}/adjustments`, {
      headers: { ...headers, 'Idempotency-Key': randomUUID() }, data: { amount: 100, reason: '创作中心临时验收' },
    });
    if (!grant.ok()) throw new Error(`Grant test credits: ${grant.status()} ${await grant.text()}`);
    teacherContext = await browser.newContext({ acceptDownloads: true });
    const page = await teacherContext.newPage();
    await login(page, username, password);
    for (const [mode, prompt, suffix] of [
      ['图片', '为高中《赤壁赋》生成一张江月小舟教学插图的图片提示词。', '.md'],
      ['视频', '为高中《赤壁赋》生成一分钟讲课视频分镜脚本。', '.md'],
      ['PPT', '为高中《赤壁赋》生成一页课件，讲清乐、悲、旷达的情感变化。', '.pptx'],
    ].filter(([mode]) => !process.env.CREATIVE_MODES || process.env.CREATIVE_MODES.split(',').includes(mode))) {
      const row = { mode, status: 'started' };
      report.tasks.push(row);
      console.log(`START ${mode}`, { flush: true });
      await page.goto(`${origin}/teacher/create?mode=${encodeURIComponent(mode)}`);
      await page.getByLabel('创作需求').fill(prompt);
      if (mode === 'PPT') await page.getByLabel('PPT 页数').fill('1');
      const request = page.waitForResponse(r => r.url().endsWith('/api/v1/resource-tasks') && r.request().method() === 'POST');
      await page.getByRole('button', { name: '开始创作', exact: true }).click();
      const createdTask = await request;
      if (createdTask.status() !== 201) throw new Error(`${mode} submit ${createdTask.status()} ${await createdTask.text()}`);
      row.id = (await createdTask.json()).data.id;
      let task = await taskState(page, row.id, mode === 'PPT' ? ['awaiting_outline'] : ['succeeded']);
      if (mode === 'PPT' && task.status === 'awaiting_outline') {
        await page.reload();
        await page.getByRole('button', { name: '确认大纲并生成 PPT' }).click();
        task = await taskState(page, row.id, ['succeeded']);
      }
      row.status = task.status;
      row.credits = task.credits;
      row.errorCode = task.errorCode;
      if (task.status !== 'succeeded') throw new Error(`${mode} task ended ${task.status}: ${task.errorCode}`);
      const download = await page.request.get(`${origin}/api/v1/resource-tasks/${row.id}/download`);
      if (!download.ok()) throw new Error(`${mode} download ${download.status()}`);
      const content = await download.body();
      if (suffix === '.pptx' ? content.subarray(0, 2).toString() !== 'PK' : !content.toString('utf8').trim())
        throw new Error(`${mode} artifact empty or invalid`);
      row.bytes = content.length;
      row.status = 'passed';
      console.log(`PASS ${mode} id=${row.id} bytes=${row.bytes} credits=${row.credits}`);
    }
  } catch (error) {
    report.error = error.message;
    process.exitCode = 1;
    console.error('FAILED', error.message);
  } finally {
    try {
      if (userId && adminContext) {
        const admin = adminContext.pages()[0];
        const removed = await admin.request.delete(`${origin}/api/v1/admin/users/${userId}`, { headers });
        report.cleanup = removed.status();
        if (!removed.ok()) process.exitCode = 1;
      }
    } catch (error) { report.cleanup = error.message; process.exitCode = 1; }
    await teacherContext?.close();
    await adminContext?.close();
    await browser.close();
    const destination = path.resolve('../test-results/deployed-creative-flow.json');
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.writeFileSync(destination, JSON.stringify(report, null, 2));
    console.log(`REPORT ${destination} cleanup=${report.cleanup}`);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
