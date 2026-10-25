// Headless screenshot: node scripts/shot.mjs <url> <out.png> [waitMs] [w] [h] [evalJs]
import puppeteer from 'puppeteer-core';
const [url, out, wait = '20000', w = '1600', h = '900', js] = process.argv.slice(2);
const browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: 'new', protocolTimeout: 600000,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-sandbox', `--window-size=${w},${h}`] });
const page = await browser.newPage();
await page.setViewport({ width: +w, height: +h });
const logs = [];
page.on('console', (m) => logs.push(m.type() + ': ' + m.text()));
page.on('pageerror', (e) => logs.push('pageerror: ' + e.message));
await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
const t0 = Date.now();
while (Date.now() - t0 < +wait) {
  const ready = await page.evaluate(() => document.documentElement.dataset.ready === 'true').catch(() => false);
  if (ready) break;
  await new Promise((r) => setTimeout(r, 500));
}
if (js) { await page.evaluate(js); await new Promise((r) => setTimeout(r, 4000)); }
console.log(logs.filter((l) => /picked|failed|error/i.test(l)).join('\n'));
await page.screenshot({ path: out });
console.log(logs.filter((l) => !l.includes('[vite]')).slice(-25).join('\n'));
await browser.close();
