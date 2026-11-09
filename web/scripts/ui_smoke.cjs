// 端到端 UI 冒烟：上传 → 确认映射 → 跑账 → 结果 → 复核，每步截图
const { chromium } = require('playwright');
const path = require('path');

const OUT = 'C:/Users/11033/AppData/Local/Temp/shots';
const FILE = 'C:/Users/11033/AppData/Local/Temp/小样序时账.xlsx';
const BASE = 'http://127.0.0.1:8502';

(async () => {
  const fs = require('fs');
  fs.mkdirSync(OUT, { recursive: true });

  // 系统里只装了完整 chromium（没有 headless_shell）时，直接指定可执行文件
  const exe = process.env.CHROME_PATH ||
    'C:/Users/11033/AppData/Local/ms-playwright/chromium-1254/chrome-win64/chrome.exe';
  const browser = await chromium.launch(
    require('fs').existsSync(exe) ? { executablePath: exe } : {});
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(OUT, '1-empty.png'), fullPage: true });

  await page.setInputFiles('input[type=file]', FILE);
  await page.waitForSelector('text=确认字段映射', { timeout: 60000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, '2-mapping.png'), fullPage: true });

  await page.getByRole('button', { name: '开始清洗' }).click();
  await page.waitForSelector('text=3 · 结果', { timeout: 180000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(OUT, '3-result.png'), fullPage: true });

  await page.getByRole('button', { name: '去复核' }).click();
  await page.waitForSelector('text=校准概率', { timeout: 30000 });
  await page.waitForSelector('text=改桶纠错', { timeout: 30000 });
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(OUT, '4-review.png'), fullPage: true });

  const stats = await page.evaluate(() => ({
    rows: document.querySelectorAll('table tbody tr').length,
    tabs: [...document.querySelectorAll('.tab')].map(t => t.textContent.trim()),
  }));

  console.log('review rows:', stats.rows);
  console.log('tabs:', stats.tabs.join(' | '));
  console.log(errors.length ? 'JS 错误:\n' + errors.join('\n') : 'JS 错误: 无');
  await browser.close();
})().catch(e => { console.error('FAILED', e.message); process.exit(1); });
