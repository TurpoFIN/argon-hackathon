// StormSim SF — Argon live agent drawer (self-contained, no deps).
import './agentPanel.css';

export interface AgentPanel {
  el: HTMLElement;
  toggle(open?: boolean): void;
  isOpen(): boolean;
  connect(jobId: string): void;
  startJob(model?: string): Promise<void>;
  pushEvent(ev: any): void;
}

type ConnState = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'offline' | 'done' | 'error';

interface RunPoint {
  runId?: string;
  n: number;
  damage: number;
  cost?: number;
  label?: string;
}

const DEFAULT_MODEL = 'gemini-4-argon';
const MAX_ITEMS = 400;
const EVENT_TYPES = ['thought', 'code', 'tool_call', 'run_result', 'status', 'text', 'phase', 'done', 'final', 'awaiting_approval', 'approval', 'run_error', 'error', 'job_start', 'baseline', 'managed_verdict', 'managed_step', 'submit'];

// ---------- helpers ----------

function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  cls?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function pick(ev: any, keys: string[]): any {
  for (const k of keys) {
    const v = ev?.[k];
    if (v !== undefined && v !== null && v !== '') return v;
  }
  return undefined;
}

function num(v: any): number | undefined {
  if (v === undefined || v === null || v === '') return undefined;
  const n = typeof v === 'number' ? v : parseFloat(String(v).replace(/[$,_\s]/g, ''));
  return Number.isFinite(n) ? n : undefined;
}

export function fmtUsd(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(a >= 1e10 ? 1 : 2).replace(/\.?0+$/, '')}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(1)}K`;
  return `${sign}$${a.toFixed(0)}`;
}

function fmtArgs(args: any): string {
  if (args === undefined || args === null) return '';
  let s: string;
  if (typeof args === 'string') {
    s = args;
  } else if (typeof args === 'object' && !Array.isArray(args)) {
    s = Object.entries(args)
      .map(([k, v]) => `${k}=${typeof v === 'string' ? `'${v}'` : JSON.stringify(v)}`)
      .join(', ');
  } else {
    s = JSON.stringify(args);
  }
  s = s.replace(/\s+/g, ' ');
  return s.length > 64 ? s.slice(0, 61) + '…' : s;
}

// ---------- tiny Python highlighter ----------

const PY_KEYWORDS = new Set(
  ('False None True and as assert async await break class continue def del elif else except finally ' +
    'for from global if import in is lambda nonlocal not or pass raise return try while with yield match case')
    .split(' '),
);
const PY_BUILTINS = new Set(
  ('abs all any bool dict enumerate filter float int len list map max min print range round set sorted ' +
    'str sum tuple zip isinstance getattr setattr hasattr type super open reversed self cls')
    .split(' '),
);

const PY_TOKEN = new RegExp(
  [
    '(#[^\\n]*)', // 1 comment
    '((?:[rRbBfFuU]{1,2})?(?:"""[\\s\\S]*?(?:"""|$)|\'\'\'[\\s\\S]*?(?:\'\'\'|$)))', // 2 triple string
    '((?:[rRbBfFuU]{1,2})?(?:"(?:\\\\.|[^"\\\\\\n])*"?|\'(?:\\\\.|[^\'\\\\\\n])*\'?))', // 3 string
    '(^[ \\t]*@[A-Za-z_][\\w.]*)', // 4 decorator
    '(\\b(?:0[xX][0-9a-fA-F_]+|\\d[\\d_]*(?:\\.\\d[\\d_]*)?(?:[eE][+-]?\\d+)?j?)\\b|\\.\\d+\\b)', // 5 number
    '([A-Za-z_]\\w*)', // 6 identifier
  ].join('|'),
  'gm',
);

export function highlightPython(src: string): string {
  let out = '';
  let last = 0;
  PY_TOKEN.lastIndex = 0;
  let m: RegExpExecArray | null;
  let prevDef = false;
  while ((m = PY_TOKEN.exec(src))) {
    if (m[0] === '') {
      PY_TOKEN.lastIndex++;
      continue;
    }
    out += esc(src.slice(last, m.index));
    last = m.index + m[0].length;
    const t = esc(m[0]);
    if (m[1]) out += `<span class="ap-c">${t}</span>`;
    else if (m[2] || m[3]) out += `<span class="ap-s">${t}</span>`;
    else if (m[4]) out += `<span class="ap-d">${t}</span>`;
    else if (m[5]) out += `<span class="ap-n">${t}</span>`;
    else if (m[6]) {
      const id = m[6];
      if (PY_KEYWORDS.has(id)) {
        out += `<span class="ap-k">${t}</span>`;
        prevDef = id === 'def' || id === 'class';
        continue;
      }
      if (prevDef) out += `<span class="ap-fn">${t}</span>`;
      else if (PY_BUILTINS.has(id)) out += `<span class="ap-b">${t}</span>`;
      else if (/^\s*\(/.test(src.slice(last, last + 8))) {
        const isMethod = src[m.index - 1] === '.';
        out += `<span class="${isMethod ? 'ap-m' : 'ap-f'}">${t}</span>`;
      } else out += t;
    } else out += t;
    prevDef = false;
  }
  out += esc(src.slice(last));
  return out;
}

// ---------- mock script ----------

function mockScript(): any[] {
  const plans = [
    {
      thought:
        'Baseline first. El Niño AR 2026 brings ~9 in. of rain over 72 h plus a 1.4 m surge at king tide. The Embarcadero, Mission Creek and the Bayview lowlands flood first; Islais Creek culverts back up. Start with cheap sponge capacity before hard walls.',
      code: `# iteration 1: sponge the low basins
import stormsim as ss

def plan(city: ss.City) -> None:
    # permeable paving across the Mission / SoMa drainage basin
    for block in city.blocks(district="SoMa", elevation_lt=4.0):
        city.permeable(block, fraction=0.35)
    city.storage("Islais Creek", volume_m3=180_000)
`,
      damage: 412.3e6,
      cost: 9.8e6,
      label: 'sponge basins',
    },
    {
      thought:
        'Damage concentrated at the waterfront: surge overtops the Embarcadero seawall between Pier 14 and Pier 40. A 1.2 m deployable wall there should cut the largest single loss term.',
      code: `# iteration 2: hold the seawall
def plan(city):
    for block in city.blocks(district="SoMa", elevation_lt=4.0):
        city.permeable(block, fraction=0.35)
    city.storage("Islais Creek", volume_m3=180_000)
    city.wall(start="Pier 14", end="Pier 40", height_m=1.2, kind="deployable")
`,
      damage: 287.6e6,
      cost: 22.4e6,
      label: '+ Embarcadero wall',
    },
    {
      thought:
        'Wall works but Mission Creek still floods from inland runoff: the combined sewer surcharges at 16th & Folsom. A diversion tunnel to the Bay outfall relieves it without more surface works.',
      code: `# iteration 3: divert Mission runoff
@ss.cached
def plan(city):
    base(city)
    city.wall(start="Pier 14", end="Pier 40", height_m=1.2, kind="deployable")
    tunnel = city.tunnel(
        start=(37.7652, -122.4152),   # 16th & Folsom
        end="Mission Creek outfall",
        diameter_m=4.5,
    )
    tunnel.set_pump(capacity_m3s=38.0)
`,
      damage: 171.9e6,
      cost: 39.7e6,
      label: '+ Mission tunnel',
    },
    {
      thought:
        'Remaining loss: wind damage to soft-story buildings in the Marina and Richmond (gusts 70 mph). Bracing is cheap per unit; target pre-1978 wood frames only to stay under the $50M budget.',
      code: `# iteration 4: brace soft-story stock
def plan(city):
    base(city); waterfront(city); mission_tunnel(city)
    targets = [b for b in city.buildings(district=["Marina", "Richmond"])
               if b.soft_story and b.year_built < 1978]
    for b in targets[:420]:
        city.brace(b, level="retrofit")   # ~$11k each
    print(f"braced {len(targets[:420])} buildings")
`,
      damage: 118.4e6,
      cost: 46.3e6,
      label: '+ soft-story bracing',
    },
    {
      thought:
        'Fine-tune: shift 60k m³ of storage to Bayview where Yosemite Slough still overtops, and drop permeable fraction where soils are clay. Plan cost $48.1M, inside budget.',
      code: `# iteration 5: rebalance storage, final
def plan(city):
    waterfront(city); mission_tunnel(city); bracing(city)
    for block in city.blocks(district="SoMa", elevation_lt=4.0):
        frac = 0.20 if block.soil == "clay" else 0.35
        city.permeable(block, fraction=frac)
    city.storage("Islais Creek", volume_m3=120_000)
    city.storage("Yosemite Slough", volume_m3=60_000)
    return city.summary()
`,
      damage: 96.2e6,
      cost: 48.1e6,
      label: 'rebalanced storage',
    },
  ];
  const evs: any[] = [{ type: 'status', state: 'running', model: DEFAULT_MODEL }];
  plans.forEach((p, i) => {
    const it = i + 1;
    evs.push({ type: 'thought', text: p.thought, iteration: it });
    evs.push({ type: 'code', code: p.code, iteration: it });
    evs.push({ type: 'tool_call', name: 'run_storm', args: { storm: 'elnino_ar_2026', plan: `plan_v${it}` }, iteration: it });
    evs.push({
      type: 'run_result',
      run_id: `mock-run-${it}`,
      damage_usd: p.damage,
      plan_cost_usd: p.cost,
      label: p.label,
      iteration: it,
    });
  });
  evs.push({ type: 'thought', text: 'Damage down 77% from baseline within budget. Recommending run 5 for approval.' });
  evs.push({ type: 'status', state: 'done' });
  return evs;
}

// ---------- panel ----------

export function createAgentPanel(opts: {
  onApprove?: (info: { jobId?: string; runId?: string }) => void;
  onRunResult?: (ev: any) => void;
} = {}): AgentPanel {
  const mock = (() => {
    try {
      return new URLSearchParams(location.search).get('mockagent') === '1';
    } catch {
      return false;
    }
  })();

  let open = false;
  let jobId: string | undefined;
  let es: EventSource | null = null;
  let conn: ConnState = 'idle';
  let model = DEFAULT_MODEL;
  let iteration = 0;
  let stick = true;
  let approved = false;
  let approving = false;
  const runs: RunPoint[] = [];
  const seen = new Set<string>();

  // DOM
  const root = h('aside', 'ap-root');
  root.setAttribute('aria-label', 'Argon agent stream');

  const tab = h('button', 'ap-tab');
  tab.type = 'button';
  tab.innerHTML = '<span class="ap-tab-dot"></span><span>ARGON</span>';
  tab.title = 'Show Argon agent';

  const head = h('header', 'ap-head');
  const titleRow = h('div', 'ap-title-row');
  const dot = h('span', 'ap-dot');
  const title = h('span', 'ap-title');
  title.innerHTML = 'ARGON <span class="ap-sep">·</span> live';
  const mockTag = h('span', 'ap-mock', 'mock');
  const connEl = h('span', 'ap-conn');
  const closeBtn = h('button', 'ap-close', '×');
  closeBtn.type = 'button';
  closeBtn.title = 'Close';
  titleRow.append(dot, title, mockTag, connEl, closeBtn);
  const metaRow = h('div', 'ap-meta');
  const modelEl = h('span', 'ap-model');
  const iterEl = h('span', 'ap-iter');
  metaRow.append(modelEl, iterEl);
  head.append(titleRow, metaRow);

  const spark = h('div', 'ap-spark');
  const stream = h('div', 'ap-stream');
  const empty = h('div', 'ap-empty', 'No agent run yet. Start Argon to watch it plan.');
  stream.append(empty);

  const foot = h('footer', 'ap-foot');
  const runBtn = h('button', 'ap-btn ap-btn-ghost', 'Run Argon');
  runBtn.type = 'button';
  const approveBtn = h('button', 'ap-btn ap-btn-primary', 'Approve plan');
  approveBtn.type = 'button';
  approveBtn.disabled = true;
  foot.append(runBtn, approveBtn);

  root.append(tab, head, spark, stream, foot);
  document.body.appendChild(root);

  if (!mock) mockTag.style.display = 'none';

  // ----- header / footer state -----
  function renderHeader(): void {
    const streaming = conn === 'live' || conn === 'connecting';
    root.classList.toggle('ap-streaming', streaming);
    dot.className = 'ap-dot' + (streaming ? ' ap-dot-on' : conn === 'reconnecting' || conn === 'error' ? ' ap-dot-warn' : '');
    const label: Record<ConnState, string> = {
      idle: 'idle',
      connecting: 'connecting…',
      live: '',
      reconnecting: 'reconnecting…',
      offline: 'offline',
      done: 'done',
      error: 'error',
    };
    connEl.textContent = label[conn];
    connEl.className = 'ap-conn' + (conn === 'reconnecting' || conn === 'offline' || conn === 'error' ? ' ap-conn-warn' : '');
    modelEl.textContent = model;
    iterEl.textContent = iteration ? `iter ${iteration}` : '';
    runBtn.style.display = jobId || mock ? 'none' : '';
  }

  function bestRun(): RunPoint | undefined {
    let b: RunPoint | undefined;
    for (const r of runs) if (!b || r.damage < b.damage) b = r;
    return b;
  }

  function renderFooter(): void {
    if (approved) {
      approveBtn.disabled = true;
      approveBtn.classList.add('ap-approved');
      approveBtn.textContent = 'Approved ✓ · report generating';
      return;
    }
    const b = bestRun();
    approveBtn.disabled = !b || approving;
    approveBtn.textContent = b ? `Approve plan · Run ${b.n}` : 'Approve plan';
  }

  // ----- sparkline -----
  function renderSpark(): void {
    if (!runs.length) {
      spark.classList.remove('ap-spark-on');
      spark.innerHTML = '';
      return;
    }
    spark.classList.add('ap-spark-on');
    const W = 340, H = 54, padL = 6, padR = 62, padT = 8, padB = 10;
    const vals = runs.map((r) => r.damage);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < 1) { hi += 1; lo -= 1; }
    const span = hi - lo;
    lo = Math.max(0, lo - span * 0.08);
    hi = hi + span * 0.08;
    const n = runs.length;
    const x = (i: number) => padL + (n === 1 ? (W - padL - padR) / 2 : (i * (W - padL - padR)) / (n - 1));
    const y = (v: number) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
    const pts = runs.map((r, i) => [x(i), y(r.damage)] as const);
    const line = pts.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join(' ');
    const area = `${line} L${pts[pts.length - 1][0].toFixed(1)},${H - padB} L${pts[0][0].toFixed(1)},${H - padB} Z`;
    const lastP = pts[pts.length - 1];
    const lastV = vals[vals.length - 1];
    const first = vals[0];
    const pct = first > 0 ? Math.round((1 - lastV / first) * 100) : 0;
    const dots = pts
      .map(([px, py], i) => `<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="${i === n - 1 ? 3 : 2}" class="${i === n - 1 ? 'ap-sp-last' : 'ap-sp-dot'}"/>`)
      .join('');
    spark.innerHTML = `
      <div class="ap-spark-cap"><span>DAMAGE / RUN</span>${n > 1 && pct > 0 ? `<span class="ap-good">▼ ${pct}% vs run 1</span>` : ''}</div>
      <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" aria-label="Damage per run">
        <defs><linearGradient id="apSpGrad" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stop-color="#5fe1ff" stop-opacity="0.28"/><stop offset="1" stop-color="#5fe1ff" stop-opacity="0"/>
        </linearGradient></defs>
        <line x1="${padL}" x2="${W - padR}" y1="${H - padB}" y2="${H - padB}" class="ap-sp-base"/>
        ${n > 1 ? `<path d="${area}" fill="url(#apSpGrad)"/><path d="${line}" class="ap-sp-line"/>` : ''}
        ${dots}
        <text x="${(lastP[0] + 7).toFixed(1)}" y="${(lastP[1] + 4).toFixed(1)}" class="ap-sp-label">${esc(fmtUsd(lastV))}</text>
      </svg>`;
  }

  // ----- stream -----
  function append(node: HTMLElement): void {
    if (empty.parentNode) empty.remove();
    stream.appendChild(node);
    while (stream.childElementCount > MAX_ITEMS) stream.firstElementChild?.remove();
    if (stick) stream.scrollTop = stream.scrollHeight;
  }

  stream.addEventListener('scroll', () => {
    stick = stream.scrollTop + stream.clientHeight >= stream.scrollHeight - 24;
  });

  function addThought(text: string): void {
    const d = h('div', 'ap-thought', text);
    if (text.length > 240 || text.split('\n').length > 4) {
      d.classList.add('ap-clamp');
      d.title = 'Click to expand';
      d.addEventListener('click', () => d.classList.toggle('ap-expanded'));
    }
    append(d);
  }

  function addCode(code: string): void {
    const wrap = h('div', 'ap-code');
    const pre = h('pre');
    const c = h('code');
    c.innerHTML = highlightPython(code.replace(/\s+$/, ''));
    pre.append(c);
    wrap.append(pre);
    append(wrap);
  }

  function addTool(name: string, args: any): void {
    const d = h('div', 'ap-tool');
    d.innerHTML = `<span class="ap-tool-arrow">▸</span><span class="ap-tool-name">${esc(name)}</span><span class="ap-tool-args">(${esc(fmtArgs(args))})</span>`;
    append(d);
  }

  function addRun(ev: any): void {
    const damage = num(pick(ev, ['damage_usd', 'total_usd', 'damage', 'total_damage_usd']));
    const cost = num(pick(ev, ['plan_cost_usd', 'cost_usd', 'plan_cost']));
    const it = num(ev.iteration);
    const prev = runs[runs.length - 1];
    const n = it ?? runs.length + 1;
    const runId = ev.run_id != null ? String(ev.run_id) : ev.id != null ? String(ev.id) : undefined;
    if (damage !== undefined) runs.push({ runId, n, damage, cost, label: ev.label });

    const d = h('div', 'ap-run');
    let html = `<span class="ap-run-n">Run ${n}</span>`;
    html += `<span class="ap-run-sep">·</span><span class="ap-run-k">damage</span> <span class="ap-run-v">${damage !== undefined ? esc(fmtUsd(damage)) : '—'}</span>`;
    if (cost !== undefined) html += `<span class="ap-run-sep">·</span><span class="ap-run-k">plan</span> <span class="ap-run-v">${esc(fmtUsd(cost))}</span>`;
    if (prev && damage !== undefined) {
      const delta = damage - prev.damage;
      if (Math.abs(delta) >= 1) {
        const lower = delta < 0;
        html += `<span class="ap-delta ${lower ? 'ap-good' : 'ap-warn'}">${lower ? '▼' : '▲'} ${esc(fmtUsd(Math.abs(delta)))}</span>`;
      }
    }
    if (typeof ev.bcr === 'number') html += `<span class="ap-run-sep">·</span><span class="ap-run-k">BCR</span> <span class="ap-run-v">${ev.bcr.toFixed(1)}</span>`;
    if (ev.label) html += `<div class="ap-run-label">${esc(String(ev.label))}</div>`;
    d.innerHTML = html;
    append(d);
    renderSpark();
    renderFooter();
    try {
      opts.onRunResult?.(ev);
    } catch (e) {
      console.warn('[agentPanel] onRunResult failed', e);
    }
  }

  function addStatus(state: string, ev: any): void {
    const s = state.toLowerCase();
    if (s === 'running' || s === 'started' || s === 'live') {
      conn = 'live';
    } else if (s === 'done' || s === 'complete' || s === 'completed' || s === 'finished') {
      conn = 'done';
      es?.close();
      es = null;
    } else if (s === 'error' || s === 'failed') {
      conn = 'error';
      es?.close();
      es = null;
    }
    const msg = pick(ev, ['message', 'text', 'detail']);
    const line = h('div', 'ap-status' + (s === 'error' || s === 'failed' ? ' ap-warn' : ''));
    line.textContent = `— ${state}${msg && msg !== state ? ` · ${msg}` : ''}`;
    append(line);
    renderHeader();
  }

  function pushEvent(raw: any): void {
    let ev = raw;
    if (typeof ev === 'string') {
      try {
        ev = JSON.parse(ev);
      } catch {
        ev = { type: 'thought', text: ev };
      }
    }
    if (!ev || typeof ev !== 'object') return;
    const key = ev.seq ?? ev.event_id;
    if (key != null) {
      const k = String(key);
      if (seen.has(k)) return;
      seen.add(k);
    }
    if (ev.model) model = String(ev.model);
    const it = num(ev.iteration);
    if (it !== undefined && it > iteration) iteration = it;
    const type = String(ev.type ?? ev.event ?? '').toLowerCase();
    if (ev.summary && typeof ev.summary === 'object') {
      const sm = ev.summary;
      if (ev.damage_usd == null && sm.damage_total_usd != null) ev.damage_usd = sm.damage_total_usd;
      if (ev.plan_cost_usd == null && sm.plan_cost_usd != null) ev.plan_cost_usd = sm.plan_cost_usd;
      if (ev.bcr == null && sm.bcr != null) ev.bcr = sm.bcr;
      if (ev.label == null && sm.label) ev.label = sm.label;
    }
    if (ev.iteration == null && typeof ev.run_index === 'number') ev.iteration = ev.run_index;
    if (['plan_object', 'code_delta', 'tool_result', 'tool_call_start', 'managed_delta', 'datapack', 'document', 'leaderboard_entry'].includes(type)) return;
    switch (type) {
      case 'text': {
        const t = pick(ev, ['text', 'content']);
        if (t != null && String(t).trim()) addThought(String(t));
        break;
      }
      case 'phase': addStatus(String(ev.title || ev.phase || 'phase'), {}); break;
      case 'job_start': addStatus('running', { text: ev.model || '' }); break;
      case 'baseline': addStatus('baseline', { text: ev.summary?.damage_total_usd ? 'no-plan damage ' + fmtUsd(ev.summary.damage_total_usd) : '' }); break;
      case 'awaiting_approval': addStatus('awaiting planner approval', {}); break;
      case 'approval': addStatus(ev.approved ? 'plan approved' : 'plan rejected', { text: ev.approver || '' }); break;
      case 'run_error': addStatus('run error', { text: String(ev.error || '').slice(0, 160) }); break;
      case 'final': addStatus('final report ready', {}); break;
      case 'done': addStatus('done', { text: ev.status || '' }); break;
      case 'managed_verdict': case 'managed_step': case 'submit': { const t = pick(ev, ['text', 'verdict', 'title', 'status']); addStatus(type.replace('_', ' '), { text: t ? String(t).slice(0, 160) : '' }); break; }
      case 'thought':
      case 'thinking': {
        const t = pick(ev, ['text', 'content', 'message', 'summary']);
        if (t != null) addThought(String(t));
        break;
      }
      case 'code': {
        const c = pick(ev, ['code', 'text', 'content']);
        if (c != null) addCode(String(c));
        break;
      }
      case 'tool_call':
      case 'tool': {
        addTool(String(pick(ev, ['name', 'tool']) ?? 'tool'), ev.args ?? ev.arguments);
        break;
      }
      case 'run_result':
      case 'result': {
        if (it === undefined) iteration = Math.max(iteration, runs.length + 1);
        addRun(ev);
        break;
      }
      case 'status': {
        addStatus(String(pick(ev, ['state', 'status', 'text']) ?? 'status'), ev);
        break;
      }
      default: {
        const t = pick(ev, ['text', 'content', 'message', 'summary']);
        if (t != null) addThought(String(t));
      }
    }
    renderHeader();
  }

  // ----- connection -----
  function connect(id: string): void {
    es?.close();
    jobId = id;
    conn = 'connecting';
    renderHeader();
    const src = new EventSource(`/api/jobs/${encodeURIComponent(id)}/events`);
    es = src;
    const handle = (name?: string) => (e: MessageEvent) => {
      if (es !== src) return;
      if (conn !== 'live' && conn !== 'done' && conn !== 'error') {
        conn = 'live';
      }
      let data: any;
      try {
        data = JSON.parse(e.data);
      } catch {
        data = { text: String(e.data) };
      }
      if (data && typeof data === 'object' && name && !data.type) data.type = name;
      pushEvent(data);
    };
    src.onopen = () => {
      if (es !== src) return;
      if (conn !== 'done' && conn !== 'error') conn = 'live';
      renderHeader();
    };
    src.onerror = () => {
      if (es !== src) return;
      if (conn === 'done' || conn === 'error') return;
      conn = src.readyState === EventSource.CLOSED ? 'offline' : 'reconnecting';
      renderHeader();
    };
    src.addEventListener('message', handle() as EventListener);
    for (const t of EVENT_TYPES) src.addEventListener(t, handle(t) as EventListener);
  }

  async function startJob(m?: string): Promise<void> {
    if (m) model = m;
    runBtn.disabled = true;
    runBtn.textContent = 'Starting…';
    try {
      const res = await fetch('/api/optimize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: m ?? DEFAULT_MODEL, budget_usd: 50_000_000, storm: 'elnino_ar_2026' }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      const id = body?.job_id ?? body?.id;
      if (!id) throw new Error('no job id in response');
      connect(String(id));
    } catch (e) {
      const line = h('div', 'ap-status ap-warn', `— could not start Argon: ${(e as Error).message}`);
      append(line);
      conn = 'error';
      renderHeader();
    } finally {
      runBtn.disabled = false;
      runBtn.textContent = 'Run Argon';
    }
  }

  async function approve(): Promise<void> {
    const b = bestRun();
    if (!b || approved || approving) return;
    approving = true;
    approveBtn.textContent = 'Approving…';
    approveBtn.disabled = true;
    if (jobId) {
      try {
        await fetch(`/api/jobs/${encodeURIComponent(jobId)}/approve`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approved: true, approver: 'planner', note: 'approved on stage', run_id: b.runId }),
        });
      } catch {
        /* ignore */
      }
    }
    approving = false;
    approved = true;
    renderFooter();
    try {
      opts.onApprove?.({ jobId, runId: b.runId });
    } catch (e) {
      console.warn('[agentPanel] onApprove failed', e);
    }
  }

  // ----- open / close -----
  function toggle(v?: boolean): void {
    open = v ?? !open;
    root.classList.toggle('ap-open', open);
    tab.setAttribute('aria-expanded', String(open));
    if (open && stick) requestAnimationFrame(() => (stream.scrollTop = stream.scrollHeight));
  }

  tab.addEventListener('click', () => toggle());
  closeBtn.addEventListener('click', () => toggle(false));
  runBtn.addEventListener('click', () => void startJob());
  approveBtn.addEventListener('click', () => void approve());

  renderHeader();
  renderFooter();
  renderSpark();

  const panel: AgentPanel = { el: root, toggle, isOpen: () => open, connect, startJob, pushEvent };

  if (mock) {
    const evs = mockScript();
    conn = 'connecting';
    renderHeader();
    let i = 0;
    setTimeout(() => toggle(true), 300);
    const timer = setInterval(() => {
      if (i >= evs.length) {
        clearInterval(timer);
        return;
      }
      pushEvent(evs[i++]);
    }, 1200);
  }

  return panel;
}
