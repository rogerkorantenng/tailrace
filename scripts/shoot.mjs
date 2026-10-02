// Drives the real UI against a running Tailrace and takes screenshots at four widths in both themes.
// usage: node scripts/shoot.mjs <round-name> [baseUrl]
import { chromium } from 'playwright-core';
import fs from 'node:fs';
const round = process.argv[2] || 'r1';
const base = process.argv[3] || 'http://localhost:8010';
const out = `shots/${round}`; fs.mkdirSync(out, { recursive: true });
const sizes = [360, 768, 1280, 1920];
const browser = await chromium.launch({ executablePath: '/home/rogerkorantenng/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome', args: ['--no-sandbox'] });
const ctxs = {};
for (const scheme of ['dark', 'light']) ctxs[scheme] = await browser.newContext({ colorScheme: scheme, viewport: { width: 1280, height: 900 } });
const pages = {}; for (const k of Object.keys(ctxs)) { pages[k] = await ctxs[k].newPage(); pages[k].on('pageerror', (e) => console.log('PAGEERROR', k, e.message)); pages[k].on('console', (m) => m.type() === 'error' && console.log('CONSOLE', k, m.text())); }
async function shootAll(state) {
  for (const scheme of ['dark', 'light']) {
    const p = pages[scheme];
    await p.goto(base); await p.waitForTimeout(1200);
    for (const w of sizes) { await p.setViewportSize({ width: w, height: 900 }); await p.waitForTimeout(250); await p.screenshot({ path: `${out}/${state}-${scheme}-${w}.png`, fullPage: true }); }
  }
}
const p = pages.dark;
const post = (path, body) => fetch(base + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).then((r) => r.json());
const only = process.env.ONLY ? process.env.ONLY.split(',') : null;
const want = (s) => !only || only.includes(s);
if (want('empty')) { await post('/api/reset'); await shootAll('s0-empty'); }
if (want('seeded')) { await post('/api/reset'); await post('/api/seed'); await shootAll('s1-seeded'); }
if (want('running') || want('done') || want('dunning')) {
  await post('/api/reset'); await post('/api/seed'); await post('/api/invoices/seed');
  const r = await post('/api/runs/settle', { crash: true });
  console.log('run', r);
  if (want('running')) { await p.goto(base); await p.waitForTimeout(16000); await shootAll('s2-running'); }
  for (let i = 0; i < 80; i++) { const s = await (await fetch(base + '/api/state')).json(); if (!s.runs.some((x) => x.status === 'running')) break; await new Promise((r) => setTimeout(r, 3000)); }
  if (want('done')) { await shootAll('s3-done'); }
  if (want('dunning')) {
    const d = await post('/api/runs/dunning'); console.log('dunning', d);
    for (let i = 0; i < 60; i++) { const s = await (await fetch(base + '/api/state')).json(); if (!s.runs.some((x) => x.status === 'running')) break; await new Promise((r) => setTimeout(r, 3000)); }
    await shootAll('s4-dunning');
  }
}
await browser.close();
