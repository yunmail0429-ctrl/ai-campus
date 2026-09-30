const { chromium } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const checks = {
  teacher: {
    '/teacher/workspace': ['收起导航', '打开账号菜单', '可用积分'],
    '/teacher/assistant': ['新建对话', '新对话', '调用详情', '内容编辑', '重命名 ', '删除 '],
    '/teacher/skills': ['学校共享', '我的助手', '只看常用', '查看说明', '＋ 创建教学 Skill', '设为常用 '],
    '/teacher/create': ['PPT', '图片', '视频', '音乐', '数字人', '3D', '2', '换一组灵感', '校园活动 · 读书节'],
    '/teacher/knowledge': ['高一语文', '教育部教师发展公开资料', '新建知识库'],
    '/teacher/settings': ['个人资料', '使用偏好', '账号安全', '我的积分'],
  },
  admin: {
    '/admin/users': ['＋ 创建账号', '下一页'],
    '/admin/credits': ['查询', '分配积分', '收回积分', '冻结账户', '积分明细', '下一页'],
    '/admin/models': ['＋ 添加模型', '编辑配置', '检查连接', '检查向量连接'],
    '/admin/statistics': ['查询统计', '查看更多统计指标'],
    '/admin/knowledge': ['教育部教师发展公开资料', '新建知识库'],
    '/admin/audit-logs': ['查看追踪信息', '下一页'],
    '/admin/skills': ['查看审核资料', '下一页'],
  },
};

(async () => {
  const browser = await chromium.launch({ channel: 'msedge' });
  const results = [];
  try {
    for (const role of ['teacher', 'admin']) {
      const context = await browser.newContext();
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('dialog', dialog => dialog.dismiss());
      await page.goto('http://localhost:3100/login');
      await page.getByLabel('账号', { exact: true }).fill(role);
      await page.getByLabel('登录密码').fill(process.env.DEPLOY_TEST_PASSWORD);
      await page.getByRole('button', { name: '登 录 系 统' }).click();
      await page.waitForURL('**/teacher/workspace');
      for (const [route, names] of Object.entries(checks[role])) {
        for (const name of names) {
          const record = { role, route, name, status: 'passed' };
          try {
            await page.goto(`http://localhost:3100${route}`);
            await page.waitForLoadState('networkidle');
            const beforeErrors = errors.length;
            const candidates = page.getByRole('button', { name: new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) });
            const target = name === '查看说明' || name === '查看追踪信息' || name === '查看更多统计指标'
              ? page.locator('summary:visible').filter({ hasText: name }).first()
              : candidates.first();
            if (!await target.count()) throw new Error('control missing');
            if (await target.isDisabled()) { record.status = 'disabled'; results.push(record); continue; }
            await target.click({ timeout: 5000 });
            await page.waitForTimeout(250);
            if (errors.length > beforeErrors) throw new Error(errors.slice(beforeErrors).join('; '));
            if (name === '收起导航' && await page.getByRole('button', { name: '展开导航' }).count() === 0) throw new Error('sidebar did not collapse');
            if (name === '打开账号菜单' && await page.getByRole('button', { name: '退出登录' }).count() === 0) throw new Error('account menu did not open');
            if (name === '＋ 创建账号' && !await page.getByRole('dialog').isVisible()) throw new Error('account dialog did not open');
            if (name === '＋ 添加模型' && !await page.getByRole('dialog').isVisible()) throw new Error('model dialog did not open');
            if (name === '＋ 创建教学 Skill' && !await page.getByRole('dialog').isVisible()) throw new Error('skill dialog did not open');
          } catch (error) {
            record.status = 'failed'; record.error = error.message.slice(0, 180);
          }
          results.push(record);
        }
      }
      await context.close();
    }
  } finally { await browser.close(); }
  const report = { passed: results.filter(x => x.status === 'passed').length, disabled: results.filter(x => x.status === 'disabled').length, failed: results.filter(x => x.status === 'failed').length, results };
  const destination = path.resolve('../test-results/deployed-button-sweep.json');
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.writeFileSync(destination, JSON.stringify(report, null, 2));
  for (const row of results) console.log(`${row.status} ${row.route} ${row.name}${row.error ? `: ${row.error}` : ''}`);
  console.log(`TOTAL passed=${report.passed} disabled=${report.disabled} failed=${report.failed}`);
  if (report.failed) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
