'use strict';
/*
  AutoPilot AI — app.js
  Requires config.js loaded first (defines CONFIG).

  ID map — every $('x') here has id="x" in index.html:
    run-btn, stop-btn, goal-input
    sdot, slabel
    cfg-live, cfg-max-steps, cfg-poll, cfg-url
    view-idle, view-run, view-result
    live-badge, goal-chip, step-feed, run-url-text
    rs-steps, rs-actions, rs-elapsed
    res-glow, res-icon, res-status, res-value, res-meta
    stat-steps, stat-actions, stat-elapsed, stat-polls
    log-scroll, log-stream, log-result
*/

// ─── State ───────────────────────────────────────────────────────────────────
const S = {
  running:       false,
  sessionId:     null,
  pollTimer:     null,
  tickTimer:     null,
  startTime:     null,
  pollErrors:    0,
  stepCount:     0,
  actionCount:   0,
  pollCount:     0,
  rendered:      new Set(),
};

// ─── DOM helpers ─────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

function setText(id, v) {
  const el = $(id);
  if (el) el.textContent = v;
}

function setDisplay(id, v) {
  const el = $(id);
  if (el) el.style.display = v;
}

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ─── Theater: switch between idle / running / result ─────────────────────────
function showView(name) {
  ['idle', 'run', 'result'].forEach(n => {
    setDisplay(`view-${n}`, n === name ? '' : 'none');
  });
}

// ─── Preset ───────────────────────────────────────────────────────────────────
function setGoal(text) {
  const el = $('goal-input');
  if (el) { el.value = text; el.focus(); }
}

// ─── Log (right panel) ───────────────────────────────────────────────────────
const TAG = {
  action:  'tag-act',
  nav:     'tag-nav',
  think:   'tag-think',
  success: 'tag-ok',
  warn:    'tag-warn',
  error:   'tag-err',
  done:    'tag-ok',
};

function nowStr() {
  return new Date().toLocaleTimeString('en', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function log(text, type = 'info', tag = '') {
  const stream = $('log-stream');
  if (!stream) return;
  const tagHtml = tag && TAG[tag] ? `<span class="log-tag ${TAG[tag]}">${esc(tag)}</span>` : '';
  const row = document.createElement('div');
  row.className = 'log-row';
  row.innerHTML = `<span class="log-ts">${nowStr()}</span><span class="log-body t-${esc(type)}">${tagHtml}${esc(text)}</span>`;
  stream.appendChild(row);
  const sc = $('log-scroll');
  if (sc) requestAnimationFrame(() => { sc.scrollTop = sc.scrollHeight; });
}

function clearTrace() {
  const s = $('log-stream');  if (s) s.innerHTML = '';
  const r = $('log-result');  if (r) r.innerHTML = '';
}

// ─── Step cards (center feed) ─────────────────────────────────────────────────
const ACT_CLASS = {
  click: 'ac-click', navigate: 'ac-nav', scroll: 'ac-scroll',
  type: 'ac-type', done: 'ac-done', report: 'ac-rep',
};

function addCard(step) {
  const feed = $('step-feed');
  if (!feed) return;
  const action = (step.action || '').toLowerCase();
  const conf   = Math.round(parseFloat(step.confidence ?? 0) * 100);
  const bar    = conf >= 70 ? 'var(--green)' : conf >= 40 ? 'var(--amber)' : 'var(--red)';

  const card = document.createElement('div');
  card.className = 'step-card';
  card.innerHTML = `
    <div class="sc-num${action === 'done' ? ' sc-done' : ''}">${step.step ?? S.stepCount}</div>
    <div class="sc-body">
      <div class="sc-row">
        <span class="ac-chip ${ACT_CLASS[action] || 'ac-click'}">${esc(action)}</span>
        ${step.target_text ? `<span class="sc-target">${esc(step.target_text)}</span>` : ''}
      </div>
      ${step.reason ? `<div class="sc-reason">${esc(step.reason)}</div>` : ''}
      ${step.url    ? `<div class="sc-url">🔗 ${esc(step.url)}</div>` : ''}
      <div class="sc-bar"><div style="width:${conf}%;height:100%;background:${bar};border-radius:2px;transition:width .4s"></div></div>
    </div>`;
  feed.appendChild(card);
  requestAnimationFrame(() => { feed.scrollTop = feed.scrollHeight; });
  if (step.url) setText('run-url-text', step.url);
}

// ─── Stats ────────────────────────────────────────────────────────────────────
function resetStats() {
  Object.assign(S, { stepCount:0, actionCount:0, pollCount:0, pollErrors:0, rendered: new Set() });
  clearInterval(S.tickTimer);
  ['stat-steps','stat-actions','stat-polls','rs-steps','rs-actions'].forEach(id => setText(id, '0'));
  setText('stat-elapsed', '0s');
  setText('rs-elapsed',   '0s');
}

function startTick() {
  S.startTime = Date.now();
  S.tickTimer = setInterval(() => {
    const s = Math.floor((Date.now() - S.startTime) / 1000) + 's';
    setText('stat-elapsed', s);
    setText('rs-elapsed',   s);
  }, 1000);
}

function bumpStep() {
  S.stepCount++;
  setText('stat-steps', S.stepCount); setText('rs-steps', S.stepCount);
}
function bumpAction() {
  S.actionCount++;
  setText('stat-actions', S.actionCount); setText('rs-actions', S.actionCount);
}
function bumpPoll() {
  S.pollCount++;
  setText('stat-polls', S.pollCount);
}

// ─── Button state ─────────────────────────────────────────────────────────────
function setRunning(on) {
  S.running = on;
  const btn  = $('run-btn');
  const stop = $('stop-btn');
  if (btn)  { btn.classList.toggle('loading', on); btn.disabled = on; }
  if (stop) stop.style.display = on ? 'flex' : 'none';
  const dot = $('sdot');
  const lbl = $('slabel');
  if (on) {
    if (dot) dot.className = 'sdot running';
    if (lbl) lbl.textContent = 'Running';
  }
}

function setFinal(isError) {
  const dot = $('sdot');
  const lbl = $('slabel');
  if (dot) dot.className = `sdot ${isError ? 'error' : 'done'}`;
  if (lbl) lbl.textContent = isError ? 'Error' : 'Done';
}

// ─── Reset ────────────────────────────────────────────────────────────────────
function resetUI() {
  clearTrace();
  resetStats();
  setRunning(false);
  showView('idle');
  const dot = $('sdot'); if (dot) dot.className = 'sdot idle';
  const lbl = $('slabel'); if (lbl) lbl.textContent = 'Idle';
  const feed = $('step-feed'); if (feed) feed.innerHTML = '';
  setText('run-url-text', '—');
  setText('goal-chip', '');
  setDisplay('live-badge', 'none');
}

// ─── Fetch with timeout ───────────────────────────────────────────────────────
async function fetchT(url, opts, ms) {
  const ctrl = new AbortController();
  const id   = setTimeout(() => ctrl.abort(), ms);
  try   { return await fetch(url, { ...opts, signal: ctrl.signal }); }
  finally { clearTimeout(id); }
}

// ─── RUN AGENT ────────────────────────────────────────────────────────────────
async function runAgent() {
  if (S.running) return;

  const goalEl = $('goal-input');
  const goal   = goalEl ? goalEl.value.trim() : '';
  if (!goal) {
    if (goalEl) { goalEl.focus(); goalEl.style.borderColor = 'var(--red)'; }
    setTimeout(() => { if (goalEl) goalEl.style.borderColor = ''; }, 2000);
    return;
  }

  // Reset UI
  const feed = $('step-feed'); if (feed) feed.innerHTML = '';
  setText('run-url-text', '—');
  clearTrace();
  resetStats();
  setRunning(true);
  startTick();

  // Read config
  // cfg-live: CHECKED = live Chrome (headless=false), UNCHECKED = headless
  const liveEl   = $('cfg-live');
  const liveMode = liveEl ? liveEl.checked : true;
  const headless = !liveMode;  // live=true → headless=false

  const maxSteps = parseInt(($('cfg-max-steps') || {}).value || CONFIG.MAX_STEPS, 10);
  const pollMs   = parseInt(($('cfg-poll')       || {}).value || 1000, 10);

  // Switch theater to running view
  setText('goal-chip', `"${goal}"`);
  showView('run');
  setDisplay('live-badge', liveMode ? '' : 'none');

  log(`Goal: "${goal}"`, 'info');
  log(`live_chrome=${liveMode}  max_steps=${maxSteps}  headless=${headless}`, 'info');
  if (liveMode) log('Chrome is opening — watch the AI browse live!', 'success', 'done');
  else          log('Running headless — no browser window.', 'info');

  try {
    const resp = await fetchT(
      `${CONFIG.API_BASE}/run`,
      {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ instruction: goal, max_steps: maxSteps, headless }),
      },
      CONFIG.RUN_TIMEOUT_MS,
    );

    if (!resp.ok) {
      const txt = await resp.text().catch(() => resp.statusText);
      throw new Error(`Server error ${resp.status}: ${txt}`);
    }

    const data = await resp.json();
    if (!data.session_id) throw new Error('Backend did not return session_id');

    S.sessionId = data.session_id;
    log(`Session: ${data.session_id}`, 'info');
    startPolling(pollMs);

  } catch (err) {
    const msg = err.name === 'AbortError'
      ? `Timed out. Is the backend running at ${CONFIG.API_BASE}?`
      : String(err.message || err);
    log(msg, 'error', 'error');
    showResult(msg, true);
    finish(true);
  }
}

// ─── Stop ─────────────────────────────────────────────────────────────────────
function stopAgent() {
  if (!S.running) return;
  clearInterval(S.pollTimer); S.pollTimer = null;
  if (S.sessionId) {
    fetch(`${CONFIG.API_BASE}/sessions/${S.sessionId}/cancel`, { method: 'POST' }).catch(() => {});
  }
  log('Stopped by user.', 'warn', 'warn');
  showResult('Stopped by user.', false);
  finish(false);
}

// ─── Polling ──────────────────────────────────────────────────────────────────
function startPolling(ms) {
  S.pollTimer = setInterval(async () => {
    try {
      const resp = await fetch(`${CONFIG.API_BASE}/status/${S.sessionId}`);
      if (!resp.ok) throw new Error(`Poll HTTP ${resp.status}`);
      const data = await resp.json();
      S.pollErrors = 0;
      bumpPoll();
      handleStatus(data);
    } catch (e) {
      S.pollErrors++;
      log(`Poll error ${S.pollErrors}/${CONFIG.MAX_POLL_ERRORS}: ${e.message}`, 'warn', 'warn');
      if (S.pollErrors >= CONFIG.MAX_POLL_ERRORS) {
        clearInterval(S.pollTimer);
        showResult(`Lost connection after ${CONFIG.MAX_POLL_ERRORS} poll errors.`, true);
        finish(true);
      }
    }
  }, ms);
}

// ─── Handle status payload from backend ──────────────────────────────────────
function handleStatus(data) {
  if (!data) return;

  // Render any new steps
  if (Array.isArray(data.steps)) {
    for (const step of data.steps) {
      const key = step.step ?? 0;
      if (S.rendered.has(key)) continue;
      S.rendered.add(key);
      renderStep(step);
    }
  }

  const status = data.status;

  if (status === 'done') {
    clearInterval(S.pollTimer); S.pollTimer = null;
    const answer  = data.result || data.goal_progress || 'Task completed.';
    const elapsed = S.startTime ? Math.floor((Date.now() - S.startTime) / 1000) + 's' : '';
    const meta    = [
      data.step_count ? `${data.step_count} steps` : '',
      elapsed,
      data.final_url ? data.final_url.replace(/https?:\/\//, '') : '',
    ].filter(Boolean).join(' · ');
    log(answer, 'success', 'done');
    showResult(answer, false, meta);
    finish(false);

  } else if (status === 'failed') {
    clearInterval(S.pollTimer); S.pollTimer = null;
    const err = data.error || 'Agent failed.';
    log(err, 'error', 'error');
    showResult(err, true);
    finish(true);

  } else if (status === 'stopped' || status === 'cancelled') {
    clearInterval(S.pollTimer); S.pollTimer = null;
    showResult(data.error || 'Agent stopped.', false);
    finish(false);
  }
}

// ─── Render one step ──────────────────────────────────────────────────────────
function renderStep(step) {
  bumpStep();
  addCard(step);

  const thought = step.thought || step.reason;
  if (thought) log(thought, 'think', 'think');

  const actionLine = [step.action, step.target_text].filter(Boolean).join(' → ');
  if (actionLine) { log(actionLine, 'action', 'action'); bumpAction(); }
  if (step.url)   log(step.url, 'nav', 'nav');
}

// ─── Show result (center hero) ────────────────────────────────────────────────
function showResult(msg, isError = false, meta = '') {
  const ico = $('res-icon');
  const st  = $('res-status');
  const val = $('res-value');
  const met = $('res-meta');
  const glo = $('res-glow');

  if (ico) { ico.textContent = isError ? '✕' : '✓'; ico.className = `res-icon${isError ? ' err' : ''}`; }
  if (st)  { st.textContent  = isError ? 'Task Failed' : 'Task Complete'; st.className = `res-status${isError ? ' err' : ''}`; }
  if (val) val.textContent = msg;
  if (met) met.textContent = meta || '';
  if (glo) glo.className   = `res-glow${isError ? ' err' : ''}`;

  showView('result');

  // Also show compact card in trace panel
  const lr = $('log-result');
  if (lr) {
    lr.innerHTML = `<div class="result-card${isError ? ' err' : ''}">
      <div class="rc-title">${isError ? '✕ Failed' : '✓ Complete'}</div>
      <div class="rc-body">${esc(msg)}</div>
    </div>`;
  }
}

// ─── Finish run ───────────────────────────────────────────────────────────────
function finish(isError = false) {
  clearInterval(S.tickTimer); S.tickTimer = null;
  setRunning(false);
  setFinal(isError);
}

// ─── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  showView('idle');
  setDisplay('live-badge', 'none');
  setDisplay('stop-btn', 'none');

  const urlEl = $('cfg-url');
  if (urlEl && typeof CONFIG !== 'undefined') {
    urlEl.textContent = CONFIG.API_BASE.replace(/https?:\/\//, '');
  }
});