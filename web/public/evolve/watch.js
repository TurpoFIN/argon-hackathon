// Additive visual-verification panel. It owns its tiny poller so evolve.js can
// keep its candidate/detail rendering independent of the watch schema.
const watchQ = new URLSearchParams(location.search);
const watchEsc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const watchCss = document.createElement('style');
watchCss.textContent = `
  #card-watch { grid-column: span 2; min-height: 190px; }
  #watchRows { display: grid; gap: 9px; overflow: auto; max-height: 280px; }
  .watch-row { border-left: 2px solid #ff7ab6; padding: 7px 10px; background: rgba(255,122,182,.045); }
  .watch-head { display:flex; gap:8px; align-items:baseline; color:var(--dim); font-size:11px; }
  .watch-head b { color:var(--text); font-family:var(--mono); }
  .watch-crit { margin-top:3px; color:var(--text); }
  .watch-fail { color:var(--warn); font-size:11px; margin-top:3px; }
  .watch-dir { color:var(--good); font-size:11px; margin:3px 0 0 14px; }
  .watch-applied { color:var(--accent); font-size:11px; margin-top:4px; }
  .watch-thumb { display:block; width:min(360px,100%); max-height:150px; object-fit:cover; object-position:top; margin-top:6px; border:1px solid var(--line-2); border-radius:5px; }
  .watch-video { display:block; width:min(360px,100%); max-height:150px; margin-top:6px; border:1px solid var(--line-2); border-radius:5px; }
  .watch-media { display:flex; gap:8px; margin-top:5px; }
  .watch-media a { color:var(--accent); font-size:11px; }
`;
document.head.appendChild(watchCss);

const grid = document.querySelector('.grid-evo');
if (grid) {
  const card = document.createElement('div');
  card.id = 'card-watch';
  card.className = 'card';
  card.innerHTML = '<div class="card-h"><h2>Argon visual verification</h2><span class="hint">replay + flood depth/damage stills feed the next generation</span></div><div id="watchRows"><div class="empty-note">waiting for the first replay critique…</div></div>';
  grid.appendChild(card);
}

async function watchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}
function mediaHtml(media) {
  const links = [];
  if (media?.video) links.push(`<a href="${watchEsc(media.video)}" target="_blank" rel="noreferrer">▶ replay</a>`);
  (media?.images || []).forEach((p, i) => links.push(`<a href="${watchEsc(p)}" target="_blank" rel="noreferrer">▧ still ${i + 1}</a>`));
  const visual = media?.images?.[0] ? `<img class="watch-thumb" src="${watchEsc(media.images[0])}" alt="flood depth and damaged buildings observed by Argon" loading="lazy">` : '';
  const video = media?.video ? `<video class="watch-video" src="${watchEsc(media.video)}" controls muted preload="metadata"></video>` : '';
  return links.length ? `${visual}${video}<div class="watch-media">${links.join('')}</div>` : '';
}
function renderWatch(st) {
  const rows = document.querySelector('#watchRows');
  if (!rows) return;
  const watch = st?.watch || [];
  if (!watch.length) { rows.innerHTML = '<div class="empty-note">no visual critique recorded yet</div>'; return; }
  rows.innerHTML = watch.slice(-6).reverse().map((w) => {
    const failures = (w.failures || []).map((f) => `<div class="watch-fail">${watchEsc(f.where)} · ${watchEsc(f.cause)}${f.evidence ? ` — ${watchEsc(f.evidence)}` : ''}</div>`).join('');
    const dirs = (w.directives || []).map((d) => `<li class="watch-dir">${watchEsc(d)}</li>`).join('');
    const applied = (st.candidates || []).filter((c) => c.gen === w.applied_to_gen && String(c.directive || '').startsWith('Visual verification directive'));
    const appliedHtml = applied.length ? `<div class="watch-applied">Next generation change: ${applied.map((c) => `${watchEsc(c.id)} · ${watchEsc(c.note || c.directive)}`).join(' | ')}</div>` : '';
    return `<div class="watch-row"><div class="watch-head"><b>${watchEsc(w.cand)}</b><span>generation ${watchEsc(w.gen)}</span><span>${watchEsc(w.status || '')}</span></div><div class="watch-crit">${watchEsc(w.critique || '')}</div>${failures}${dirs ? `<ul style="margin:0;padding:0">${dirs}</ul>` : ''}${appliedHtml}${mediaHtml(w.media)}</div>`;
  }).join('');
}

let watchJob = watchQ.get('job');
async function watchPoll() {
  try {
    if (!watchJob) {
      const list = await watchJson('/api/evolve');
      const jobs = Array.isArray(list) ? list : (list.jobs || []);
      watchJob = jobs[0]?.id;
    }
    if (watchJob) renderWatch(await watchJson(`/api/evolve/${encodeURIComponent(watchJob)}`));
  } catch (_) { /* the main evolve view owns the error state */ }
  setTimeout(watchPoll, 2000);
}
watchPoll();
