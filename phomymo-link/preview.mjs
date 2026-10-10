#!/usr/bin/env node
// Usage: node preview.mjs LINK OUT.png [--model m110] [--tz America/Los_Angeles]
// Opens a Phomymo design link in headless Chromium and saves the exact bitmap Print would send, enlarged 3x.
// --tz is the timezone [[date]] expressions are evaluated in; it should be where the label gets printed.
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { join } from 'node:path';

const args = process.argv.slice(2);
const option = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 ? args.splice(i, 2)[1] : fallback;
};
const model = option('--model', 'm110');
const timezone = option('--tz', 'America/Los_Angeles');
const [link, outPath] = args;
if (!link || !outPath) {
  console.error('usage: node preview.mjs LINK OUT.png [--model m110] [--tz America/Los_Angeles]');
  process.exit(2);
}

const CACHE = join(homedir(), '.cache', 'phomymo-preview');
mkdirSync(CACHE, { recursive: true });
const run = (cmd, cmdArgs) =>
  execFileSync(cmd, cmdArgs, { cwd: CACHE, encoding: 'utf8', shell: process.platform === 'win32' });

// A plain Chromium only: CloakBrowser (web-browser's default) adds noise to canvas reads.
function findChromium() {
  const candidates = [
    process.env.PHOMYMO_CHROME,
    '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable',
  ];
  const searchRoots = ['/opt/pw-browsers', join(homedir(), '.cache', 'ms-playwright'),
    join(homedir(), '.cache', 'puppeteer'), join(CACHE, 'browsers')];
  const names = new Set(['chrome', 'chrome-headless-shell', 'headless_shell', 'chrome.exe', 'chrome-headless-shell.exe']);
  const find = (dir, depth) => {
    if (depth > 5 || !existsSync(dir)) return undefined;
    const entries = readdirSync(dir, { withFileTypes: true });
    const hit = entries.find((e) => e.isFile() && names.has(e.name));
    if (hit) return join(dir, hit.name);
    for (const e of entries) {
      const found = e.isDirectory() && find(join(dir, e.name), depth + 1);
      if (found) return found;
    }
    return undefined;
  };
  const usable = (c) => c && existsSync(c) && !c.includes('cloakbrowser');
  return candidates.find(usable) || searchRoots.map((root) => find(root, 0)).find(usable);
}

let chromium = findChromium();
if (!chromium) {
  console.error('no Chromium found; installing chrome-headless-shell (~100 MB, cached afterwards)');
  run('npx', ['-y', '@puppeteer/browsers', 'install', 'chrome-headless-shell@stable', '--path', join(CACHE, 'browsers')]);
  chromium = findChromium();
  if (!chromium) throw new Error('chrome-headless-shell install finished but no binary was found');
}
if (!existsSync(join(CACHE, 'node_modules', 'puppeteer-core'))) {
  run('npm', ['install', '--prefix', CACHE, '--no-fund', '--no-audit', '--silent', 'puppeteer-core']);
}
const puppeteer = createRequire(join(CACHE, 'package.json'))('puppeteer-core');

const browser = await puppeteer.launch({ executablePath: chromium, headless: true, args: ['--no-sandbox'] });
try {
  const page = await browser.newPage();
  await page.emulateTimezone(timezone);
  await page.goto(link, { waitUntil: 'load', timeout: 60000 });
  await page.waitForFunction(() => typeof window.phomymoPrintPreview === 'function', { timeout: 30000 });
  const png = await page.evaluate(async (printerModel) => {
    const src = await window.phomymoPrintPreview(printerModel);
    const img = new Image();
    await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = reject; img.src = src; });
    const scale = 3;
    const canvas = document.createElement('canvas');
    canvas.width = img.width * scale;
    canvas.height = img.height * scale;
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = '#999';
    ctx.strokeRect(0.5, 0.5, canvas.width - 1, canvas.height - 1);
    return canvas.toDataURL('image/png');
  }, model);
  writeFileSync(outPath, Buffer.from(png.split(',')[1], 'base64'));
  console.log(`${outPath} (rendered with ${chromium})`);
} finally {
  await browser.close();
}
