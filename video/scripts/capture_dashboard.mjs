// Captures the Recall dashboard pages as full-page PNGs (1920x1080 viewport, deviceScaleFactor 2, dark mode).
// Read-only: it only loads pages. Needs the API (8000) and web (3000) running.
// Usage (from video/): node scripts/capture_dashboard.mjs [answerId]
import puppeteer from 'puppeteer-core';
import fs from 'fs';
import path from 'path';

const CHROME = path.resolve('node_modules/.remotion/chrome-headless-shell/win64/chrome-headless-shell-win64/chrome-headless-shell.exe');
const OUT = path.resolve('stills');
const BASE = 'http://localhost:3000';
const answerId = process.argv[2] ?? '40'; // question 68 "What two types of glass..." (found via /api/today)

const pages = [
  ['answer68', `/answers/${answerId}`],
  ['progress', '/progress'],
  ['sessions', '/sessions'],
  ['privacy', '/privacy'],
];

fs.mkdirSync(OUT, {recursive: true});
const browser = await puppeteer.launch({executablePath: CHROME, headless: 'shell', args: ['--no-sandbox', '--lang=en-US']});
const page = await browser.newPage();
await page.setExtraHTTPHeaders({'Accept-Language': 'en-US,en;q=0.9'});
await page.setViewport({width: 1920, height: 1080, deviceScaleFactor: 2});
await page.emulateMediaFeatures([{name: 'prefers-color-scheme', value: 'dark'}]);

for (const [name, route] of pages) {
  await page.goto(BASE + route, {waitUntil: 'networkidle0', timeout: 90000});
  // hide Next's dev indicator and let the entrance animations finish
  await page.addStyleTag({content: 'nextjs-portal{display:none!important}'});
  await new Promise((r) => setTimeout(r, 2500));
  const file = path.join(OUT, `${name}.png`);
  await page.screenshot({path: file, fullPage: true});
  const dims = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]);
  console.log(name, route, 'css', dims.join('x'), '->', file);
}
await browser.close();
