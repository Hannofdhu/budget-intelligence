import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('/Users/hann/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser = await chromium.launch({ headless: true, executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
const context = await browser.newContext({ viewport: { width: 1600, height: 900 } });
const page = await context.newPage();
await page.goto('http://127.0.0.1:8899/index.html', { waitUntil: 'networkidle' });

const report = {};
report.slides = await page.locator('section.slide').count();
report.notes = await page.evaluate(() => window.__SPEAKER_NOTES__?.length || 0);
report.startIndex = await page.evaluate(() => window.__currentSlideIndex || 0);
await page.keyboard.press('ArrowRight');
await page.waitForTimeout(900);
report.afterArrow = await page.evaluate(() => window.__currentSlideIndex);
await page.keyboard.press('Escape');
report.overviewVisible = await page.locator('#overview').evaluate((el) => getComputedStyle(el).display !== 'none');
await page.keyboard.press('Escape');
await page.keyboard.press('b');
report.lowPower = await page.locator('body').evaluate((el) => el.classList.contains('low-power'));

const popupPromise = context.waitForEvent('page', { timeout: 5000 }).catch(() => null);
await page.keyboard.press('p');
const audience = await popupPromise;
await page.waitForTimeout(1000);
report.presenterMode = await page.locator('body').evaluate((el) => el.classList.contains('ppt-presenter'));
report.audienceOpened = Boolean(audience);
await page.screenshot({ path: 'output/presenter-mode.png' });

console.log(JSON.stringify(report, null, 2));
await browser.close();
