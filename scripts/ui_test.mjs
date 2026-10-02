// Functional UI checks against a running Tailrace that already has an escalated payout (run shoot.mjs ONLY=done first).
import { chromium } from 'playwright-core';
const base = process.argv[2] || 'http://localhost:8010';
const b = await chromium.launch({ executablePath: '/home/rogerkorantenng/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome', args: ['--no-sandbox'] });
const p = await (await b.newContext({ viewport: { width: 360, height: 800 } })).newPage();
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fails++; };
await p.goto(base); await p.waitForSelector('.card');
// 1. validation message names the fix
await p.fill('.send-form input', 'not-an-email'); await p.click('.send-form button');
ok((await p.textContent('.form-err')).includes('full email address'), 'bad email shows how to fix it');
// 2. text at 200% does not scroll sideways at 360px
await p.evaluate(() => { document.documentElement.style.fontSize = '200%'; }); await p.waitForTimeout(500);
ok(await p.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1), '200% text: no horizontal scroll at 360px');
await p.evaluate(() => { document.documentElement.style.fontSize = ''; });
// 3. every button and input is at least 28pt (37.3px) in both directions
const small = await p.evaluate(() => [...document.querySelectorAll('button, input:not([type=checkbox]), summary')].filter((e) => e.offsetParent).map((e) => [e.textContent.trim().slice(0, 24) || e.id, e.getBoundingClientRect()]).filter(([, r]) => r.height < 37.3 || r.width < 37.3).map(([n, r]) => `${n} ${Math.round(r.width)}x${Math.round(r.height)}`));
ok(small.length === 0, 'all controls >= 37.3px (28pt): ' + (small.join('; ') || 'none under'));
// 4. smallest text is at least 10pt (13.33px)
const tiny = await p.evaluate(() => { const out = new Set(); document.querySelectorAll('body *').forEach((e) => { if (!e.childNodes.length || ![...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) return; const fs = parseFloat(getComputedStyle(e).fontSize); if (fs < 13.3) out.add(e.tagName + ' ' + fs); }); return [...out]; });
ok(tiny.length === 0, 'no text under 10pt: ' + (tiny.join(', ') || 'ok'));
// 5. keyboard: tab reaches controls and focus is visible
await p.keyboard.press('Tab'); await p.keyboard.press('Tab');
const ring = await p.evaluate(() => { const e = document.activeElement; const c = getComputedStyle(e); return c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) >= 2; });
ok(ring, 'focused element shows an outline of 2px or more');
// 6. valid address starts a resend that lands
await p.setViewportSize({ width: 1280, height: 900 });
await p.fill('.send-form input', 'sb-patient@personal.example.com'); await p.click('.send-form button');
await p.waitForFunction(() => document.querySelector('#run-meta')?.textContent.includes('Finished') && document.querySelector('#headline').textContent.includes('5 of 6'), null, { timeout: 120000 });
ok(true, 'resend run finished and the headline now reads: ' + (await p.textContent('#headline')));
// 7. buttons that cannot work say why instead of doing nothing
await p.evaluate(() => document.querySelector('#b-seed').click()); ok((await p.textContent('#notice')).includes('already loaded'), 'disabled button explains itself');
await b.close(); process.exit(fails ? 1 : 0);
