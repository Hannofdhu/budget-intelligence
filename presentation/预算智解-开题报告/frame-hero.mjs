import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const sharp = require('/Users/hann/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');

const canvas = { width: 2100, height: 900 };
const bg = 'images/ikb-dot-gradient.webp';
const shot = 'images/01-source-workspace.png';
const out = 'images/05-workspace-hero.png';

const screenshot = await sharp(shot)
  .resize({ width: 1370, height: 790, fit: 'contain', background: '#fafaf8' })
  .png()
  .toBuffer();

await sharp(bg)
  .resize(canvas.width, canvas.height, { fit: 'cover' })
  .composite([
    { input: { create: { width: canvas.width, height: canvas.height, channels: 4, background: { r: 0, g: 47, b: 167, alpha: 0.42 } } }, blend: 'over' },
    { input: screenshot, left: 650, top: 55 },
    { input: { create: { width: 12, height: 790, channels: 4, background: '#ffffff' } }, left: 638, top: 55 }
  ])
  .webp({ quality: 92 })
  .toFile(out);

console.log(out);
