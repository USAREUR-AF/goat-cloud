// Capture the documentation screenshots from the demo dashboard.
//
//   python3 serve.py &                 # in another terminal
//   npm install playwright             # once
//   node capture.mjs [base-url] [output-dir]
//
// PLAYWRIGHT_MODULE may point at an installed playwright package directory.
import fs from 'fs';
import path from 'path';
import { pathToFileURL, fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const base = process.argv[2] || 'http://localhost:8888';
const out = process.argv[3] || path.join(here, '..', 'docs', 'img', 'screens');

const mod = process.env.PLAYWRIGHT_MODULE
  ? await import(pathToFileURL(path.join(process.env.PLAYWRIGHT_MODULE, 'index.mjs')).href)
  : await import('playwright');
const { chromium } = mod;

const report = JSON.parse(fs.readFileSync(path.join(here, 'data', 'compliance-report.json'), 'utf8'));
const detail = report.discovered_resources.find(r => r.name === 'analytics-copy').arn;

// name, path, viewport height
const shots = [
  ['overview', '/', 1000],
  ['findings', '/findings', 1000],
  ['cost', '/burn', 790],
  ['changes', '/changes', 1000],
  ['repositories', '/repos', 620],
  ['repository-detail', '/repos/storefront-api', 1000],
  ['resource-detail', '/cure?arn=' + encodeURIComponent(detail), 800],
  ['tree', '/tree', 1000],
  ['network', '/network', 1000],
  ['pending-approvals', '/pending', 1000],
  ['protected', '/protected', 900],
  ['report-html', '/report.html', 1100],
];

const only = process.env.ONLY ? process.env.ONLY.split(',') : null;
// Installed Google Chrome when there is one, otherwise Playwright's own Chromium.
const browser = await chromium.launch({ channel: 'chrome', headless: true }).catch(() => chromium.launch({ headless: true }));
const context = await browser.newContext({ viewport: { width: 1360, height: 1000 }, deviceScaleFactor: 1.5 });
const page = await context.newPage();
for (const [name, route, height] of shots) {
  if (only && !only.includes(name)) continue;
  await page.setViewportSize({ width: 1360, height });
  const url = route === '/report.html' ? pathToFileURL(path.join(here, 'data', 'compliance-report.html')).href : base + route;
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  if (name === 'network') { await page.keyboard.press('f'); await page.waitForTimeout(1500); }
  await page.screenshot({ path: path.join(out, `${name}.png`) });
  console.log(name);
}
await browser.close();
