'use strict';
const SPEEDS = [1, 1.25, 1.5, 1.75, 2];
const STATUS_LABEL = { unwatched: 'Unwatched', in_progress: 'In progress', completed: 'Watched', skipped: 'Skipped' };
const NEXT = { unwatched: 'in_progress', in_progress: 'completed', completed: 'unwatched', skipped: 'unwatched' };
const VIEWS = [['todo', 'To watch'], ['in_progress', 'In progress'], ['completed', 'Watched'], ['skipped', 'Skipped'], ['all', 'Everything'], ['suggested', 'AI suggestions'], ['gone', 'Not in a playlist']];
const OPTIONAL_VIEWS = ['skipped', 'suggested', 'gone'];  // only shown when they have videos
const TABS = [['home', 'Home'], ['', 'Library'], ['knowledge', 'Knowledge'], ['sources', 'Playlists'], ['categories', 'Categories'], ['tools', 'Tools']];
const SHORTCUTS = [['/', 'Search'], ['A', 'Ask your library'], ['?', 'Show keyboard shortcuts'], ['G then H', 'Home'], ['G then L', 'Library'], ['G then K', 'Knowledge'], ['G then Y', 'Playlists'], ['G then C', 'Categories'], ['G then T', 'Tools'], ['G then S', 'Settings'], ['G then ?', 'Help'], ['Esc', 'Close dialog, Ask panel or video drawer'], ['J / K', 'Next / previous video in drawer']];
const shortcutTable = () => `<table class="shortcut-table"><thead><tr><th>Key</th><th>Action</th></tr></thead><tbody>${SHORTCUTS.map(([key, action]) => `<tr><td><kbd>${key}</kbd></td><td>${action}</td></tr>`).join('')}</tbody></table>`;
const BROWSERS = [['auto', 'Detect my browser login'], ['firefox', 'Use my Firefox login'], ['chrome', 'Use my Chrome login'], ['chromium', 'Use my Chromium login'],
  ['brave', 'Use my Brave login'], ['edge', 'Use my Edge login']];
const JOB_TEXT = {
  sync: ['Reading playlist. Large playlists take a minute.', 'Playlist synced', 'Sync failed. See the log.'],
  discover: ['Asking the AI to propose categories…', 'Categories proposed. Review them below.', 'AI run failed. See the log.'],
  suggest: ['Asking the AI to sort videos…', 'Suggestions ready to review', 'AI run failed. See the log.'],
  details: ['Fetching video details, one video every few seconds…', 'Video details fetched', 'Details run stopped early. See the log.'],
  rate: ['Rating videos…', 'Videos rated', 'Rating stopped early. See the log.'],
  summarize: ['Summarising videos…', 'Video summaries ready', 'Summarising stopped early. See the log.'],
  knowledge: ['Building the knowledge page…', 'Knowledge page ready', 'Knowledge page build stopped or failed. See the log.'],
  pull: ['Downloading the model into Ollama…', 'Model downloaded', 'Download stopped. See the log.'],
  asr: ['Transcribing locally: downloading audio, then speech-to-text…', 'Local transcription finished', 'Transcription stopped or failed. See the log.'],
  visual: ['Reading slides: downloading video, picking frames, reading them…', 'Slides read', 'Slide reading stopped or failed. See the log.'],
  embed: ['Updating the meaning index…', 'Meaning index updated', 'Index update stopped or failed. See the log.'],
  remove: ['Removing the playlist from YourTube…', 'Playlist removed from YourTube', 'Removing the playlist failed. See the log.'],
  backup: ['Backing up the library…', 'Library backup created', 'Library backup failed. See the log.'],
};
const DETAIL_LABEL = { done: 'Transcript', no_captions: 'No captions', music: 'Music, no transcript', unavailable: 'Unavailable', failed: 'Failed, will retry' };

const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* unavailable */ } },
};
const S = {
  data: null,
  storage: null,
  speed: SPEEDS.includes(+store.get('ytkb.speed')) ? +store.get('ytkb.speed') : 2,
  theme: store.get('ytkb.theme') || 'auto',
  view: { status: 'todo', cat: '', src: '', q: '', sort: 'order' },
  sel: new Set(), limit: 150, visible: [],
  plan: { mins: +store.get('ytkb.plan.mins') || 60, days: +store.get('ytkb.plan.days') || 5 },
  session: { cat: '', mins: 45, order: 'order' },
  ingest: { url: '', access: store.get('ytkb.access') || 'auto', cookies: store.get('ytkb.cookies') || '' },
  ai: { provider: null, key: '', models: {} },
  statusUndo: [],
  drawer: null, polling: false, detailCache: {}, summaryCache: {}, local: null, guide: null, asrPlan: null, visPlan: null,
  busy: {},   // video IDs a running media job was started for: { asr: [...], visual: [...] }
  stopPending: false,
  search: { q: '', kind: 'all', res: null, seq: 0, limit: 50, meaning: store.get('ytkb.meaning') === '1' },
  lib: Number.isInteger(+store.get('ytkb.lib')) && store.get('ytkb.lib') !== null ? +store.get('ytkb.lib') : 1,   // 0 = all libraries
  ask: { q: '', cat: '', src: '', done: true, video: null, busy: false, thread: [], hist: null, hq: '', open: false, returnFocus: null },
  knowledge: { list: null, pages: {}, loadingList: false, loadingPage: null },
  logs: null,
  addingCat: false,
  about: { look: '', error: '', suggestion: '', recording: false, seconds: 0 },
  tools: { links: '', browser: false, items: [], busy: false, ranges: {}, mins: +store.get('ytkb.tools.mins') || 60, days: +store.get('ytkb.tools.days') || 5, speed: +store.get('ytkb.tools.speed') || 1 },
};

/* ---------------- helpers ---------------- */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sx = s => `${s}×`;
const plural = (n, w) => `${n} ${n === 1 ? w : /[^aeiou]y$/.test(w) ? w.slice(0, -1) + 'ies' : w + 's'}`;
const tip = text => `<button type="button" class="tip" aria-label="More info" data-tip="${esc(text)}"><span aria-hidden="true">i</span><span class="tip-text">${esc(text)}</span></button>`;
function fmt(sec) {
  sec = Math.round(sec || 0);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return sec ? `${s}s` : '0m';
}
function studyFinish(studyDays, daysWeek) {
  if (!studyDays) return 'Done';
  const d = new Date();
  d.setDate(d.getDate() + Math.ceil((studyDays / Math.min(7, Math.max(1, daysWeek))) * 7));
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}
// "skipped" means decided not to watch: done for remaining time, like "completed" (mirrors metrics.py)
const isDone = v => v.status === 'completed' || v.status === 'skipped';
const isCombined = name => name.split(/\s*(?:&|,|\+|\/|\band\b)\s*/i).filter(part => part.trim()).length > 1;   // as ai_classifier._topics
function stats(list) {
  let total = 0, rem = 0, done = 0, prog = 0, unknown = 0;
  for (const v of list) {
    const d = v.duration_seconds || 0;
    total += d;
    if (isDone(v)) done++; else rem += d;
    if (v.status === 'in_progress') prog++;
    if (!d) unknown++;
  }
  return { n: list.length, total, rem, done, prog, left: list.length - done, unknown };
}
async function api(path, body) {
  if (body && typeof body === 'object' && !Array.isArray(body) && !('library' in body) && S.data) body = { ...body, library: S.lib };
  const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-YTKB': '1' }, body: JSON.stringify(body) };
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `Request failed (${r.status})`);
  return j;
}
let toastT;
function toast(msg, bad) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'show' + (bad ? ' bad' : '');
  clearTimeout(toastT);
  toastT = setTimeout(() => { t.className = bad ? 'bad' : ''; }, 2600);
}
// The current library's videos (a video saved in playlists of two libraries is in both); S.lib 0 = all libraries
const videos = () => S.lib ? S.data.videos.filter(v => v.libraries.includes(S.lib)) : S.data.videos;
const byId = id => S.data.videos.find(v => v.video_id === id);
const libPlaylists = () => S.lib ? S.data.playlists.filter(p => p.library_id === S.lib) : S.data.playlists;
const libName = id => (S.data.libraries.find(l => l.id === id) || {}).name || '';
const isSingle = id => /^_single(-\d+)?$/.test(id);
const FALLBACK = () => S.data.fallback_category;
const playlistName = id => (S.data.playlists.find(p => p.id === id) || {}).title || id;
const srcEntry = (v, src) => v.sources.find(s => s.id === src);
const isGone = (v, src) => src ? !!srcEntry(v, src)?.removed : (!v.in_source && v.sources.length > 0);
function orderKey(v, src) {
  if (src) return srcEntry(v, src)?.position ?? 1e9;
  const order = S.data.playlists.map(p => p.id);
  const first = v.sources.find(s => !s.removed) || v.sources[0];
  if (!first) return 1e12;
  return Math.max(0, order.indexOf(first.id)) * 1e6 + (first.position ?? 0);
}
function sortList(list, sort, src) {
  const by = {
    order: (a, b) => orderKey(a, src) - orderKey(b, src),
    score: (a, b) => (b.rating?.score ?? -1) - (a.rating?.score ?? -1) || orderKey(a, src) - orderKey(b, src),
    shortest: (a, b) => (a.duration_seconds || 1e9) - (b.duration_seconds || 1e9),
    longest: (a, b) => (b.duration_seconds || 0) - (a.duration_seconds || 0),
    title: (a, b) => a.title.localeCompare(b.title),
    channel: (a, b) => (a.channel || '').localeCompare(b.channel || ''),
  }[sort];
  return by ? [...list].sort(by) : list;
}
function categoryNames() {
  const names = new Set([...Object.keys(S.data.taxonomy), ...videos().map(v => v.category)]);
  names.delete('Uncategorized');
  return [...names].sort((a, b) => (a === FALLBACK()) - (b === FALLBACK()) || a.localeCompare(b));
}
function queueUrls(list) {
  const ids = list.map(v => v.video_id), out = [];
  for (let i = 0; i < ids.length; i += 50) out.push('https://www.youtube.com/watch_videos?video_ids=' + ids.slice(i, i + 50).map(encodeURIComponent).join(','));
  return out;
}
function playLinks(list, label = 'Play') {
  const todo = list.filter(v => !isDone(v));
  const urls = queueUrls(todo);
  if (!urls.length) return '';
  return `<details class="play-menu"><summary class="btn btn-dark btn-sm">▶ Play on YouTube</summary><div class="play-menu-links">${urls.map((u, i) =>
    `<a href="${esc(u)}" target="_blank" rel="noopener">Videos ${i * 50 + 1}–${Math.min((i + 1) * 50, todo.length)} (opens YouTube)</a>`).join('')}</div></details>`;
}
const statusIcon = s => s === 'completed'
  ? '<svg viewBox="0 0 20 20"><circle class="st-fill" cx="10" cy="10" r="8.5"/><path class="st-check" d="M6.5 10.2l2.4 2.4 4.6-5"/></svg>'
  : s === 'skipped'
    ? '<svg viewBox="0 0 20 20"><circle class="st-ring" cx="10" cy="10" r="8" stroke-dasharray="2.5 2.4"/><path class="st-skip" d="M7.2 7.2l5.6 5.6"/></svg>'
  : s === 'in_progress'
    ? '<svg viewBox="0 0 20 20"><circle class="st-ring" cx="10" cy="10" r="8"/><path class="st-half" d="M10 3.5a6.5 6.5 0 0 1 0 13z"/></svg>'
    : '<svg viewBox="0 0 20 20"><circle class="st-ring" cx="10" cy="10" r="8"/></svg>';

const VERDICT_LABEL = { watch: 'Watch', skim: 'Skim', skip: 'Skip' };
const thumb = v => `<img class="thumb" src="https://i.ytimg.com/vi/${encodeURIComponent(v.video_id)}/mqdefault.jpg" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.visibility='hidden'" onload="if (this.naturalWidth <= 120) this.style.visibility='hidden'">`;  // 120px = YouTube's "no thumbnail" placeholder
const badge = v => v.rating
  ? `<span class="vb vb-${v.rating.verdict}"><b>${v.rating.score.toFixed(1)}</b>${VERDICT_LABEL[v.rating.verdict]}</span>`
  : '<span class="vb vb-none">Not rated</span>';
const wantsTranscript = v => S.data.transcripts[v.category] !== false;
const needsDetails = v => wantsTranscript(v) && (!v.details || (v.details.status === 'failed' && v.details.tries < 3));
const clock = sec => { sec = Math.floor(sec || 0); const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), x = String(sec % 60).padStart(2, '0'); return h ? `${h}:${String(m).padStart(2, '0')}:${x}` : `${m}:${x}`; };

/* ---------------- routing & load ---------------- */
const route = () => (location.hash.replace(/^#\/?/, '').split('?')[0] || '');
const routeParam = name => new URLSearchParams(location.hash.split('?')[1] || '').get(name);
async function load({ keepEdits = false } = {}) {
  if (!location.hash || location.hash === '#') location.replace('#/home');
  const dirty = keepEdits && unsavedCategoryEdits();
  try {
    S.data = await api(`/api/state?library=${S.lib}`);
    if (S.data.library !== S.lib) { S.lib = S.data.library; store.set('ytkb.lib', S.lib); }   // e.g. a deleted library
  } catch (e) {
    $('#view').innerHTML = `<div class="empty"><h2>The local server isn't running</h2>Start it with <code>python dashboard.py</code>, then reload this page.<p class="faint">${esc(e.message)}</p></div>`;
    return;
  }
  if (S.view.src && !S.data.playlists.some(p => p.id === S.view.src)) S.view.src = '';
  const ids = new Set(videos().map(v => v.video_id));
  for (const id of [...S.sel]) if (!ids.has(id)) S.sel.delete(id);
  if (dirty) { updateJob(); renderJobs(); toast('Updated in the background. Your unsaved category edits are kept; save to see the update.'); return; }
  render();
  if (S.data.job.running || S.data.job.queue?.length) pollJob();
}
function render() { renderBar(); renderView(); renderDrawer(); renderJobs(); renderAskPop(); }
function renderShortcutHelp() {
  const root = $('#shortcut-root');
  if (!S.shortcutsOpen) { root.innerHTML = ''; return; }
  root.innerHTML = `<div class="shortcut-scrim" data-act="shortcut-dismiss"><section class="shortcut-dialog" role="dialog" aria-modal="true" aria-labelledby="shortcut-title"><h2 id="shortcut-title">Keyboard shortcuts</h2>${shortcutTable()}<div class="actions" style="margin-top:16px"><button class="btn" data-act="shortcut-close">Close</button></div></section></div>`;
  root.querySelector('[data-act="shortcut-close"]')?.focus();
}
function refresh() { const y = scrollY; renderView(); scrollTo(0, y); }
function renderBar() {
  const r = route();
  const tab = r === 'settings' || r.startsWith('settings/') ? 'settings' : r;
  $('#libpick').innerHTML = S.data.libraries.length > 1 || S.lib !== 1
    ? `<select data-change="lib" aria-label="Library" class="libsel">${S.data.libraries.map(l => `<option value="${l.id}" ${S.lib === l.id ? 'selected' : ''}>${esc(l.name)}</option>`).join('')}
        <option value="0" ${S.lib === 0 ? 'selected' : ''}>All libraries</option></select>` : '';
  $('#tabs').innerHTML = TABS.map(([k, l]) => `<a href="#/${k}" class="${tab === k ? 'on' : ''}"${tab === k ? ' aria-current="page"' : ''}>${l}</a>`).join('');
  { const on = $('#tabs a.on'), nav = $('#tabs'); if (on && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = on.offsetLeft - nav.offsetLeft - 8; }   // phone: the menu scrolls, keep this page's tab in view
  $('#gsearch-btn').classList.toggle('on', r === 'search');
  $('#settings-btn').classList.toggle('on', tab === 'settings');
  $('#help-btn').classList.toggle('on', r === 'help');
  for (const [btn, on] of [['#settings-btn', tab === 'settings'], ['#help-btn', r === 'help']]) {
    if (on) $(btn).setAttribute('aria-current', 'page'); else $(btn).removeAttribute('aria-current');
  }
}
const darkNow = () => S.theme === 'dark' || (S.theme === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
function applyTheme() {
  if (S.theme === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = S.theme;
  const b = $('#theme-btn'), dark = darkNow();
  if (b) {
    b.innerHTML = dark ? '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>';
    b.title = dark ? 'Switch to light mode' : 'Switch to dark mode'; b.setAttribute('aria-label', b.title);
  }
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);
function renderView() {
  const r = route(), v = $('#view');
  if (r === 'plan') { location.replace('#/home'); return; }
  if (r === 'triage') { S.view.sort = 'score'; S.view.status = 'todo'; S.view.verdict = ''; location.replace('#/'); return; }
  if (r === 'ai') { location.replace('#/settings/cloud'); return; }
  if (r === 'setup') { location.replace('#/help'); return; }
  if (r === 'home') {
    v.innerHTML = videos().length ? viewHome() : emptyState();
    if (!S.knowledge.list && !S.knowledge.loadingList) loadKnowledgeList();
  }
  else if (r === 'sources') { v.innerHTML = viewSources(); updateJob(); }
  else if (r === 'tools') { v.innerHTML = viewTools(); }
  else if (r === 'categories') { v.innerHTML = viewCategories(); updateJob(); }
  else if (r === 'settings' || r.startsWith('settings/')) {
    const section = r.split('/')[1] || 'general';
    const valid = SETTINGS_PAGES.some(([key]) => key === section) ? section : 'general';
    v.innerHTML = settingsNav(valid) + settingsContent(valid);
    if (['cloud', 'local', 'routing'].includes(valid)) updateJob();
    if (['local', 'cloud', 'routing'].includes(valid) && !S.local) loadLocal();
    if (valid === 'storage' && !S.storage) loadStorage();
  }
  else if (r === 'help') { v.innerHTML = '<div class="eyebrow">Help</div><h1 class="display">Help<em>setup, downloads and guide</em></h1>' + viewSetup() + viewLogs() + `<section class="section"><h2 class="h">Keyboard shortcuts</h2>${shortcutTable()}</section>`; if (!S.guide) loadGuide(); }
  else if (r === 'ask') {
    const id = +routeParam('id') || null;
    if (id && !S.ask.thread.some(t => t.id === id) && !S.ask.busy) openAsk(id);
    if (!id && S.ask.thread.length && !S.ask.busy) S.ask.thread = [];
    v.innerHTML = viewAsk();
    if (!S.ask.hist) loadAskHistory();
    if (!S.ask.busy) $('#aq')?.focus({ preventScroll: true });
  }
  else if (r === 'knowledge') {
    const cat = routeParam('category');
    if (cat) {
      v.innerHTML = viewKnowledgePage(cat);
      if (!S.knowledge.pages[cat] && S.knowledge.loadingPage !== cat) loadKnowledgePage(cat);
      if (!S.knowledge.list && !S.knowledge.loadingList) loadKnowledgeList();
    } else {
      v.innerHTML = viewKnowledgeList();
      if (!S.knowledge.list && !S.knowledge.loadingList) loadKnowledgeList();
    }
    updateJob();
  }
  else if (r === 'search') {
    const q = routeParam('q');
    if (q != null && q !== S.search.q) { S.search.q = q; S.search.res = null; S.search.limit = 50; }
    v.innerHTML = viewSearch(); renderSearchResults();
    if (S.search.q && !S.search.res) runSearch();
    else $('#sq')?.focus();
  }
  else { v.innerHTML = videos().length ? viewLibrary() : emptyState(); renderList(); }
}
const emptyState = () => `<div class="empty"><h2>Your library is empty</h2><p>Add a playlist to start building your study library.</p><a class="btn btn-dark" href="#/sources">Add a playlist</a></div>`;

/* ---------------- library ---------------- */
function statusFilter(v, status, src) {
  switch (status) {
    case 'todo': return !isDone(v);
    case 'in_progress': return v.status === 'in_progress';
    case 'completed': return v.status === 'completed';
    case 'skipped': return v.status === 'skipped';
    case 'gone': return isGone(v, src);
    case 'suggested': return !!v.suggestion;
    default: return true;
  }
}
function viewLibrary() {
  const { cat, src, status } = S.view;
  const inSrc = videos().filter(v => !src || srcEntry(v, src));
  const scope = inSrc.filter(v => !cat || v.category === cat);
  const st = stats(scope);
  const scopeName = [S.data.libraries.length > 1 ? (S.lib ? libName(S.lib) : 'All libraries') : '', src ? playlistName(src) : (libPlaylists().length > 1 ? 'All playlists' : (S.data.libraries.length > 1 ? '' : 'Library')), cat].filter(Boolean).join(' · ');
  const catCounts = {};
  for (const v of inSrc) if (statusFilter(v, status, src)) catCounts[v.category] = (catCounts[v.category] || 0) + 1;
  const viewCounts = Object.fromEntries(VIEWS.map(([k]) => [k, scope.filter(v => statusFilter(v, k, src)).length]));
  const pct = st.n ? Math.round((st.done / st.n) * 100) : 0;
  const cats = categoryNames().filter(c => catCounts[c] || c === cat);

  return `
  <section>
    <div class="eyebrow">${esc(scopeName)}</div>
    <h1 class="display num">${fmt(st.rem / S.speed)}<em>left at ${sx(S.speed)}</em></h1>
    <p class="lede">${st.left} of ${plural(st.n, 'video')} to go · ${fmt(st.rem)} at normal speed · ${pct}% done</p>
    <div class="progress"><i style="width:${st.n ? (st.done / st.n) * 100 : 0}%"></i><i style="width:${st.n ? (st.prog / st.n) * 100 : 0}%"></i></div>
  </section>

  <div class="lib">
    <aside class="rail">
      <div class="rail-group views">
        <h4>Show</h4>
        ${VIEWS.filter(([k]) => !OPTIONAL_VIEWS.includes(k) || viewCounts[k] || status === k).map(([k, l]) =>
          `<a href="#/" data-act="v-status" data-v="${k}" class="${status === k ? 'on' : ''}"><span class="lbl">${l}</span><span class="c">${viewCounts[k]}</span></a>`).join('')}
      </div>
      <div class="rail-group collapsible">
        <h4>Categories</h4>
        <a href="#/" data-act="v-cat" data-v="" class="${!cat ? 'on' : ''}"><span class="lbl">All categories</span></a>
        ${cats.map(c => `<a href="#/" data-act="v-cat" data-v="${esc(c)}" class="${cat === c ? 'on' : ''}" title="${esc(c)}"><span class="lbl">${esc(c)}</span><span class="c">${catCounts[c] || 0}</span></a>`).join('')}
      </div>
      ${libPlaylists().length > 1 || src ? `<div class="rail-group collapsible">
        <h4>Playlists</h4>
        <a href="#/" data-act="v-src" data-v="" class="${!src ? 'on' : ''}"><span class="lbl">All playlists</span></a>
        ${libPlaylists().map(p => `<a href="#/" data-act="v-src" data-v="${esc(p.id)}" class="${src === p.id ? 'on' : ''}"><span class="lbl">${esc(p.title)}</span><span class="c">${p.active_videos || 0}</span></a>`).join('')}
      </div>` : ''}
      <div class="rail-group rail-pick">
        <select data-change="v-cat" aria-label="Category"><option value="">All categories</option>
          ${categoryNames().map(c => `<option value="${esc(c)}" ${cat === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>
        ${libPlaylists().length > 1 ? `<select data-change="v-src" aria-label="Playlist"><option value="">All playlists</option>
          ${libPlaylists().map(p => `<option value="${esc(p.id)}" ${src === p.id ? 'selected' : ''}>${esc(p.title)}</option>`).join('')}</select>` : ''}
      </div>
    </aside>
    <div>
      <div class="list-head">
        <label class="field-label" for="q">Search ${tip('Find videos by title, channel or notes.')}</label>
        <input type="search" id="q" placeholder="Titles, channels and notes" value="${esc(S.view.q)}" aria-label="Search">
        <label class="field-label" for="v-sort">Sort</label>
        <select id="v-sort" data-change="v-sort" aria-label="Sort">
          ${[['order', 'Playlist order'], ['score', 'AI score'], ['shortest', 'Shortest first'], ['longest', 'Longest first'], ['title', 'Title'], ['channel', 'Channel']]
            .map(([k, l]) => `<option value="${k}" ${S.view.sort === k ? 'selected' : ''}>${l}</option>`).join('')}
        </select>
      </div>
      <div id="list"></div>
    </div>
  </div>`;
}
function renderList() {
  const box = $('#list');
  if (!box) return;
  const { status, src, cat, q, sort, verdict = '' } = S.view;
  let list = videos().filter(v => (!src || srcEntry(v, src)) && (!cat || v.category === cat) && statusFilter(v, status, src));
  if (q) {
    const needle = q.toLowerCase();
    list = list.filter(v => `${v.title} ${v.channel || ''} ${v.notes || ''}`.toLowerCase().includes(needle));
  }
  const scoped = list;
  if (verdict === 'unrated') list = list.filter(v => !v.rating);
  else if (verdict) list = list.filter(v => v.rating?.verdict === verdict);
  list = sortList(list, sort, src);
  S.visible = list;
  const st = stats(list);
  box.innerHTML = `
    ${q.trim().length >= 3 ? `<p class="hint" style="margin:10px 0 0"><a href="#/search?q=${encodeURIComponent(q.trim())}">Search inside transcripts, chapters and summaries for “${esc(q.trim())}” →</a></p>` : ''}
    ${S.sel.size ? selBar() : status === 'suggested' ? sugSum(list) : `<div class="list-sum"><span>Showing ${Math.min(S.limit, list.length)} of ${plural(list.length, 'video')} · ${fmt(st.rem / S.speed)} at ${sx(S.speed)}</span>
      <span class="play-links">${list.length ? exportMenu() : ''}${playLinks(list)}</span></div>`}
    ${scoped.some(v => v.rating || (!v.rating && !isDone(v))) ? `<div class="verdict-bar">${scoped.some(v => v.rating) ? `<div class="chips">${[['', 'All'], ['watch', 'Watch'], ['skim', 'Skim'], ['skip', 'Skip'], ['unrated', 'Not rated']].map(([k, label]) => {
      const count = k === '' ? scoped.length : k === 'unrated' ? scoped.filter(v => !v.rating).length : scoped.filter(v => v.rating?.verdict === k).length;
      return `<button class="chip ${verdict === k ? 'on' : ''}" data-act="v-verdict" data-v="${k}">${label}<span class="c">${count}</span></button>`;
    }).join('')}</div>` : '<span></span>'}<div class="chips">
      ${scoped.filter(v => !v.rating && !isDone(v)).length ? `<button class="btn btn-sm" data-act="rate-filtered" data-ids="${esc(scoped.filter(v => !v.rating && !isDone(v)).map(v => v.video_id).join(','))}" data-job-btn>Rate ${scoped.filter(v => !v.rating && !isDone(v)).length} unrated</button>` : ''}
      ${scoped.some(v => v.status === 'unwatched' && v.rating?.verdict === 'skip') ? `<button class="btn btn-sm" data-act="accept-skips" data-ids="${esc(scoped.filter(v => v.status === 'unwatched' && v.rating?.verdict === 'skip').map(v => v.video_id).join(','))}">Skip the ${plural(scoped.filter(v => v.status === 'unwatched' && v.rating?.verdict === 'skip').length, 'AI skip')}</button>` : ''}
    </div></div>` : ''}
    ${S.statusUndo.length ? '<div class="chips" style="margin-top:10px"><button class="btn-text" data-act="status-undo">Undo last status change</button></div>' : ''}
    ${list.length ? `<ul class="rows ${S.sel.size ? 'selecting' : ''}">${list.slice(0, S.limit).map(row).join('')}</ul>`
      : `<div class="empty">Nothing here${q ? ' matches your search' : ''}.</div>`}
    ${list.length > S.limit ? `<div class="more"><button class="btn" data-act="more">Show ${Math.min(50, list.length - S.limit)} more</button></div>` : ''}`;
}
function row(v) {
  const d = v.duration_seconds || 0;
  const meta = [esc(v.channel || 'Unknown channel'), S.view.cat ? '' : `<span class="cat-chip" title="${esc(v.category)}">${esc(v.category)}</span>`,
    isGone(v, S.view.src) ? '<span class="gone">no longer in playlist</span>' : '', v.notes ? 'has notes' : '']
    .filter(Boolean).join(' · ');
  return `<li class="row ${isDone(v) ? 'done' : ''} ${S.sel.has(v.video_id) ? 'is-sel' : ''}">
    <input type="checkbox" class="pick" data-act="pick" data-id="${esc(v.video_id)}" ${S.sel.has(v.video_id) ? 'checked' : ''} aria-label="Select">
    <button class="st" data-act="cycle" data-id="${esc(v.video_id)}" title="${STATUS_LABEL[v.status]}. Click to change." aria-label="Status: ${STATUS_LABEL[v.status]}">${statusIcon(v.status)}</button>
    <button class="thumb-btn" data-act="open" data-id="${esc(v.video_id)}" tabindex="-1" aria-hidden="true">${thumb(v)}</button>
    <div class="row-c"><button class="row-main" data-act="open" data-id="${esc(v.video_id)}"><span class="row-title">${esc(v.title)}</span><span class="row-meta">${v.rating ? badge(v) + ' ' : ''}${meta}</span></button>${sugLine(v)}</div>
    <span class="row-time">${d ? fmt(d / S.speed) : '—'}${d && S.speed !== 1 ? `<small>${fmt(d)} at 1×</small>` : ''}</span>
  </li>`;
}
const pct = c => c == null ? '' : ` · ${Math.round(c * 100)}%`;
function sugLine(v) {
  const g = v.suggestion;
  if (!g) return '';
  return `<div class="sug">AI suggests <b>${esc(g.category)}</b><span class="faint">${pct(g.confidence)}</span>
    <button class="btn-text" data-act="sug-accept" data-id="${esc(v.video_id)}">Accept</button>
    <button class="btn-text" data-act="sug-dismiss" data-id="${esc(v.video_id)}">Dismiss</button></div>`;
}
function sugSum(list) {
  const sure = list.filter(v => (v.suggestion.confidence ?? 0) >= 0.8).length;
  return `<div class="list-sum"><span>${plural(list.length, 'suggestion')} to review. Nothing changes until you accept.</span>
    <span class="play-links">
      ${sure && sure < list.length ? `<button class="btn btn-sm" data-act="sug-accept-sure">Accept ${sure} at 80%+</button>` : ''}
      <button class="btn btn-dark btn-sm" data-act="sug-accept-list">Accept ${list.length === 1 ? '' : 'all '}${list.length}</button>
      <button class="btn btn-sm" data-act="sug-dismiss-list">Dismiss ${list.length === 1 ? '' : 'all'}</button></span></div>`;
}
function exportMenu() {
  const src = S.view.src ? `&playlist=${encodeURIComponent(S.view.src)}` : '';
  return `<details class="export"><summary class="btn btn-sm">Export</summary><div class="export-pop">
    <button class="btn-text" data-act="export-notes" data-scope="list">Notes for these ${plural(S.visible.length, 'video')} (Markdown .zip)</button>
    <a class="btn-text" href="/api/export?format=csv${src}" download>Spreadsheet (CSV)${S.view.src ? ', this playlist' : ', whole library'}</a>
    <a class="btn-text" href="/api/export?format=json${src}" download>Data (JSON)${S.view.src ? ', this playlist' : ', whole library'}</a>
  </div></details>`;
}
async function exportNotes(ids) {
  if (!ids.length) return;
  try {
    const r = await fetch('/api/export/markdown', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-YTKB': '1' }, body: JSON.stringify({ ids }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `Export failed (${r.status})`);
    const name = (r.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1] || 'yourtube_notes.zip';
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(await r.blob()), download: name });
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    toast(`Exported ${plural(ids.length, 'note')}`);
  } catch (e) { toast(e.message, true); }
}
function selBar() {
  const sel = videos().filter(v => S.sel.has(v.video_id));
  return `<div class="selbar">
    <strong>${S.sel.size} selected</strong>
    <button class="btn-text" data-act="bulk" data-status="completed">Mark watched</button>
    <button class="btn-text" data-act="bulk" data-status="in_progress">In progress</button>
    <button class="btn-text" data-act="bulk" data-status="skipped">Skip</button>
    <button class="btn-text" data-act="bulk" data-status="unwatched">Unwatched</button>
    <select data-change="bulk-cat" aria-label="Move to category"><option value="">Move to…</option>
      ${categoryNames().map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('')}<option value="__new">New category…</option></select>
    ${sel.some(v => v.suggestion) ? `<button class="btn-text" data-act="sug-accept-sel">Accept AI suggestions</button>` : ''}
    ${S.data.ai.ready_for.summarize ? `<button class="btn-text" data-act="summarize-sel" data-job-btn>Summarise selected</button>` : ''}
    ${queueUrls(sel).map((u, i, a) => `<a class="btn-text" href="${esc(u)}" target="_blank" rel="noopener">Play${a.length > 1 ? ` ${i + 1}/${a.length}` : ''}</a>`).join('')}
    <span class="grow"></span>
    ${S.sel.size < S.visible.length ? `<button class="btn-text" data-act="sel-all">Select all ${S.visible.length}</button>` : ''}
    <button class="btn-text" data-act="export-notes" data-scope="sel">Export notes</button>
    <button class="btn-text" data-act="sel-clear">Clear</button>
  </div>`;
}

function viewHome() {
  const p = S.plan, perDay = Math.max(5, p.mins) * 60, days = Math.min(7, Math.max(1, p.days));
  const finish = studyDays => {
    return studyFinish(studyDays, days);
  };
  const all = stats(videos()), allDays = Math.ceil(all.rem / S.speed / perDay);
  const session = buildSession(), used = session.picked.reduce((a, v) => a + v.duration_seconds / S.speed, 0);
  const inProgress = videos().filter(v => v.status === 'in_progress').slice(0, 5);
  const rated = videos().filter(v => v.rating);
  const picks = videos().filter(v => v.status === 'unwatched' && v.rating?.verdict === 'watch')
    .sort((a, b) => b.rating.score - a.rating.score).slice(0, 5);
  const suggestions = videos().filter(v => v.suggestion).length;
  const unrated = videos().filter(v => !v.rating && !isDone(v)).length;   // what Rate would rate
  const failedSync = S.data.job?.recent?.find(j => j.kind === 'sync' && !j.ok && !j.stopped && !j.dismissed);
  const knowledgeStale = Array.isArray(S.knowledge.list) ? S.knowledge.list.filter(c => c.page === 'stale').length : 0;
  const rows = categoryNames().map(c => ({ c, st: stats(videos().filter(v => v.category === c)) }))
    .filter(r => r.st.n).map(r => ({ ...r, sd: Math.ceil(r.st.rem / S.speed / perDay) })).sort((a, b) => a.st.rem - b.st.rem);
  return `
  <section>
    <div class="eyebrow">Home</div>
    <h1 class="display num">${allDays ? `${fmt(all.rem / S.speed)} left at ${sx(S.speed)}` : 'All watched'}<em>${allDays ? `done by ${finish(allDays)}` : ''}</em></h1>
    <div class="form-row" style="margin-top:18px">
      <label class="field">Minutes a day<input type="number" min="5" step="5" value="${p.mins}" data-change="p-mins" aria-label="Minutes a day"></label>
      <label class="field">Days a week<input type="number" min="1" max="7" value="${p.days}" data-change="p-days" aria-label="Days a week"></label>
      <label class="field">Speed<select data-change="speed" aria-label="Speed">${SPEEDS.map(x => `<option value="${x}" ${x === S.speed ? 'selected' : ''}>${sx(x)}</option>`).join('')}</select></label>
    </div>
  </section>

  <section class="section home-card">
    <h2 class="h">Today's session</h2>
    <div class="form-row" style="margin-top:12px">
      <label class="field">Minutes<input type="number" min="5" step="5" value="${S.session.mins}" data-change="s-mins" aria-label="Session minutes"></label>
      <label class="field">Category<select data-change="s-cat" aria-label="Category"><option value="">Any category</option>${categoryNames().map(c => `<option value="${esc(c)}" ${S.session.cat === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></label>
      <label class="field">Order<select data-change="s-order" aria-label="Order">${[['order', 'Playlist order'], ['shortest', 'Shortest first'], ['longest', 'Longest first'], ['progress', 'Unfinished first']].map(([k, l]) => `<option value="${k}" ${S.session.order === k ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
    </div>
    <div style="margin-top:16px"><div class="note">${plural(session.picked.length, 'video')} · ${fmt(used)} of ${S.session.mins}m</div><div class="meter"><i style="width:${Math.min(100, used / (S.session.mins * 60) * 100)}%"></i></div>
      ${session.picked.length ? `<ul class="rows">${session.picked.map(row).join('')}</ul><div class="actions">${playLinks(session.picked)} <button class="btn btn-sm" data-act="session-done" data-ids="${esc(session.picked.map(v => v.video_id).join(','))}">Mark all watched</button></div>`
        : `<p class="note" style="margin-top:12px">${session.pool ? 'No single video fits. Try more minutes or a faster speed.' : 'Nothing left to watch here.'}</p>`}</div>
  </section>

  <section class="section home-card"><h2 class="h">Continue watching</h2>
    ${inProgress.length ? `<ul class="rows">${inProgress.map(row).join('')}</ul>` : '<p class="note">Nothing in progress.</p>'}
  </section>

  <section class="section home-card"><h2 class="h">Top picks</h2>
    ${!rated.length ? `<p class="note">Rate your videos to get picks. <button class="btn-text" data-act="rate-batch">Rate videos</button></p>`
      : picks.length ? `<ul class="rows">${picks.map(row).join('')}</ul><a class="btn-text" href="#/" data-act="home-picks">See all →</a>`
        : '<p class="note">No unwatched Watch picks yet.</p>'}
  </section>

  <section class="section home-card"><h2 class="h">Needs you</h2>
    ${suggestions || unrated || failedSync || knowledgeStale || videos().some(v => v.category === FALLBACK()) ? `<ul class="rows" style="margin-top:10px">
      ${suggestions ? `<li><a href="#/" data-act="v-status" data-v="suggested">${plural(suggestions, 'AI suggestion')} to review →</a></li>` : ''}
      ${unrated ? `<li><button class="btn-text" data-act="rate-batch">${plural(unrated, 'video')} not rated yet · Rate</button></li>` : ''}
      ${failedSync ? '<li><a href="#/sources">Last sync failed →</a></li>' : ''}
      ${knowledgeStale ? `<li><a href="#/knowledge">${plural(knowledgeStale, 'knowledge page')} out of date →</a></li>` : ''}
      ${videos().filter(v => v.category === FALLBACK()).length ? `<li><a href="#/categories">${plural(videos().filter(v => v.category === FALLBACK()).length, 'unsorted video')} →</a></li>` : ''}
    </ul>` : '<p class="note">Nothing needs your attention.</p>'}
  </section>

  <section class="section home-card"><details ${innerWidth > 760 ? 'open' : ''}><summary class="h" style="cursor:pointer">Time by category</summary>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Category</th><th class="r">Left</th><th class="r">Time at ${sx(S.speed)}</th><th class="r">Study days</th><th class="r">Done by</th></tr></thead>
      <tbody>${rows.map(({ c, st, sd }) => `<tr><td><a href="#/" data-act="v-cat" data-v="${esc(c)}">${esc(c)}</a></td><td class="r">${st.left}</td><td class="r">${fmt(st.rem / S.speed)}</td><td class="r">${sd || '–'}</td><td class="r">${finish(sd)}</td></tr>`).join('')}</tbody>
      <tfoot><tr><td>Everything</td><td class="r">${all.left}</td><td class="r">${fmt(all.rem / S.speed)}</td><td class="r">${allDays}</td><td class="r">${finish(allDays)}</td></tr></tfoot></table></div>
  </details></section>`;
}
function buildSession() {
  const s = S.session;
  let pool = videos().filter(v => !isDone(v) && v.duration_seconds > 0 && (!s.cat || v.category === s.cat));
  pool = s.order === 'progress'
    ? sortList(pool, 'order').sort((a, b) => (b.status === 'in_progress') - (a.status === 'in_progress'))
    : sortList(pool, s.order);
  const budget = Math.max(5, s.mins) * 60;
  let used = 0;
  const picked = [];
  for (const v of pool) {
    const t = v.duration_seconds / S.speed;
    if (used + t <= budget) { picked.push(v); used += t; }
  }
  return { picked, pool: pool.length };
}

async function setStatuses(ids, status) {
  const prev = ids.map(id => [id, byId(id)?.status]).filter(([, st]) => st);
  S.statusUndo.push(prev);
  await update(ids, { status });
}
async function statusUndo() {
  const last = S.statusUndo.pop();
  if (!last) return;
  const by = {};
  for (const [id, st] of last) (by[st] = by[st] || []).push(id);
  for (const [st, ids] of Object.entries(by)) await update(ids, { status: st });
  toast('Undone');
}
async function startRate(ids) {
  if (!ids && S.lib) ids = videos().filter(v => !isDone(v) && !v.rating).map(v => v.video_id);   // only this library
  if (ids && !ids.length) { toast('Nothing to rate in this library'); return; }
  try {
    const est = await api('/api/rate/estimate', ids ? { ids } : {});
    if (!est.videos) { toast('Nothing to rate'); return; }
    if (!ids || ids.length > 1) {
      const cost = est.cost === 0 ? 'free (local model)' : est.cost == null ? 'cost unknown for this model' : `about $${est.cost < 0.01 ? '0.01 or less' : est.cost.toFixed(2)}`;
      if (!confirm(`Rate ${plural(est.videos, 'video')} with ${est.uses}?\n\n${est.with_transcript} with transcripts, ${est.videos - est.with_transcript} from title only.\n≈ ${Math.round(est.tokens_in / 1000)}k tokens in, ${cost}.`)) return;
    }
    await queued(await api('/api/rate', ids ? { ids } : {}), ids?.length === 1 ? 'Rating' : 'Rating run');
  } catch (e) { toast(e.message, true); }
}
async function startSummary(ids) {
  if (!ids?.length) { toast('Select at least one video', true); return; }
  try {
    const batches = [];
    for (let i = 0; i < ids.length; i += 50) batches.push(ids.slice(i, i + 50));
    const estimates = await Promise.all(batches.map(batch => api('/api/summarize/estimate', { ids: batch })));
    const count = estimates.reduce((n, e) => n + (e.videos || 0), 0);
    if (!count) { toast('Nothing to summarise'); return; }
    if (count > 1) {
      const sum = estimates.reduce((n, e) => n + (e.cost || 0), 0);
      const unknown = estimates.some(e => e.cost == null);
      const cost = unknown ? 'cost unknown for this model' : sum === 0 ? 'free (local model)' : `about $${sum < 0.01 ? '0.01 or less' : sum.toFixed(2)}`;
      const uses = [...new Set(estimates.map(e => e.uses).filter(Boolean))].join(', ');
      if (!confirm(`Summarise ${plural(count, 'video')} with ${uses}?\n\n≈ ${cost}. Local models take about 1–2 minutes per video.`)) return;
    }
    for (const batch of batches) await queued(await api('/api/summarize', { ids: batch }), batch.length === 1 ? 'Summarising' : 'Summaries');
  } catch (e) { toast(e.message, true); }
}
function playAt(id, t) {
  if (!byId(id)) return toast('This video was removed from YourTube with its playlist', true);   // old answers can cite it
  openDrawer(id); if (t) seek(t);
}

// Sync tasks are labelled "Sync <url>" (dashboard /api/ingest)
const isSyncOf = (j, p) => j?.kind === 'sync' && [`Sync ${p.original_url}`, `Sync ${p.id}`].includes(j.label || '') || (j?.kind === 'sync' && (j.label || '').includes(`list=${p.id}`));
// Synced, and its latest sync didn't fail: then the button can say what a re-read does
function isPlaylistSynced(p) {
  const job = S.data?.job;
  if (!p.updated_at) return false;
  const last = (job?.recent || []).find(j => isSyncOf(j, p));
  return !(last && !last.ok && !last.stopped);
}

/* ---------------- playlists ---------------- */
function viewSources() {
  const g = S.ingest, job = S.data.job, pls = libPlaylists(), many = S.data.libraries.length > 1;
  const winChromium = S.data.platform === 'win32' && /^(chrome|chromium|edge|brave)$/.test(g.access);
  return `
  <section>
    <div class="eyebrow">Playlists</div>
    <h1 class="display">${plural(pls.length, 'playlist')}<em>${plural(videos().length, 'video')} in your library</em></h1>
    <p class="lede">Add playlists to your library and keep them in sync with YouTube.</p>
    <div class="policy">
      <div><b>Read-only ${tip('YourTube never adds, removes or reorders videos on YouTube.')}</b></div>
      <div><b>Multiple playlists ${tip('A video saved in two playlists appears once in your library and keeps one status.')}</b></div>
      <div><b>Removed videos ${tip('A removed video stays here as “no longer in playlist” until you mark it watched.')}</b></div>
    </div>
  </section>

  <section class="section card">
    <h2 class="h">Add or sync a playlist</h2>
    <div class="form-grid">
      <div><label class="field-label" for="i-url">Playlist or video link</label>
        <input id="i-url" type="text" data-input="i-url" value="${esc(g.url)}" placeholder="youtube.com/playlist?list=…, WL for Watch Later, or a single video link" style="width:100%"></div>
      <div><label class="field-label" for="i-access">Sign-in</label>
        <select id="i-access" data-change="i-access" style="width:100%">
          ${[...BROWSERS, ['public', 'None (public or unlisted)'], ['cookies', 'cookies.txt file']]
            .map(([k, l]) => `<option value="${k}" ${g.access === k ? 'selected' : ''}>${l}</option>`).join('')}
        </select></div>
      <div style="align-self:end"><button class="btn btn-dark" id="run" data-act="ingest">Sync</button></div>
    </div>
    ${many ? `<div style="margin-top:12px;max-width:320px"><label class="field-label" for="i-lib">Add new playlists and videos to</label>
      <select id="i-lib" data-change="i-lib" style="width:100%">${S.data.libraries.map(l => `<option value="${l.id}" ${(g.lib || S.lib || 1) === l.id ? 'selected' : ''}>${esc(l.name)}</option>`).join('')}</select></div>` : ''}
    ${g.access === 'cookies' ? `<div style="margin-top:12px"><label class="field-label" for="i-cookies">Path to cookies.txt</label>
      <input id="i-cookies" type="text" data-input="i-cookies" value="${esc(g.cookies)}" placeholder="C:\\Users\\you\\cookies.txt" style="width:100%"></div>` : ''}
    <p class="hint">${winChromium
      ? '<span class="warn-text">On Windows, Chrome-based browsers usually block reading their login.</span> If the sync fails, use Firefox or a cookies.txt export.'
      : `Sign-in help ${tip('Private playlists and Watch Later need a sign-in. Detect tries each browser signed in to YouTube until one can read the playlist. The Sync button on a playlist row syncs that playlist again with the sign-in it last used; to add another playlist, paste its link above. Syncing again keeps statuses, notes and categories.')}`}
      ${S.data.ai.ready_for.categorize && S.data.ai.auto_after_sync ? '<br>After each sync, the AI suggests categories for new unsorted videos.' : ''}</p>
    ${syncFailureCard(job)}
    <div id="job"></div>
  </section>

  ${viewDetailsCard()}

  <section class="section">
    <h2 class="h">Your playlists</h2>
    ${pls.length ? `<div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>Playlist</th>${many ? '<th>Library</th>' : ''}<th class="r">Videos</th><th class="r">Watch time</th><th>Last synced</th><th></th></tr></thead>
      <tbody>${pls.map(p => {
        const synced = isPlaylistSynced(p);
        const syncBtn = isSingle(p.id)
          ? '<span class="faint" style="font-size:12.5px">Videos added by link</span>'
          : synced
          ? `<button class="btn btn-sm" data-act="resync" data-id="${esc(p.id)}" title="Reads the playlist again: adds new videos, flags removed ones. Nothing changes on YouTube.">Check for new videos</button>`
          : `<button class="btn btn-sm" data-act="resync" data-id="${esc(p.id)}">Sync</button>`;
        const syncCell = `<td class="muted"${p.updated_at ? ` title="${esc(fmtFullDate(p.updated_at))}"` : ''}>${p.updated_at ? esc(fmtRelTime(p.updated_at)) : '—'}</td>`;
        return `<tr>
        <td><a href="#/" data-act="v-src" data-v="${esc(p.id)}" style="font-weight:500">${esc(p.title)}</a>
          ${p.access?.startsWith('browser:') ? `<div class="faint" style="font-size:12px">with your ${esc(p.access.slice(8).replace(/\b\w/g, c => c.toUpperCase()))} login</div>` : p.access === 'cookies' ? '<div class="faint" style="font-size:12px">with cookies.txt</div>' : ''}
          ${p.removed_videos ? `<div class="faint" style="font-size:12.5px">${p.removed_videos} removed on YouTube, kept in library</div>` : ''}</td>
        ${many ? `<td><select data-change="pl-lib" data-id="${esc(p.id)}" aria-label="Library for ${esc(p.title)}" style="max-width:160px">
          ${S.data.libraries.map(l => `<option value="${l.id}" ${p.library_id === l.id ? 'selected' : ''}>${esc(l.name)}</option>`).join('')}</select></td>` : ''}
        <td class="r">${p.active_videos || 0}</td>
        <td class="r">${fmt(p.duration_seconds)}</td>
        ${syncCell}
        <td class="r" style="white-space:nowrap">${syncBtn}
          ${/^[\w-]+$/.test(p.id) && !isSingle(p.id) ? `<a class="btn-text" href="https://www.youtube.com/playlist?list=${encodeURIComponent(p.id)}" target="_blank" rel="noopener">YouTube ↗</a>` : ''}
          <button class="btn-text" data-act="pl-remove" data-id="${esc(p.id)}" data-job-btn aria-label="Remove ${esc(p.title)} from YourTube">Remove</button></td>
      </tr>`;
      }).join('')}</tbody></table></div>` : `<p class="note">${S.lib && S.data.playlists.length ? `No playlists in ${esc(libName(S.lib))} yet. Add one above, or move one here from another library.` : 'No playlists yet. Add one above.'}</p>`}
  </section>
  ${viewLibraries()}`;
}
function viewLibraries() {
  const libs = S.data.libraries;
  return `<section class="section card">
    <h2 class="h">Libraries</h2>
    <p class="note">Libraries ${tip('Separate areas such as Tech, Health and Personal. Each has its own playlists, categories, rating interests, time left and answers. Switch libraries at the top.')}</p>
    ${libs.map(l => `<div class="prov"><div style="display:flex;gap:8px;align-items:center;min-width:0">
        <input type="text" value="${esc(l.name)}" data-change="lib-rename" data-lib="${l.id}" aria-label="Library name" style="max-width:220px">
        <span class="faint" style="font-size:12.5px">${plural(l.playlists, 'playlist')}</span></div>
      ${l.id === 1 ? '<span class="faint" style="font-size:12.5px">First library</span>' : `<button class="btn-text" data-act="lib-delete" data-lib="${l.id}" ${l.playlists ? 'disabled title="Move its playlists to another library first"' : ''}>Delete</button>`}</div>`).join('')}
    <div class="actions"><input type="text" id="lib-new" placeholder="New library, e.g. Health" maxlength="40" style="width:240px">
      <button class="btn btn-sm" data-act="lib-create">Create</button></div>
  </section>`;
}

function toolsSpeeds(seconds, id = '') {
  return `<div class="tbl-wrap"><table class="tbl"${id ? ` id="${id}"` : ''}><thead><tr><th>Speed</th>${SPEEDS.map(x => `<th class="r">${sx(x)}</th>`).join('')}</tr></thead><tbody><tr><td>Watch time</td>${SPEEDS.map(x => `<td class="r num">${fmt(seconds / x)}</td>`).join('')}</tr></tbody></table></div>`;
}
function toolsCsv(videos) {
  const cell = value => `"${String(value ?? '').replace(/"/g, '""')}"`;
  const rows = [['position', 'title', 'channel', 'duration', 'url'], ...videos.map((v, i) => [v.position ?? i + 1, v.title, v.channel, v.duration_seconds, v.url || `https://www.youtube.com/watch?v=${encodeURIComponent(v.video_id)}`])];
  const blob = new Blob(['\ufeff' + rows.map(row => row.map(cell).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'yourtube-videos.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
// A playlist's videos split into shared with the other read playlists, and only here
function toolsSplit(item) {
  const others = new Set(S.tools.items.filter(x => x !== item && Array.isArray(x.videos)).flatMap(x => x.videos.map(v => v.video_id)));
  return { shared: item.videos.filter(v => others.has(v.video_id)), only: item.videos.filter(v => !others.has(v.video_id)) };
}
const toolsSum = vs => vs.reduce((sum, v) => sum + (v.duration_seconds || 0), 0);
function toolVideos(item, index) {
  const from = Math.max(1, Math.min(item.videos.length || 1, +(S.tools.ranges[index]?.from || 1)));
  const to = Math.max(from, Math.min(item.videos.length || 1, +(S.tools.ranges[index]?.to || item.videos.length || 1)));
  return item.videos.slice(from - 1, to);
}
function viewTools() {
  const t = S.tools, items = t.items.filter(item => item.videos), allById = new Map();
  items.forEach(item => item.videos.forEach(v => { if (!allById.has(v.video_id)) allById.set(v.video_id, v); }));
  const total = [...allById.values()].reduce((sum, v) => sum + (v.duration_seconds || 0), 0);
  const errors = t.items.filter(item => item.error);
  const videosInLibrary = new Set(S.data.videos.map(v => v.video_id));
  const plannerDays = Math.ceil(total / Math.max(0.25, t.speed) / (Math.max(5, t.mins) * 60));
  const resultCards = items.map(item => {
    const index = t.items.indexOf(item), range = t.ranges[index] || {};
    const rangeFrom = fromValue(range.from, item.videos.length, 1), rangeTo = Math.max(rangeFrom, fromValue(range.to, item.videos.length, item.videos.length));
    const vids = toolVideos(item, index), seconds = vids.reduce((sum, v) => sum + (v.duration_seconds || 0), 0);
    const known = vids.filter(v => videosInLibrary.has(v.video_id));
    const knownTime = known.reduce((sum, v) => sum + (v.duration_seconds || 0), 0);
    const compare = items.length > 1 && items.every(x => x.kind === 'playlist');
    return `<section class="section card"><h2 class="h">${esc(item.title || item.id || 'Untitled')}</h2><p class="note">${plural(item.videos.length, 'video')}${item.unavailable ? ` · ${item.unavailable} unavailable` : ''} · ${fmt(item.videos.reduce((sum, v) => sum + (v.duration_seconds || 0), 0))} total</p>
      <div class="form-row" style="margin-top:12px"><label class="field">From video<input type="number" min="1" max="${item.videos.length}" value="${fromValue(range.from, item.videos.length, 1)}" data-change="tools-range" data-index="${index}" data-bound="from"></label><label class="field">To video<input type="number" min="1" max="${item.videos.length}" value="${fromValue(range.to, item.videos.length, item.videos.length)}" data-change="tools-range" data-index="${index}" data-bound="to"></label></div>
      <p class="note" id="tools-range-summary-${index}">Range: videos ${vids.length ? `${rangeFrom}–${rangeTo}` : 'none'} · ${fmt(seconds)}</p>${toolsSpeeds(seconds, `tools-speeds-${index}`)}
      <p class="note" id="tools-library-${index}">${known.length} already in your library (${fmt(knownTime)} total)</p>
      <div class="actions"><button class="btn" data-act="tools-export" data-index="${index}">Export CSV</button>${item.kind === 'playlist' ? `<button class="btn btn-dark" data-act="tools-add" data-index="${index}">Add to library</button>` : ''}${compare ? `<button class="btn" data-act="tools-export-only" data-index="${index}">Export only here</button>` : ''}</div></section>`;
  }).join('');
  const sharedIds = new Set();
  if (items.length > 1 && items.every(x => x.kind === 'playlist')) {
    const counts = new Map(); items.forEach(x => new Set(x.videos.map(v => v.video_id)).forEach(id => counts.set(id, (counts.get(id) || 0) + 1)));
    counts.forEach((count, id) => { if (count > 1) sharedIds.add(id); });
  }
  return `<section><div class="eyebrow">Tools</div><h1 class="display">Playlist length<em>for any playlist or video</em></h1><p class="lede">Paste playlist or video links to see how long they take, without adding them to your library.</p></section>
    <section class="section card"><label class="field">Playlist or video links<textarea id="tools-links" rows="4" placeholder="One YouTube link per line">${esc(t.links)}</textarea></label><label class="check" style="margin-top:12px"><input id="tools-browser" type="checkbox" ${t.browser ? 'checked' : ''}> Use my YouTube sign-in for private playlists</label><div class="actions" style="margin-top:14px"><button class="btn btn-dark" data-act="tools-calculate" ${t.busy ? 'disabled' : ''}>${t.busy ? `Reading ${t.links.split(/\r?\n/).filter(x => x.trim()).length} links…` : 'Calculate'}</button></div></section>
    ${errors.map(x => `<p class="banner warn">${esc(x.link || '')}: ${esc(x.error)}${!t.browser && !x.link?.includes('watch?v=') ? ' If it is a private playlist, tick “Use my YouTube sign-in” and calculate again.' : ''}</p>`).join('')}
    ${items.length > 1 ? `<section class="section card"><h2 class="h">Combined length</h2><p class="note">${allById.size} unique videos · ${fmt(total)}</p>${toolsSpeeds(total)}
      ${items.every(x => x.kind === 'playlist') ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Playlist</th><th class="r">Videos</th><th class="r">Total</th><th class="r">Shared</th><th class="r">Only here</th></tr></thead><tbody>${items.map(item => { const { shared, only } = toolsSplit(item);
        return `<tr><td>${esc(item.title || item.id || 'Untitled')}</td><td class="r">${item.videos.length}</td><td class="r">${fmt(toolsSum(item.videos))}</td><td class="r">${shared.length} · ${fmt(toolsSum(shared))}</td><td class="r">${only.length} · ${fmt(toolsSum(only))}</td></tr>`; }).join('')}</tbody></table></div>` : ''}</section>` : ''}${resultCards}
    ${sharedIds.size ? `<p class="note">${plural(sharedIds.size, 'video')} appear in more than one playlist.</p>` : ''}
    ${items.length ? `<section class="section card"><h2 class="h">Daily planner</h2><div class="form-row" style="margin-top:12px"><label class="field">Minutes a day<input type="number" min="5" step="5" value="${t.mins}" data-change="tools-plan" data-key="mins"></label><label class="field">Days a week<input type="number" min="1" max="7" value="${t.days}" data-change="tools-plan" data-key="days"></label><label class="field">Speed<select data-change="tools-plan" data-key="speed">${SPEEDS.map(x => `<option value="${x}" ${x === t.speed ? 'selected' : ''}>${sx(x)}</option>`).join('')}</select></label></div><p class="note" id="tools-plan-summary">Done in ${plural(plannerDays, 'day')} · by ${esc(studyFinish(plannerDays, t.days))}</p></section>` : ''}`;
}
function fromValue(value, count, fallback) { return Math.min(count || 1, Math.max(1, +(value || fallback))); }
function viewDetailsCard() {
  const vs = videos(), counts = {};
  for (const v of vs) if (v.details) counts[v.details.status] = (counts[v.details.status] || 0) + 1;
  const need = vs.filter(needsDetails).length, off = vs.filter(v => !wantsTranscript(v)).length, running = !!S.data.job.running;
  return `
  <section class="section card">
    <h2 class="h">Video details and transcripts</h2>
    <p class="note">Video details ${tip('Descriptions, chapters and captions enable search and summaries. They are fetched without your sign-in, one video every few seconds. Music categories are excluded; change this on Categories.')}</p>
    <div class="det-counts">
      <span><b>${counts.done || 0}</b>with transcript</span>
      <span><b>${counts.no_captions || 0}</b>no captions</span>
      <span><b>${(counts.music || 0) + off}</b>music / left out</span>
      <span><b>${counts.unavailable || 0}</b>unavailable</span>
      <span><b>${need}</b>still to fetch</span>
    </div>
    <div class="actions">
      <button class="btn btn-dark btn-sm" data-act="details-fetch" data-job-btn ${!need ? 'disabled' : ''}>${need ? `Fetch details for ${plural(Math.min(need, 200), 'video')}` : 'All fetched'}</button>
      ${need > 200 ? `<span class="faint" style="font-size:12.5px">200 per run (about ${Math.round(200 * 5 / 60)} min). Run again for the rest.</span>`
        : need ? `<span class="faint" style="font-size:12.5px">About ${Math.max(1, Math.round(need * 5 / 60))} min</span>` : ''}
      <a class="btn-text" href="#/settings/sync">Sync settings →</a>
    </div>
    ${viewAsr()}
    ${viewVisuals()}
  </section>`;
}
function viewVisuals() {
  const P = S.visPlan, running = !!S.data.job.running;
  if (!P || P === 'loading') { loadVisPlan(); return '<div class="asr-box"><h3>Slides and whiteboards</h3><p class="note">Checking…</p></div>'; }
  if (P.error) return '';
  const suggested = videos().filter(v => v.visual?.hint && v.has_visuals == null && !isDone(v));
  return `<div class="asr-box">
    <h3>Slides and whiteboards</h3>
    <p class="note">Reads what videos <b>show</b>: slides, code, terminals, diagrams and whiteboards.
      Only the video picture is downloaded (no audio), anonymously; it is deleted after reading, and small thumbnails are kept for a slide strip.
      Mark videos in their panel, switch on <b>Slides</b> for a category, or accept the suggestions below.</p>
    ${!P.available ? `<div class="banner"><span>Needs Pillow and ffmpeg: <code>bash scripts/setup.sh --local-ai</code>.</span></div>`
      : P.engine === 'local' && !P.model_ok ? `<div class="banner"><span>The vision model (${esc(P.model || 'none')}) isn't ready in Ollama or can't read images. <a href="#/settings/local">Choose one in Local AI settings</a>.</span></div>` : ''}
    <div class="actions">
      ${P.videos ? `<button class="btn btn-dark btn-sm" data-act="vis-run" data-job-btn ${!P.available || (P.engine === 'local' && !P.model_ok) ? 'disabled' : ''}>Read slides for ${plural(P.videos, 'video')}</button>
        <span class="faint" style="font-size:12.5px">${fmt(P.seconds)} of video · about ${Math.max(1, Math.round(P.minutes))} min${P.engine !== 'local' && P.cost != null ? ` · about $${Number(P.cost).toFixed(4)} to ${esc(S.data.ai.providers[P.engine]?.label || P.engine)}` : ` with ${esc(P.model)}`}</span>${P.sends ? `<p class="hint">${esc(P.sends)}</p>` : ''}`
        : '<span class="faint" style="font-size:12.5px">No videos marked yet.</span>'}
    </div>
    ${suggested.length ? `<div class="actions" style="margin-top:10px"><button class="btn btn-sm" data-act="vis-mark-suggested" data-ids="${esc(suggested.map(v => v.video_id).join(','))}">Mark ${plural(suggested.length, 'suggested video')}</button>
      <span class="faint" style="font-size:12.5px">${suggested.slice(0, 3).map(v => `${esc(v.title.slice(0, 40))} (${esc(v.visual.hint)})`).join(' · ')}${suggested.length > 3 ? ' …' : ''}</span></div>` : ''}
  </div>`;
}
async function loadVisPlan() {
  if (S.visPlan === 'loading') return;
  S.visPlan = 'loading';
  try { S.visPlan = await api('/api/visuals/plan'); } catch (e) { S.visPlan = { error: e.message }; }
  if (route() === 'sources') refresh();
}
function viewAsr() {
  const P = S.asrPlan, running = !!S.data.job.running;
  if (!P || P === 'loading') { loadAsrPlan(); return '<div class="asr-box"><h3>Local transcription</h3><p class="note">Checking…</p></div>'; }
  if (P.error) return '';
  const where = P.device === 'cuda' ? 'on your GPU' : 'on the CPU';
  return `<div class="asr-box">
    <h3>Transcription</h3>
    <p class="note">For videos with <b>no captions</b>, or captions in the <b>wrong script</b> (for example English speech written in Devanagari).
      ${P.engine === 'local' ? 'Whisper runs on this computer;' : `Audio is uploaded to ${esc(S.data.ai.providers[P.engine]?.label || P.engine)} for transcription;`} only the original-language audio is downloaded, anonymously, and it is deleted right after.
      Where captions exist, both are kept and you choose per video.</p>
    ${P.engine === 'local' && !P.available ? `<div class="banner"><span>Needs faster-whisper. Install it with <code>bash scripts/setup.sh --local-ai</code>.</span></div>`
      : P.videos ? `<div class="actions">
          <button class="btn btn-dark btn-sm" data-act="asr-run" data-job-btn>Transcribe ${plural(P.videos, 'video')}</button>
          <span class="faint" style="font-size:12.5px">${P.no_captions} without captions · ${P.wrong_script} wrong script · ${fmt(P.audio_s)} of audio ·
            about ${Math.max(1, Math.round(P.minutes))} min ${P.engine === 'local' ? `${where} (${esc(P.model)})` : `with ${esc(S.data.ai.providers[P.engine]?.label || P.engine)} · about $${Number(P.cost || 0).toFixed(4)}`}</span>${P.sends ? `<p class="hint">${esc(P.sends)}</p>` : ''}</div>`
      : '<p class="hint">Nothing to transcribe: every fetched video has usable captions. Fetch details for more videos first.</p>'}
  </div>`;
}
async function loadAsrPlan() {
  if (S.asrPlan === 'loading') return;
  S.asrPlan = 'loading';
  try { S.asrPlan = await api('/api/asr/plan'); } catch (e) { S.asrPlan = { error: e.message }; }
  if (route() === 'sources') refresh();
}
function fmtDate(s) {
  if (!s) return '';
  const d = new Date(s.replace(' ', 'T') + 'Z');
  return isNaN(d) ? s : d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
function fmtFullDate(s) {
  if (!s) return '';
  const d = new Date(s.replace(' ', 'T') + (s.endsWith('Z') ? '' : 'Z'));
  return isNaN(d) ? s : d.toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function fmtRelTime(s) {
  if (!s) return '';
  const d = new Date(s.replace(' ', 'T') + (s.endsWith('Z') ? '' : 'Z'));
  if (isNaN(d)) return s;
  const now = new Date();
  const diffSec = Math.floor((now.getTime() - d.getTime()) / 1000);
  if (diffSec < 60) return 'just now';
  if (diffSec < 3600) return `${Math.max(1, Math.floor(diffSec / 60))} m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} h ago`;
  if (diffSec < 86400 * 2) return 'yesterday';
  if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)} d ago`;
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString(undefined, sameYear
    ? { day: 'numeric', month: 'short' }
    : { day: 'numeric', month: 'short', year: 'numeric' });
}
// Job times are local ISO strings from the server, unlike SQLite's UTC timestamps
const fmtLocal = s => { const d = new Date(s || ''); return isNaN(d) ? '' : d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); };
// Which step a running task is on, from its log: a sync reads, sorts, saves, then may fetch details and update the index
const STAGES = [[/^Step 1/m, 'Reading the playlist'], [/^Step 2/m, 'Sorting into categories'], [/^Step [3-5]/m, 'Saving'],
  [/^AI suggestions/m, 'AI sorting'], [/^Video details/m, 'Fetching details'], [/meaning index/m, 'Updating the search index']];
function jobStage(job) {
  let at = -1, label = '';
  for (const [re, name] of STAGES) {
    const m = [...(job.log || '').matchAll(new RegExp(re.source, 'gm'))].pop();
    if (m && m.index > at) { at = m.index; label = name; }
  }
  return label;
}
const lastError = log => ((log || '').split('\n').reverse().find(l => /^\s*(Error|Unexpected error)/.test(l)) || '').trim().slice(0, 160);
function syncFailureCard(job) {
  if (!job || job.kind !== 'sync' || job.running || job.ok || job.stopped || job.dismissed) return '';
  const lines = (job.log || '').split('\n');
  const cause = lines.find(line => /^\s*Cause:\s*/i.test(line))?.replace(/^\s*Cause:\s*/i, '').trim() || lastError(job.log) || 'The sync did not complete.';
  const fix = lines.find(line => /^\s*Fix:\s*/i.test(line))?.replace(/^\s*Fix:\s*/i, '').trim();
  return `<div class="banner warn" style="display:block"><b>Sync failed</b><p style="margin:6px 0">${esc(cause)}</p>${fix ? `<p style="margin:6px 0"><b>Fix:</b> ${esc(fix)}</p>` : ''}
    <div class="actions"><button class="btn btn-sm btn-dark" data-act="job-retry" data-id="${job.id}">Try again</button><button class="btn btn-sm" data-act="job-dismiss" data-id="${job.id}">Dismiss</button>
    <details><summary class="btn-text" style="cursor:pointer">See details</summary><pre class="log">${esc(lines.filter(Boolean).slice(-40).join('\n'))}</pre></details></div></div>`;
}
function renderJobs() {
  const box = $('#jobs'), job = S.data?.job;
  if (!box || !job) return;
  const waiting = job.queue?.length || 0, last = job.recent?.[0];
  const active = (job.lanes || []).filter(l => l.running);
  const stopping = !!job.stopping || S.stopPending;
  const flash = !job.running && last && S.flash?.id === last.id && Date.now() < S.flash.until;
  if (flash) setTimeout(renderJobs, S.flash.until - Date.now() + 50);
  const pill = active.length
    ? `<button class="jobpill run" data-act="jobs-toggle" aria-expanded="${!!S.jobsOpen}" title="${active.map(l => esc(l.label)).join(', ')}${waiting ? ` (+${waiting} waiting)` : ''}"><span class="spin"></span><span class="jl">${active.length} running</span>${waiting ? `<b>+${waiting}<span class="jw"> waiting</span></b>` : ''}</button>`
    : flash ? `<button class="jobpill ${last.ok && !last.stopped ? 'ok' : 'bad'}" data-act="jobs-toggle"><span class="jl">${last.stopped ? 'Stopped' : last.ok ? 'Finished' : 'Failed'}: ${esc(last.label)}</span></button>`
    : job.recent?.length ? `<button class="jobpill idle" data-act="jobs-toggle" title="Tasks">Tasks</button>` : '';
  const row = (t, state, action) => `<div class="jobrow"><span class="st st-${state}">${{ run: 'Running', stopping: 'Stopping…', wait: 'Waiting', ok: 'Finished', bad: 'Failed', stop: 'Stopped' }[state]}</span>
      <span class="jl" title="${esc(t.label)}">${esc(t.label)}</span>${action}</div>`;
  const panel = S.jobsOpen ? `<div class="jobpanel" role="dialog" aria-label="Tasks">
      ${active.length ? active.map(l => `<div><div class="faint" style="font-size:11.5px">${esc(({youtube:'YouTube',local:'This computer',cloud:'Cloud'})[l.lane] || l.lane)}</div>${row(l, l.stopping ? 'stopping' : 'run', l.stopping ? '<button class="btn-text" disabled>Stopping…</button>' : `<button class="btn-text" data-act="job-stop-one" data-task="${l.id}">Stop</button>`)}<pre class="log">${esc(l.log || '')}</pre></div>`).join('') : '<p class="note faint" style="margin:0 0 6px">Nothing running.</p>'}
      ${(job.queue || []).map(t => row({ ...t, label: `${({youtube:'YouTube',local:'This computer',cloud:'Cloud'})[t.lane] || t.lane}: ${t.label}` }, 'wait', `<button class="btn-text" data-act="job-drop" data-task="${t.id}">Remove</button>`)).join('')}
      ${(job.recent || []).length ? `<div class="faint" style="font-size:11.5px;margin:10px 0 4px">Recent</div>` + job.recent.map(t => row(t, t.stopped ? 'stop' : t.ok ? 'ok' : 'bad',
        `<span class="faint" style="font-size:11.5px">${esc(fmtLocal(t.finished_at))}</span>`)).join('') : ''}
    </div>` : '';
  box.innerHTML = pill + panel;
}
function updateLogJump(box) {
  const log = $('.log', box), btn = $('[data-act="log-latest"]', box);
  if (!log || !btn) return;
  const isUp = (log.scrollHeight - log.scrollTop - log.clientHeight) >= 24;
  btn.style.display = isUp ? '' : 'none';
}
function attachLogScroll(log) {
  if (!log || log.dataset.hasScroll) return;
  log.dataset.hasScroll = '1';
  log.addEventListener('scroll', () => {
    const box = log.closest('#job');
    if (!box) return;
    S.logPos = { id: Number(box.dataset.jobId), top: log.scrollTop, atBottom: log.scrollHeight - log.scrollTop - log.clientHeight < 24 };
    updateLogJump(box);
  }, { passive: true });
}
function setLogText(el, text) {
  let offsets = null;
  const sel = window.getSelection();
  if (sel && sel.rangeCount) {
    const range = sel.getRangeAt(0);
    if (el.contains(range.startContainer) && el.contains(range.endContainer)) {
      try {
        const pre = range.cloneRange();
        pre.selectNodeContents(el);
        pre.setEnd(range.startContainer, range.startOffset);
        const start = pre.toString().length;
        pre.setEnd(range.endContainer, range.endOffset);
        const end = pre.toString().length;
        offsets = { start, end };
      } catch (_) {}
    }
  }
  el.textContent = text;
  if (offsets && el.firstChild) {
    try {
      const node = el.firstChild, len = node.textContent.length;
      const r = document.createRange();
      r.setStart(node, Math.min(offsets.start, len));
      r.setEnd(node, Math.min(offsets.end, len));
      sel.removeAllRanges();
      sel.addRange(r);
    } catch (_) {}
  }
}
function updateJob() {
  const box = $('#job'), all = S.data?.job;
  if (!box || !all) return;
  const myKinds = route() === 'categories' ? ['discover', 'suggest'] : route().startsWith('settings') ? ['pull', 'embed'] : route() === 'knowledge' ? ['knowledge'] : ['sync', 'details', 'asr', 'visual'];
  // The top-level fields follow the newest task in any lane; show this page's own running task instead.
  const lane = (all.lanes || []).filter(l => l.running && myKinds.includes(l.kind)).sort((a, b) => b.id - a.id)[0];
  const job = lane ? { ...all, ...lane } : all;
  const mine = !!lane || myKinds.includes(job.kind) || (!job.kind && !route().startsWith('settings') && !['categories', 'knowledge'].includes(route()));
  const waiting = (job.queue || []).filter(t => myKinds.includes(t.kind));
  const waitLine = waiting.length ? `<p class="note" style="margin-top:12px">Waiting in the queue: ${waiting.map(t => esc(t.label)).join(', ')}. ${all.running ? `Starts after “${esc(all.label)}”.` : ''}</p>` : '';
  if (!mine || (!job.log && !job.running) || (job.dismissed && !job.running)) {
    box.innerHTML = waitLine;
    delete box.dataset.jobId;
    return;
  }
  const text = JOB_TEXT[job.kind] || JOB_TEXT.sync;
  const stopping = !!job.stopping || S.stopPending;
  const stage = job.running ? jobStage(job) : '';
  const head = job.running ? `<span class="status-line"><span class="spin"></span>${stage ? `<b style="font-weight:600">${esc(stage)}</b>&nbsp;·&nbsp;` : ''}${stopping ? 'Stopping…' : text[0]} ${stopping ? '<button class="btn-text" disabled>Stopping…</button>' : `<button class="btn-text" data-act="job-stop" data-task="${job.id}">Stop</button>`}</span>`
    : `<div class="done-banner ${job.stopped ? 'bad' : job.ok ? 'ok' : 'bad'}"><b>${job.stopped ? `Stopped: ${esc(job.label || '')}` : job.ok ? 'Finished' : 'Failed'}</b>
        <span>${esc(fmtLocal(job.finished_at))}${!job.ok && !job.stopped ? ` · ${esc(lastError(job.log))}` : ''}</span>
        <button class="btn-text" data-act="job-dismiss" data-id="${job.id}" style="margin-left:auto">Close</button></div>`;
  const oldLog = box.dataset.jobId === String(job.id) ? $('.log', box) : null;
  // A rebuilt box (re-render, or another task shown meanwhile) returns to where the reader was.
  const pos = S.logPos?.id === job.id ? S.logPos : null;
  const atBottom = oldLog ? (oldLog.scrollHeight - oldLog.scrollTop - oldLog.clientHeight < 24) : pos ? pos.atBottom : true;
  const oldScrollTop = oldLog ? oldLog.scrollTop : pos ? pos.top : 0;
  const same = oldLog && $('.job-head', box);
  let log = oldLog;
  if (same) {
    const headEl = $('.job-head', box);
    if (headEl && headEl.innerHTML !== head) headEl.innerHTML = head;
    const waitEl = $('.job-wait', box);
    if (waitEl && waitEl.innerHTML !== waitLine) waitEl.innerHTML = waitLine;
    const newLog = job.log || '';
    if (log.textContent !== newLog) setLogText(log, newLog);
  } else {
    box.dataset.jobId = String(job.id);
    box.innerHTML = `<div class="job-head" style="margin-top:18px">${head}</div><div class="job-wait">${waitLine}</div><div class="log-wrap"><pre class="log">${esc(job.log || '')}</pre><button type="button" class="btn btn-sm log-latest" data-act="log-latest" style="display:none">Jump to latest ↓</button></div>`;
    log = $('.log', box);
  }
  if (log) {
    attachLogScroll(log);
    if (atBottom) log.scrollTop = log.scrollHeight;
    else log.scrollTop = oldScrollTop;
    updateLogJump(box);
  }
}
async function pollJob() {
  if (S.polling) return;
  S.polling = true;
  try {
    for (;;) {
      const job = await api('/api/job');
      const seen = S.data.job?.recent?.[0]?.id ?? 0, last = job.recent?.[0];
      S.data.job = job;
      if (last && last.id !== seen) await taskFinished(last);
      updateJob(); renderJobs();
      if (!job.running && !job.queue.length) break;
      await sleep(1000);
    }
  } catch (e) { toast(e.message, true); } finally { S.polling = false; }
}
async function taskFinished(t) {
  const text = JOB_TEXT[t.kind] || JOB_TEXT.sync;
  if (['details', 'sync', 'asr', 'visual', 'remove'].includes(t.kind)) { S.detailCache = {}; S.asrPlan = null; S.visPlan = null; }  // transcripts or slides changed
  if (t.kind === 'summarize' || t.kind === 'remove') S.summaryCache = {};
  if (['summarize', 'knowledge', 'remove'].includes(t.kind)) { S.knowledge.list = null; S.knowledge.pages = {}; }
  if (!S.data.job.running) S.busy = {};
  if (t.kind === 'pull' || t.kind === 'embed') S.local = null;        // new model downloaded, or index status changed
  if (t.kind === 'backup') S.storage = null;
  S.flash = { id: t.id, until: Date.now() + 15000 };
  S.stopPending = false;
  if (!t.stopped) toast(t.ok ? text[1] : text[2], !t.ok);
  // The reload below rebuilds the page; focusout never fires on a removed box, so save a note being typed first
  const typing = document.activeElement?.id === 'd-notes' ? $('#d-notes').selectionStart : null;
  saveNotes();
  const job = S.data.job; await load({ keepEdits: true }); S.data.job = job;
  if (typing !== null) { const ta = $('#d-notes'); if (ta) { ta.focus(); ta.setSelectionRange(typing, typing); } }
}
// Category edits not saved yet, which a background reload would wipe
function unsavedCategoryEdits() {
  if (route() !== 'categories') return false;
  const tax = S.data.taxonomy || {}, words = v => v.split(',').map(w => w.trim()).filter(Boolean).join(',');
  return !!$('#new-cat-name')?.value.trim() || $$('[data-change="rename"]').some(i => i.value.trim() !== i.dataset.old)
    || $$('.kw').some(ta => words(ta.value) !== (tax[ta.dataset.name] || []).join(','));
}
// After asking the server to start a task: say whether it started or is waiting, then follow the queue
async function queued(res, what) {
  toast(res.position ? `${what} queued. It starts after: ${res.after.join(', ')}` : `${what} started`);
  await refreshJob();
}
async function refreshJob() {
  try { S.data.job = await api('/api/job'); } catch (e) { return; }
  updateJob(); renderJobs(); pollJob();
}
async function startAi(path, body = {}) {
  try {
    const res = await api(path, body);
    if (route() !== 'categories') location.hash = '#/categories';
    await queued(res, path.endsWith('discover') ? 'Category proposal' : 'Category suggestions');
  } catch (e) { toast(e.message, true); }
}
async function runIngest(url) {
  const g = S.ingest;
  const browser = BROWSERS.some(([k]) => k === g.access) ? g.access : null;
  try {
    await queued(await api('/api/ingest', { url, access: browser ? 'browser' : g.access, browser, cookies_file: g.cookies, library: g.lib || S.lib || 1 }), `Sync of ${url}`);
  } catch (e) { toast(e.message, true); }
}
async function runResync(playlistId) {
  const g = S.ingest, p = S.data.playlists.find(x => x.id === playlistId);
  const browser = BROWSERS.some(([k]) => k === g.access) ? g.access : null;
  // playlists synced before access was stored use the form's choice once; the server then remembers it
  const body = p?.access ? { playlist_id: playlistId } : { playlist_id: playlistId, access: browser ? 'browser' : g.access, browser, cookies_file: g.cookies };
  try { await queued(await api('/api/ingest', body), `Sync of ${p?.title || playlistId}`); await load(); }
  catch (e) { toast(e.message, true); }
}

/* ---------------- categories ---------------- */
function viewCategories() {
  if (!S.lib) return `<section><div class="eyebrow">Categories</div><h1 class="display">Choose a library<em>each library has its own categories</em></h1>
    <p class="lede">Choose a library to edit its categories.</p>
    <div class="chips" style="margin-top:16px">${S.data.libraries.map(l => `<button class="chip" data-act="lib-go" data-lib="${l.id}">${esc(l.name)}</button>`).join('')}</div></section>`;
  const counts = {};
  for (const v of videos()) counts[v.category] = (counts[v.category] || 0) + 1;
  const tax = S.data.taxonomy, ai = S.data.ai, notes = ai.category_notes || {}, presets = new Set(ai.presets || []);
  const manual = Object.keys(counts).filter(c => !(c in tax) && c !== FALLBACK() && c !== 'Uncategorized');
  const fb = (counts[FALLBACK()] || 0) + (counts.Uncategorized || 0);
  const pending = videos().filter(v => v.suggestion).length;
  // Mirrors storage.videos_for_ai('unsorted'): unlocked, unsorted, no suggestion waiting, not already "no match" for these categories
  const unsortedOpen = videos().filter(v => !v.category_locked && !v.suggestion && (v.category === FALLBACK() || v.category === 'Uncategorized'));
  const noMatch = unsortedOpen.filter(v => v.ai_no_match === S.data.tax_key).length, toSort = unsortedOpen.length - noMatch;
  const running = !!S.data.job.running;
  return `
  <section>
    <div class="eyebrow">Categories</div>
    <h1 class="display">${plural(Object.keys(tax).length + manual.length, 'category')}<em>${fb ? `${fb} unsorted` : 'everything sorted'}</em></h1>
    <p class="lede">Categories sort your videos. Keywords sort instantly on this computer; the AI sorts the rest and, for videos that fit nothing, proposes new categories for you to accept.</p>
    <div class="actions">
      <button class="btn btn-dark btn-sm" data-act="reclassify">Save and re-sort</button>
      <button class="btn btn-sm" data-act="add-cat">Add category</button>
      ${ai.ready_for.categorize ? Object.keys(tax).length
        ? `<button class="btn btn-sm" data-act="ai-suggest" data-job-btn ${!toSort ? 'disabled' : ''} title="${toSort ? '' : noMatch ? 'The AI found no category for the rest' : 'Every unsorted video already has a suggestion or was set by hand'}">${toSort ? `Sort ${plural(toSort, 'unsorted video')} with AI` : !fb ? 'Nothing to sort' : noMatch ? 'The AI found no category for the rest' : 'Unsorted videos already have suggestions'}</button>`
        : `<button class="btn btn-sm" data-act="ai-discover" data-job-btn ${!ai.interests ? 'disabled title="Write your interests in About you first"' : ''}>Create categories from my interests</button>`
        : `<a class="btn-text" href="#/settings/routing">Set up AI sorting →</a>`}
      ${pending ? `<a class="btn-text" href="#/" data-act="v-status" data-v="suggested">Review ${plural(pending, 'suggestion')} →</a>` : ''}
    </div>
    ${!Object.keys(tax).length ? `<p class="note" style="margin-top:10px">No categories yet. ${ai.interests ? 'Create categories from your interests with AI, or add your own.' : '<a href="#/settings/about">Write your interests in About you first →</a> to create categories, or add your own.'}</p>`
      : ai.interests ? '<p class="hint" style="margin-top:8px">AI proposals follow your learning interests from About you.</p>' : ''}
    <div id="job"></div>
  </section>
    ${ai.proposal ? viewProposal(ai.proposal, tax, counts) : ''}
  <section class="section">
    ${ai.proposal ? '<h2 class="h">Current categories</h2>' : ''}
    ${S.addingCat ? viewAddCategoryRow() : ''}
    ${Object.keys(tax).map(n => `<div class="cat-row">
      <div><label class="field-label">Category name</label><input type="text" value="${esc(n)}" title="${esc(n)}" data-change="rename" data-old="${esc(n)}" aria-label="Category name">
        <div class="meta"><a href="#/" data-act="v-cat" data-v="${esc(n)}">${plural(counts[n] || 0, 'video')}</a></div>
        <div class="cat-opts">
          <label class="keep"><input type="checkbox" data-change="preset" data-name="${esc(n)}" ${presets.has(n) ? 'checked' : ''}> Protect from AI ${tip('When you ask the AI to propose categories, this one stays exactly as it is (name and keywords). Untick to let the AI merge or rename it.')}</label>
          <label class="keep"><input type="checkbox" data-change="transcripts" data-name="${esc(n)}" ${S.data.transcripts[n] !== false ? 'checked' : ''}> Fetch transcripts ${tip('Download captions for videos in this category, for search, summaries and ratings. Turn off for music or other videos where words do not matter.')}</label>
          <label class="keep"><input type="checkbox" data-change="visual-cat" data-name="${esc(n)}" ${S.data.visual_categories[n] ? 'checked' : ''}> Read slides ${tip('Also read slides, whiteboards and code shown on screen (local AI, downloads the video briefly). For lectures and tutorials.')}</label>
        </div></div>
      <div><label class="field-label" for="kw-${n.replace(/[^\w-]/g, '_')}">Keywords</label><textarea id="kw-${n.replace(/[^\w-]/g, '_')}" class="kw" data-name="${esc(n)}" rows="4" placeholder="Separate keywords with commas">${esc(tax[n].join(', '))}</textarea>
        ${notes[n] ? `<div class="field-label">Category note ${tip(notes[n])}</div>` : ''}</div>
      <div>${isCombined(n) ? `<button class="btn-text" data-act="split-topics" data-name="${esc(n)}">Split into topics</button>` : ''}<button class="btn-text" data-act="del-cat" data-name="${esc(n)}">Delete</button></div>
    </div>`).join('')}
    ${manual.map(n => `<div class="cat-row">
      <div><b style="font-weight:500;overflow-wrap:anywhere" title="${esc(n)}">${esc(n)}</b><div class="meta"><a href="#/" data-act="v-cat" data-v="${esc(n)}">${plural(counts[n], 'video')}</a> · set by hand</div></div>
      <p class="field-label" style="padding-top:6px">No keywords ${tip('This category only holds videos you moved here yourself.')}</p>
      <div><button class="btn-text" data-act="adopt-cat" data-name="${esc(n)}">Add keywords</button><button class="btn-text" data-act="del-cat" data-name="${esc(n)}">Delete</button></div>
    </div>`).join('')}
  </section>`;
}
function viewAddCategoryRow() {
  return `
  <div class="cat-row cat-row-add" id="cat-add-row">
    <div>
      <label class="field-label" for="new-cat-name">Category name</label>
      <input type="text" id="new-cat-name" placeholder="Category name" aria-label="Category name" autofocus>
      <div id="new-cat-err" class="hint warn-text" role="alert" style="margin-top:4px;display:none"></div>
    </div>
    <div>
      <label class="field-label" for="new-cat-kw">Keywords</label>
      <textarea id="new-cat-kw" class="cat-add-kw" rows="4" placeholder="Separate keywords with commas (optional)" aria-label="Keywords for new category"></textarea>
    </div>
    <div class="cat-add-actions">
      <button class="btn btn-dark btn-sm" data-act="save-add-cat">Save</button>
      <button class="btn-text" data-act="cancel-add-cat">Cancel</button>
    </div>
  </div>`;
}
function viewProposal(p, tax, counts) {
  const proposed = new Set(p.categories.map(c => c.name));
  const dropped = Object.keys(tax).filter(n => !proposed.has(n) && !(S.data.ai.presets || []).includes(n));
  const rows = p.categories.map((c, i) => ({ ...c, i }));
  const fresh = rows.filter(c => !c.existing), changed = rows.filter(c => c.existing && (c.added || []).length),
    same = rows.filter(c => c.existing && !(c.added || []).length);
  return `
  <section class="section card">
    <h2 class="h">${p.for_videos ? `New categories for ${plural(p.for_videos, 'video')} that fit none` : 'Proposed changes'}</h2>
    <p class="note">${p.model ? esc(p.model) + ' suggests ' : ''}${plural(fresh.length, 'new category')}${changed.length ? ` and new keywords for ${plural(changed.length, 'category')}` : ''}.
      ${tip('Nothing changes until you apply. Untick anything you do not want. Categories you protect stay exactly as they are; near-copies of existing categories were merged into them as keywords.')}</p>
    <div id="proposal" style="margin-top:8px">
      ${fresh.length ? `<h3 class="field-label" style="margin-top:14px">New categories</h3>` + fresh.map(c => `<div class="prop-row" data-i="${c.i}">
        <label class="prop-add"><input type="checkbox" class="p-on" checked> Add</label>
        <div><input type="text" class="p-name" value="${esc(c.name)}" title="${esc(c.name)}" style="width:100%;font-weight:500" aria-label="Name of the new category">
          ${c.description ? `<div class="meta" style="font-size:12.5px;color:var(--ink-3);margin-top:6px">${esc(c.description)}</div>` : ''}</div>
        <div><label class="field-label">Keywords</label><textarea class="p-kw" rows="3" aria-label="Keywords for ${esc(c.name)}">${esc(c.keywords.join(', '))}</textarea></div>
      </div>`).join('') : '<p class="note" style="margin-top:12px">No new categories: your current ones already cover these videos.</p>'}
      ${changed.length ? `<h3 class="field-label" style="margin-top:22px">New keywords for existing categories</h3>` + changed.map(c => `<div class="prop-changed" data-i="${c.i}">
        <label class="prop-add"><input type="checkbox" class="p-on" checked> Add</label>
        <div><b style="font-weight:500">${esc(c.name)}</b> <span class="faint" style="font-size:12.5px">${plural(counts[c.name] || 0, 'video')}</span><div>${c.added.map(k => `<span class="kw-chip">+ ${esc(k)}</span>`).join('')}</div></div>
      </div>`).join('') : ''}
      ${same.length ? `<details style="margin-top:18px"><summary class="note" style="cursor:pointer">${plural(same.length, 'category')} unchanged</summary>
        <p class="note faint" style="margin-top:6px">${same.map(c => esc(c.name)).join(' · ')}</p></details>` : ''}
      ${dropped.length && !p.for_videos ? `<div style="margin-top:20px"><span class="field-label">Categories the AI left out</span>
        ${dropped.map(n => `<label class="check" style="margin-top:8px"><input type="checkbox" class="p-drop" value="${esc(n)}">
          <span>Remove <b style="font-weight:500">${esc(n)}</b> <span class="faint">(${plural(counts[n] || 0, 'video')} go back to automatic sorting)</span></span></label>`).join('')}
        <p class="hint">Unticked ones stay as they are.</p></div>` : ''}
    </div>
    <div class="actions">
      <button class="btn btn-dark btn-sm" data-act="prop-apply">Apply changes</button>
      <button class="btn btn-sm" data-act="prop-dismiss">Discard proposal</button>
    </div>
  </section>`;
}
function collectProposal() {
  // Only additions are sent: categories not listed stay exactly as they are (the server keeps them)
  const p = S.data.ai.proposal, categories = [];
  for (const row of $$('#proposal .prop-row, #proposal .prop-changed')) {
    if (!$('.p-on', row).checked) continue;
    const c = p.categories[+row.dataset.i];
    if (row.classList.contains('prop-row'))
      categories.push({ name: $('.p-name', row).value.trim() || c.name, description: c.description,
        keywords: $('.p-kw', row).value.split(',').map(x => x.trim()).filter(Boolean) });
    else categories.push({ name: c.name, keywords: c.keywords });   // existing category plus its new keywords
  }
  return { categories, remove: $$('#proposal .p-drop').filter(x => x.checked).map(x => x.value) };
}

/* ---------------- search ---------------- */
const SEARCH_KINDS = [['all', 'Everything'], ['transcript', 'Transcripts'], ['slide', 'Slides'], ['chapter', 'Chapters'], ['title', 'Titles'],
  ['summary', 'AI summaries'], ['notes', 'My notes'], ['description', 'Descriptions']];
const KIND_LABEL = { title: 'Title', chapter: 'Chapter', transcript: 'Transcript', slide: 'On screen', summary: 'AI summary', notes: 'Your notes', description: 'Description' };
// Snippets arrive with \x02…\x03 around matches: escape first, then highlight
const hl = s => esc(s).replace(/\x02/g, '<mark>').replace(/\x03/g, '</mark>');
function viewSearch() {
  const sr = S.search;
  const indexed = videos().filter(v => v.details?.status === 'done').length;
  return `
  <section>
    <div class="eyebrow">Search</div>
    <h1 class="display">Find it again<em>Search your library</em></h1>
    <p class="lede">Search titles, transcripts, chapters, summaries and notes across your library.</p>
    <label class="field-label" for="sq">Search ${tip('Search titles, chapters, transcripts, AI summaries and your notes. Use quotes for an exact phrase; words of 3+ letters match inside longer words.')}</label>
    <input type="search" id="sq" class="search-big" value="${esc(sr.q)}" placeholder="e.g. neo4j setup, &quot;plan mode&quot;, lora">
    <div class="chips" style="margin-top:14px">${SEARCH_KINDS.map(([k, l]) =>
      `<button class="chip ${sr.kind === k ? 'on' : ''}" data-act="s-kind" data-v="${k}">${l}</button>`).join('')}
      <span style="flex:1"></span>
      <label class="check" style="align-items:center">
        <input type="checkbox" data-change="s-meaning" ${sr.meaning ? 'checked' : ''} style="margin:0"><span class="note">By meaning</span></label></div>
    <p class="hint">By meaning ${tip('Find pieces with similar meaning, even when they use different words or another language. Results are ordered by closeness, so check the top matches.')}</p>
    <p class="hint">${indexed} of ${plural(videos().length, 'video')} have transcripts. ${indexed < videos().length ? '<a href="#/sources">Fetch details</a> to search inside more.' : ''}</p>
  </section>
  <div id="sresults"></div>`;
}
function renderSearchResults() {
  const box = $('#sresults'), sr = S.search;
  if (!box) return;
  if (!sr.q.trim()) { box.innerHTML = ''; return; }
  if (!sr.res) { box.innerHTML = '<p class="note" style="margin-top:24px"><span class="spin" style="display:inline-block"></span> Searching…</p>'; return; }
  if (sr.res.short) { box.innerHTML = '<div class="empty">Type at least 3 letters.</div>'; return; }
  const here = new Set(videos().map(v => v.video_id));
  const list = sr.res.results.filter(r => here.has(r.video_id));
  if (!list.length) { box.innerHTML = `<div class="empty">No matches for “${esc(sr.q)}”.</div>`; return; }
  const off = sr.meaning && sr.res.mode === 'keyword' ? ' · meaning index is off, so these are keyword matches (Settings → AI → download the embedding model)' : '';
  const exact = list.filter(r => r.exact).length, words = sr.q.trim().split(/\s+/).length > 1;
  const shown = list.slice(0, sr.limit);
  box.innerHTML = `<div class="list-sum"><span>Showing ${shown.length} of ${plural(list.length, 'match')}${list.length >= 50 ? ' (top 50 returned by search)' : ''}${sr.meaning ? ' · by meaning' : ''}${words && exact ? ` · ${exact} with the exact phrase` : ''}${off}</span></div>` + shown.map((r, i) => {
    const v = byId(r.video_id);
    const divider = words && exact && i === exact ? `<div class="list-sum" style="margin-top:18px"><span>${sr.meaning ? 'Related by meaning' : 'With the words apart'}</span></div>` : '';
    return `${divider}<div class="s-row">
      <button class="thumb-btn" data-act="open" data-id="${esc(v.video_id)}" tabindex="-1" aria-hidden="true">${thumb(v)}</button>
      <div style="min-width:0">
        <div class="tri-head">${v.rating ? badge(v) : ''}${isDone(v) ? `<span class="vb vb-none">${STATUS_LABEL[v.status]}</span>` : ''}</div>
        <button class="tri-title" data-act="open" data-id="${esc(v.video_id)}">${esc(v.title)}</button>
        <div class="tri-meta">${esc(v.channel || '')} · <span class="cat-chip" title="${esc(v.category)}">${esc(v.category)}</span> · ${fmt((v.duration_seconds || 0) / S.speed)} at ${sx(S.speed)}${r.matches > r.hits.length ? ` · ${r.matches} matches` : ''}</div>
        <ul class="hits">${r.hits.map(h => `<li><button class="hit" data-act="play-at" data-id="${esc(v.video_id)}" data-t="${h.t == null ? 0 : Math.floor(h.t)}">
          <span class="k">${KIND_LABEL[h.kind]}${h.t != null ? `<time>${clock(h.t)}</time>` : ''}</span><span>${hl(h.snippet)}</span></button></li>`).join('')}</ul>
      </div>
    </div>`;
  }).join('') + (list.length > sr.limit ? `<div class="more"><button class="btn" data-act="search-more">Show ${Math.min(50, list.length - sr.limit)} more</button></div>` : '');
}
async function runSearch() {
  const sr = S.search, seq = ++sr.seq;
  if (!sr.q.trim()) { sr.res = null; renderSearchResults(); return; }
  sr.res = null; renderSearchResults();
  try {
    const res = await api(`/api/search?q=${encodeURIComponent(sr.q)}${sr.kind !== 'all' ? `&kinds=${sr.kind}` : ''}${sr.meaning ? '&meaning=1' : ''}`);
    if (seq === sr.seq) { sr.res = res; renderSearchResults(); }   // ignore answers to older keystrokes
  } catch (e) { if (seq === sr.seq) { sr.res = { results: [], short: false }; toast(e.message, true); renderSearchResults(); } }
}
function goSearch(q) {
  S.search.q = q; S.search.res = null;
  const target = `#/search?q=${encodeURIComponent(q)}`;
  if (location.hash !== target) location.hash = target; else { refresh(); }
}

/* ---------------- ask ---------------- */
const MODE_LABEL = { hybrid: 'meaning + keywords', keyword: 'keywords only (meaning index off)' };
const askLabel = x => `${x.title}${x.t != null ? ' @ ' + clock(x.t) : ''}`;
function askScope(f) {
  const vid = f?.video_ids?.length === 1 ? f.video_ids[0] : null;
  return [vid && `about “${byId(vid)?.title || vid}”`, f?.categories?.length && f.categories.join(', '),
    f?.playlists?.length && f.playlists.map(playlistName).join(', '), f && f.include_done === false && 'unwatched only'].filter(Boolean).join(' · ');
}
function viewAsk() {
  const a = S.ask, ai = S.data.ai, ready = ai.ready_for.ask, th = a.thread, follow = th.length > 0;
  const local = ai.providers[ai.tasks.ask || ai.tasks.rate || ai.provider]?.local;
  const box = `<div class="ask-box">
      <label class="field-label" for="aq">${follow ? 'Follow-up' : 'Question'}</label>
      <input type="text" id="aq" class="search-big" value="${esc(a.q)}" maxlength="1000" ${a.busy ? 'disabled' : ''}
        placeholder="${follow ? 'Ask a follow-up, e.g. “and where does he show the code?”' : a.video ? 'Ask about this video' : 'e.g. How do I fine-tune a big model on one GPU?'}">
      <button class="btn btn-dark" data-act="ask-go" ${a.busy || !ready ? 'disabled' : ''}>${a.busy ? '<span class="spin" style="display:inline-block"></span>' : follow ? 'Ask' : 'Ask'}</button>
    </div>`;
  const vid = a.video && byId(a.video);
  return `
  <section>
    <div class="eyebrow">Ask</div>
    <h1 class="display">Ask your library<em>Get answers from your videos</em></h1>
    <p class="lede">Ask a question and follow cited moments back to your videos.</p>
    ${follow ? `<div class="ask-filters"><button class="btn btn-sm" data-act="ask-new">+ New question</button>
        ${askScope(th[0].filters) ? `<span class="note faint">Within: ${esc(askScope(th[0].filters))}</span>` : ''}</div>` : `${box}
    <div class="ask-filters">
      ${vid ? `<span class="chip on" style="max-width:100%"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">Only: ${esc(vid.title)}</span>
          <button class="btn-text" data-act="ask-unscope" aria-label="Ask across the whole library" style="color:inherit;height:auto;padding:0 0 0 4px">✕</button></span>`
        : `<label class="field-label">Category <select data-change="ask-cat"><option value="">All categories</option>
        ${categoryNames().map(c => `<option value="${esc(c)}" ${a.cat === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>
      </label>${libPlaylists().length > 1 ? `<label class="field-label">Playlist <select data-change="ask-src"><option value="">All playlists</option>
        ${libPlaylists().map(p => `<option value="${esc(p.id)}" ${a.src === p.id ? 'selected' : ''}>${esc(p.title)}</option>`).join('')}</select></label>` : ''}
      <label class="check" style="align-items:center"><input type="checkbox" data-change="ask-done" ${a.done ? 'checked' : ''} style="margin:0"><span class="note">Include watched</span> ${tip('Include videos marked watched or skipped in the answer.')}</label>`}
    </div>
    <p class="hint">${ready ? `Answer model: ${esc(ai.uses.ask)}${local ? ' · Runs on this computer' : ''}.` : 'Set up an AI for questions in Settings → AI.'} ${tip("Answers use your videos' transcripts, slides, chapters, summaries and notes. Click a citation number to play that moment.")}</p>`}
  </section>
  <div class="ask">
    <div id="aresult">${askResult()}${follow ? `<div style="margin-top:22px">${box}</div>` : ''}</div>
    <aside class="hist">${askHistory()}</aside>
  </div>`;
}
function askTurn(r, last) {
  const src = Object.fromEntries(r.sources.map(x => [x.n, x]));
  const cite = n => src[n] ? `<button class="cite" data-act="play-at" data-id="${esc(src[n].video_id)}" data-t="${Math.floor(src[n].t || 0)}" title="${esc(askLabel(src[n]))}">${n}</button>` : '';
  const inline = t => esc(t).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  const body = r.answer.split(/\n\s*\n/).map(par => `<p>${par.split(/(\[\d+\])/).map(x => /^\[\d+\]$/.test(x) ? cite(+x.slice(1, -1)) : inline(x).replace(/\n/g, '<br>')).join('')}</p>`).join('');
  const cited = r.sources.filter(x => x.cited);
  const ch = r.checked || {};
  const fixes = [ch.moved && `${plural(ch.moved, 'citation')} moved to a better-matching excerpt`, ch.dropped && `${plural(ch.dropped, 'invalid citation')} removed`].filter(Boolean);
  return `<article class="answer">
    <h2>${esc(r.question)}</h2>
    ${r.standalone ? `<p class="kv-line" style="margin:-6px 0 10px">Searched for: ${esc(r.standalone)}</p>` : ''}
    <div class="body">${body}</div>
    ${cited.length ? `<div class="cited-row">${cited.map(x => `<button class="chip" data-act="play-at" data-id="${esc(x.video_id)}" data-t="${Math.floor(x.t || 0)}" title="${esc(askLabel(x))}"><b style="color:var(--accent)">${x.n}</b><span>${esc(askLabel(x))}</span></button>`).join('')}</div>` : ''}
    <p class="kv-line">${[r.model, MODE_LABEL[r.mode], r.seconds != null ? r.seconds + ' s' : '', ...fixes, r.created_at ? 'asked ' + r.created_at.slice(0, 16) : ''].filter(Boolean).map(esc).join(' · ')}</p>
    ${r.sources.length ? `<details class="srcs" ${last ? 'open' : ''}><summary class="field-label" style="margin:16px 0 6px;cursor:pointer">Sources (${r.sources.length} excerpts sent to the AI)</summary>
    ${r.sources.map(x => `<button class="src ${x.cited ? '' : 'uncited'}" data-act="play-at" data-id="${esc(x.video_id)}" data-t="${Math.floor(x.t || 0)}">
      <span class="n">${x.n}</span><span style="min-width:0"><span class="t">${esc(x.title)}${x.t != null ? `<time>${clock(x.t)}</time>` : ''}</span>
      <span class="faint" style="font-size:12px">${KIND_LABEL[x.kind] || esc(x.kind)}${x.cited ? '' : ' · not cited'}</span>
      <span class="x">${esc(x.text)}</span></span></button>`).join('')}</details>` : ''}
  </article>`;
}
function askResult() {
  const a = S.ask, th = a.thread;
  const busy = a.busy ? `<p class="note" style="margin-top:24px"><span class="spin" style="display:inline-block"></span> ${th.length ? 'Working out the follow-up, reading' : 'Reading'} your videos and writing an answer…</p>` : '';
  if (!th.length) return busy;
  return th.map((r, i) => askTurn(r, i === th.length - 1)).join('<hr class="ask-sep">') + busy + `
    <div class="actions" style="margin-top:14px">
      <button class="btn btn-sm" data-act="ask-copy">Copy</button>
      <a class="btn btn-sm" href="/api/export/ask?id=${th[0].id}" download>Export note</a>
      <button class="btn btn-sm" data-act="ask-del" data-ask="${th[0].id}">Delete${th.length > 1 ? ' conversation' : ''}</button>
    </div>`;
}
function renderAskPop() {
  const a = S.ask, root = $('#ask-pop-root'), fab = $('#ask-fab'), full = route() === 'ask';
  fab.hidden = full;
  if (!a.open || full) { root.innerHTML = ''; return; }
  const vid = a.video && byId(a.video), title = vid?.title;
  const th = a.thread, busy = a.busy;
  const latest = th[th.length - 1];
  const turns = th.map(r => askTurn(r, false)).join('<hr class="ask-sep">');
  root.innerHTML = `<section class="ask-pop" role="dialog" aria-label="Ask your library" aria-modal="true">
    <header class="ask-pop-head"><strong>Ask your library</strong><span class="ask-pop-scope">${vid ? `Only: ${esc(title)}` : 'This library'}</span>
      ${vid ? '<button class="btn" data-act="ask-unscope" aria-label="Remove video scope">✕</button>' : ''}
      <button class="btn" data-act="ask-new">New</button>
      <a class="btn" href="#/ask${latest ? `?id=${latest.id}` : ''}" data-act="ask-full">Open full page</a>
      <button class="btn" data-act="ask-close" aria-label="Close Ask">Close</button></header>
    <div class="ask-pop-body" id="ask-pop-body">${turns || (!busy ? '<p class="ask-pop-empty">Ask a question about your videos.</p>' : '')}${busy ? '<p class="note" style="margin-top:18px"><span class="spin" style="display:inline-block"></span> Reading your videos and writing an answer…</p>' : ''}</div>
    <div class="ask-pop-input"><input id="aq" type="text" value="${esc(a.q)}" maxlength="1000" ${busy ? 'disabled' : ''} aria-label="Question" placeholder="${th.length ? 'Ask a follow-up' : vid ? 'Ask about this video' : 'Ask your library'}">
      <button class="btn btn-dark" data-act="ask-go" ${busy || !S.data.ai.ready_for.ask ? 'disabled' : ''}>${busy ? 'Working…' : 'Ask'}</button></div>
  </section>`;
  if (!busy) $('#aq')?.focus({ preventScroll: true });
}
function askHistory() {
  const a = S.ask, items = a.hist || [], open = a.thread[0]?.thread_id;
  return `<h4>Earlier questions</h4>
    <input type="search" id="ahq" value="${esc(a.hq)}" placeholder="Filter" aria-label="Filter earlier questions">
    ${a.hist == null ? '<p class="note faint">Loading…</p>' : !items.length ? `<p class="note faint">${a.hq ? 'No matches.' : 'Your questions and answers are kept here.'}</p>`
      : items.map(h => `<a href="#/ask?id=${h.id}" class="${open === h.id ? 'on' : ''}">${esc(h.question)}<small>${esc((h.created_at || '').slice(0, 16))}${h.follow_ups ? ` · ${plural(h.follow_ups, 'follow-up')}` : ''}${h.filters?.video_ids?.length === 1 ? ` · ${esc(askScope(h.filters))}` : ''}</small></a>`).join('')}`;
}
function renderAsk() {
  if (route() !== 'ask') { renderAskPop(); return; }
  const y = scrollY; renderView(); scrollTo(0, y);
}
async function loadAskHistory() {
  try { S.ask.hist = (await api(`/api/ask/history?q=${encodeURIComponent(S.ask.hq)}`)).items; } catch (e) { S.ask.hist = []; }
  const side = $('.hist');
  if (route() === 'ask' && side) {
    const focused = document.activeElement?.id === 'ahq', pos = $('#ahq')?.selectionStart;
    side.innerHTML = askHistory();
    if (focused) { const f = $('#ahq'); f.focus(); f.setSelectionRange(pos, pos); }
  }
}
async function openAsk(id) {
  try { S.ask.thread = (await api(`/api/ask/item?id=${id}`)).thread; S.ask.q = ''; } catch (e) { S.ask.thread = []; toast(e.message, true); }
  renderAsk();
}
function askAbout(id) {
  Object.assign(S.ask, { video: id, thread: [], q: '', open: true });
  closeDrawer();
  renderAskPop();
  $('#aq')?.focus({ preventScroll: true });
}
async function runAsk() {
  const a = S.ask, q = ($('#aq')?.value || a.q).trim(), last = a.thread[a.thread.length - 1];
  if (!q || a.busy) return;
  a.q = q; a.busy = true;
  renderAsk();
  $('.ask-sep:last-of-type, #aresult .note')?.scrollIntoView({ block: 'nearest' });
  try {
    const inLib = S.lib ? (libPlaylists().map(p => p.id).length ? libPlaylists().map(p => p.id) : ['_none_']) : [];
    const filters = a.video ? { video_ids: [a.video] }
      : { categories: a.cat ? [a.cat] : [], playlists: a.src ? [a.src] : inLib, include_done: a.done };
    const r = await api('/api/ask', last ? { question: q, follow_up_of: last.id } : { question: q, filters });
    a.thread = [...a.thread, r]; a.q = '';
    if (route() === 'ask') history.replaceState(null, '', `#/ask?id=${r.id}`);
  } catch (e) { toast(e.message, true); }
  a.busy = false;
  renderAsk();
  if (a.thread.length > 1) $$('.answer').pop()?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  loadAskHistory();
}

/* ---------------- knowledge ---------------- */
async function buildKnowledge(category) {
  try {
    const lib = S.lib || 1;
    const res = await api('/api/knowledge/build', { category, library: lib });
    await queued(res, `Knowledge page: ${category}`);
  } catch (e) {
    toast(e.message, true);
  }
}

async function loadKnowledgeList() {
  if (S.knowledge.loadingList) return;
  S.knowledge.loadingList = true;
  try {
    const lib = S.lib || 1;
    const res = await api(`/api/knowledge?library=${lib}`);
    S.knowledge.list = res.categories || [];
  } catch (e) {
    S.knowledge.list = { error: e.message };
  } finally {
    S.knowledge.loadingList = false;
  }
  if ((route() === 'knowledge' && !routeParam('category')) || route() === 'home') refresh();
}

async function loadKnowledgePage(cat) {
  if (S.knowledge.loadingPage === cat) return;
  S.knowledge.loadingPage = cat;
  try {
    const lib = S.lib || 1;
    const res = await api(`/api/knowledge/page?library=${lib}&category=${encodeURIComponent(cat)}`);
    S.knowledge.pages[cat] = res;
  } catch (e) {
    S.knowledge.pages[cat] = { error: e.message };
  } finally {
    S.knowledge.loadingPage = null;
  }
  if (route() === 'knowledge' && routeParam('category') === cat) refresh();
}

function viewKnowledgeList() {
  const lib = S.lib || 1;
  const list = S.knowledge.list;
  const ai = S.data.ai;
  return `
  <section>
    <div class="eyebrow">Knowledge</div>
    <h1 class="display">Knowledge base<em>Topic pages from your summaries</em></h1>
    <p class="lede">Study pages combining notes, minutes, and quotes across your videos, citing every moment.</p>
    ${!S.lib ? `<div class="banner"><span>Knowledge pages belong to a single library. Showing pages for <b>${esc(libName(1) || 'Library 1')}</b>. Pick a specific library above to switch.</span></div>` : ''}
    ${!ai.ready_for.summarize ? `<div class="banner"><span>Set up an AI for summaries in Settings to build knowledge pages.</span><a class="btn btn-sm" href="#/settings/routing">Set up AI</a></div>` : ''}
    <div id="job"></div>
  </section>

  <section class="section">
    ${list === null ? '<p class="note"><span class="spin" style="display:inline-block"></span> Loading topics…</p>'
      : list.error ? `<p class="note warn-text">Could not load topics: ${esc(list.error)}</p>`
      : !list.length ? '<div class="empty"><p>No topics are available for study yet.</p><a class="btn btn-dark" href="#/categories">Review categories</a></div>'
      : `<div class="tbl-wrap"><table class="tbl">
          <thead>
            <tr>
              <th>Topic</th>
              <th>Summaries</th>
              <th>Status</th>
              <th class="r">Actions</th>
            </tr>
          </thead>
          <tbody>
            ${list.map(c => {
              const hasPage = c.page && c.page !== 'none';
              const isStale = c.page === 'stale';
              const isReady = c.page === 'ready';
              const sumText = `${c.summarised} of ${c.videos} summarised`;
              const statusBadge = isReady
                ? `<span class="tag" style="color:var(--watch);border-color:color-mix(in srgb, var(--watch) 40%, transparent)">Ready</span>`
                : isStale
                ? `<span class="tag" style="color:var(--accent);border-color:color-mix(in srgb, var(--accent) 40%, transparent)">Stale</span>`
                : `<span class="faint" style="font-size:12.5px">No page</span>`;
              const builtDate = c.built_at ? `<div class="faint" style="font-size:11.5px;margin-top:2px">${isStale ? 'Built ' : ''}${esc(fmtDate(c.built_at))}</div>` : '';
              return `<tr>
                <td>
                  ${hasPage
                    ? `<a href="#/knowledge?category=${encodeURIComponent(c.category)}" style="font-weight:600">${esc(c.category)}</a>`
                    : `<span style="font-weight:600">${esc(c.category)}</span>`}
                </td>
                <td style="font-size:13.5px">${esc(sumText)}</td>
                <td>${statusBadge}${builtDate}</td>
                <td class="r">
                  <div style="display:inline-flex;gap:6px;align-items:center;justify-content:flex-end">
                    <button class="btn btn-sm" data-act="knowledge-summarize-five" data-cat="${esc(c.category)}" ${!ai.ready_for.summarize || c.summarised >= c.videos ? 'disabled' : ''}>Summarise 5</button>
                    ${hasPage ? `<a class="btn btn-sm" href="#/knowledge?category=${encodeURIComponent(c.category)}">Open</a>` : ''}
                    ${isStale ? `<button class="btn btn-sm btn-dark" data-act="knowledge-build" data-cat="${esc(c.category)}" ${!c.summarised || !ai.ready_for.summarize ? 'disabled' : ''} title="${!c.summarised ? 'Summarise at least one video in this category first' : !ai.ready_for.summarize ? 'Set up an AI for summaries first' : 'Update knowledge page'}">Update</button>`
                      : isReady ? `<button class="btn btn-sm btn-text" data-act="knowledge-build" data-cat="${esc(c.category)}" ${!c.summarised || !ai.ready_for.summarize ? 'disabled' : ''} title="${!c.summarised ? 'Summarise at least one video in this category first' : !ai.ready_for.summarize ? 'Set up an AI for summaries first' : 'Rebuild knowledge page'}">Rebuild</button>`
                      : c.summarised >= 3 ? `<button class="btn btn-sm btn-dark" data-act="knowledge-build" data-cat="${esc(c.category)}" ${!ai.ready_for.summarize ? 'disabled' : ''} title="Build knowledge page">Build</button>` : ''}
                  </div>
                  ${!c.summarised && !hasPage ? `<div class="faint" style="font-size:11.5px;margin-top:4px">Summarise videos first</div>` : ''}
                </td>
              </tr>`;
            }).join('')}
          </tbody>
        </table></div>`}
  </section>
  ${Array.isArray(list) && list.length ? (list.some(c => c.page && c.page !== 'none')
    ? `<details class="section"><summary class="h" style="cursor:pointer">How this works</summary><ol class="note"><li>Pick a category you're studying.</li><li>Summarise at least 3 of its videos.</li><li>Build its page.</li></ol></details>`
    : `<section class="section card"><h2 class="h">Start here</h2><ol class="note"><li>Pick a category you're studying.</li><li>Summarise at least 3 of its videos.</li><li>Build its page.</li></ol></section>`)
    : ''}`;
}

async function summarizeKnowledgeFive(category) {
  try {
    const candidates = videos().filter(v => v.category === category && !isDone(v)).sort((a, b) => (a.status === 'unwatched' ? 0 : 1) - (b.status === 'unwatched' ? 0 : 1));
    const done = new Set(S.data.summarised || []);
    const selected = candidates.filter(v => !done.has(v.video_id)).slice(0, 5).map(v => v.video_id);
    if (!selected.length) { toast('No unsummarised unwatched videos in this category'); return; }
    await startSummary(selected);
  } catch (e) { toast(e.message, true); }
}

function viewKnowledgePage(cat) {
  const lib = S.lib || 1;
  const data = S.knowledge.pages[cat];
  const ai = S.data.ai;
  const exportUrl = `/api/export/knowledge?library=${encodeURIComponent(lib)}&category=${encodeURIComponent(cat)}`;

  if (!data) {
    return `
    <section>
      <div style="margin-bottom:14px"><a href="#/knowledge" class="btn-text" style="padding-left:0">← Back</a></div>
      <div class="eyebrow">Knowledge page</div>
      <h1 class="display">${esc(cat)}</h1>
      <p class="note" style="margin-top:20px"><span class="spin" style="display:inline-block"></span> Loading page…</p>
      <div id="job"></div>
    </section>`;
  }

  if (data.error) {
    return `
    <section>
      <div style="margin-bottom:14px"><a href="#/knowledge" class="btn-text" style="padding-left:0">← Back</a></div>
      <div class="eyebrow">Knowledge page</div>
      <h1 class="display">${esc(cat)}</h1>
      <p class="note warn-text" style="margin-top:20px">Could not load knowledge page: ${esc(data.error)}</p>
      <div id="job"></div>
    </section>`;
  }

  if (!data.page) {
    return `
    <section>
      <div style="margin-bottom:14px"><a href="#/knowledge" class="btn-text" style="padding-left:0">← Back</a></div>
      <div class="eyebrow">Knowledge page</div>
      <h1 class="display">${esc(cat)}</h1>
      <p class="lede">Build a study page from this topic’s video summaries.</p>
      <div class="actions" style="margin-top:16px">
        <button class="btn btn-dark btn-sm" data-act="knowledge-build" data-cat="${esc(cat)}" ${!ai.ready_for.summarize ? 'disabled' : ''}>Build page</button>
      </div>
      <div id="job"></div>
    </section>`;
  }

  const p = data.page;
  const sources = data.sources || [];
  const srcMap = Object.fromEntries(sources.map(s => [s.n, s]));
  const catItem = (S.knowledge.list || []).find(c => c.category === cat);
  const isStale = catItem?.page === 'stale';

  const renderCite = n => {
    const s = srcMap[n];
    if (!s) return `<span class="cite" style="cursor:default" title="Source ${n}">${n}</span>`;
    const at = s.t != null ? ` @ ${clock(s.t)}` : '';
    const title = `${s.title}${at}`;
    return `<button class="cite" data-act="play-at" data-id="${esc(s.video_id)}" data-t="${Math.floor(s.t || 0)}" title="${esc(title)}">${s.n}</button>`;
  };

  return `
  <section>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:8px">
      <a href="#/knowledge" class="btn-text" style="padding-left:0">← Back</a>
      <div style="display:flex;gap:8px;align-items:center">
        <a class="btn btn-sm" href="${esc(exportUrl)}" download>Export note</a>
        <button class="btn btn-sm ${isStale ? 'btn-dark' : ''}" data-act="knowledge-build" data-cat="${esc(cat)}" ${!ai.ready_for.summarize ? 'disabled' : ''} title="${isStale ? 'Update page with newest summaries' : 'Rebuild page'}">${isStale ? 'Update' : 'Rebuild'}</button>
      </div>
    </div>
    <div class="eyebrow">Knowledge page${!S.lib ? ` · ${esc(libName(1) || 'Library 1')}` : ''}</div>
    <h1 class="display">${esc(cat)}</h1>
    <p class="kv-line" style="margin-top:6px">
      ${[p.created_at ? 'Built ' + fmtDate(p.created_at) : '', data.model ? 'Model: ' + data.model : '', isStale ? 'Update available (summaries changed)' : ''].filter(Boolean).map(esc).join(' · ')}
    </p>
    ${p.shortened ? `<p class="note faint" style="margin-top:8px">Older videos were shortened to fit the model.</p>` : ''}
    <div id="job"></div>
  </section>

  <article class="knowledge-page">
    ${p.overview ? `<div style="margin-bottom:28px;color:var(--ink)">${esc(p.overview)}</div>` : ''}

    ${(p.sections || []).map(sec => `
      <section style="margin-top:28px">
        <h2 class="h" style="font-size:18px;margin-bottom:12px;padding-top:12px;border-top:1px solid var(--line)">${esc(sec.heading)}</h2>
        <ul style="list-style:disc;padding-left:22px;margin:0 0 16px;line-height:1.65">
          ${(sec.points || []).map(pt => `
            <li style="margin-bottom:8px">
              <span>${esc(pt.text)}</span> ${(pt.cites || []).map(renderCite).join(' ')}
            </li>
          `).join('')}
        </ul>
      </section>
    `).join('')}

    ${p.open_questions?.length ? `
      <section style="margin-top:32px">
        <h3 class="h" style="font-size:17px;margin-bottom:8px;padding-top:16px;border-top:1px solid var(--line)">Open questions</h3>
        <p class="note faint" style="margin-bottom:10px">Things the videos disagree on or leave unanswered:</p>
        <ul style="list-style:disc;padding-left:22px;margin:0 0 16px;font-size:14.5px;line-height:1.6">
          ${p.open_questions.map(q => `<li style="margin-bottom:6px">${esc(q)}</li>`).join('')}
        </ul>
      </section>
    ` : ''}

    ${sources.length ? `
      <details class="srcs" style="margin-top:32px">
        <summary class="field-label" style="cursor:pointer;font-weight:600">Sources (${sources.length} excerpts from ${new Set(sources.map(s => s.video_id)).size} videos)</summary>
        ${sources.map(s => `
          <button class="src" data-act="play-at" data-id="${esc(s.video_id)}" data-t="${Math.floor(s.t || 0)}">
            <span class="n">${s.n}</span>
            <span style="min-width:0">
              <span class="t">${esc(s.title)}${s.t != null ? `<time>${clock(s.t)}</time>` : ''}</span>
              <span class="x">${esc(s.text)}</span>
            </span>
          </button>
        `).join('')}
      </details>
    ` : ''}
  </article>`;
}

/* ---------------- setup guide ---------------- */
async function loadGuide() {
  try {
    const r = await fetch('/docs/setup');
    S.guide = r.ok ? await r.text() : `Could not load the guide (${r.status}).`;
  } catch (e) { S.guide = 'Could not load the guide: ' + e.message; }
  if (route() === 'help') refresh();
}
function viewSetup() {
  const win = S.data.platform === 'win32';
  return `
  <section>
    <p class="lede">Setup scripts and guide ${tip('Download a setup script, place it in the YourTube folder (the one with dashboard.py), and run it. The local AI option also installs Ollama and a model that fits the computer. The script asks before installing anything system-wide.')}</p>
    <div class="dl-grid">
      <a class="dl" href="/download/setup.sh" download><b>⬇ setup.sh</b><span>Linux and macOS · <code>bash setup.sh --local-ai</code></span></a>
      <a class="dl" href="/download/setup.ps1" download><b>⬇ setup.ps1</b><span>Windows PowerShell · <code>-LocalAI</code> · not yet tested</span></a>
      <a class="dl" href="/download/SETUP.md" download><b>⬇ SETUP.md</b><span>This full guide as a Markdown file</span></a>
    </div>
    ${win ? '<p class="hint">You are on Windows: use setup.ps1.</p>' : ''}
  </section>
  <article class="guide">${S.guide ? md(S.guide) : '<p class="note">Loading the guide…</p>'}</article>`;
}
function viewLogs() {
  const shown = S.logs ? `<p class="note" style="margin-top:10px">Current log file: <code>${esc(S.logs.path)}</code></p><pre class="log">${esc((S.logs.lines || []).join('\n') || 'No recent problems found.')}</pre>` : '';
  return `<section class="section card"><h2 class="h">Logs</h2><p class="note">Log file: <code>logs/yourtube.log</code>. Recent warnings and errors can help diagnose problems.</p>
    <div class="actions"><button class="btn btn-sm" data-act="logs-show">Show recent problems</button><a class="btn btn-sm" href="/download/yourtube.log" download>Download log</a></div>${shown}</section>`;
}
// Small Markdown renderer for docs/SETUP.md: headings, code blocks, tables, lists, rules, paragraphs.
// Text is escaped before any markup is added, so the guide can't inject HTML.
function md(src) {
  const inline = t => {
    // Code spans become placeholders first, so bold or links around them still match
    const codes = [];
    let h = esc(t).replace(/`([^`]+)`/g, (m, c) => `\u0000${codes.push(c) - 1}\u0000`);
    h = h.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
      .replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s).,;:]|$)/g, '$1<i>$2</i>')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+|#[^)\s]*)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]*[^\s<).,:;])/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
    return h.replace(/\u0000(\d+)\u0000/g, (m, n) => `<code>${codes[n]}</code>`);
  };
  const lines = src.replace(/\r/g, '').split('\n'), out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      const code = [];
      for (i++; i < lines.length && !/^```/.test(lines[i]); i++) code.push(lines[i]);
      i++;
      out.push(`<div class="codeblock"><pre>${esc(code.join('\n'))}</pre><button class="btn btn-sm copy" data-act="copy-code">Copy</button></div>`);
      continue;
    }
    if (/^#{1,3} /.test(line)) { const n = line.match(/^#+/)[0].length; out.push(`<h${n}>${inline(line.slice(n + 1))}</h${n}>`); i++; continue; }
    if (/^---+\s*$/.test(line)) { out.push('<hr>'); i++; continue; }
    if (/^\|/.test(line)) {
      const rows = [];
      for (; i < lines.length && /^\|/.test(lines[i]); i++) rows.push(lines[i]);
      const cells = r => r.replace(/^\||\|\s*$/g, '').split('|').map(c => c.trim());
      const body = rows.filter((r, k) => k !== 1 || !/^\|[\s:|-]+\|?\s*$/.test(r));
      out.push(`<div class="tbl-wrap"><table class="tbl"><thead><tr>${cells(body[0]).map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead>
        <tbody>${body.slice(1).map(r => `<tr>${cells(r).map(c => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
      continue;
    }
    if (/^\s*([-*]|\d+\.) /.test(line)) {
      const ordered = /^\s*\d+\./.test(line), items = [];
      for (; i < lines.length && /^\s*([-*]|\d+\.) /.test(lines[i]); i++) items.push(lines[i].replace(/^\s*([-*]|\d+\.) /, ''));
      out.push(`<${ordered ? 'ol' : 'ul'}>${items.map(t => `<li>${inline(t)}</li>`).join('')}</${ordered ? 'ol' : 'ul'}>`);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para = [];
    for (; i < lines.length && lines[i].trim() && !/^(```|#{1,3} |\||---|\s*([-*]|\d+\.) )/.test(lines[i]); i++) para.push(lines[i]);
    out.push(`<p>${inline(para.join(' '))}</p>`);
  }
  return out.join('\n');
}

/* ---------------- AI settings ---------------- */
function viewAI() {
  const ai = S.data.ai;
  const sameAs = name => {
    const f = ai.task_follows?.[name], p = f ? ai.tasks[f] || ai.provider : ai.provider;
    return `Same as ${f ? esc(ai.task_labels[f].split(' ')[0].toLowerCase()) : 'default'}${p !== 'none' && ai.providers[p] ? ` (${esc(ai.providers[p].label)})` : ''}`;
  };
  const pick = (name, value, allowSame) => `<select data-ai-route="${name}" style="width:100%">
      ${allowSame ? `<option value="" ${!value ? 'selected' : ''}>${sameAs(name)}</option>`
        : `<option value="none" ${value === 'none' ? 'selected' : ''}>Off (keywords only)</option>`}
      ${Object.entries(ai.providers).map(([k, p]) => `<option value="${k}" ${value === k ? 'selected' : ''} ${p.ready ? '' : 'disabled'}>${esc(p.label)}${p.ready ? ` · ${esc(p.model)}` : p.blocked ? ' (blocked: local only)' : ' (not set up)'}</option>`).join('')}
    </select>`;
  const cloud = providerCards(Object.entries(ai.providers).filter(([, p]) => !p.local));
  const media = ai.media_tasks || {};
  const pickMedia = (t, meta) => `<select id="media-${esc(t)}" data-media-task="${esc(t)}" aria-label="${esc(meta.label)} runs on" style="width:100%">
      ${meta.providers.map(k => { const p = ai.providers[k], ready = k === 'local' || p?.ready;
        return `<option value="${esc(k)}" ${(meta.selected || 'local') === k ? 'selected' : ''} ${ready ? '' : 'disabled'}>${esc(k === 'local' ? 'This computer' : p?.label || k)}${ready ? '' : ' (add a key in Cloud AI)'}</option>`; }).join('')}
    </select>`;
  const helperCell = t => t in (ai.helpers || {})
    ? `<select data-helper-task="${esc(t)}" aria-label="Helper for ${esc(media[t]?.label || ai.task_labels[t])}" style="width:100%">${helperOptions(t, mainOf(t, ai), ai.helpers[t])}</select>`
    : '<span class="faint" style="font-size:12.5px">—</span>';
  const row = (label, main, t) => `<div class="route route3"><span class="field-label" style="margin:0">${esc(label)}</span>${main}${t ? helperCell(t) : '<span></span>'}</div>`;
  const routing = `
  <section class="section card">
    <h2 class="h">Which AI does what</h2>
    <p class="hint">Each task runs on its AI. A <b>helper</b> works at the same time on big batches so they finish sooner: a cloud AI next to a local one, or this computer next to a cloud AI. ${tip('A smaller or local model often works well for categories. Ratings read whole transcripts, so a larger context window can help. Questions use the ratings choice unless you choose another model. Cloud runs show what is sent and the most they can cost before they start.')}</p>
    <div class="route route3 route-head"><span></span><span class="field-label" style="margin:0">Runs on</span><span class="field-label" style="margin:0">Helper</span></div>
    ${row('Default', pick('provider', ai.provider, false))}
    ${Object.entries(ai.task_labels).map(([t, label]) => row(label, pick(t, ai.tasks[t] || '', true), t)).join('')}
    ${Object.entries(media).map(([t, meta]) => row(meta.label, pickMedia(t, meta), t)).join('')}
    <p class="hint" id="media-sends">${esc(mediaSends(ai, Object.fromEntries(Object.entries(media).map(([t, m]) => [t, m.selected || 'local']))))}</p>
    <div class="actions"><button class="btn btn-dark btn-sm" data-act="ai-save-routes">Save</button></div>
  </section>`;
  return { cloud, routing };
}
function providerCards(providers) {
  const edit = S.ai.provider;
  return `<section class="section card"><h2 class="h">${providers.some(([, p]) => p.local) ? 'Local AI providers' : 'Cloud AI providers'}</h2>
    ${providers.map(([k, p]) => `<div class="prov"><div><span class="dot ${p.ready ? 'ok' : ''}"></span><b style="font-weight:500">${esc(p.label)}</b>
      <span class="faint" style="font-size:12.5px">${p.ready ? ' · ' + esc(p.model) : p.local ? ' · runs on this computer, free' : ' · needs an API key'}</span></div>
      <button class="btn btn-sm" data-act="ai-edit" data-p="${k}">${edit === k ? 'Close' : p.ready ? 'Edit' : 'Set up'}</button></div>${edit === k ? aiEditor(k, p) : ''}`).join('')}</section>`;
}
function viewGeneral() {
  const ai = S.data.ai;
  return `
  <section class="section card">
    <h2 class="h">Display</h2>
    <div class="route" style="margin-top:8px"><label class="field-label" style="margin:0">Playback speed</label>
      <select data-change="speed" style="width:100%">${SPEEDS.map(x => `<option value="${x}" ${x === S.speed ? 'selected' : ''}>${sx(x)}${x === 1 ? ' (normal)' : ''}</option>`).join('')}</select></div>
    <div class="route"><label class="field-label" style="margin:0">Theme</label>
      <select data-change="theme" style="width:100%">${[['auto', 'Match this computer'], ['light', 'Light'], ['dark', 'Dark']].map(([k, l]) => `<option value="${k}" ${S.theme === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
    <label class="check" style="margin-top:12px"><input type="checkbox" data-change="show-descriptions" ${ai.show_descriptions ? 'checked' : ''}>
      <span>Show descriptions in the video panel</span></label>
    <p class="hint">Descriptions are collapsed by default in each video panel.</p>
    <p class="hint">Display options ${tip('Watch times use this speed by default; the Plan page can change it too. Display settings are saved in this browser.')}</p>
  </section>
  <section class="section card">
    <h2 class="h">API reference</h2>
    <p class="note">Every endpoint the dashboard uses, with a Try it out button. It works offline and needs this same access.</p>
    <div class="actions"><a class="btn btn-sm" href="/docs/api" target="_blank" rel="noopener">Open the API reference ↗</a></div>
  </section>`;
}
function viewScoring() {
  const ai = S.data.ai, formula = ratingFormula(ai.rating_formula || {});
  return `<section class="section card">
    <h2 class="h">How scores are calculated</h2>
    <p class="hint">Score formula ${tip('Quality combines depth, time value and freshness. Fit adjusts the result with an interest penalty or bonus.')}</p>
    <div class="ai-grid">
      ${[['depth','Depth weight'],['value','Time value weight'],['freshness','Freshness weight']].map(([key,label]) => `<div><label class="field-label" for="formula-${key}">${label} (%)</label><input id="formula-${key}" type="number" min="0" max="100" step="1" data-formula="${key}" value="${Math.round(formula[key]*100)}"></div>`).join('')}
      <div><label class="field-label" for="formula-interest_penalty">Interest penalty</label><input id="formula-interest_penalty" type="number" min="0" max="5" step="0.1" data-formula="interest_penalty" value="${formula.interest_penalty}"></div>
      <div><label class="field-label" for="formula-interest_bonus">Interest bonus</label><input id="formula-interest_bonus" type="number" min="0" max="2" step="0.1" data-formula="interest_bonus" value="${formula.interest_bonus}"></div>
    </div>
    <div class="kv-line" id="formula-example">Example: ${formulaExample(formula)}</div>
    <div class="actions"><button class="btn btn-dark btn-sm" data-act="formula-save">Save</button><button class="btn btn-sm" data-act="formula-reset">Reset to default</button></div>
  </section>`;
}
function viewAbout() {
  const ai = S.data.ai;
  const langs = ai.languages || [];
  const curLang = ai.language || 'English';
  const customLang = (curLang && !langs.includes(curLang)) ? curLang : null;
  const langOptions = [
    ...(customLang ? [`<option value="${esc(customLang)}" selected>${esc(customLang)} (custom)</option>`] : []),
    ...langs.map(l => `<option value="${esc(l)}" ${curLang === l ? 'selected' : ''}>${esc(l)}</option>`)
  ].join('');
  const interests = ai.interests || '';
  const currentInterests = S.about.current ?? interests;
  const voiceAvailable = !!S.data.voice;
  const voiceHint = "Voice input needs local transcription. Run: bash scripts/setup.sh --local-ai";
  return `<section class="section card">
    <h2 class="h">About you${S.data.libraries.length > 1 ? ` · ${esc(S.lib ? libName(S.lib) : libName(1))}` : ''}</h2>
    ${S.data.libraries.length > 1 ? `<p class="note">Library-specific interests ${tip(`Ratings judge each video against its library's goals.${S.lib ? '' : ` “All libraries” is selected: this edits ${libName(1)}.`}`)}</p>` : ''}
    <div class="ai-grid">
      <div class="full" style="padding:14px;border:1px solid var(--line);border-radius:8px;background:var(--sunken)">
        <label class="field-label" for="ai-look">What are you looking for?</label>
        <textarea id="ai-look" rows="3" placeholder="Say or type it in your own words: what you want to learn, your level, what to skip, and why">${esc(S.about.look)}</textarea>
        <div class="actions" style="margin-top:8px">
          <button type="button" class="btn btn-sm" data-act="voice-rec" aria-label="Record your answer" ${voiceAvailable ? '' : `disabled title="${esc(voiceHint)}"`}>${S.about.recording ? `<span class="rec-dot" aria-hidden="true"></span>■ Stop ${clock(S.about.seconds)}` : '🎙 Record'}</button>
          ${!voiceAvailable ? `<span class="hint" title="${esc(voiceHint)}">Voice unavailable · Run <code>bash scripts/setup.sh --local-ai</code></span>` : ''}
          <button type="button" class="btn btn-dark btn-sm" data-act="interests-draft" ${!S.about.look.trim() || S.about.recording ? 'disabled' : ''}>${S.about.drafting ? '<span class="spin" style="display:inline-block"></span> Writing…' : 'Write my interests with AI'}</button>
        </div>
        <div class="hint warn-text" role="status" aria-live="polite" style="margin-top:6px">${esc(S.about.error)}</div>
        ${S.about.suggestion ? `<div style="margin-top:12px;padding:12px;background:var(--raised);border:1px solid var(--line);border-radius:8px"><div class="field-label">Suggested interests</div><pre style="white-space:pre-wrap;font:inherit;margin:6px 0 10px">${esc(S.about.suggestion)}</pre><div class="actions"><button type="button" class="btn btn-dark btn-sm" data-act="interests-use">Use this</button><button type="button" class="btn btn-sm" data-act="interests-edit">Edit</button><button type="button" class="btn-text" data-act="interests-discard">Discard</button></div></div>` : ''}
      </div>
      <div class="full">
        <div style="display:flex;justify-content:space-between;align-items:baseline">
          <label class="field-label" for="ai-interests">Learning interests ${tip('Ratings favour videos that fit these interests.')}</label>
          <span id="ai-interests-count" class="hint" style="font-size:12px">${currentInterests.length} / 2000</span>
        </div>
        <textarea id="ai-interests" rows="3" placeholder="e.g. Building AI agents and RAG in Python, system design for interviews. Not interested in crypto or AI news hype.">${esc(currentInterests)}</textarea>
        <div id="ai-interests-err" class="hint warn-text" style="margin-top:4px;display:none"></div>
      </div>
      <div><label class="field-label" for="ai-lang">Response language</label>
        <select id="ai-lang">${langOptions}</select></div>
      <div></div>
    </div>
    <div class="actions"><button class="btn btn-dark btn-sm" data-act="ai-save-prefs">Save</button></div>
  </section>`;
}
const SETTINGS_PAGES = [['general', 'General'], ['about', 'About you'], ['routing', 'AI by task'], ['cloud', 'Cloud AI'], ['local', 'Local AI'], ['sync', 'Sync and details'], ['storage', 'Storage'], ['scoring', 'Scoring']];
function settingsNav(cur) {
  const descriptions = {
    general: 'Choose how the dashboard looks and how watch times are shown.',
    about: 'Set the learning interests used to rate videos and shape suggestions.',
    routing: 'Choose which AI handles each task and whether it has a helper.',
    cloud: 'Set up cloud AI providers for tasks that use online models.',
    local: 'Check local models and tools available on this computer.',
    sync: 'Choose what YourTube fetches when playlists are synced.',
    storage: 'See what YourTube keeps on this computer, and manage backups, caches and exports.',
    scoring: 'Adjust how video ratings weigh depth, value, freshness and fit.'
  };
  return `<div class="eyebrow">Settings</div><h1 class="display">Settings<em>${SETTINGS_PAGES.find(([k]) => k === cur)?.[1] || 'General'}</em></h1>
    <p class="lede">${descriptions[cur] || descriptions.general}</p>
    <div class="settings-layout"><nav class="settings-side" aria-label="Settings sections">${SETTINGS_PAGES.map(([k, label]) => `<a class="${cur === k ? 'on' : ''}" href="#/settings/${k}"${cur === k ? ' aria-current="page"' : ''}>${label}</a>`).join('')}</nav><div class="settings-content">`;
}
function settingsContent(section) {
  if (section === 'general') return viewGeneral() + '</div></div>';
  if (section === 'about') return viewAbout() + '</div></div>';
  if (section === 'scoring') return viewScoring() + '</div></div>';
  if (section === 'sync') return viewSyncSettings() + '</div></div>';
  if (section === 'storage') return viewStorage() + '</div></div>';
  const ai = viewAI();
  return (section === 'cloud' ? ai.cloud : section === 'local' ? viewLocal() + providerCards(Object.entries(S.data.ai.providers).filter(([, p]) => p.local)) : ai.routing) + '</div></div>';
}
function storageBytes(bytes) {
  bytes = Number(bytes) || 0;
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024, i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[i]}`;
}
function storageDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value || 'Unknown date') : date.toLocaleString();
}
async function loadStorage() {
  try { S.storage = await api('/api/files'); }
  catch (e) { S.storage = { error: e.message }; }
  if (route() === 'settings/storage') refresh();
}
function viewStorage() {
  const data = S.storage;
  if (!data) return '<section class="section card"><p class="note">Loading storage details…</p></section>';
  if (data.error) return `<section class="section card"><p class="warn-text">${esc(data.error)}</p><button class="btn btn-sm" data-act="storage-refresh">Try again</button></section>`;
  const areas = (data.areas || []).map(a => `<tr><td>${esc(a.label)}</td><td class="r">${storageBytes(a.bytes)}</td><td class="r">${Number(a.files) || 0}</td><td class="r">${a.clearable ? `<button class="btn btn-sm" data-act="storage-clear" data-key="${esc(a.key)}" data-label="${esc(a.label)}">Clear</button>` : '—'}</td></tr>`).join('');
  const backups = (data.backups || []).map(b => `<tr><td>${esc(storageDate(b.created))}</td><td class="r">${storageBytes(b.bytes)}</td><td class="r">${Number(b.videos) || 0}</td><td class="r" style="white-space:nowrap"><button class="btn btn-sm" data-act="storage-restore" data-name="${esc(b.name)}" data-date="${esc(storageDate(b.created))}">Restore</button> <button class="btn btn-sm" data-act="storage-delete" data-area="backups" data-name="${esc(b.name)}">Delete</button></td></tr>`).join('');
  const exports = (data.exports || []).map(x => `<tr><td>${esc(x.name)}</td><td>${esc(storageDate(x.created))}</td><td class="r">${storageBytes(x.bytes)}</td><td class="r" style="white-space:nowrap"><a class="btn btn-sm" href="/files/exports/${encodeURIComponent(x.name)}" download>Download</a> <button class="btn btn-sm" data-act="storage-delete" data-area="exports" data-name="${esc(x.name)}">Delete</button></td></tr>`).join('');
  return `<section class="section card storage-card"><h2 class="h">On this computer</h2><p class="note">Data folder: <code>${esc(data.data_dir || '')}</code></p>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Area</th><th class="r">Size</th><th class="r">Files</th><th class="r">Action</th></tr></thead><tbody>${areas || '<tr><td colspan="4">No storage areas found.</td></tr>'}</tbody></table></div></section>
    <section class="section card storage-card"><h2 class="h">Backups</h2><p class="note">A backup includes your library and taxonomy, but not API keys.</p><div class="actions"><button class="btn btn-dark btn-sm" data-act="storage-backup">Back up now</button></div>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Date</th><th class="r">Size</th><th class="r">Videos</th><th class="r">Actions</th></tr></thead><tbody>${backups || '<tr><td colspan="4">No backups yet.</td></tr>'}</tbody></table></div></section>
    <section class="section card storage-card"><h2 class="h">Exports</h2><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Name</th><th>Date</th><th class="r">Size</th><th class="r">Actions</th></tr></thead><tbody>${exports || '<tr><td colspan="4">No exports yet.</td></tr>'}</tbody></table></div></section>`;
}
function viewSyncSettings() {
  const ai = S.data.ai;
  return `<section class="section card"><h2 class="h">Sync and details</h2>
    <label class="check" style="margin-top:12px"><input type="checkbox" data-change="details-after-sync" ${S.data.details_after_sync ? 'checked' : ''}>
      <span>Fetch details for new videos after each sync ${tip('Fetch descriptions, chapters and captions for up to 50 new videos per sync.')}</span></label>
    <label class="check" style="margin-top:10px"><input type="checkbox" data-change="use-descriptions" ${S.data.use_descriptions ? 'checked' : ''}>
      <span>Use descriptions in search and AI ${tip('Descriptions are included with captions. Turn this off if they are mostly links and sponsor text.')}</span></label>
    <label class="check" style="margin-top:10px"><input type="checkbox" id="ai-auto" ${ai.auto_after_sync ? 'checked' : ''}>
      <span>Suggest categories for new videos after each sync ${tip('After each playlist sync, suggest categories for new unsorted videos.')}</span></label>
    <div class="actions"><button class="btn btn-dark btn-sm" data-act="ai-save-prefs">Save</button></div>
  </section>`;
}
async function loadLocal() {
  try { S.local = await api('/api/local/status'); } catch (e) { S.local = { error: e.message }; }
  if (route().startsWith('settings/')) refresh();
}
function viewLocal() {
  const L = S.local, ai = S.data.ai, running = !!S.data.job.running;
  if (!L) return `<section class="section card"><h2 class="h">Local AI on this computer</h2><p class="note"><span class="spin" style="display:inline-block"></span> Checking this computer…</p></section>`;
  if (L.error) return `<section class="section card"><h2 class="h">Local AI on this computer</h2><p class="note warn-text">${esc(L.error)}</p></section>`;
  const row = (ok, label, detail) => `<div class="prov"><div><span class="dot ${ok ? 'ok' : ''}"></span><b style="font-weight:500">${label}</b>
    <span class="faint" style="font-size:12.5px"> · ${esc(detail)}</span></div><span></span></div>`;
  const have = new Set((L.ollama.models || []).map(m => m.name));
  const current = ai.providers.ollama.model;
  const useBtn = name => current === name && ai.provider === 'ollama'
    ? '<span class="vb vb-watch">In use</span>'
    : `<button class="btn btn-sm" data-act="local-use" data-model="${esc(name)}">Use</button>`;
  return `
  <section class="section card">
    <h2 class="h">Local AI on this computer</h2>
    <p class="note">Local AI ${tip('Run models on your own machine with Ollama: free, private and works offline. Run bash scripts/setup.sh --local-ai (Windows: scripts/setup.ps1 -LocalAI) to set up Ollama and a suitable model.')} <a href="#/help">Setup guide and downloads →</a></p>
    <div style="margin-top:8px">
      ${row(L.gpu.ok, 'GPU', L.gpu.detail)}
      ${row(L.memory.ok, 'Memory', L.memory.detail)}
      ${row(L.ollama.ok, 'Ollama', L.ollama.detail)}
      ${row(L.ffmpeg.ok, 'ffmpeg', L.ffmpeg.detail)}
      ${row(L.whisper.ok, 'Speech-to-text', L.whisper.detail)}
    </div>
    ${!L.ollama.installed ? `<div class="banner"><span>Install Ollama: <code>curl -fsSL https://ollama.com/install.sh | sh</code> (macOS and Windows: ollama.com/download), then click Check again.</span></div>`
      : !L.ollama.running ? `<div class="banner"><span>Ollama is installed but not running. Start it with <code>ollama serve</code>.</span></div>` : ''}
    ${L.ollama.running ? `
      <h3 style="font-size:14px;margin:22px 0 4px">Models</h3>
      ${(L.ollama.models || []).map(m => `<div class="prov"><div><b style="font-weight:500">${esc(m.name)}</b>
        <span class="faint" style="font-size:12.5px"> · ${m.size_gb} GB${m.params ? ' · ' + esc(m.params) : ''}${m.quant ? ' · ' + esc(m.quant) : ''}</span></div>${useBtn(m.name)}</div>`).join('')}
      ${L.recommended.filter(r => !have.has(r.model)).map(r => `<div class="prov"><div>${r.best ? '<span class="vb vb-watch" style="margin-right:6px">Best fit</span>' : ''}<b style="font-weight:500">${esc(r.model)}</b>
        <span class="faint" style="font-size:12.5px"> · ${esc(r.size)} · ${esc(r.why)}${r.fits_gpu ? '' : ' Too big for this GPU: runs partly on the CPU.'}</span></div>
        <button class="btn btn-sm ${r.best ? 'btn-dark' : ''}" data-act="local-pull" data-model="${esc(r.model)}" data-job-btn>Download</button></div>`).join('')}
      <div class="actions"><label class="field-label" for="local-model">Model name</label><input type="text" id="local-model" placeholder="e.g. gemma4:e2b" style="width:260px">
        <button class="btn btn-sm" data-act="local-pull" data-job-btn>Download</button></div>` : ''}
    <div class="route" style="margin-top:14px"><label class="field-label" style="margin:0">Speech model</label>
      <select data-change="asr-model" style="width:100%">${[['auto', 'Auto: large-v3-turbo on a GPU, small on the CPU'], ['large-v3-turbo', 'large-v3-turbo (best; ~1.6 GB)'],
        ['small', 'small (faster on CPU; ~0.5 GB)'], ['medium', 'medium (~1.5 GB)']].map(([k, l]) => `<option value="${k}" ${ai.asr_model === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
    ${L.ollama.running ? `<div class="route"><label class="field-label" style="margin:0">Slide model</label>
      <select data-change="vision-model" style="width:100%"><option value="" ${!ai.vision_model ? 'selected' : ''}>Same as the text model${ai.providers.ollama.model ? ` (${esc(ai.providers.ollama.model)})` : ''}</option>
        ${(L.ollama.models || []).map(m => `<option value="${esc(m.name)}" ${ai.vision_model === m.name ? 'selected' : ''} ${m.vision ? '' : 'disabled'}>${esc(m.name)}${m.vision ? '' : ' (no image support)'}</option>`).join('')}</select></div>
    <div class="route"><label class="field-label" style="margin:0">Frame quality</label>
      <select data-change="visual-height" style="width:100%">${[[480, '480p (default, smaller downloads)'], [720, '720p (small text, ~2× download)']].map(([k, l]) => `<option value="${k}" ${ai.visual_height === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div>` : ''}
    ${viewEmbed(L, running)}
    <label class="check" style="margin-top:16px"><input type="checkbox" data-change="local-only" ${ai.local_only ? 'checked' : ''}>
      <span><b style="font-weight:500">Local only</b></span> ${tip('Never send anything to a cloud AI (Claude, Gemini or hosted open-weight models).')}</label>
    <div class="actions"><button class="btn-text" data-act="local-check">Check again</button></div>
    <div id="job"></div>
  </section>`;
}
function viewEmbed(L, running) {
  const E = L.embed;
  if (!E) return '';
  const pct = E.pieces ? Math.round(E.embedded / E.pieces * 100) : 0;
  const state = !L.ollama.running ? 'Ollama is not running: questions and search use keywords only.'
    : !E.installed ? `${E.model} isn't downloaded yet: questions and search use keywords only.`
    : !E.pieces ? 'Nothing to index yet: sync a playlist and fetch details.'
    : E.embedded >= E.pieces ? `All ${E.pieces} pieces indexed.` : `${E.embedded} of ${E.pieces} pieces indexed (${pct}%). The rest are added after the next sync or with Update index.`;
  const known = E.choices.some(c => c.model === E.model) ? E.choices : [{ model: E.model, size: '', why: 'Your choice.', installed: E.installed }, ...E.choices];
  return `
    <h3 style="font-size:14px;margin:22px 0 4px">Meaning-based search</h3>
    <p class="note">Meaning index ${tip('Ask and Search → By meaning finds pieces by what they mean, in any language, using a small embedding model. The index updates after syncs, details, transcription, slide reading and ratings.')}</p>
    <div class="prov"><div><span class="dot ${E.installed && E.embedded >= E.pieces && E.pieces ? 'ok' : ''}"></span><b style="font-weight:500">Index</b>
      <span class="faint" style="font-size:12.5px"> · ${esc(state)}</span></div><span></span></div>
    ${E.pieces ? `<div class="progress" style="margin:6px 0 4px"><i style="width:${pct}%"></i></div>` : ''}
    ${L.ollama.running ? known.map(c => `<div class="prov"><div>${c.model === E.model ? '<span class="vb vb-watch" style="margin-right:6px">In use</span>' : ''}<b style="font-weight:500">${esc(c.model)}</b>
      <span class="faint" style="font-size:12.5px">${c.size ? ' · ' + esc(c.size) : ''} · ${esc(c.why)}</span></div>
      <span style="display:flex;gap:6px">${!c.installed ? `<button class="btn btn-sm ${c.model === E.model ? 'btn-dark' : ''}" data-act="local-pull" data-model="${esc(c.model)}" data-job-btn>Download</button>` : ''}
        ${c.model !== E.model && c.installed ? `<button class="btn btn-sm" data-act="embed-use" data-model="${esc(c.model)}">Use</button>` : ''}</span></div>`).join('') : ''}
    ${L.ollama.running && E.installed ? `<div class="actions">
      <button class="btn btn-sm" data-act="embed-run" data-job-btn>Update index</button>
      <button class="btn btn-sm" data-act="embed-rebuild" data-job-btn title="Embed every piece again, e.g. after an Ollama or model update">Rebuild</button></div>` : ''}`;
}
function aiEditor(k, p) {
  const models = [...new Set([...(p.models || []), ...(S.ai.models[k] || [])])];
  const keyNote = p.key_source === 'saved' ? 'A key is saved. Leave blank to keep it.'
    : p.key_source === 'env' ? `Using ${esc(p.env)} from your environment.` : p.needs_key ? 'Required.' : 'Optional for local servers.';
  return `<div class="ai-grid" style="padding:4px 0 18px">
    <div><label class="field-label" for="ai-model">Model</label>
      <input id="ai-model" type="text" list="ai-models" value="${esc(p.model)}" placeholder="${p.local ? 'e.g. llama3.1:8b' : 'model name'}">
      <datalist id="ai-models">${models.map(m => `<option value="${esc(m)}">`).join('')}</datalist>
      ${p.kind === 'openai' || p.kind === 'ollama' ? `<button class="btn-text" style="padding:0;margin-top:4px" data-act="ai-models" data-p="${k}">List models from server</button>` : ''}</div>
    ${p.kind === 'openai' || p.kind === 'ollama' ? `<div><label class="field-label" for="ai-base">Server URL</label>
      <input id="ai-base" type="text" value="${esc(p.base_url)}" placeholder="${p.kind === 'ollama' ? 'http://localhost:11434' : 'https://…/v1'}"></div>` : '<div></div>'}
    ${p.kind === 'ollama' ? `<div><label class="field-label" for="ai-ctx">Context size (tokens)</label>
      <input id="ai-ctx" type="number" min="2048" max="131072" step="1024" value="${p.num_ctx}">
      <div class="hint" style="margin-top:6px">Context size ${tip('How much transcript the model reads at once. Larger values use more memory; 8192 suits a 4 GB GPU.')}</div></div><div></div>` : ''}
    <div class="full"><label class="field-label" for="ai-key">API key</label>
      <input id="ai-key" type="password" autocomplete="off" value="${esc(S.ai.key)}" data-input="ai-key" placeholder="${p.key_source ? '••••••••' : ''}">
      <div class="hint" style="margin-top:6px">${keyNote} Stored only in <code>settings.json</code> on this computer.
        ${p.key_source === 'saved' ? `<button class="btn-text" style="padding:0;height:auto" data-act="ai-clear-key" data-p="${k}">Remove saved key</button>` : ''}</div></div>
    ${p.local ? `<p class="hint full" style="margin:0">Install ${k === 'ollama' ? '<a href="https://ollama.com" target="_blank" rel="noopener">Ollama</a>, then run <code>ollama pull llama3.1:8b</code>' : 'LM Studio, load a model and start its local server'}. Smaller models make more mistakes.</p>` : ''}
    <div class="actions full" style="margin-top:0">
      <button class="btn btn-dark btn-sm" data-act="ai-save" data-p="${k}">Save</button>
      <button class="btn btn-sm" data-act="ai-test" data-p="${k}">Save and test</button>
      <span id="ai-status" class="status-line"></span>
    </div>
  </div>`;
}
function aiEditorBody(k) {
  const config = {};
  if ($('#ai-model')) config.model = $('#ai-model').value;
  if ($('#ai-base')) config.base_url = $('#ai-base').value;
  if ($('#ai-ctx')) config.num_ctx = +$('#ai-ctx').value;
  if ($('#ai-key') && $('#ai-key').value.trim()) config.api_key = $('#ai-key').value.trim();
  const body = { edit: k, config };
  // The first AI you set up becomes the default
  if (S.data.ai.provider === 'none' || !S.data.ai.providers[S.data.ai.provider]) body.provider = k;
  return body;
}
function aiPrefsBody() {
  const body = {};
  if ($('#ai-interests')) body.interests = $('#ai-interests').value;
  if ($('#ai-lang')) body.language = $('#ai-lang').value;
  if ($('#ai-auto')) body.auto_after_sync = $('#ai-auto').checked;
  return body;
}
// The AI a task runs on: the media choice, its own choice, the task it follows, or the default (from the page's selects when given)
function mainOf(t, ai, live) {
  if (ai.media_tasks?.[t]) return live?.media?.[t] ?? (ai.media_tasks[t].selected || 'local');
  const own = live ? live.tasks[t] : ai.tasks[t];
  if (own) return own;
  const f = ai.task_follows?.[t];
  return f ? mainOf(f, ai, live) : (live ? live.provider : ai.provider);
}
// A helper must sit on the other side of the main AI (cloud next to local, local next to cloud) and be set up
function helperOptions(t, main, value) {
  const ai = S.data.ai, meta = ai.media_tasks?.[t];
  const isLocal = k => k === 'local' || !!ai.providers[k]?.local;
  const keys = (meta ? meta.providers : Object.keys(ai.providers))
    .filter(k => k !== main && isLocal(k) !== isLocal(main) && (k === 'local' || (ai.providers[k]?.ready && !ai.providers[k]?.blocked)));
  const name = k => k === 'local' ? 'This computer' : ai.providers[k].label;
  return `<option value="none">None</option>` + keys.map(k => `<option value="${esc(k)}" ${value === k ? 'selected' : ''}>${esc(name(k))}</option>`).join('');
}
function mediaSends(ai, chosen) {
  const label = k => ai.providers[k]?.label || k;
  const parts = [chosen.transcribe !== 'local' && `audio to ${label(chosen.transcribe)}`, chosen.slides !== 'local' && `slide frames to ${label(chosen.slides)}`,
    chosen.embed !== 'local' && `all indexed text to ${label(chosen.embed)}`].filter(Boolean);
  return parts.length ? `Sent to the cloud: ${parts.join('; ')}. Audio is deleted here after upload.` : 'Transcription, slide reading and the search index run on this computer; nothing is uploaded.';
}
function liveRoutes() {
  const tasks = {}, media = {};
  for (const sel of $$('[data-ai-route]')) if (sel.dataset.aiRoute !== 'provider') tasks[sel.dataset.aiRoute] = sel.value || null;
  for (const sel of $$('[data-media-task]')) media[sel.dataset.mediaTask] = sel.value;
  return { provider: $('[data-ai-route="provider"]')?.value, tasks, media };
}
// After a "Runs on" change: offer only helpers that fit the new choice (a helper that no longer fits goes back to None)
function refreshHelpers() {
  const live = liveRoutes();
  for (const sel of $$('[data-helper-task]')) {
    const t = sel.dataset.helperTask, keep = sel.value;
    sel.innerHTML = helperOptions(t, mainOf(t, S.data.ai, live), keep);
    if (![...sel.options].some(o => o.value === keep)) sel.value = 'none';
  }
  const sends = $('#media-sends');
  if (sends) sends.textContent = mediaSends(S.data.ai, live.media);
}
function aiRoutesBody() {
  const tasks = {};
  for (const sel of $$('[data-ai-route]')) if (sel.dataset.aiRoute !== 'provider') tasks[sel.dataset.aiRoute] = sel.value || null;
  for (const sel of $$('[data-media-task]')) tasks[sel.dataset.mediaTask] = sel.value;
  const helpers = Object.fromEntries($$('[data-helper-task]').map(sel => [sel.dataset.helperTask, sel.value === 'none' ? null : sel.value]));
  return { provider: $('[data-ai-route="provider"]').value, tasks, helpers };
}
const selectedMediaProvider = (ai, task) => ai.media_tasks?.[task]?.selected || 'local';
async function confirmEstimate(path, title) {
  const estimate = await api(path);
  if (estimate.engine === 'local' && !estimate.cost) return true;   // nothing leaves this computer
  if (estimate.cost == null) return confirm(`${title}. ${estimate.sends || ''} Cost estimate unavailable. Continue?`);
  return confirm(`${title}. Estimated cost: $${Number(estimate.cost).toFixed(4)}. ${estimate.sends || ''} Continue?`);
}
async function saveAi(body) {
  const r = await api('/api/ai/settings', body);
  S.data.ai = r.ai; S.ai.key = '';
  return r.ai;
}

function collectTax() {
  const t = {};
  for (const ta of $$('.kw')) t[ta.dataset.name] = ta.value.split(',').map(s => s.trim()).filter(Boolean);
  return t;
}
async function saveTax(t) { S.data.taxonomy = (await api('/api/taxonomy', { taxonomy: t })).taxonomy; }
async function saveNewCategory() {
  const nameInput = $('#new-cat-name');
  const name = (nameInput?.value || '').trim();
  const errEl = $('#new-cat-err');
  if (!name) {
    if (errEl) { errEl.textContent = 'Category name is required'; errEl.style.display = 'block'; }
    nameInput?.focus();
    return;
  }
  const t = collectTax();
  const lower = name.toLowerCase();
  const existing = new Set([
    ...Object.keys(t).map(k => k.toLowerCase()),
    ...videos().map(v => (v.category || '').toLowerCase()),
    (FALLBACK() || '').toLowerCase(),
    'uncategorized'
  ]);
  if (existing.has(lower)) {
    if (errEl) { errEl.textContent = 'That category already exists'; errEl.style.display = 'block'; }
    nameInput?.focus();
    return;
  }
  if (errEl) { errEl.textContent = ''; errEl.style.display = 'none'; }
  const kw = ($('#new-cat-kw')?.value || '').split(',').map(s => s.trim()).filter(Boolean);
  t[name] = kw;
  const saveBtn = $('[data-act="save-add-cat"]');
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving…'; }
  let saved = false;
  try {
    await saveTax(t); saved = true;
    const r = await api('/api/presets', { name, preset: true });
    S.data.ai.presets = r.presets;
    S.addingCat = false;
    refresh();
    toast(`Added "${name}"`);
  } catch (err) {
    // undo the half-done add so Save can be retried without the duplicate check blocking it
    if (saved) { delete t[name]; try { await saveTax(t); } catch {} }
    if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save'; }
    if (errEl) { errEl.textContent = err.message; errEl.style.display = 'block'; }
    toast(err.message, true);
  }
}

/* ---------------- drawer ---------------- */
function renderDrawer() {
  const box = $('#drawer'), v = S.drawer && byId(S.drawer);
  if (!v) { box.innerHTML = ''; S.drawer = null; return; }
  const d = v.duration_seconds || 0;
  const i = S.visible.findIndex(x => x.video_id === v.video_id);
  const cats = categoryNames();
  if (!cats.includes(v.category)) cats.unshift(v.category);
  box.innerHTML = `
  <div class="scrim" data-act="close"></div>
  <aside class="drawer" role="dialog" aria-label="Video details">
    <div class="drawer-top">
      <button class="btn icon-btn" data-act="step" data-dir="-1" ${i > 0 ? '' : 'disabled'} aria-label="Previous video">↑</button>
      <button class="btn icon-btn" data-act="step" data-dir="1" ${i >= 0 && i < S.visible.length - 1 ? '' : 'disabled'} aria-label="Next video">↓</button>
      <span class="grow">${i >= 0 ? `${i + 1} of ${S.visible.length}` : ''}</span>
      <button class="btn-text" data-act="ask-video" data-id="${esc(v.video_id)}" title="Ask a question answered from this video only">Ask about this video</button>
      <a class="btn-text" href="/api/export/markdown?id=${encodeURIComponent(v.video_id)}" download title="Download this video's notes, summary and key points as Markdown">Export note</a>
      <a class="btn-text" href="${esc(v.url)}" target="_blank" rel="noopener">Open on YouTube ↗</a>
      <button class="btn icon-btn" data-act="close" aria-label="Close">✕</button>
    </div>
    <div class="drawer-body">
      <div class="embed"><iframe src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(v.video_id)}?rel=0" title="${esc(v.title)}"
        referrerpolicy="strict-origin-when-cross-origin" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen loading="lazy"></iframe></div>
      <div><h3>${esc(v.title)}</h3><div class="by">${esc(v.channel || 'Unknown channel')}</div></div>
      <div class="speeds">${d ? SPEEDS.map(s => `<span class="${s === S.speed ? 'on' : ''}">${sx(s)} <b>${fmt(d / s)}</b></span>`).join('') : 'No duration available'}</div>
      <div id="d-summary">${drawerSummary(v)}</div>
      <div id="d-rating">${drawerRating(v)}</div>
      <div><span class="field-label">Status</span>
        <div class="seg" id="d-status">${Object.entries(STATUS_LABEL).map(([k, l]) => `<button data-act="set-status" data-id="${esc(v.video_id)}" data-status="${k}" class="${v.status === k ? 'on' : ''}">${l}</button>`).join('')}</div></div>
      <div><label class="field-label" for="d-cat">Category</label>
        <select id="d-cat" data-change="d-cat" data-id="${esc(v.video_id)}" style="width:100%">${cats.map(c => `<option value="${esc(c)}" ${c === v.category ? 'selected' : ''}>${esc(c)}</option>`).join('')}<option value="__new">New category…</option></select>
        <div class="kv-line" id="d-cat-note">${catNote(v)}</div></div>
      <div id="d-sug">${drawerSug(v)}</div>
      <div><label class="field-label" for="d-notes">Notes</label>
        <textarea id="d-notes" rows="6" placeholder="Takeaways, timestamps, follow-ups…">${esc(v.notes || '')}</textarea>
        <div class="kv-line">Notes save when you click away.</div></div>
      <div id="d-details">${drawerDetails(v)}</div>
      <div id="d-slides">${drawerSlides(v)}</div>
      <div class="kv-line">In: ${v.sources.map(s => `${esc(playlistName(s.id))}${s.removed ? ' (removed on YouTube)' : ''}`).join(' · ') || 'no playlist'}</div>
    </div>
  </aside>`;
  loadSummary(v.video_id);
}
function drawerRating(v) {
  const r = v.rating;
  const hasSummary = !!S.summaryCache[v.video_id]?.summary;
  if (!r) return S.data.ai.ready_for.rate
    ? `<div class="kv-line">Not rated yet. <button class="btn-text" style="padding:0;height:auto" data-act="rate-one" data-id="${esc(v.video_id)}" data-job-btn>Rate this video</button></div>`
    : '<div class="kv-line">Not rated yet.</div>';
  const factorLabels = { depth: 'Depth', fit: 'Fit', value: 'Time value', freshness: 'Freshness' };
  const calc = r.calc;
  const breakdown = r.breakdown || {};
  // Quality uses the weights divided by their sum (rater.compute_score), so show those shares
  const wsum = calc ? ['depth', 'value', 'freshness'].reduce((a, k) => a + (calc.formula?.[k] || 0), 0) || 1 : 1;
  const pct = k => Math.round((calc?.formula?.[k] || 0) / wsum * 100);
  const calcLine = calc ? `${calc.score} = quality ${calc.quality} (depth ${breakdown.depth?.score ?? '—'} × ${pct('depth')}% + value ${breakdown.value?.score ?? '—'} × ${pct('value')}% + freshness ${breakdown.freshness?.score ?? '—'} × ${pct('freshness')}%) + interest ${calc.interest}` : '';
  return `<div class="rate-box">
    <div class="tri-head"><span class="score-big">${r.score.toFixed(1)}</span>${badge(v).replace(/<b>.*?<\/b>/, '')}
      <span class="muted" style="font-size:13px">${esc(r.reason)}</span></div>
    <div id="why-score" class="field-label">Why this score</div>
    ${Object.keys(breakdown).filter(k => factorLabels[k]).length ? `<div class="score-factors">${['depth','fit','value','freshness'].filter(k => breakdown[k]).map(k => `<div class="factor"><div><b>${factorLabels[k]}</b><span>${breakdown[k].score}/10</span></div><div class="factor-track"><i style="width:${Math.max(0,Math.min(10,+breakdown[k].score||0))*10}%"></i></div>${breakdown[k].why ? `<small>${esc(breakdown[k].why)}</small>` : ''}</div>`).join('')}</div>${calcLine ? `<div class="kv-line">${esc(calcLine)}</div>` : ''}` : `<div class="kv-line">Rate again for a score breakdown.</div>`}
    ${!hasSummary && r.tldr ? `<p class="tldr">${esc(r.tldr)}</p>` : ''}
    ${!hasSummary && r.minutes?.length ? `<div class="field-label">Minutes</div><ul>${r.minutes.map(m => `<li><button class="chap" data-act="seek" data-t="${m.t}"><time>${clock(m.t)}</time><span><b>${esc(m.topic)}</b>${(m.notes || []).map(n => `<small class="minute-note">${esc(n)}</small>`).join('')}</span></button></li>`).join('')}</ul>` : ''}
    ${!hasSummary && r.takeaways?.length ? `<div class="field-label">Takeaways</div><ul>${r.takeaways.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
    ${r.skim.length ? `<div class="ranges">${r.skim.map(x => `<button class="range" data-act="seek" data-t="${x.start}" title="${esc(x.why)}">${clock(x.start)}–${clock(x.end)} ${esc(x.why)}</button>`).join('')}</div>` : ''}
    ${!hasSummary && r.points.length && !(r.minutes || []).length ? `<ul>${r.points.map(p => p.t != null
      ? `<li><button class="chap" data-act="seek" data-t="${p.t}"><time>${clock(p.t)}</time><span>${esc(p.text)}</span></button></li>`
      : `<li class="chap" style="cursor:default"><time></time><span>${esc(p.text)}</span></li>`).join('')}</ul>` : ''}
    <div class="kv-line">${r.basis === 'metadata' ? 'Rated from title and description only' : 'Rated from the transcript'} · ${esc(r.model || '')}
      · <button class="btn-text" style="padding:0;height:auto;font-size:12.5px" data-act="rate-one" data-id="${esc(v.video_id)}" data-job-btn>Rate again</button></div>
  </div>`;
}
function drawerSummary(v) {
  const result = S.summaryCache[v.video_id];
  if (result === 'loading' || result === undefined) return '<div class="kv-line">Loading summary…</div>';
  if (result?.error) return `<div class="kv-line">Could not load summary: ${esc(result.error)}. <button class="btn-text" style="padding:0;height:auto" data-act="summary-retry" data-id="${esc(v.video_id)}">Retry</button></div>`;
  if (!result?.summary) return S.data.ai.ready_for.summarize
    ? `<div class="kv-line">No summary yet. <button class="btn-text" style="padding:0;height:auto" data-act="summarize-one" data-id="${esc(v.video_id)}" data-job-btn>Summarise</button> <span class="faint">(local models take about 1–2 minutes)</span></div>`
    : '<div class="kv-line">No summary yet. <a href="#/settings/routing">Choose an AI for summaries in Settings</a>.</div>';
  const s = result.summary, moments = result.moments || {};
  const at = t => Number.isFinite(+t) ? Math.max(0, Math.floor(+t)) : 0;
  const time = t => t == null || !Number.isFinite(+t) ? '' : `<button class="time-link" data-act="seek" data-t="${at(t)}">${clock(at(t))}</button>`;   // no time: no fake 0:00 link
  const relationLabel = { prerequisite: 'Prerequisites', deeper: 'Go deeper', other_view: 'Other views', same_topic: 'Same topic' };
  const grouped = {};
  for (const item of s.learn_more?.related || []) (grouped[item.relation] ||= []).push(item);
  return `<section class="section card" style="margin:0"><h4 class="h">AI summary</h4>
    ${s.tldr ? `<p class="tldr">${esc(s.tldr)}</p>` : ''}
    ${s.minutes?.length ? `<div class="field-label">Minutes</div><ul>${s.minutes.map((m, i) => `<li><div class="chap"><span>${time(m.t)}</span><span><b>${esc(m.topic)}</b>${(m.notes || []).map(n => `<small class="minute-note">${esc(n)}</small>`).join('')}${(moments[i] || []).length ? `<span class="also">Also in ${(moments[i] || []).map(x => `<button class="also-chip" data-act="play-at" data-id="${esc(x.video_id)}" data-t="${at(x.t)}" title="${esc(x.title)} @ ${clock(at(x.t))}">${esc(x.title)} · ${clock(at(x.t))}</button>`).join('')}</span>` : ''}</span></div></li>`).join('')}</ul>` : ''}
    ${s.claims?.length ? `<div class="field-label">Key claims and quotes</div><ul>${s.claims.map(c => `<li><div class="chap"><span>${time(c.t)}</span><span><b>${esc(c.kind)}</b> · ${esc(c.text)}</span></div></li>`).join('')}</ul>` : ''}
    ${s.quiz?.length ? `<div class="field-label">Check yourself</div><ul>${s.quiz.map(q => `<li><div class="chap"><span>${time(q.t)}</span><span>${esc(q.q)} <details><summary>Show answer</summary><p>${esc(q.a)}</p></details></span></div></li>`).join('')}</ul>` : ''}
    ${s.takeaways?.length ? `<div class="field-label">Takeaways</div><ul>${s.takeaways.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
    ${Object.entries(grouped).map(([kind, items]) => `<div class="field-label">${esc(relationLabel[kind] || kind)}</div><ul>${items.map(x => `<li><button class="btn-text" data-act="open" data-id="${esc(x.video_id)}">${esc(x.video_id)}</button> · ${esc(x.why)}</li>`).join('')}</ul>`).join('')}
    ${(s.learn_more?.searches || []).length ? `<div class="field-label">YouTube searches</div><ul>${s.learn_more.searches.map(x => `<li><a href="https://www.youtube.com/results?search_query=${encodeURIComponent(x.query || '')}" target="_blank" rel="noopener">${esc(x.query)}</a> · ${esc(x.why)}</li>`).join('')}</ul>` : ''}
    <div class="kv-line">${esc(s.model || '')}</div></section>`;
}
async function loadSummary(id) {
  if (Object.hasOwn(S.summaryCache, id)) return;
  S.summaryCache[id] = 'loading';
  try { S.summaryCache[id] = await api(`/api/summary?id=${encodeURIComponent(id)}`); }
  catch (e) { S.summaryCache[id] = { error: e.message }; }
  if (S.drawer === id && $('#d-summary')) $('#d-summary').innerHTML = drawerSummary(byId(id));
  if (S.drawer === id && $('#d-rating')) $('#d-rating').innerHTML = drawerRating(byId(id));
}
function ratingFormula(custom) {
  return { depth: .45, value: .40, freshness: .15, interest_penalty: 2, interest_bonus: .5, ...custom };
}
function formulaExample(f) {
  const quality = (7*f.depth + 6*f.value + 8*f.freshness) / Math.max(.0001, f.depth+f.value+f.freshness);
  const fit = 5, interest = -f.interest_penalty * Math.max(0, Math.min(1, (7-fit)/4));
  return `${(Math.max(0, Math.min(10, quality+interest))).toFixed(1)} = quality ${quality.toFixed(1)} (7 × ${Math.round(f.depth*100)}% + 6 × ${Math.round(f.value*100)}% + 8 × ${Math.round(f.freshness*100)}%) + interest ${interest.toFixed(1)}`;
}
function drawerSlides(v) {
  const vis = v.visual || {}, cached = S.detailCache[v.video_id], full = cached && cached !== 'loading' ? cached : null;
  const busy = S.data.job.running && S.data.job.kind === 'visual'
    && ((S.busy.visual || []).includes(v.video_id) || (vis.job && ['queued', 'downloading', 'reading'].includes(vis.job.status)));
  const mark = v.has_visuals == null ? '' : v.has_visuals ? 'checked' : '';
  const head = `<label class="check"><input type="checkbox" data-change="has-visuals" data-id="${esc(v.video_id)}" ${mark}>
    <span>Has slides / whiteboard / on-screen code${v.has_visuals == null && vis.hint ? ` <span class="faint">(suggested: ${esc(vis.hint)})</span>` : ''}</span></label>`;
  const readBtn = label => `<button class="btn-text" style="padding:0;height:auto" data-act="vis-one" data-id="${esc(v.video_id)}" data-job-btn>${label}</button>`;
  let body = '';
  if (busy) body = `<div class="kv-line"><span class="spin" style="display:inline-block;vertical-align:-1px"></span> Reading slides${vis.job && vis.job.status !== 'done' && vis.job.status !== 'failed' ? `: ${esc(vis.job.status)}${vis.job.progress ? ` ${Math.round(vis.job.progress * 100)}%` : ''}` : ''}… The job log is on the Playlists page.</div>`;
  else if (vis.slides && full?.visuals) {
    body = `<div class="strip">${full.visuals.filter(x => x.thumb).map(x => `<button data-act="seek" data-t="${Math.floor(x.t)}" title="${esc((x.text || x.visual || '').slice(0, 140))}">
        <img src="/frames/${encodeURIComponent(v.video_id)}/${esc(x.thumb.replace('.jpg', ''))}.jpg" alt="" loading="lazy"><time>${clock(x.t)}</time></button>`).join('')}</div>
      <details style="margin-top:6px"><summary>On-screen text · ${plural(full.visuals.length, 'slide')}</summary>
        ${full.visuals.map(x => `<button class="tline" data-act="seek" data-t="${Math.floor(x.t)}"><time>${clock(x.t)}</time><span>${x.visual ? `<i>${esc(x.visual)}</i><br>` : ''}<span class="slide-text">${esc(x.text)}</span></span></button>`).join('')}
      </details>
      <div class="kv-line">${readBtn('Read again')}</div>`;
  } else if (vis.slides) { if (cached !== 'loading') loadDetails(v.video_id); body = '<div class="kv-line">Loading slides…</div>'; }
  else if (vis.job?.status === 'failed') body = `<div class="kv-line">Last try failed: ${esc((vis.job.error || '').slice(0, 120))}. ${readBtn('Try again')}</div>`;
  else if (vis.job?.status === 'done') body = `<div class="kv-line">Read: no frames with slides or code were found. ${readBtn('Read again')}</div>`;
  else if (v.has_visuals) body = `<div class="kv-line">${readBtn('Read slides now')} <span class="faint">(about ${Math.max(1, Math.round((v.duration_seconds || 0) / 60 * 1.4 * 7 / 60))} min)</span></div>`;
  return `<span class="field-label">Slides and whiteboard</span>${head}${body}`;
}
function drawerDetails(v) {
  const det = v.details, cached = S.detailCache[v.video_id], full = cached === 'loading' ? null : cached;
  const fetchBtn = label => `<button class="btn-text" style="padding:0;height:auto" data-act="details-one" data-id="${esc(v.video_id)}" data-job-btn>${label}</button>`;
  if (!det) return `<span class="field-label">Details</span><div class="kv-line">Not fetched yet. ${fetchBtn('Fetch now')}</div>`;
  const head = det.status === 'done'
    ? `${det.caption_kind === 'manual' ? 'Captions' : 'Auto captions'} (${esc(det.caption_lang || '?')})${det.chapters ? ` · ${plural(det.chapters, 'chapter')}` : ''}`
    : `${esc(DETAIL_LABEL[det.status] || det.status)}${det.chapters ? ` · ${plural(det.chapters, 'chapter')}` : ''}`;
  if (!full) {
    if (det.status !== 'unavailable') loadDetails(v.video_id);
    return `<span class="field-label">Details</span><div class="kv-line">${head}${det.status === 'unavailable' || det.status === 'failed' ? ` · ${fetchBtn('Try again')}` : ''}</div>`;
  }
  const asrBtn = label => `<button class="btn-text" style="padding:0;height:auto" data-act="asr-one" data-id="${esc(v.video_id)}" data-job-btn>${label}</button>`;
  const job = det.asr;
  const asrLine = det.sources.length > 1
    ? `<div style="margin-top:10px"><span class="field-label">Transcript used for search, ratings and summaries</span>
        <div class="seg" style="max-width:360px">${[['captions', 'YouTube captions'], ['local-asr', `Local (Whisper${det.asr_language ? ', ' + esc(det.asr_language) : ''})`]].map(([k, l]) =>
          `<button data-act="transcript-source" data-id="${esc(v.video_id)}" data-source="${k}" class="${full.transcript === k ? 'on' : ''}">${l}</button>`).join('')}</div></div>`
    : S.data.job.running && S.data.job.kind === 'asr' && ((S.busy.asr || []).includes(v.video_id) || (job && ['queued', 'downloading', 'transcribing'].includes(job.status)))
      ? `<div class="kv-line"><span class="spin" style="display:inline-block;vertical-align:-1px"></span> Transcribing locally${job && !['done', 'failed'].includes(job.status) ? `: ${esc(job.status)}${job.progress ? ` ${Math.round(job.progress * 100)}%` : ''}` : ''}…</div>`
    : det.asr_reason
      ? `<div class="kv-line">${det.asr_reason === 'wrong_script' ? 'These captions look like the wrong script for the spoken language.' : 'No captions.'}
          ${job?.status === 'failed' ? `Last try failed: ${esc((job.error || '').slice(0, 120))}. ` : ''}${asrBtn('Transcribe locally')}</div>` : '';
  return `<span class="field-label">Details</span><div class="kv-line">${head}${det.sources.includes('local-asr') && det.sources.length === 1 ? ' · local transcript' : ''} · ${fetchBtn('Refresh')}</div>
    ${asrLine}
    ${full.chapters.length ? `<div style="margin-top:10px">${full.chapters.map(c => `<button class="chap" data-act="seek" data-t="${Math.floor(c.start)}"><time>${clock(c.start)}</time><span>${esc(c.title)}</span></button>`).join('')}</div>` : ''}
    ${full.description ? `<details style="margin-top:12px" ${S.data.ai.show_descriptions ? 'open' : ''}><summary>Show description</summary><p class="desc-text">${esc(full.description)}</p></details>` : ''}
    ${full.segments.length ? `<details style="margin-top:10px"><summary>Transcript · ${plural(full.segments.length, 'line')}</summary>
      <div class="transcript">${full.segments.map(g => `<button class="tline" data-act="seek" data-t="${Math.floor(g.start)}"><time>${clock(g.start)}</time><span>${esc(g.text)}</span></button>`).join('')}</div></details>` : ''}`;
}
async function loadDetails(id) {
  if (S.detailCache[id] === 'loading') return;
  S.detailCache[id] = 'loading';
  try {
    S.detailCache[id] = (await api(`/api/video?id=${encodeURIComponent(id)}`)).details;
  } catch (e) { delete S.detailCache[id]; return; }
  if (S.drawer === id && $('#d-details')) { $('#d-details').innerHTML = drawerDetails(byId(id)); $('#d-slides').innerHTML = drawerSlides(byId(id)); }
}
function seek(t) {
  const f = $('.drawer .embed iframe'), v = S.drawer && byId(S.drawer);
  if (f && v) f.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(v.video_id)}?rel=0&autoplay=1&start=${Math.max(0, t | 0)}`;
}
const catNote = v => v.category_locked
  ? `${v.category_source === 'ai' ? 'Accepted from an AI suggestion.' : 'Set by hand.'} <button class="btn-text" style="height:auto;padding:0;font-size:12.5px" data-act="reset-cat" data-id="${esc(v.video_id)}">Sort automatically instead</button>`
  : 'Sorted automatically by keywords.';
const drawerSug = v => v.suggestion ? `<div class="sug-box"><span class="field-label">AI suggestion</span>${sugLine(v).replace('AI suggests ', '')}</div>` : '';
function syncDrawer() {
  const v = S.drawer && byId(S.drawer);
  if (!v || !$('#d-status')) return;
  for (const b of $$('#d-status button')) b.classList.toggle('on', b.dataset.status === v.status);
  $('#d-cat').value = v.category;
  $('#d-cat-note').innerHTML = catNote(v);
  $('#d-sug').innerHTML = drawerSug(v);
  $('#d-rating').innerHTML = drawerRating(v);
  if ($('#d-slides')) $('#d-slides').innerHTML = drawerSlides(v);
}
function saveNotes() {
  const ta = $('#d-notes'), v = S.drawer && byId(S.drawer);
  if (ta && v && ta.value !== (v.notes || '')) update([v.video_id], { notes: ta.value });
}
function openDrawer(id) { saveNotes(); S.drawer = id; renderDrawer(); }
function closeDrawer() { saveNotes(); S.drawer = null; renderDrawer(); }

/* ---------------- updates ---------------- */
async function update(ids, patch) {
  if (!ids.length) return;
  for (const id of ids) {
    const v = byId(id);
    if (!v) continue;
    Object.assign(v, patch);
    if (patch.category !== undefined) { v.category_locked = 1; v.category_source = 'manual'; v.suggestion = null; }
  }
  refresh(); syncDrawer();
  try { await api('/api/videos/update', { ids, ...patch }); } catch (e) { toast(e.message, true); await load(); }
}
function askName() {
  const n = (prompt('Name for the new category:') || '').trim();
  return n && n !== '__new' ? n : null;
}
function setView(patch) {
  Object.assign(S.view, patch);
  S.sel.clear(); S.limit = 150;
  if (route() !== '') location.hash = '#/';
  else refresh();
  scrollTo(0, 0);
}

/* ---------------- events ---------------- */
let aboutRecorder = null, aboutStream = null, aboutChunks = [], aboutTimer = null, aboutStarted = 0;
function releaseAboutMic() {
  clearInterval(aboutTimer); aboutTimer = null;
  aboutStream?.getTracks().forEach(track => track.stop()); aboutStream = null;
}
async function startAboutRecording() {
  const a = S.about;
  a.error = '';
  try {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('Voice recording is unavailable in this browser. Type instead.');
    aboutStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const types = ['audio/webm;codecs=opus', 'audio/ogg', 'audio/mp4'];
    const mimeType = types.find(type => MediaRecorder.isTypeSupported(type));
    aboutChunks = [];
    aboutRecorder = new MediaRecorder(aboutStream, mimeType ? { mimeType } : undefined);
    aboutRecorder.ondataavailable = event => { if (event.data?.size) aboutChunks.push(event.data); };
    aboutRecorder.onstop = async () => {
      releaseAboutMic(); a.recording = false; a.error = 'Transcribing…'; refresh();
      try {
        const blob = new Blob(aboutChunks, { type: aboutRecorder.mimeType || mimeType || 'audio/webm' });
        const buffer = await blob.arrayBuffer();
        let binary = ''; for (const byte of new Uint8Array(buffer)) binary += String.fromCharCode(byte);
        const result = await api('/api/voice/transcribe', { audio: btoa(binary), mime: blob.type });
        a.look = [a.look.trim(), result.text].filter(Boolean).join(a.look.trim() ? '\n' : ''); a.error = '';
      } catch (err) { a.error = err.message; toast(err.message, true); }
      aboutRecorder = null; aboutChunks = []; refresh(); $('#ai-look')?.focus();
    };
    aboutRecorder.start(); a.recording = true; a.seconds = 0; aboutStarted = Date.now();
    aboutTimer = setInterval(() => {
      a.seconds = Math.floor((Date.now() - aboutStarted) / 1000);
      if (a.seconds >= 180) stopAboutRecording();
      else { const button = $('[data-act="voice-rec"]'); if (button) button.innerHTML = `<span class="rec-dot" aria-hidden="true"></span>■ Stop ${clock(a.seconds)}`; }
    }, 1000);
    refresh();
  } catch (err) {
    releaseAboutMic(); a.recording = false; aboutRecorder = null; aboutChunks = [];
    a.error = err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError'
      ? "Allow the microphone for this page (the address bar's site settings), or type instead" : err.message;
    refresh(); toast(a.error, true);
  }
}
function stopAboutRecording() {
  if (aboutRecorder && aboutRecorder.state !== 'inactive') aboutRecorder.stop();
  else releaseAboutMic();
  S.about.recording = false; refresh();
}
document.addEventListener('click', async e => {
  if (S.jobsOpen && !e.target.closest('#jobs')) { S.jobsOpen = false; renderJobs(); }
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const act = el.dataset.act, id = el.dataset.id;
  if (el.tagName === 'A' && el.getAttribute('href') === '#/') e.preventDefault();
  switch (act) {
    case 'storage-refresh': S.storage = null; await loadStorage(); break;
    case 'storage-backup': {
      try { await queued(await api('/api/files/backup', {}), 'Library backup'); S.storage = null; await loadStorage(); }
      catch (err) { toast(err.message, true); }
      break;
    }
    case 'storage-restore': {
      const name = el.dataset.name, date = el.dataset.date;
      if (!confirm(`Restore the backup from ${date}? Your current library will first be saved as a safety backup. The current library and taxonomy will be replaced.`)) break;
      try { S.storage = null; await api('/api/files/restore', { name }); await load(); toast('Backup restored'); }
      catch (err) { toast(err.message, true); }
      break;
    }
    case 'storage-clear': {
      const key = el.dataset.key, label = el.dataset.label;
      const lost = { frames: 'slide pictures (their text stays)', media: 'cached media files', logs: 'log files except today’s log' }[key];
      if (!lost || !confirm(`Clear ${label}? This permanently removes ${lost}.`)) break;
      try { const result = await api('/api/files/clear', { key }); toast(`Cleared ${Number(result.removed) || 0} files`); S.storage = null; await loadStorage(); }
      catch (err) { toast(err.message, true); }
      break;
    }
    case 'storage-delete': {
      const area = el.dataset.area, name = el.dataset.name;
      if (!confirm(`Delete ${name}? This permanently removes this ${area === 'backups' ? 'backup' : 'export'}.`)) break;
      try { await api('/api/files/delete', { area, name }); S.storage = null; await loadStorage(); toast('File deleted'); }
      catch (err) { toast(err.message, true); }
      break;
    }
    case 'tools-calculate': {
      const links = S.tools.links.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
      if (!links.length) { toast('Paste at least one playlist or video link', true); $('#tools-links')?.focus(); break; }
      S.tools.busy = true; S.tools.items = []; renderView();
      try { S.tools.items = (await api('/api/tools/length', { links, ...(S.tools.browser ? { browser: 'auto' } : {}) })).items || []; }
      catch (err) { toast(err.message, true); }
      finally { S.tools.busy = false; renderView(); }
      break;
    }
    case 'tools-export': {
      const index = +el.dataset.index, item = S.tools.items[index]; if (item?.videos) toolsCsv(toolVideos(item, index)); break;
    }
    case 'tools-export-only': {
      const index = +el.dataset.index, item = S.tools.items[index];
      if (item?.videos) toolsCsv(toolsSplit(item).only);
      break;
    }
    case 'tools-add': {
      const item = S.tools.items[+el.dataset.index]; if (item?.url) runIngest(item.url); break;
    }
    case 'voice-rec': if (S.about.recording) stopAboutRecording(); else startAboutRecording(); break;
    case 'interests-draft': {
      S.about.look = $('#ai-look')?.value || ''; S.about.error = ''; S.about.drafting = true; refresh();
      try { S.about.suggestion = (await api('/api/interests/draft', { text: S.about.look, current: $('#ai-interests')?.value || '', library: S.lib })).interests; }
      catch (err) { S.about.error = err.message; toast(err.message, true); }
      finally { S.about.drafting = false; refresh(); }
      break;
    }
    case 'interests-use': case 'interests-edit': {
      const target = $('#ai-interests'); if (target) { target.value = S.about.suggestion; target.dispatchEvent(new Event('input', { bubbles: true })); target.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
      S.about.suggestion = ''; refresh();
      if (act === 'interests-use') $('[data-act="ai-save-prefs"]')?.focus(); else $('#ai-interests')?.focus();
      break;
    }
    case 'interests-discard': S.about.suggestion = ''; refresh(); break;
    case 'v-status': setView({ status: el.dataset.v }); break;
    case 'home-picks': S.view.status = 'todo'; S.view.cat = ''; S.view.src = ''; S.view.sort = 'score'; S.view.verdict = 'watch'; S.sel.clear(); S.limit = 150; if (route() !== '') location.hash = '#/'; else refresh(); break;
    case 'v-cat': setView({ cat: el.dataset.v, status: S.view.status === 'gone' ? 'todo' : S.view.status }); break;
    case 'v-src': setView({ src: el.dataset.v }); break;
    case 'cycle': { const v = byId(id); if (v) update([id], { status: NEXT[v.status] || 'in_progress' }); break; }
    case 'set-status': update([id], { status: el.dataset.status }); break;
    case 'open': openDrawer(id); break;
    case 'open-why': openDrawer(id); requestAnimationFrame(() => $('#why-score')?.scrollIntoView({ behavior: 'smooth', block: 'start' })); break;
    case 'close': closeDrawer(); break;
    case 'step': { const i = S.visible.findIndex(x => x.video_id === S.drawer) + +el.dataset.dir; if (S.visible[i]) openDrawer(S.visible[i].video_id); break; }
    case 'pick': if (el.checked) S.sel.add(id); else S.sel.delete(id); renderList(); break;
    case 'sel-all': S.visible.forEach(v => S.sel.add(v.video_id)); renderList(); break;
    case 'sel-clear': S.sel.clear(); renderList(); break;
    case 'bulk': {
      const ids = [...S.sel]; S.sel.clear();
      await update(ids, { status: el.dataset.status });
      toast(`${plural(ids.length, 'video')} marked ${STATUS_LABEL[el.dataset.status].toLowerCase()}`);
      break;
    }
    case 'more': S.limit += 50; renderList(); break;
    case 'search-more': S.search.limit += 50; renderSearchResults(); break;
    case 'seek': seek(+el.dataset.t); break;
    case 'ask-go': runAsk(); break;
    case 'ask-open': S.ask.returnFocus = el; S.ask.open = true; renderAskPop(); $('#aq')?.focus({ preventScroll: true }); break;
    case 'ask-close': S.ask.open = false; renderAskPop(); (S.ask.returnFocus || $('#ask-fab'))?.focus(); break;
    case 'ask-full': S.ask.open = false; break;
    case 'ask-new': S.ask.thread = []; S.ask.q = ''; if (route() === 'ask') location.hash = '#/ask'; else renderAskPop(); break;
    case 'ask-unscope': S.ask.video = null; renderAsk(); break;
    case 'ask-video': askAbout(id); break;
    case 'ask-copy': {
      const th = S.ask.thread;
      if (!th.length) break;
      const text = th.map(r => `Q: ${r.question}\n\n${r.answer}\n\n` + r.sources.filter(x => x.cited).map(x => `[${x.n}] ${askLabel(x)} https://www.youtube.com/watch?v=${x.video_id}&t=${Math.floor(x.t || 0)}s`).join('\n')).join('\n\n---\n\n');
      try { await navigator.clipboard.writeText(text); toast('Copied'); } catch (err) { toast('Copy failed: select the text instead', true); }
      break;
    }
    case 'ask-del':
      if (!confirm(S.ask.thread.length > 1 ? 'Delete this conversation (all its questions and answers)?' : 'Delete this question and its answer?')) break;
      try { await api('/api/ask/delete', { id: +el.dataset.ask }); S.ask.thread = []; history.replaceState(null, '', '#/ask'); refresh(); loadAskHistory(); toast('Deleted'); }
      catch (err) { toast(err.message, true); }
      break;
    case 'vis-run': case 'vis-one': {
      try {
        if (!await confirmEstimate('/api/visuals/plan', 'Read slides')) break;
        const res = await api('/api/visuals', act === 'vis-one' ? { ids: [id] } : {});
        S.busy = { visual: act === 'vis-one' ? [id] : videos().filter(v => v.has_visuals === 1 || S.data.visual_categories[v.category]).map(v => v.video_id) };
        await queued(res, 'Slide reading'); renderDrawer();
      } catch (err) { toast(err.message, true); }
      break;
    }
    case 'vis-mark-suggested': {
      const ids = el.dataset.ids.split(',').filter(Boolean);
      try { await api('/api/videos/visuals', { ids, value: true }); S.visPlan = null; await load(); toast(`${plural(ids.length, 'video')} marked as having slides`); }
      catch (err) { toast(err.message, true); }
      break;
    }
    case 'asr-run': case 'asr-one': {
      try {
        if (!await confirmEstimate('/api/asr/plan', 'Transcribe audio')) break;
        const res = await api('/api/asr', act === 'asr-one' ? { ids: [id] } : {});
        S.busy = { asr: act === 'asr-one' ? [id] : videos().filter(v => v.details?.asr_reason).map(v => v.video_id) };
        await queued(res, 'Local transcription'); renderDrawer();
      } catch (err) { toast(err.message, true); }
      break;
    }
    case 'transcript-source':
      try {
        await api('/api/transcript/source', { id, source: el.dataset.source });
        delete S.detailCache[id]; await load(); toast(el.dataset.source === 'local-asr' ? 'Using the local transcript' : 'Using YouTube captions');
      } catch (err) { toast(err.message, true); }
      break;
    case 'export-notes': el.closest('details')?.removeAttribute('open'); exportNotes(el.dataset.scope === 'sel' ? [...S.sel] : S.visible.map(v => v.video_id)); break;
    case 'v-verdict': S.view.verdict = el.dataset.v; S.limit = 150; refresh(); break;
    case 's-kind': S.search.kind = el.dataset.v; S.search.res = null; S.search.limit = 50; refresh(); runSearch(); break;
    case 'accept-skips': {
      const ids = el.dataset.ids.split(',').filter(Boolean);
      await setStatuses(ids, 'skipped'); toast(`${plural(ids.length, 'video')} skipped. Undo is above the list.`);
      break;
    }
    case 'play-at': playAt(id, +el.dataset.t); break;
    case 'rate-batch': startRate(null); break;
    case 'rate-filtered': startRate(el.dataset.ids.split(',').filter(Boolean)); break;
    case 'rate-one': startRate([id]); break;
    case 'summarize-one': startSummary([id]); break;
    case 'summarize-sel': startSummary([...S.sel]); break;
    case 'summary-retry': delete S.summaryCache[id]; loadSummary(id); break;
    case 'knowledge-build': buildKnowledge(el.dataset.cat); break;
    case 'knowledge-summarize-five': summarizeKnowledgeFive(el.dataset.cat); break;
    case 'details-fetch': case 'details-one': {
      try {
        const res = await api('/api/details/fetch', act === 'details-one' ? { ids: [id] } : {});
        if (act === 'details-one') delete S.detailCache[id];
        await queued(res, 'Fetching details');
      } catch (err) { toast(err.message, true); }
      break;
    }
    case 'lib-go': S.lib = +el.dataset.lib; store.set('ytkb.lib', S.lib); await load(); break;
    case 'lib-create': {
      const name = $('#lib-new')?.value.trim();
      if (!name) { toast('Type a name for the library', true); $('#lib-new')?.focus(); break; }
      try { const r = await api('/api/libraries', { action: 'create', name }); S.lib = r.id; store.set('ytkb.lib', S.lib); await load(); toast(`Created ${name}. Add playlists to it, or move some here.`); }
      catch (err) { toast(err.message, true); }
      break;
    }
    case 'lib-delete':
      if (!confirm(`Delete the library “${libName(+el.dataset.lib)}”? It has no playlists, so no videos are affected.`)) break;
      try { await api('/api/libraries', { action: 'delete', id: +el.dataset.lib }); if (S.lib === +el.dataset.lib) { S.lib = 1; store.set('ytkb.lib', 1); } await load(); toast('Library deleted'); }
      catch (err) { toast(err.message, true); }
      break;
    case 'job-stop-one':
      try { await api('/api/job/cancel', { id: +el.dataset.task }); await refreshJob(); }
      catch (err) { toast(err.message, true); }
      break;
    case 'job-stop':
      if (S.stopPending || S.data.job.stopping) break;
      S.stopPending = true; updateJob(); renderJobs();
      try {
        const result = await api('/api/job/cancel', el.dataset.task ? { id: Number(el.dataset.task) } : {});
        if (result.ok) toast('Stopping…');
        else { S.stopPending = false; toast('The task has already ended', true); }
        await refreshJob();
      } catch (err) { S.stopPending = false; updateJob(); renderJobs(); toast(err.message, true); }
      break;
    case 'job-drop': try { await api('/api/job/cancel', { id: +el.dataset.task }); await refreshJob(); toast('Removed from the queue'); } catch (err) { toast(err.message, true); } break;
    case 'jobs-toggle': S.jobsOpen = !S.jobsOpen; renderJobs(); break;
    case 'theme-toggle': S.theme = darkNow() ? 'light' : 'dark'; store.set('ytkb.theme', S.theme); applyTheme(); if (route().startsWith('settings')) refresh(); break;
    case 'log-latest': {
      const box = el.closest('#job') || $('#job');
      const log = $('.log', box);
      if (log) {
        log.scrollTop = log.scrollHeight;
        updateLogJump(box);
      }
      break;
    }
    case 'job-retry': {
      try { await queued(await api('/api/job/retry', { id: +id }), 'Sync'); await load(); }
      catch (err) { toast(err.message, true); }
      break;
    }
    case 'job-dismiss': {
      try { await api('/api/job/dismiss', { id: +id }); await load(); }
      catch (err) { toast(err.message, true); }
      break;
    }
    case 'logs-show': {
      try { S.logs = await api('/api/logs?level=warning&lines=200'); renderView(); }
      catch (err) { toast(err.message, true); }
      break;
    }
    case 'status-undo': statusUndo(); break;
    case 'session-done': {
      const ids = el.dataset.ids.split(',').filter(Boolean);
      if (confirm(`Mark ${plural(ids.length, 'video')} as watched?`)) { await update(ids, { status: 'completed' }); toast('Session marked watched'); }
      break;
    }
    case 'reset-cat':
      try { await api('/api/videos/update', { ids: [id], reset_category: true }); await load(); syncDrawer(); toast('Sorted automatically'); } catch (err) { toast(err.message, true); }
      break;
    case 'ingest': {
      const url = S.ingest.url.trim();
      if (!url) { toast('Paste a playlist or video link first', true); $('#i-url')?.focus(); break; }
      runIngest(url);
      break;
    }
    case 'resync': runResync(el.dataset.id); break;
    case 'pl-remove': {
      const pid = el.dataset.id, name = playlistName(pid);
      try {
        const plan = await api(`/api/playlists/remove?id=${encodeURIComponent(pid)}`);
        const noted = S.data.videos.filter(v => v.sources.length === 1 && v.sources[0].id === pid && (v.notes || '').trim());
        const lines = [`${plural(plan.removed, 'video')} will be removed with their subtitles, ratings and summaries.`,
          plan.kept_notes && `${plural(plan.kept_notes, 'video')} with your notes ${plan.kept_notes === 1 ? 'keeps its' : 'keep their'} notes (downloaded first as a .zip).`,
          plan.shared && `${plural(plan.shared, 'video')} also in other playlists ${plan.shared === 1 ? 'stays' : 'stay'}.`, 'Nothing changes on YouTube.'].filter(Boolean);
        if (!confirm(`Remove “${name}” from YourTube?\n\n${lines.join('\n')}`)) break;
        if (noted.length) await exportNotes(noted.map(v => v.video_id));
        await queued(await api('/api/playlists/remove', { id: pid }), `Removing ${name}`);
      } catch (err) { toast(err.message, true); }
      break;
    }
    case 'sug-accept': case 'sug-dismiss': case 'sug-accept-list': case 'sug-dismiss-list': case 'sug-accept-sure': case 'sug-accept-sel': {
      const accept = act.startsWith('sug-accept');
      const ids = act === 'sug-accept-sel' ? [...S.sel] : act.endsWith('-list') || act === 'sug-accept-sure' ? S.visible.map(v => v.video_id) : [id];
      const body = { ids, ...(act === 'sug-accept-sure' ? { min_confidence: 0.8 } : {}) };
      if (ids.length > 1 && !accept && !confirm(`Dismiss ${plural(ids.length, 'suggestion')}? No videos change.`)) break;
      try {
        const r = await api(`/api/ai/suggestions/${accept ? 'accept' : 'dismiss'}`, body);
        if (act === 'sug-accept-sel') S.sel.clear();
        await load(); syncDrawer();
        toast(accept ? `${plural(r.accepted, 'video')} moved` : `${plural(r.dismissed, 'suggestion')} dismissed`);
      } catch (err) { toast(err.message, true); }
      break;
    }
    case 'ai-discover': try { await saveTax(collectTax()); } catch (err) { toast(err.message, true); break; } startAi('/api/ai/discover'); break;
    case 'ai-suggest': try { await saveTax(collectTax()); } catch (err) { toast(err.message, true); break; } startAi('/api/ai/suggest', { scope: 'unsorted' }); break;
    case 'split-topics': {
      const name = el.dataset.name;
      try {
        await saveTax(collectTax());
        const preview = await api('/api/categories/separate', { names: [name], preview: true });
        const topics = preview.topics || [];
        if (!topics.length) { toast('No topics found in that category name', true); break; }
        const videoCount = videos().filter(video => video.category === name).length;
        const names = topics.map(topic => topic.name);
        const topicList = names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
        const existingNames = topics.filter(topic => topic.exists).map(topic => topic.name);
        const existingNote = existingNames.length ? ` (${existingNames.join(' and ')} ${existingNames.length === 1 ? 'already exists' : 'already exist'})` : '';
        if (!confirm(`Split “${name}” into ${topicList}${existingNote}? Its ${plural(videoCount, 'video')} get AI suggestions to review.`)) break;
        const result = await api('/api/categories/separate', { names: [name] });
        await load();
        if (result.queued) {
          await refreshJob();
          toast(`Split into ${plural(result.topics.length, 'topic')}. Sorting suggestions are running.`);
        } else toast(`Split into ${plural(result.topics.length, 'topic')}. Set up AI to sort the videos.`);
      } catch (err) { toast(err.message, true); }
      break;
    }
    case 'prop-apply': {
      const body = collectProposal();
      if (!body.categories.length) { toast('Tick at least one category', true); break; }
      if (body.remove.length && !confirm(`Remove ${plural(body.remove.length, 'category')}? Their videos go back to automatic sorting. No videos are deleted.`)) break;
      try { const r = await api('/api/ai/proposal/apply', body); await load(); toast(`Saved ${plural(r.categories, 'category')}. ${plural(r.changed, 'video')} re-sorted.`); } catch (err) { toast(err.message, true); }
      break;
    }
    case 'prop-dismiss':
      if (!confirm('Discard the proposed categories?')) break;
      try { await api('/api/ai/proposal/dismiss', {}); await load(); } catch (err) { toast(err.message, true); }
      break;
    case 'local-check': S.local = null; refresh(); loadLocal(); break;
    case 'copy-code': {
      const text = el.parentElement.querySelector('pre').textContent;
      try { await navigator.clipboard.writeText(text); toast('Copied'); } catch (err) { toast('Copy failed: select the text instead', true); }
      break;
    }
    case 'local-pull': {
      const model = el.dataset.model || $('#local-model')?.value.trim();
      if (!model) { toast('Type a model name first', true); break; }
      try { await queued(await api('/api/local/pull', { model }), `Download of ${model}`); }
      catch (err) { toast(err.message, true); }
      break;
    }
    case 'embed-use':
      if (!confirm(`Use ${el.dataset.model} for meaning-based search? The whole library is indexed again with it (the small model does about 12 pieces a second).`)) break;
      try {
        await saveAi({ embed_model: el.dataset.model });
        const have = S.local?.embed?.choices.find(c => c.model === el.dataset.model)?.installed;
        if (have) await queued(await api('/api/embed', {}), 'Indexing');
        S.local = null; loadLocal();
        toast(have ? `Indexing with ${el.dataset.model}…` : `Now download ${el.dataset.model}; it is indexed right after`);
      }
      catch (err) { toast(err.message, true); }
      break;
    case 'embed-run': case 'embed-rebuild':
      if (act === 'embed-rebuild' && !confirm('Rebuild the meaning index from scratch? Questions use keywords only until it finishes.')) break;
      try {
        if (selectedMediaProvider(S.data.ai, 'embed') !== 'local' && !await confirmEstimate('/api/embed/estimate', 'Update the meaning index with the selected cloud provider')) break;
        await queued(await api('/api/embed', { rebuild: act === 'embed-rebuild' }), act === 'embed-rebuild' ? 'Rebuild' : 'Index update');
      }
      catch (err) { toast(err.message, true); }
      break;
    case 'local-use':
      try { S.data.ai = (await api('/api/local/use', { model: el.dataset.model })).ai; refresh(); toast(`Using ${el.dataset.model}`); }
      catch (err) { toast(err.message, true); }
      break;
    case 'ai-edit': S.ai.provider = S.ai.provider === el.dataset.p ? null : el.dataset.p; S.ai.key = ''; refresh(); break;
    case 'ai-save': try { await saveAi(aiEditorBody(el.dataset.p)); refresh(); renderBar(); toast('Saved'); } catch (err) { toast(err.message, true); } break;
    case 'ai-save-prefs': {
      const interestsEl = $('#ai-interests');
      if (interestsEl) {
        const val = interestsEl.value;
        const trimmed = val.trim();
        const errEl = $('#ai-interests-err');
        let errMsg = '';
        if (val.length > 2000) {
          errMsg = 'Interests must be 2,000 characters or fewer';
        } else if (trimmed && trimmed.split(/\s+/).filter(Boolean).length < 3) {
          errMsg = 'Describe your interests in at least 3 words, or leave blank';
        }
        if (errMsg) {
          if (errEl) { errEl.textContent = errMsg; errEl.style.display = 'block'; }
          toast(errMsg, true);
          break;
        }
        if (errEl) { errEl.textContent = ''; errEl.style.display = 'none'; }
      }
      try {
        await saveAi(aiPrefsBody());
        S.about.current = undefined;
        refresh();
        toast('Saved');
      } catch (err) {
        const errEl = $('#ai-interests-err');
        if (errEl && err.message.toLowerCase().includes('interests')) {
          errEl.textContent = err.message;
          errEl.style.display = 'block';
        }
        toast(err.message, true);
      }
      break;
    }
    case 'formula-save': {
      const values = Object.fromEntries($$('[data-formula]').map(input => [input.dataset.formula, +input.value]));
      values.depth /= 100; values.value /= 100; values.freshness /= 100;
      try { await saveAi({ rating_formula: values }); refresh(); toast('Score formula saved'); }
      catch (err) { toast(err.message, true); }
      break;
    }
    case 'formula-reset':
      try { await saveAi({ rating_formula: {} }); refresh(); toast('Default formula restored'); }
      catch (err) { toast(err.message, true); }
      break;
    case 'ai-save-routes': {
      try {
        const body = aiRoutesBody(), embed = body.tasks.embed, previous = selectedMediaProvider(S.data.ai, 'embed');
        if (embed && embed !== previous && embed !== 'local' && !await confirmEstimate('/api/embed/estimate?provider=' + encodeURIComponent(embed), 'Build the meaning index with ' + (S.data.ai.providers[embed]?.label || embed))) {
          body.tasks.embed = previous;   // keep the index where it is; save the rest
        }
        await saveAi(body);
        if (body.tasks.embed && body.tasks.embed !== previous) {   // a new index provider builds its index (the old one is kept)
          const r = await api('/api/embed/switch', { provider: body.tasks.embed });
          S.data.ai = r.ai; refresh();
          if (r.job) await queued({ after: [], ...r.job }, 'Meaning index build'); else toast('Saved. An index update is already running');
        } else { refresh(); toast('Saved'); }
      } catch (err) { toast(err.message, true); }
      break;
    }
    case 'ai-clear-key': try { await saveAi({ edit: el.dataset.p, config: { api_key: '__clear__' } }); refresh(); toast('Key removed'); } catch (err) { toast(err.message, true); } break;
    case 'ai-test': {
      const k = el.dataset.p, st = () => $('#ai-status');
      try {
        await saveAi(aiEditorBody(k)); refresh();
        st().innerHTML = '<span class="spin"></span>Testing…';
        await api('/api/ai/test', { provider: k });
        st().innerHTML = '✓ The model answered';
      } catch (err) { if (st()) st().innerHTML = `<span class="warn-text">${esc(err.message)}</span>`; }
      break;
    }
    case 'ai-models': {
      const k = el.dataset.p;
      try {
        await saveAi(aiEditorBody(k));
        const r = await api('/api/ai/models', { provider: k });
        S.ai.models[k] = r.models; refresh();
        toast(r.models.length ? `${plural(r.models.length, 'model')} found. Pick one in the Model box.` : 'The server lists no models', !r.models.length);
      } catch (err) { toast(err.message, true); }
      break;
    }
    case 'add-cat': {
      if (S.addingCat) {
        const input = $('#new-cat-name');
        input?.focus();
        input?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        break;
      }
      if (S.data?.taxonomy) Object.assign(S.data.taxonomy, collectTax());
      S.addingCat = true;
      refresh();
      const input = $('#new-cat-name');
      input?.focus();
      input?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      break;
    }
    case 'save-add-cat':
      await saveNewCategory();
      break;
    case 'cancel-add-cat':
      S.addingCat = false;
      refresh();
      $('[data-act="add-cat"]')?.focus();
      break;
    case 'adopt-cat': { const t = collectTax(); t[el.dataset.name] = []; try { await saveTax(t); refresh(); } catch (err) { toast(err.message, true); } break; }
    case 'reclassify':
      try { await saveTax(collectTax()); const r = await api('/api/reclassify', {}); await load(); toast(`Saved. ${plural(r.changed, 'video')} moved.`); } catch (err) { toast(err.message, true); }
      break;
    case 'del-cat': {
      const n = el.dataset.name;
      if (!confirm(`Delete "${n}"? Its videos go back to automatic sorting. No videos are deleted.`)) break;
      try { if ($$('.kw').length) await saveTax(collectTax()); await api('/api/categories/delete', { name: n }); await load(); toast('Category deleted'); } catch (err) { toast(err.message, true); }
      break;
    }
  }
});
document.addEventListener('change', async e => {
  if (e.target.matches?.('[data-ai-route], [data-media-task]')) refreshHelpers();
  const el = e.target, ch = el.dataset.change;
  if (ch === 'lib') {
    S.lib = +el.value; store.set('ytkb.lib', S.lib);
    S.about.current = undefined;   // unsaved interests belong to the library that was shown
    S.addingCat = false;
    Object.assign(S.view, { cat: '', src: '' }); S.sel.clear(); S.ask.video = null; S.search.res = null;
    S.knowledge.list = null; S.knowledge.pages = {};
    await load(); if (route() === 'search' && S.search.q) runSearch();
    toast(S.lib ? `Library: ${libName(S.lib)}` : 'All libraries'); return;
  }
  if (ch === 'theme') { S.theme = el.value; store.set('ytkb.theme', S.theme); applyTheme(); toast('Theme saved'); return; }
  if (ch === 'show-descriptions') {
    try { S.data.ai = await saveAi({ show_descriptions: el.checked }); renderView(); if (S.drawer) renderDrawer(); toast('Saved'); }
    catch (err) { el.checked = !el.checked; toast(err.message, true); }
    return;
  }
  if (ch === 'speed') {
    S.speed = +el.value; store.set('ytkb.speed', S.speed);
    renderBar(); refresh(); if (S.drawer) renderDrawer();
    return;
  }
  switch (ch) {
    case 'v-sort': S.view.sort = el.value; renderList(); break;
    case 'v-cat': setView({ cat: el.value }); break;
    case 'v-src': setView({ src: el.value }); break;
    case 'bulk-cat': {
      let c = el.value;
      if (!c) break;
      if (c === '__new') c = askName();
      if (!c) { el.value = ''; break; }
      const ids = [...S.sel]; S.sel.clear();
      await update(ids, { category: c }); toast(`${plural(ids.length, 'video')} moved to ${c}`);
      break;
    }
    case 'd-cat': {
      let c = el.value;
      if (c === '__new') c = askName();
      if (!c) { el.value = byId(el.dataset.id)?.category; break; }
      update([el.dataset.id], { category: c });
      break;
    }
    case 'p-mins': S.plan.mins = Math.max(5, +el.value || 60); store.set('ytkb.plan.mins', S.plan.mins); refresh(); break;
    case 'p-days': S.plan.days = Math.min(7, Math.max(1, +el.value || 5)); store.set('ytkb.plan.days', S.plan.days); refresh(); break;
    case 's-mins': S.session.mins = Math.max(5, +el.value || 45); refresh(); break;
    case 's-cat': S.session.cat = el.value; refresh(); break;
    case 's-order': S.session.order = el.value; refresh(); break;
    case 's-meaning': S.search.meaning = el.checked; store.set('ytkb.meaning', el.checked ? '1' : '0'); refresh(); runSearch(); break;
    case 'ask-cat': S.ask.cat = el.value; break;
    case 'ask-src': S.ask.src = el.value; break;
    case 'ask-done': S.ask.done = el.checked; break;
    case 'i-access': S.ingest.access = el.value; store.set('ytkb.access', el.value); refresh(); break;
    case 'i-lib': S.ingest.lib = +el.value; break;
    case 'pl-lib':
      try { await api('/api/playlists/library', { id: el.dataset.id, library: +el.value }); await load(); toast(`Moved to ${libName(+el.value)}`); }
      catch (err) { toast(err.message, true); await load(); }
      break;
    case 'lib-rename':
      try { S.data.libraries = (await api('/api/libraries', { action: 'rename', id: +el.dataset.lib, name: el.value })).libraries; renderBar(); toast('Renamed'); }
      catch (err) { toast(err.message, true); await load(); }
      break;
    case 'has-visuals': {
      const v = byId(el.dataset.id);
      try { await api('/api/videos/visuals', { ids: [el.dataset.id], value: el.checked }); if (v) v.has_visuals = el.checked ? 1 : 0; S.visPlan = null; renderDrawer(); }
      catch (err) { el.checked = !el.checked; toast(err.message, true); }
      break;
    }
    case 'visual-cat':
      try { S.data.visual_categories = (await api('/api/visuals/settings', { category: el.dataset.name, on: el.checked })).visual_categories; S.visPlan = null; }
      catch (err) { el.checked = !el.checked; toast(err.message, true); }
      break;
    case 'vision-model': case 'visual-height':
      try { await saveAi(ch === 'vision-model' ? { vision_model: el.value } : { visual_height: +el.value }); S.visPlan = null; toast('Saved'); }
      catch (err) { toast(err.message, true); }
      break;
    case 'asr-model':
      try { await saveAi({ asr_model: el.value }); S.asrPlan = null; toast('Speech-to-text model saved'); } catch (err) { toast(err.message, true); }
      break;
    case 'local-only':
      try { await saveAi({ local_only: el.checked }); refresh(); toast(el.checked ? 'Local only: cloud AIs are blocked' : 'Cloud AIs allowed again'); }
      catch (err) { el.checked = !el.checked; toast(err.message, true); }
      break;
    case 'transcripts': case 'details-after-sync': case 'use-descriptions':
      try {
        const r = await api('/api/details/settings', ch === 'transcripts' ? { category: el.dataset.name, on: el.checked }
          : ch === 'use-descriptions' ? { use_descriptions: el.checked } : { after_sync: el.checked });
        S.data.transcripts = r.transcripts; S.data.details_after_sync = r.details_after_sync; S.data.use_descriptions = r.use_descriptions;
      } catch (err) { el.checked = !el.checked; toast(err.message, true); }
      break;
    case 'preset':
      try { const r = await api('/api/presets', { name: el.dataset.name, preset: el.checked }); S.data.ai.presets = r.presets; }
      catch (err) { el.checked = !el.checked; toast(err.message, true); }
      break;
    case 'rename': {
      const old = el.dataset.old, n = el.value.trim();
      if (!n || n === old) { el.value = old; break; }
      try { await saveTax(collectTax()); await api('/api/categories/rename', { old, new: n }); if (S.view.cat === old) S.view.cat = n; await load(); toast('Renamed'); }
      catch (err) { el.value = old; toast(err.message, true); }
      break;
    }
  }
});
let qT, sT, aT;
document.addEventListener('input', e => {
  const el = e.target;
  if (el.id === 'ai-look') {
    S.about.look = el.value;
    const draft = $('[data-act="interests-draft"]'); if (draft) draft.disabled = !el.value.trim() || S.about.recording || S.about.drafting;
    return;
  }
  if (el.id === 'ai-interests') { S.about.current = el.value; }
  if (el.dataset.formula) {
    const f = ratingFormula(Object.fromEntries($$('[data-formula]').map(input => [input.dataset.formula, +input.value / (['depth','value','freshness'].includes(input.dataset.formula) ? 100 : 1)])));
    const example = $('#formula-example'); if (example) example.textContent = `Example: ${formulaExample(f)}`;
    return;
  }
  if (el.id === 'ai-interests') {
    const cnt = $('#ai-interests-count');
    if (cnt) {
      cnt.textContent = `${el.value.length} / 2000`;
      cnt.classList.toggle('warn-text', el.value.length > 2000);
    }
    const errEl = $('#ai-interests-err');
    if (errEl && errEl.style.display !== 'none') {
      errEl.textContent = '';
      errEl.style.display = 'none';
    }
    return;
  }
  if (el.id === 'new-cat-name') {
    const errEl = $('#new-cat-err');
    if (errEl && errEl.style.display !== 'none') {
      errEl.textContent = '';
      errEl.style.display = 'none';
    }
    return;
  }
  if (el.id === 'sq') {
    S.search.q = el.value; S.search.limit = 50; clearTimeout(sT);
    sT = setTimeout(() => { history.replaceState(null, '', `#/search?q=${encodeURIComponent(S.search.q)}`); runSearch(); }, 250);
    return;
  }
  if (el.id === 'aq') { S.ask.q = el.value; return; }
  if (el.id === 'ahq') { S.ask.hq = el.value; clearTimeout(aT); aT = setTimeout(loadAskHistory, 200); return; }
  if (el.id === 'q') { S.view.q = el.value; clearTimeout(qT); qT = setTimeout(() => { S.limit = 150; renderList(); }, 120); }
  else if (el.id === 'tools-links') S.tools.links = el.value;
  else if (el.matches('#tools-browser')) S.tools.browser = el.checked;
  else if (el.dataset.change === 'tools-range') {
    const i = +el.dataset.index, range = S.tools.ranges[i] || (S.tools.ranges[i] = {});
    range[el.dataset.bound] = Math.max(1, +el.value || 1);
    const item = S.tools.items[i];
    if (item) {
      const from = fromValue(range.from, item.videos.length, 1), to = Math.max(from, fromValue(range.to, item.videos.length, item.videos.length));
      const selected = item.videos.slice(from - 1, to), seconds = selected.reduce((sum, v) => sum + (v.duration_seconds || 0), 0);
      const summary = $(`#tools-range-summary-${i}`); if (summary) summary.textContent = `Range: videos ${from}–${to} · ${fmt(seconds)}`;
      const table = $(`#tools-speeds-${i}`); if (table) [...table.querySelectorAll('tbody td')].forEach((td, j) => { if (j) td.textContent = fmt(seconds / SPEEDS[j - 1]); });
      const known = selected.filter(v => S.data.videos.some(saved => saved.video_id === v.video_id));
      const knownTime = known.reduce((sum, v) => sum + (v.duration_seconds || 0), 0);
      const lib = $(`#tools-library-${i}`); if (lib) lib.textContent = `${known.length} already in your library (${fmt(knownTime)} total)`;
    }
  }
  else if (el.dataset.change === 'tools-plan') {
    const key = el.dataset.key, value = +el.value;
    S.tools[key] = key === 'days' ? Math.min(7, Math.max(1, value || 5)) : key === 'mins' ? Math.max(5, value || 60) : Math.max(0.25, value || 1);
    store.set(`ytkb.tools.${key}`, S.tools[key]);
    if (route() === 'tools') {
      const unique = new Map(); S.tools.items.filter(item => item.videos).forEach(item => item.videos.forEach(v => { if (!unique.has(v.video_id)) unique.set(v.video_id, v); }));
      const total = [...unique.values()].reduce((sum, v) => sum + (v.duration_seconds || 0), 0);
      const days = Math.ceil(total / Math.max(0.25, S.tools.speed) / (Math.max(5, S.tools.mins) * 60));
      const summary = $('#tools-plan-summary'); if (summary) summary.textContent = `Done in ${plural(days, 'day')} · by ${studyFinish(days, S.tools.days)}`;
    }
  }
  else if (el.dataset.input === 'i-url') S.ingest.url = el.value;
  else if (el.dataset.input === 'i-cookies') { S.ingest.cookies = el.value; store.set('ytkb.cookies', el.value); }
  else if (el.dataset.input === 'ai-key') S.ai.key = el.value;
});
document.addEventListener('focusout', e => { if (e.target.id === 'd-notes') saveNotes(); });
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && S.about.recording) { e.preventDefault(); stopAboutRecording(); return; }
  const typing = e.target?.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName);
  if (S.shortcutsOpen) {
    if (e.key === 'Escape') { e.preventDefault(); S.shortcutsOpen = false; renderShortcutHelp(); (S.shortcutReturnFocus || $('#help-btn'))?.focus(); return; }
    if (e.key === 'Tab') {
      const nodes = [...$('#shortcut-root').querySelectorAll('button, a[href], [tabindex]:not([tabindex="-1"])')];
      if (nodes.length) { const first = nodes[0], last = nodes[nodes.length - 1]; if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); } }
    }
    return;
  }
  if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'g') { S.goAt = Date.now(); return; }
  if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey && S.goAt && Date.now() - S.goAt < 1500 && e.key === '?') { S.goAt = 0; e.preventDefault(); location.hash = '#/help'; return; }
  if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey && S.goAt && Date.now() - S.goAt < 1500) {
    const routes = { h: '#/home', l: '#/', k: '#/knowledge', y: '#/sources', c: '#/categories', t: '#/tools', s: '#/settings' };
    const dest = routes[e.key.toLowerCase()]; S.goAt = 0;
    if (dest) { e.preventDefault(); location.hash = dest; return; }
  }
  if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey && e.key === '?') { e.preventDefault(); S.shortcutsOpen = true; S.shortcutReturnFocus = document.activeElement; renderShortcutHelp(); return; }
  if (e.key === 'Escape' && S.ask.open && route() !== 'ask') { S.ask.open = false; renderAskPop(); (S.ask.returnFocus || $('#ask-fab'))?.focus(); return; }
  if (e.key === 'Escape' && S.addingCat && route() === 'categories') {
    e.preventDefault(); S.addingCat = false; refresh(); $('[data-act="add-cat"]')?.focus(); return;
  }
  if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'a' && route() !== 'ask') {
    e.preventDefault(); S.ask.returnFocus = $('#ask-fab'); S.ask.open = true; renderAskPop(); $('#aq')?.focus({ preventScroll: true }); return;
  }
  if (e.key === 'Escape' && S.drawer) { closeDrawer(); return; }
  if (typing || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
  if (e.key === '/') { e.preventDefault(); if (route() !== 'search') location.hash = '#/search'; setTimeout(() => $('#sq')?.focus(), 50); }
  if (S.drawer && (e.key.toLowerCase() === 'j' || e.key.toLowerCase() === 'k')) $(`[data-act="step"][data-dir="${e.key.toLowerCase() === 'j' ? 1 : -1}"]`)?.click();

});
document.addEventListener('click', e => {
  if (!S.shortcutsOpen) return;
  if (e.target.closest('[data-act="shortcut-close"]') || (e.target.classList.contains('shortcut-scrim') && !e.target.closest('.shortcut-dialog'))) {
    S.shortcutsOpen = false; renderShortcutHelp(); (S.shortcutReturnFocus || $('#help-btn'))?.focus();
  }
});
// href would change the hash route, so only move focus
document.querySelector('.skip-link').addEventListener('click', e => { e.preventDefault(); $('#view')?.focus(); });
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.id === 'aq' && !e.isComposing) { e.preventDefault(); runAsk(); }
  if (e.key === 'Enter' && e.target.id === 'new-cat-name' && !e.isComposing) { e.preventDefault(); saveNewCategory(); }
});
addEventListener('hashchange', () => { if (!location.hash || location.hash === '#') { location.replace('#/home'); return; } if (S.about.recording) stopAboutRecording(); if (S.data) { S.sel.clear(); S.addingCat = false; renderBar(); renderView(); renderAskPop(); scrollTo(0, 0); } });

applyTheme();
load();
