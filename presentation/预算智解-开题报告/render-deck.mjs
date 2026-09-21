import fs from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('/Users/hann/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const sharp = require('/Users/hann/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');

fs.mkdirSync('output/rendered', { recursive: true });
const browser = await chromium.launch({
  headless: true,
  executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
});
const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
await page.goto('http://127.0.0.1:8899/index.html', { waitUntil: 'networkidle' });
await page.evaluate(() => window.__setLowPowerMode?.(true, { persist: false }));
const total = await page.locator('section.slide').count();
const tiles = [];
for (let i = 0; i < total; i += 1) {
  await page.evaluate((n) => window.go(n, { force: true }), i);
  await page.waitForTimeout(900);
  const path = `output/rendered/slide-${String(i + 1).padStart(2, '0')}.png`;
  await page.screenshot({ path });
  const thumb = await sharp(path).resize(640, 360).png().toBuffer();
  tiles.push({ input: thumb, left: (i % 3) * 640, top: Math.floor(i / 3) * 360 });
}
await sharp({ create: { width: 1920, height: 1080, channels: 4, background: '#d4d4d2' } })
  .composite(tiles)
  .png()
  .toFile('output/contact-sheet.png');
await browser.close();
console.log(`rendered ${total} slides`);
