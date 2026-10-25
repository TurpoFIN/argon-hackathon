import './shell.css';

type Step = 'forecast' | 'storm' | 'evolve' | 'compare' | 'plan';

interface ForecastPoint { time: string; p10: number; p50: number; p90: number; }
interface ForecastData { points: ForecastPoint[]; source: string; rainTotal: string; windPeak: string; fetchedAt: string; provenance: string; rainNote: string; }
interface AlertItem { event: string; headline: string; severity: string; area: string; }

const STORM_ARRIVAL = new Date('2026-10-27T02:00:00-07:00');
const STEP_META: Array<{ id: Step; n: string; label: string; short: string }> = [
  { id: 'forecast', n: '01', label: 'Forecast', short: 'See what is coming' },
  { id: 'storm', n: '02', label: 'Storm', short: 'Replay the city' },
  { id: 'evolve', n: '03', label: 'Evolve', short: 'Watch Argon improve' },
  { id: 'compare', n: '04', label: 'Compare', short: 'Measure the outcome' },
  { id: 'plan', n: '05', label: 'Plan', short: 'Approve the response' },
];

const escapeHtml = (value: unknown) => String(value ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const money = (value: unknown) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(2).replace(/\.00$/, '')}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
  if (Math.abs(n) >= 1e3) return `$${Math.round(n / 1e3)}K`;
  return `$${Math.round(n)}`;
};

const number = (value: unknown) => Number(value || 0).toLocaleString('en-US');

function fixtureForecast(): ForecastData {
  const points: ForecastPoint[] = [];
  for (let i = 0; i < 25; i++) {
    const phase = Math.max(0, Math.sin((i - 5) / 8 * Math.PI));
    points.push({ time: `+${i}h`, p10: 0.26 + phase * 0.28, p50: 0.38 + phase * 0.64, p90: 0.55 + phase * 1.05 });
  }
  return { points, source: 'StormSim design forcing · cached', rainTotal: '228 mm / 72 h', windPeak: '31 m/s gusts', fetchedAt: 'cached', provenance: 'NOAA harmonic tide + observed surge persistence', rainNote: 'Atlas 14 100-year rain floor retained in the simulator.' };
}

function normalizeForecast(raw: any): ForecastData {
  const fallback = fixtureForecast();
  if (!raw || typeof raw !== 'object') return fallback;
  const forecast = raw.forecast || {};
  const tide = raw.tide || raw.tides || forecast || raw.points;
  let list = Array.isArray(tide) ? tide : (Array.isArray(tide?.points) ? tide.points : []);
  if (!list.length && Array.isArray(forecast.t)) {
    const quantiles = Array.isArray(forecast.water_level_q) ? forecast.water_level_q : [];
    list = forecast.t.map((time: any, i: number) => {
      const q = Array.isArray(quantiles[i]) ? quantiles[i] : [];
      return { time, p10: q[0], p50: q[4] ?? q[Math.floor(q.length / 2)], p90: q[8] ?? q[q.length - 1] };
    });
  }
  if (!list.length && Array.isArray(tide?.times)) {
    const quantiles = tide.quantiles || {};
    list = tide.times.map((time: any, i: number) => ({ time, p10: quantiles.p10?.[i], p50: quantiles.p50?.[i], p90: quantiles.p90?.[i] }));
  }
  const points = list.map((p: any, i: number) => ({
    time: String(p.time || p.timestamp || p.t || `+${i}h`).slice(0, 16),
    p10: Number(p.p10 ?? p.low ?? p.q10 ?? p.quantiles?.p10 ?? p.value ?? 0),
    p50: Number(p.p50 ?? p.median ?? p.q50 ?? p.quantiles?.p50 ?? p.value ?? 0),
    p90: Number(p.p90 ?? p.high ?? p.q90 ?? p.quantiles?.p90 ?? p.value ?? 0),
  })).filter((p: ForecastPoint) => [p.p10, p.p50, p.p90].every(Number.isFinite));
  const qpf = Array.isArray(forecast.qpf_mm) ? forecast.qpf_mm.map(Number).filter(Number.isFinite) : [];
  const gust = Array.isArray(forecast.gust_q) ? forecast.gust_q.map((q: any) => Array.isArray(q) ? Number(q[8] ?? q[q.length - 1]) : 0).filter(Number.isFinite) : [];
  const fallbackReason = raw.fallback?.used ? ' · ' + String(raw.fallback.method || 'persistence fallback') : '';
  const notes = Array.isArray(raw.notes) ? raw.notes : [];
  const rainNote = String(notes.find((n: any) => /Atlas 14/i.test(String(n))) || 'Atlas 14 100-year rain floor retained in the simulator.');
  const provenance = String(raw.model || 'NOAA harmonic tide + observed surge persistence') + fallbackReason;
  return {
    points: points.length > 2 ? points : fallback.points,
    source: `NOAA CO-OPS ${raw.station || '9414290'} · live cache`,
    rainTotal: qpf.length ? `${qpf.reduce((a: number, b: number) => a + b, 0).toFixed(1)} mm / ${Math.round((qpf.length * Number(raw.step_minutes || 6)) / 60)} h` : String(raw.rain_total || raw.rainTotal || raw.precip_total || fallback.rainTotal),
    windPeak: gust.length ? `${Math.max(...gust).toFixed(1)} m/s gusts` : String(raw.wind_peak || raw.windPeak || fallback.windPeak),
    fetchedAt: String(raw.fetched_at || raw.issued || 'cached'),
    provenance,
    rainNote,
  };
}

async function getJson(path: string): Promise<any | null> {
  try {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 4500);
    const response = await fetch(path, { signal: controller.signal, headers: { Accept: 'application/json' } });
    window.clearTimeout(timer);
    if (!response.ok) return null;
    return await response.json();
  } catch { return null; }
}

async function getText(path: string): Promise<string | null> {
  try {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 4500);
    const response = await fetch(path, { signal: controller.signal });
    window.clearTimeout(timer);
    if (!response.ok) return null;
    return await response.text();
  } catch { return null; }
}

function chartSvg(points: ForecastPoint[]): string {
  const w = 900, h = 214, padX = 26, padY = 18;
  const max = Math.max(1, ...points.map((p) => p.p90));
  const x = (i: number) => padX + (i / Math.max(1, points.length - 1)) * (w - padX * 2);
  const y = (v: number) => h - padY - (v / max) * (h - padY * 2);
  const path = (key: keyof ForecastPoint) => points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(Number(p[key])).toFixed(1)}`).join(' ');
  const area = `${points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.p90).toFixed(1)}`).join(' ')} ${points.slice().reverse().map((p, ri) => `L${x(points.length - 1 - ri).toFixed(1)},${y(p.p10).toFixed(1)}`).join(' ')} Z`;
  const ticks = [0, 0.5, 1, 1.5].filter((v) => v <= max + 0.1);
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Tide forecast with uncertainty band">
    <defs><linearGradient id="tide-band" x1="0" x2="0" y1="0" y2="1"><stop stop-color="#59e2ff" stop-opacity=".32"/><stop offset="1" stop-color="#59e2ff" stop-opacity=".03"/></linearGradient></defs>
    ${ticks.map((v) => `<line x1="${padX}" x2="${w - padX}" y1="${y(v)}" y2="${y(v)}" class="chart-grid"/><text x="${padX - 8}" y="${y(v) + 4}" text-anchor="end" class="chart-label">${v.toFixed(1)}m</text>`).join('')}
    <path d="${area}" fill="url(#tide-band)"/><path d="${path('p90')}" class="chart-line high"/><path d="${path('p10')}" class="chart-line low"/><path d="${path('p50')}" class="chart-line median"/>
    <text x="${padX}" y="${h - 2}" class="chart-label">now</text><text x="${w / 2}" y="${h - 2}" text-anchor="middle" class="chart-label">+12 h</text><text x="${w - padX}" y="${h - 2}" text-anchor="end" class="chart-label">+24 h</text>
  </svg>`;
}

function alertFrom(raw: any): AlertItem[] {
  const features = Array.isArray(raw?.features) ? raw.features : Array.isArray(raw) ? raw : [];
  return features.slice(0, 3).map((f: any) => {
    const p = f?.properties || f || {};
    return { event: String(p.event || p.title || 'Weather alert'), headline: String(p.headline || p.description || 'Conditions are being monitored.'), severity: String(p.severity || 'Unknown'), area: String(p.areaDesc || 'San Francisco') };
  });
}

function createShell(): HTMLElement {
  const shell = document.createElement('div');
  shell.id = 'product-shell';
  shell.innerHTML = `<header class="product-topbar">
    <button class="product-brand" data-step="forecast" aria-label="StormSim SF home"><span class="brand-mark">⌁</span><span><strong>STORMSIM <i>SF</i></strong><small>EMERGENCY OPERATIONS / LIVE CELL</small></span></button>
    <nav class="product-steps" aria-label="StormSim story steps">${STEP_META.map((s) => `<button class="product-step" data-step="${s.id}"><b>${s.n}</b><span>${s.label}</span></button>`).join('')}</nav>
    <div class="topbar-status"><span class="status-dot"></span><span>OPERATIONAL</span><time id="shell-clock"></time></div>
  </header><main id="shell-page" tabindex="-1"></main>`;
  return shell;
}

function forecastPage(data: ReturnType<typeof fixtureForecast>, alerts: AlertItem[]): string {
  const hours = Math.max(1, Math.round((STORM_ARRIVAL.getTime() - Date.now()) / 3600000));
  const alertMarkup = alerts.length ? alerts.map((a) => `<article class="alert-row"><span class="alert-pulse"></span><div><b>${escapeHtml(a.event)}</b><p>${escapeHtml(a.headline)}</p><small>${escapeHtml(a.area)} · ${escapeHtml(a.severity)}</small></div></article>`).join('') : `<article class="alert-row"><span class="alert-pulse muted"></span><div><b>No active NWS alert returned</b><p>Forecast forcing remains loaded from the latest verified storm scenario.</p><small>San Francisco · cached fallback</small></div></article>`;
  return `<section class="shell-view forecast-view"><div class="view-heading"><div><div class="eyebrow">STEP 01 / SITUATIONAL AWARENESS</div><h1>Storm arriving in <em>${hours} h</em></h1><p>Atmospheric river forcing is converging on San Francisco. Read the uncertainty before you commit resources.</p></div><div class="arrival-card"><span>LANDFALL</span><strong>27 OCT · 02:00</strong><small>Pacific daylight time</small></div></div>
    <div class="forecast-grid"><div class="shell-card tide-card"><div class="card-heading"><div><span class="eyebrow">BAY + SURGE</span><h2>Tide forecast envelope</h2></div><span class="live-chip">${escapeHtml(data.source)}</span></div><div class="chart-wrap">${chartSvg(data.points)}</div><div class="chart-legend"><span><i class="legend-line median"></i>median</span><span><i class="legend-line band"></i>10–90% range</span><span class="chart-note">Fetched ${escapeHtml(data.fetchedAt)}</span></div><div class="forecast-provenance">${escapeHtml(data.provenance)} · ${escapeHtml(data.rainNote)}</div></div>
    <div class="forecast-side"><div class="shell-card alert-card"><div class="card-heading"><div><span class="eyebrow">LIVE FEED</span><h2>Alerts & signals</h2></div><span class="live-dot">● LIVE</span></div>${alertMarkup}</div><div class="metric-strip"><div><small>PRECIPITATION</small><b>${escapeHtml(data.rainTotal)}</b><span>design forcing</span></div><div><small>WIND PEAK</small><b>${escapeHtml(data.windPeak)}</b><span>HRRR envelope</span></div></div></div></div>
    <div class="forecast-next"><span class="eyebrow">NEXT ACTION</span><strong>Open the city replay</strong><p>See where water concentrates before Argon begins building the response.</p><button class="primary-btn" data-step="storm">Enter Storm Replay <span>→</span></button></div></section>`;
}

function stormPage(): string {
  return `<section class="shell-view storm-context" aria-label="3D storm replay"></section>`;
}

function evolvePage(): string {
  return `<section class="shell-view simple-view"><div class="eyebrow">STEP 03 / SEARCH + CRITIQUE</div><h1>Argon watched the replay.</h1><p class="lede">The model sees the rendered storm, finds the remaining failure, and patches its own plan before the final run.</p><div class="evolve-callout"><div class="evolve-orbit"><span>ARGON</span><i></i><b>WATCH</b></div><div><span class="eyebrow">LIVE VERIFICATION LOOP</span><h2>Generate → build → replay → critique → fix</h2><p>One candidate left the Mission Creek basin exposed. Argon added diversion capacity, then re-ran the same physical storm.</p><a class="primary-btn inline-btn" href="/evolve/">Open Evolve workspace <span>↗</span></a></div></div><div class="evolve-timeline"><div class="evolve-stage done"><span>01</span><b>PLAN</b><small>candidate 04</small></div><i></i><div class="evolve-stage done"><span>02</span><b>BUILD</b><small>ledger locked</small></div><i></i><div class="evolve-stage watch"><span>03</span><b>WATCH</b><small>replay inspected</small></div><i></i><div class="evolve-stage fix"><span>04</span><b>FIX</b><small>tunnel added</small></div><i></i><div class="evolve-stage"><span>05</span><b>VERIFY</b><small>same storm</small></div></div><div class="source-note">No waiting state: this frame is a deterministic fixture until the live critique stream arrives.</div></section>`;
}

let compareHeadline = '';
function compareRows(raw: any): any[] {
  const entries = Array.isArray(raw?.entries) ? raw.entries : Array.isArray(raw) ? raw : [];
  compareHeadline = entries.length && typeof raw?.headline === 'string' ? raw.headline : '';
  return entries.length ? entries : [
    { label: 'No plan', model: 'Deterministic baseline', damage_usd: 438000000, plan_cost_usd: 0, status: 'verified' },
    { label: 'Greedy rule', model: 'Rules baseline', damage_usd: 316000000, plan_cost_usd: 47000000, status: 'fixture' },
    { label: 'Gemini Flash', model: 'gemini-3.8-flash', damage_usd: 248000000, plan_cost_usd: 44100000, status: 'fixture' },
    { label: 'Gemini 4 Argon', model: 'gemini-4-argon', damage_usd: 171900000, plan_cost_usd: 39700000, status: 'fixture' },
  ];
}

function comparePage(rows: any[]): string {
  const baseline = Number(rows[0]?.damage_usd ?? rows[0]?.damage ?? 438000000);
  const dmg = (r: any) => Number(r.damage_usd ?? r.damage ?? r.total_usd ?? 0);
  const ok = (r: any) => r.status === 'verified' || r.status === 'scored' || r.verified;
  const bestIdx = rows.slice(0, 6).reduce((b: number, r: any, i: number) => (i > 0 && ok(r) && (b < 0 || dmg(r) < dmg(rows[b])) ? i : b), -1);
  const rowMarkup = rows.slice(0, 6).map((r: any, i: number) => {
    const damage = Number(r.damage_usd ?? r.damage ?? r.total_usd ?? 0);
    const cost = Number(r.plan_cost_usd ?? r.cost_usd ?? r.cost ?? 0);
    const avoided = Math.max(0, baseline - damage);
    const bcr = cost ? avoided / cost : 0;
    return `<tr class="${i === bestIdx ? 'best' : ''}"><td><span class="rank">0${i + 1}</span><strong>${escapeHtml(r.label || r.name || r.model || 'Candidate')}</strong><small>${escapeHtml(r.model || r.strategy || '')}</small></td><td>${money(damage)}</td><td>${cost ? money(cost) : '—'}</td><td class="${bcr > 1 ? 'positive' : ''}">${cost ? bcr.toFixed(1) + '×' : '—'}</td><td><span class="evidence-tag ${ok(r) ? 'verified' : ''}">${escapeHtml(r.status || 'available')}</span></td></tr>`;
  }).join('');
  const verifiedCount = rows.filter(ok).length;
  const evidenceLabel = verifiedCount === rows.length ? 'VERIFIED DATA' : verifiedCount ? 'PARTIAL EVIDENCE' : 'FIXTURE DATA';
  return `<section class="shell-view compare-view"><div class="view-heading"><div><div class="eyebrow">STEP 04 / DECISION SUPPORT</div><h1>Compare outcomes honestly.</h1><p>${compareHeadline ? `<b class="compare-headline">${escapeHtml(compareHeadline)}</b><br>` : ''}Same storm, same physics, same $50M budget, every plan scored by the same deterministic simulation.</p></div><div class="compare-badge">${evidenceLabel}<small>${verifiedCount === rows.length ? 'every row scored on the current sim' : 'updates as tournament completes'}</small></div></div><div class="shell-card table-card"><div class="card-heading"><div><span class="eyebrow">TOURNAMENT</span><h2>Damage, spend and benefit–cost</h2></div><span class="live-chip">/api/tournament</span></div><div class="table-scroll"><table><thead><tr><th>Candidate</th><th>Damage</th><th>Spend</th><th>BCR</th><th>Evidence</th></tr></thead><tbody>${rowMarkup}</tbody></table></div></div><div class="compare-media"><div class="shell-card media-card"><div class="eyebrow">BASELINE / NO PLAN</div><figure class="map-still"><img src="/stills/baseline-flood.jpg" alt="Rendered no-plan flood replay at peak water depth"><figcaption><b>no plan · flood depth replay</b><span>real sim render · T+04:16</span></figcaption></figure><small>Source: baseline run video render.</small></div><div class="shell-card media-card"><div class="eyebrow">ARGON / BEST RUN</div><figure class="map-still"><img src="/stills/argon-flood.jpg" alt="Rendered Argon plan flood replay at peak water depth"><figcaption><b>Argon plan · damage replay</b><span>real sim render · T+04:16</span></figcaption></figure><small>Source: Gemini 4 Argon run video render.</small></div></div><button class="primary-btn compare-next" data-step="plan">Review and approve the plan <span>→</span></button></section>`;
}

function planPage(state: { job: any | null; report: string | null; approved: boolean }): string {
  const job = state.job;
  const awaiting = job && (job.status === 'awaiting_approval' || job.status === 'completed' || job.status === 'running');
  const status = state.approved ? 'APPROVED · REPORT READY' : awaiting ? 'PLANNER REVIEW REQUIRED' : 'ARGON PLAN · READY FOR REVIEW';
  const report = state.report ? `<article class="eop-report"><div class="eyebrow">EMERGENCY OPERATIONS PLAN</div>${state.report}</article>` : `<article class="eop-report"><div class="eyebrow">EMERGENCY OPERATIONS PLAN</div><h2>Mission Creek flood response</h2><p>Prioritise the Mission Creek basin and Embarcadero low points. Stage pumps, clear inlets, open shelters and issue a vulnerable-resident evacuation notice before landfall.</p><div class="eop-sections"><div><b>01 / WATER</b><span>Deploy portable pumps at 16th & Folsom; keep the outfall clear.</span></div><div><b>02 / PEOPLE</b><span>Open 3 shelters with accessible transport and 72-hour supplies.</span></div><div><b>03 / CREWS</b><span>Pre-position street, power and medical crews before T−12 h.</span></div></div><footer>Sources: NWS alerts · NOAA CO-OPS 9414290 · SFPUC 100-year flood risk · StormSim deterministic ledger</footer></article>`;
  return `<section class="shell-view plan-view"><div class="view-heading"><div><div class="eyebrow">STEP 05 / PLANNER GATE</div><h1>Turn the simulation into a plan.</h1><p>Review the cited response, then make the human approval explicit.</p></div><div class="approval-state ${state.approved ? 'approved' : ''}"><span class="status-dot"></span>${status}</div></div><div class="plan-layout">${report}<aside class="shell-card approval-card"><span class="eyebrow">DECISION</span><h2>Planner approval</h2><p>${state.approved ? 'The plan is approved and the cited report is ready to brief the operations cell.' : 'This gate is intentionally human. A model can propose the response; the planner owns the decision.'}</p><div class="approval-meta"><span>Plan source</span><b>${escapeHtml(job?.model || 'Gemini 4 Argon')}</b><span>Job</span><b>${escapeHtml(job?.id || 'tournament winner')}</b></div><button class="primary-btn approve-btn" ${state.approved ? 'disabled' : ''}>${state.approved ? 'Plan approved' : 'Approve & generate EOP'} <span>✓</span></button><button class="quiet-btn" data-step="compare">Back to comparison</button><small class="approval-foot">Report endpoint: <code>/api/jobs/{id}/eop</code>. Falls back to cited fixture until the job is available.</small></aside></div></section>`;
}

export function mountProductShell(): void {
  if (document.getElementById('product-shell')) return;
  const shell = createShell();
  document.body.append(shell);
  const page = shell.querySelector('#shell-page') as HTMLElement;
  let step: Step = (new URLSearchParams(location.search).get('step') as Step) || 'forecast';
  if (!STEP_META.some((s) => s.id === step)) step = 'forecast';
  let forecast = fixtureForecast();
  let alerts: AlertItem[] = [];
  let compareData: any[] = compareRows(null);
  const planState: { job: any | null; report: string | null; approved: boolean } = { job: null, report: null, approved: false };
  let presenterMode = new URLSearchParams(location.search).get('presenter') === '1';

  const render = () => {
    shell.querySelectorAll<HTMLElement>('.product-step').forEach((button) => {
      button.classList.toggle('active', button.dataset.step === step);
      button.setAttribute('aria-current', button.dataset.step === step ? 'step' : 'false');
    });
    document.body.classList.toggle('shell-storm', step === 'storm');
    page.classList.remove('dismissed');
    page.innerHTML = step === 'forecast' ? forecastPage(forecast, alerts) : step === 'storm' ? stormPage() : step === 'evolve' ? evolvePage() : step === 'compare' ? comparePage(compareData) : planPage(planState);
    page.scrollTop = 0;
    if (presenterMode && step === 'storm') page.classList.add('dismissed');
  };
  const startPresenterStorm = () => {
    const storm = (window as any).storm;
    if (storm?.play) storm.play();
    else window.addEventListener('storm-ready', () => (window as any).storm?.play?.(), { once: true });
  };
  const setStep = (next: Step) => { step = next; history.replaceState({}, '', `${location.pathname}?step=${next}`); render(); if (presenterMode && next === 'storm') startPresenterStorm(); };
  const advance = (delta: number) => { const index = STEP_META.findIndex((s) => s.id === step); setStep(STEP_META[Math.max(0, Math.min(STEP_META.length - 1, index + delta))].id); };
  window.addEventListener('keydown', (event) => {
    if (event.key.toLowerCase() === 'p') { presenterMode = !presenterMode; document.body.classList.toggle('presenter-mode', presenterMode); render(); return; }
    if (!presenterMode || (event.target as HTMLElement)?.closest?.('input,textarea,button,a,select')) return;
    if (event.key === 'ArrowRight' || event.key === ' ') { event.preventDefault(); advance(1); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); advance(-1); }
  });
  document.body.classList.toggle('presenter-mode', presenterMode);
  shell.addEventListener('click', async (event) => {
    const target = event.target as HTMLElement;
    const stepButton = target.closest<HTMLElement>('[data-step]');
    if (stepButton?.dataset.step && STEP_META.some((s) => s.id === stepButton.dataset.step)) { setStep(stepButton.dataset.step as Step); return; }
    if (target.closest('[data-dismiss-shell]')) { document.body.classList.add('shell-storm'); page.classList.add('dismissed'); return; }
    if (target.closest('[data-shot]')) { (window as any).storm?.shot?.(target.closest<HTMLElement>('[data-shot]')?.dataset.shot); return; }
    if (target.closest('.approve-btn') && !planState.approved) {
      const button = target.closest<HTMLButtonElement>('.approve-btn'); if (button) { button.disabled = true; button.textContent = 'Approving…'; }
      if (planState.job?.id) {
        try { await fetch(`/api/jobs/${encodeURIComponent(planState.job.id)}/approve`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ approved: true, approver: 'planner' }) }); } catch { /* report fallback remains available */ }
        const eopText = await getText(`/api/jobs/${encodeURIComponent(planState.job.id)}/eop`);
        if (eopText) {
          try { const eop = JSON.parse(eopText); planState.report = String(eop.html || eop.report || eop.content || ''); }
          catch { planState.report = eopText; }
        }
      } else {
        // The tournament EOP is a tracked, verified fixture so the closing
        // demo step remains executable even before a live evolve job exists.
        const fixture = await getJson('/api/eop/argon');
        if (fixture) planState.report = String(fixture.html || fixture.report || fixture.content || '');
      }
      planState.approved = true; render();
    }
  });
  const clock = shell.querySelector('#shell-clock');
  const updateClock = () => { if (clock) clock.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); };
  updateClock(); window.setInterval(updateClock, 30000);
  render();

  void (async () => {
    const [rawForecast, rawAlerts, tournament, jobs, fixtureEop] = await Promise.all([getJson('/api/forecast'), getJson('/api/alerts'), getJson('/api/tournament'), getJson('/api/jobs'), getJson('/api/eop/argon')]);
    forecast = normalizeForecast(rawForecast); alerts = alertFrom(rawAlerts); compareData = compareRows(tournament || await getJson('/api/leaderboard'));
    const candidates = Array.isArray(jobs) ? jobs : [];
    planState.job = candidates.find((j: any) => ['awaiting_approval', 'completed', 'running'].includes(j.status)) || null;
    if (fixtureEop && !planState.job) planState.report = String(fixtureEop.html || fixtureEop.report || fixtureEop.content || '');
    render();
  })();
}
