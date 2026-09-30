const { chromium } = require('../frontend/node_modules/@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const state = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../local-data/campus-live-generation.json'), 'utf8'));
  const id = state.jobs['10'].conversationId;
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 1200, height: 850 } });
  try {
    const login = await context.request.post('http://localhost:3100/api/v1/auth/login', {
      headers: { Origin: 'http://localhost:3100' },
      data: { username: 'teacher', password: process.env.CAMPUS_PASSWORD },
    });
    if (!login.ok()) throw new Error(`Login failed: ${login.status()}`);
    const page = await context.newPage();
    await page.goto(`http://localhost:3100/teacher/assistant?id=${id}`);
    await page.locator('.message-markdown .katex').first().waitFor({ state: 'visible', timeout: 20000 });
    const count = await page.locator('.message-markdown .katex').count();
    if (count < 10) throw new Error(`Only ${count} formulas rendered`);
    const rawDelimiters = await page.locator('.message-markdown').filter({ hasText: '\\(' }).count();
    const screenshot = path.resolve(__dirname, '../local-data/math-render-verified.png');
    await page.locator('.message-markdown').last().screenshot({ path: screenshot });
    console.log(JSON.stringify({ renderedFormulaNodes: count, rawDelimiterBlocks: rawDelimiters, screenshot }));
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
