// Tailrace UI. No framework: each panel re-renders only when its data changes, so typing is never interrupted.
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const icon = (n, cls = '') => `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
const money = (cur, v) => { const n = Number(v); try { return new Intl.NumberFormat(cur === 'USD' ? 'en-US' : 'en-GB', { style: 'currency', currency: cur }).format(n); } catch { return `${cur} ${n.toFixed(2)}`; } };
const state = { app: null, runId: null, run: null, followLatest: true, agentTab: null, busy: false, resetArmed: false, seen: {} };

async function api(path, opts = {}) {
  const r = await fetch(path, { method: opts.body !== undefined || opts.method ? (opts.method || 'POST') : 'GET', headers: { 'Content-Type': 'application/json' }, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  let j = null; try { j = await r.json(); } catch { /* empty body */ }
  if (!r.ok) throw new Error(j?.detail || j?.error || `Request failed (${r.status})`);
  return j;
}
function paint(key, html, el) { if (state.seen[key] !== html) { state.seen[key] = html; el.innerHTML = html; } }
function notice(msg, kind = '') { const n = $('#notice'); n.textContent = msg; n.className = 'notice ' + kind; }

// ---- vocab
const OB = {
  queued: ['Queued', 'dot', 'mut'], sending: ['Sending', 'run', 'live'], settled: ['Landed', 'check', 'ok'], unclaimed: ['Held by PayPal', 'clock', 'warn'],
  failed: ['Failed', 'cross', 'bad'], returned: ['Returned to sender', 'return', 'mut'], held: ['On hold', 'pause', 'warn'], escalated: ['Needs a person', 'flag', 'warn'], stopped: ['Stopped', 'stop', 'mut'],
};
const STEP = { running: ['Running', 'run', 'live'], retrying: ['Retrying', 'retry', 'warn'], succeeded: ['Done', 'check', 'ok'], failed: ['Failed', 'cross', 'bad'] };
const ACT = { retry: ['Retry', 'retry'], correct: ['Correct the address and resend', 'check'], escalate: ['Escalate to a person', 'flag'], stop: ['Stop', 'stop'] };
const tag = (m, extra = '') => `<span class="tag ${m[2]}">${icon(m[1], m[1] === 'run' && extra === 'spin' ? 'spin' : '')}${esc(m[0])}</span>`;
const obTag = (s) => tag(OB[s] || [s, 'dot', 'mut'], s === 'sending' ? 'spin' : '');
const stepTag = (s) => tag(STEP[s] || [s, 'dot', 'mut'], s === 'running' ? 'spin' : '');
const KIND = { settle: 'Payout run', dunning: 'Dunning sweep', nightly: 'Nightly sweep', resend: 'Resend', webhook: 'Webhook' };

function ago(ts) {
  if (!ts) return '';
  const s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`; if (s < 3600) return `${Math.round(s / 60)} min ago`; return `${Math.round(s / 3600)} h ago`;
}

// ---- header + hero
function renderEnv(env) {
  const exec = { 'render': 'Render Workflows', 'render-local': 'Render Workflows (local task server)', 'inline': 'In-process fallback, not Render' }[env.executor] || env.executor;
  $('#exec-val').textContent = exec;
  const short = env.agent.replace(/^Claude via Amazon Bedrock.*/, 'Claude on Bedrock').replace(/^Claude via Anthropic API.*/, 'Claude (Anthropic API)').replace(/^Rule-based.*/, 'Scripted policy, no model key');
  $('#agent-val').textContent = short; $('#chip-agent').title = env.agent;
}
function counts(obs) { const c = {}; obs.forEach((o) => { c[o.status] = (c[o.status] || 0) + 1; }); return c; }
function renderHero(s) {
  const obs = s.obligations; const c = counts(obs); const n = obs.length;
  let h, l;
  if (!n) { h = 'Nothing in the ledger yet.'; l = 'Load six sample payouts, then send them. PayPal answers each one differently, and the pipeline has to cope with every answer.'; }
  else if (c.queued === n) { h = `${n} payouts are queued and none has been sent.`; l = 'Send them as one run. Each currency goes as its own batch, in parallel, and the run waits for PayPal to say where every item ended up.'; }
  else if (c.sending) { h = `${c.sending} of ${n} payouts are still with PayPal.`; l = 'A 201 from PayPal proves nothing. The run keeps polling until each item reaches a final state.'; }
  else {
    const parts = [];
    if (c.settled) parts.push(`${c.settled} of ${n} payouts landed`);
    if (c.escalated) parts.push(`${c.escalated} waiting on a person`);
    if (c.stopped) parts.push(`${c.stopped} stopped on purpose`);
    if (c.unclaimed) parts.push(`${c.unclaimed} held by PayPal`);
    if (c.failed) parts.push(`${c.failed} failed`);
    h = parts.join(', ') + '.';
    l = c.escalated ? 'The agent fixed what it safely could. What is left needs an address only a person can confirm.' : 'Every item has reached a final state, and the ledger agrees with PayPal.';
  }
  $('#headline').textContent = h; $('#lede').textContent = l;
}
function renderControls(s) {
  const obs = s.obligations; const queued = obs.filter((o) => o.status === 'queued' && o.attempts === 0).length;
  const running = s.runs.some((r) => r.status === 'running');
  const set = (id, off, why) => { const b = $(id); b.setAttribute('aria-disabled', off ? 'true' : 'false'); b.title = off ? why : ''; };
  set('#b-seed', running || obs.length > 0, obs.length ? 'The sample payouts are already loaded.' : '');
  set('#b-settle', running || !queued, running ? 'A run is still going.' : 'Nothing is queued. Load the sample payouts first.');
  set('#b-inv', running || s.invoices.length > 0, 'The sample invoices are already loaded.');
  set('#b-dun', running || !s.invoices.length, running ? 'A run is still going.' : 'Load the sample invoices first.');
  set('#b-night', running, 'A run is still going.');
  set('#b-reset', running, 'A run is still going.');
  state.busy = running;
}

// ---- payouts
function renderPayouts(s) {
  const rows = s.obligations.map((o) => {
    const l = o.latest || {};
    const say = l.item_status ? `PayPal says <span class="mono">${esc(l.item_status)}${l.error_name ? ' / ' + esc(l.error_name) : ''}</span>` : 'Not sent yet';
    return `<li><div class="row-top"><span class="who">${esc(o.payee_name)} <span class="row-sub mono">${esc(o.id)}</span></span><span class="amt">${esc(money(o.currency, o.amount))}</span></div>
      <div class="row-top">${obTag(o.status)}<span class="row-sub">attempt ${o.attempts} of ${s.max_attempts}</span></div>
      <p class="row-sub">${say}. To <span class="mono">${esc(o.receiver)}</span></p>
      ${o.outcome && ['stopped', 'escalated'].includes(o.status) ? `<p class="row-sub">${esc(o.outcome)}</p>` : ''}</li>`;
  }).join('');
  paint('payouts', rows || '<li class="empty">No payouts yet. Load the samples to begin.</li>', $('#payouts'));
  const tot = (st) => { const m = {}; s.obligations.filter((o) => o.status === st).forEach((o) => { m[o.currency] = (m[o.currency] || 0) + Number(o.amount); }); return m; };
  const t = tot('settled'); const keys = Object.keys(t);
  $('#ledger-line').textContent = keys.length ? 'Landed: ' + keys.map((k) => money(k, t[k])).join(' and ') : '';
}

// ---- needs a person
function renderNeeds(s) {
  const el = $('#needs');
  const esc_ = s.escalations;
  if (!esc_.length) { paint('needs', '<p class="empty">Nothing is waiting on you.</p>', el); return; }
  const html = esc_.map((e) => {
    if (e.subject_type === 'payout') {
      const o = s.obligations.find((x) => x.id === e.subject_id) || {};
      return `<article class="card" data-eid="${e.id}"><h3>${esc(o.payee_name || e.subject_id)}, ${esc(money(o.currency || 'USD', o.amount || 0))}</h3>
        <p>${esc(e.reason)}</p>
        <form class="send-form" data-eid="${e.id}" novalidate><label class="field-label" for="addr-${e.id}">Payee's confirmed PayPal email</label>
        <div class="inline-form"><input type="email" id="addr-${e.id}" name="address" autocomplete="off" placeholder="name@example.com" aria-describedby="err-${e.id}"><button class="btn primary" type="submit">Send to this address</button></div>
        <p class="form-err" id="err-${e.id}" role="alert"></p></form></article>`;
    }
    const inv = s.invoices.find((x) => x.id === e.subject_id) || {};
    return `<article class="card" data-eid="${e.id}"><h3>${esc(inv.payee_name || e.subject_id)}, invoice</h3><p>${esc(e.reason)}</p>
      <div><button class="btn close-esc" type="button" data-eid="${e.id}">Mark as handled</button></div></article>`;
  }).join('');
  if (state.seen.needs !== html) {
    const active = document.activeElement; const keepId = active && active.id && el.contains(active) ? active.id : null; const val = keepId ? active.value : null;
    paint('needs', html, el);
    if (keepId) { const n = document.getElementById(keepId); if (n) { n.value = val; n.focus(); } }
  }
}

// ---- invoices
function renderInvoices(s) {
  const rows = s.invoices.map((i) => {
    const d = i.days_overdue;
    const due = d > 0 ? `<span class="tag ${d >= 14 ? 'bad' : 'warn'}">${icon(d >= 14 ? 'flag' : 'clock')}${d} days overdue</span>` : `<span class="tag mut">${icon('dot')}Due in ${-d} days</span>`;
    const sent = i.reminders.map((r) => `Stage ${r.stage} ${r.state === 'sent' ? 'sent' : r.state}`).join(', ');
    const paid = Number(i.paypal_paid) > 0 ? ` ${money(i.currency, i.paypal_paid)} already paid.` : '';
    return `<li><div class="row-top"><span class="who">${esc(i.payee_name)}</span><span class="amt">${esc(money(i.currency, i.balance))}</span></div>
      <div class="row-top">${due}<span class="row-sub">${esc(i.description || '')}</span></div>
      <p class="row-sub">${sent ? 'Reminders: ' + esc(sent) + '.' : 'No reminder sent yet.'}${esc(paid)}</p>
      <details class="mini"><summary>Sandbox: record a payment</summary>
        <form class="inline-form pay-form" data-id="${esc(i.id)}" novalidate><label class="field-label" for="p-${esc(i.id)}" style="margin:0">Amount</label>
        <input type="text" inputmode="decimal" id="p-${esc(i.id)}" name="amount" placeholder="50.00" autocomplete="off"><button class="btn" type="submit">Record payment</button></form><p class="form-err" role="alert"></p></details></li>`;
  }).join('');
  paint('invoices', rows || '<li class="empty">No invoices yet. Load the samples to create five.</li>', $('#invoices'));
}

// ---- webhooks
function renderWebhooks(s) {
  const head = `<p class="sub" style="padding:.8rem 1rem">${s.env.webhook_verifying ? 'Signature checking is on. A body that was changed in transit is refused with 401.' : 'Signature checking is off until a webhook id is set.'}</p>`;
  // PayPal sends several event types per batch and each carries the same re-read
  // line. Group by that line so it is said once, with the event types beside it.
  const groups = new Map();
  for (const w of s.webhooks) {
    const key = (w.detail || '') + '|' + (w.verified ? 'v' : 'x');
    if (!groups.has(key)) groups.set(key, { detail: w.detail, verified: w.verified, at: w.received_at, types: [] });
    const g = groups.get(key);
    if (!g.types.includes(w.event_type)) g.types.push(w.event_type);
  }
  const rows = [...groups.values()].map((g) => `<div class="wh">${g.types.map((x) => `<span class="mono">${esc(x)}</span>`).join(' ')}<span>${g.verified ? tag(['Signature valid', 'check', 'ok']) : tag(['Rejected', 'cross', 'bad'])} <span class="row-sub">${ago(g.at)}</span></span>${g.detail ? `<span class="row-sub">${esc(g.detail)}</span>` : ''}</div>`).join('');
  paint('webhooks', head + (rows || '<p class="empty" style="padding-top:0">No events yet. Signed events from PayPal will appear here.</p>'), $('#webhooks'));
}

// ---- runs
function renderRunPicker(s) {
  // One webhook delivery is one run, so a busy minute produces a dozen identical
  // pills. They are counted instead; the runs worth picking are the ones a person started.
  const hooks = s.runs.filter((r) => r.kind === 'webhook');
  const picks = s.runs.filter((r) => r.kind !== 'webhook');
  const pill = (r) => {
    const st = r.status === 'running' ? 'Running' : r.status === 'done' ? 'Done' : r.status === 'failed' ? 'Failed' : 'Needs attention';
    return `<button class="run-pill" type="button" data-run="${esc(r.id)}" aria-pressed="${r.id === state.runId}">${esc(KIND[r.kind] || r.kind)} <span class="mono">${esc(r.id)}</span> <span class="row-sub">${st}</span></button>`;
  };
  const counted = hooks.length ? `<span class="sub">${hooks.length} webhook${hooks.length === 1 ? '' : 's'} ingested</span>` : '';
  const html = picks.length || hooks.length
    ? picks.map(pill).join('') + counted
    : '<span class="sub">No runs yet.</span>';
  paint('runs', html, $('#run-picker'));
}
function renderRun(d) {
  const meta = $('#run-meta'); const el = $('#steps');
  if (!d) { paint('meta', '', meta); paint('steps', '<p class="empty">Send the queued payouts to see the steps.</p>', el); return; }
  const r = d.run; const ex = r.render_run_id ? `<span>Render run <b class="mono">${esc(r.render_run_id)}</b></span>` : '';
  const dur = r.finished_at ? `${((new Date(r.finished_at) - new Date(r.started_at)) / 1000).toFixed(0)}s` : 'in progress';
  const retried = d.steps.reduce((n, s) => n + s.retries, 0);
  const st = r.status === 'running' ? 'Running' : r.status === 'done' ? 'Finished' : r.status === 'failed' ? 'Failed' : 'Finished, needs attention';
  paint('meta', `<span><b>${esc(st)}</b></span><span>${d.steps.length} steps</span><span>${retried} ${retried === 1 ? 'retry' : 'retries'}</span><span>${esc(dur)}</span>${ex}${r.status === 'failed' && r.summary?.error ? `<span class="tag bad">${icon('cross')}${esc(r.summary.error)}</span>` : ''}`, meta);
  const total = Math.max(1000, ...d.steps.map((s) => s.start_ms + s.dur_ms));
  const depth = {}; const byKey = Object.fromEntries(d.steps.map((s) => [s.key, s]));
  const dep = (s) => { if (depth[s.key] !== undefined) return depth[s.key]; depth[s.key] = s.parent && byKey[s.parent] ? dep(byKey[s.parent]) + 1 : 0; return depth[s.key]; };
  // order: depth-first so children sit under their parent
  const kids = {}; d.steps.forEach((s) => { (kids[s.parent && byKey[s.parent] ? s.parent : ''] ||= []).push(s); });
  const ordered = []; const walk = (k) => (kids[k] || []).forEach((s) => { ordered.push(s); walk(s.key); }); walk('');
  const html = ordered.map((s) => {
    const left = (s.start_ms / total) * 100; const w = Math.max(0.8, (s.dur_ms / total) * 100);
    const att = s.retries ? `<span class="badge">attempt ${s.attempts.length}</span>` : '';
    const lastFail = [...s.attempts].reverse().find((a) => a.outcome === 'retrying' || a.outcome === 'failed');
    const detail = s.state === 'failed' || s.state === 'retrying' ? `<span class="err">${esc(lastFail?.detail || s.detail)}</span>` : esc(s.detail);
    const retriedNote = s.retries && s.state === 'succeeded' && lastFail ? ` <span class="err">Attempt ${lastFail.attempt} failed: ${esc(lastFail.detail)}</span>` : '';
    return `<div class="step" style="--d:${dep(s)}"><div class="step-head">${stepTag(s.state)}<span class="step-name">${esc(s.task)}</span><span class="step-subj">${esc(s.subject)}</span>${att}</div>
      <div class="step-detail">${detail}${retriedNote}</div>
      <div class="track" aria-hidden="true"><div class="bar ${s.state}" style="left:${left.toFixed(2)}%;width:${Math.min(w, 100 - left).toFixed(2)}%"></div></div></div>`;
  }).join('');
  paint('steps', html, el);
}

function renderAgent(d) {
  const tabs = $('#agent-tabs'); const body = $('#agent-body');
  const ids = d ? [...new Set(d.agent_turns.map((t) => t.obligation_id))] : [];
  if (!ids.length) { paint('atabs', '', tabs); paint('agent', '<p class="empty">Nothing failed in this run.</p>', body); return; }
  if (!ids.includes(state.agentTab)) state.agentTab = ids[0];
  paint('atabs', ids.map((id) => `<button class="tab" role="tab" type="button" data-ob="${esc(id)}" aria-selected="${id === state.agentTab}">${esc(id)}</button>`).join(''), tabs);
  const turns = d.agent_turns.filter((t) => t.obligation_id === state.agentTab);
  const dec = d.decisions.find((x) => x.obligation_id === state.agentTab);
  const parts = turns.map((t) => {
    const p = t.payload || {};
    if (t.kind === 'start') return `<p class="src">Decided by ${esc(p.provider)}</p>`;
    if (t.kind === 'thought') return `<div class="turn thought"><span class="lbl">Agent</span><p class="txt">${esc(p.text)}</p></div>`;
    if (t.kind === 'call') return `<div class="turn"><span class="lbl">Tool call</span><p class="call">${esc(t.name)}(${esc(Object.values(p).map((v) => JSON.stringify(v)).join(', '))})</p></div>`;
    if (t.kind === 'result') {
      const refused = (p.problems && p.problems.length) || p.accepted === false;
      if (refused) return `<div class="turn refused"><span class="lbl">Rules gate refused ${esc(t.name)}</span><ul>${p.problems.map((x) => `<li>${icon('cross')} ${esc(x)}</li>`).join('')}</ul></div>`;
      return `<details class="out"><summary>${icon('check')}&nbsp;Result of ${esc(t.name)}</summary><pre>${esc(JSON.stringify(p, null, 2))}</pre></details>`;
    }
    if (t.kind === 'fallback') return `<div class="turn refused"><span class="lbl">No valid decision</span><p class="txt">${esc(p.reasoning)}</p></div>`;
    return '';
  }).join('');
  const a = dec ? ACT[dec.action] || [dec.action, 'dot'] : null;
  const decHtml = dec ? `<div class="decision"><div class="act">${icon(a[1])} ${esc(a[0])}${dec.address ? ` <span class="mono">${esc(dec.address)}</span>` : ''}</div><p>${esc(dec.reasoning)}</p>
     <p class="src">Outcome: ${dec.result ? esc(dec.result) : 'applying'}. ${dec.gate && dec.gate.length ? 'The rules gate refused ' + dec.gate.length + ' earlier proposal' + (dec.gate.length > 1 ? 's' : '') + ' first.' : 'The rules gate accepted the first proposal.'}</p></div>` : '';
  paint('agent', parts + decHtml, body);
}

function renderRec(d) {
  const el = $('#rec-body');
  if (!d || !d.reconciliation.length) { paint('rec', '<p class="empty">Nothing to reconcile yet.</p>', el); return; }
  const V = { match: ['Match', 'check', 'ok'], healed: ['Ledger corrected', 'retry', 'info'], mismatch: ['Needs a person', 'flag', 'bad'] };
  paint('rec', d.reconciliation.map((r) => `<div class="rec"><span class="mono">${esc(r.subject_id.length > 14 ? r.subject_id.slice(0, 14) + '...' : r.subject_id)}</span>
    <div class="rec-pair"><span class="row-sub">Ledger</span><span class="mono">${esc(r.ledger)}</span></div>
    <div class="rec-pair"><span class="row-sub">PayPal</span><span class="mono">${esc(r.paypal)}</span></div>
    <div>${tag(V[r.verdict] || [r.verdict, 'dot', 'mut'])}${r.note ? `<p class="row-sub">${esc(r.note)}</p>` : ''}</div></div>`).join(''), el);
}

// ---- data loop
async function loadState() {
  const s = await api('/api/state'); state.app = s;
  if (state.followLatest && s.runs.length) state.runId = s.runs[0].id;
  renderEnv(s.env); renderHero(s); renderControls(s); renderPayouts(s); renderNeeds(s); renderInvoices(s); renderWebhooks(s); renderRunPicker(s);
}
async function loadRun() {
  if (!state.runId) { renderRun(null); renderAgent(null); renderRec(null); return; }
  try { state.run = await api('/api/runs/' + encodeURIComponent(state.runId)); } catch { state.run = null; }
  renderRun(state.run); renderAgent(state.run); renderRec(state.run);
}
let timer;
async function tick() {
  try { await loadState(); await loadRun(); } catch (e) { notice('Lost contact with the server. Retrying.', 'err'); }
  clearTimeout(timer); timer = setTimeout(tick, state.busy ? 1500 : 5000);
}

async function act(btn, fn, okMsg) {
  if (btn.getAttribute('aria-disabled') === 'true') { notice(btn.title || 'That is not available right now.', 'err'); return; }
  btn.setAttribute('aria-disabled', 'true'); notice('Working...');
  try { const r = await fn(); notice(okMsg ? okMsg(r) : '', 'ok'); if (r && r.run_id) { state.runId = r.run_id; state.followLatest = true; } }
  catch (e) { notice(e.message, 'err'); }
  await tick();
}

// ---- events
document.addEventListener('click', (e) => {
  const t = e.target.closest('button'); if (!t) return;
  if (t.id === 'b-seed') act(t, () => api('/api/seed', { method: 'POST' }), (r) => `${r.added} payouts queued.`);
  else if (t.id === 'b-inv') act(t, () => api('/api/invoices/seed', { method: 'POST' }), (r) => `${r.created} invoices created and sent in the PayPal sandbox.`);
  else if (t.id === 'b-settle') act(t, () => api('/api/runs/settle', { method: 'POST', body: { crash: $('#crash').checked } }), () => 'Run started. Watch the pipeline below.');
  else if (t.id === 'b-dun') act(t, () => api('/api/runs/dunning', { method: 'POST' }), () => 'Dunning sweep started.');
  else if (t.id === 'b-night') act(t, () => api('/api/runs/nightly', { method: 'POST' }), () => 'Nightly sweep started: payouts and dunning in parallel.');
  else if (t.id === 'b-reset') {
    if (t.getAttribute('aria-disabled') === 'true') return;
    if (!state.resetArmed) { state.resetArmed = true; t.textContent = 'Press again to clear everything'; setTimeout(() => { state.resetArmed = false; t.textContent = 'Clear everything'; }, 5000); return; }
    state.resetArmed = false; t.textContent = 'Clear everything'; state.runId = null; state.seen = {};
    act(t, () => api('/api/reset', { method: 'POST' }), () => 'Cleared. PayPal keeps what it already holds.');
  } else if (t.id === 'theme') {
    const cur = document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    const next = cur === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = next;
    try { localStorage.setItem('tailrace-theme', next); } catch { /* storage blocked */ }
  } else if (t.classList.contains('run-pill')) { state.runId = t.dataset.run; state.followLatest = state.app.runs[0]?.id === t.dataset.run; loadRun(); renderRunPicker(state.app); }
  else if (t.classList.contains('tab')) { state.agentTab = t.dataset.ob; renderAgent(state.run); }
  else if (t.classList.contains('close-esc')) act(t, () => api(`/api/escalations/${t.dataset.eid}/close`, { method: 'POST' }), () => 'Marked as handled.');
});
document.addEventListener('submit', async (e) => {
  const f = e.target; e.preventDefault();
  if (f.classList.contains('send-form')) {
    const err = f.querySelector('.form-err'); err.textContent = '';
    const address = f.address.value.trim();
    if (!/^\S+@\S+\.\S+$/.test(address)) { err.textContent = 'Enter a full email address such as name@example.com.'; f.address.focus(); return; }
    const b = f.querySelector('button'); b.setAttribute('aria-disabled', 'true');
    try { const r = await api(`/api/escalations/${f.dataset.eid}/send`, { body: { address } }); state.runId = r.run_id; state.followLatest = true; notice('Resend started.', 'ok'); }
    catch (ex) { err.textContent = ex.message; }
    b.removeAttribute('aria-disabled'); tick();
  } else if (f.classList.contains('pay-form')) {
    const err = f.parentElement.querySelector('.form-err'); err.textContent = '';
    const amount = f.amount.value.trim();
    if (!/^\d+(\.\d{1,2})?$/.test(amount)) { err.textContent = 'Enter an amount such as 50.00.'; f.amount.focus(); return; }
    try { await api(`/api/invoices/${encodeURIComponent(f.dataset.id)}/payment`, { body: { amount } }); notice(`Recorded ${amount} at PayPal. The next sweep will notice it.`, 'ok'); f.amount.value = ''; }
    catch (ex) { err.textContent = ex.message; }
  }
});
tick();
