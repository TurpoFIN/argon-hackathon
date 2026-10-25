"""Build the demo page for a prep-loop run: python -m loop.report data/prep-loop/<run>

Expects <run>/argon/argon.json and <run>/flash/flash.json (either may be missing or partial) and
writes <run>/index.html, which references the images and videos next to each log.
"""
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CSS = """
:root { color-scheme: light dark; --bg:#fafafa; --surface:#fdfdfd; --fg:#070707; --muted:#747474; --edge:rgba(0,0,0,.102);
  --c1:#006cff; --c2:#f2b200; --c7:#00c769; --fail:#b00a1d; --font:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
@media (prefers-color-scheme: dark) { :root { --bg:#020202; --surface:#161616; --fg:#eeeeee; --muted:#a4a4a4; --edge:rgba(255,255,255,.08); --fail:#ff9a92; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.45 var(--font); }
main { padding:24px 16px 48px; max-width:1500px; margin:0 auto; }
h1 { font-size:22px; font-weight:400; margin:0 0 4px; }
h2 { font-size:16px; font-weight:500; margin:32px 0 12px; }
.lede { color:var(--muted); margin:0 0 20px; max-width:900px; }
.bars { display:grid; gap:8px; max-width:900px; }
.bar { display:grid; grid-template-columns:220px 1fr 130px; gap:12px; align-items:center; }
.bar .track { height:14px; background:color-mix(in srgb, var(--fg) 6%, transparent); border-radius:3px; overflow:hidden; }
.bar .fill { height:100%; border-radius:3px; }
.bar .usd { text-align:right; font-variant-numeric:tabular-nums; }
.cols { display:grid; grid-template-columns:repeat(auto-fit, minmax(560px, 1fr)); gap:24px; }
.round { background:var(--surface); border-radius:8.4px; box-shadow:inset 0 0 0 .5px var(--edge); padding:12px; margin-bottom:16px; }
.round header { display:flex; justify-content:space-between; gap:12px; align-items:baseline; margin-bottom:8px; }
.round header b { font-weight:500; }
.num { font-variant-numeric:tabular-nums; white-space:nowrap; }
.better { color:var(--c7); } .worse { color:var(--fail); }
.round img { width:100%; border-radius:4.8px; display:block; cursor:pointer; }
.round video { width:100%; border-radius:4.8px; display:none; }
.round.playing img { display:none; } .round.playing video { display:block; }
.label { font-size:12px; color:var(--muted); margin:10px 0 2px; }
.text { margin:0; white-space:pre-wrap; }
.plan { font-size:12px; color:var(--muted); font-variant-numeric:tabular-nums; }
.error { color:var(--fail); }
footer { margin-top:40px; color:var(--muted); font-size:12px; max-width:1000px; }
footer a { color:inherit; }
#live { display:flex; gap:12px; align-items:center; margin:0 0 20px; }
#live[hidden] { display:none; }
#live button { height:32px; padding:0 14px; border-radius:9999px; border:0; background:var(--fg); color:var(--bg); font:500 13px var(--font); cursor:pointer; }
#live button:disabled { opacity:.5; cursor:default; }
#live span { color:var(--muted); }
.fans { display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:16px; max-width:1400px; }
.fan { margin:0; position:relative; background:var(--surface); border-radius:8.4px; box-shadow:inset 0 0 0 .5px var(--edge); padding:12px; }
.fan figcaption { font-size:13px; font-weight:500; margin-bottom:4px; }
.fan svg { width:100%; height:auto; display:block; }
.fan .grid { stroke:color-mix(in srgb, var(--fg) 8%, transparent); stroke-width:1; }
.fan .tick, .fan .lbl { font:11px var(--font); fill:var(--muted); }
.fan .band { fill:color-mix(in srgb, var(--c1) 20%, transparent); }
.fan .p50 { fill:none; stroke:var(--c1); stroke-width:2; stroke-linejoin:round; }
.fan .hist { fill:none; stroke:var(--muted); stroke-width:1.5; stroke-linejoin:round; }
.fan .ref { fill:none; stroke:var(--muted); stroke-width:1; stroke-dasharray:4 3; }
.fan .issue { stroke:var(--muted); stroke-width:1; stroke-dasharray:2 3; }
.fan .obs { fill:var(--surface); stroke:var(--fg); stroke-width:2; }
.fan .cross { stroke:var(--fg); stroke-width:1; opacity:.35; }
.fan .tip { position:absolute; top:28px; padding:6px 8px; border-radius:4.8px; background:var(--bg); box-shadow:inset 0 0 0 .5px var(--edge);
  font-size:12px; font-variant-numeric:tabular-nums; pointer-events:none; white-space:nowrap; }
.scen { border-collapse:collapse; margin:12px 0 0; font-size:13px; }
.scen th, .scen td { padding:4px 12px 4px 0; text-align:left; border-bottom:.5px solid var(--edge); }
.scen th { font-weight:500; color:var(--muted); }
.scen td.num { text-align:right; font-variant-numeric:tabular-nums; }
.thumbs { display:grid; grid-template-columns:repeat(3, 1fr); gap:6px; margin-top:6px; }
.thumbs figure { margin:0; } .thumbs img { width:100%; border-radius:3.6px; display:block; }
.thumbs figcaption { font-size:12px; color:var(--muted); font-variant-numeric:tabular-nums; }
@media (max-width: 640px) { .bar { grid-template-columns:1fr; } .cols { grid-template-columns:1fr; } }
"""

SCRIPT = """
document.querySelectorAll('.round img').forEach(img => img.addEventListener('click', () => {
  const card = img.closest('.round'); const video = card.querySelector('video');
  if (!video) return; card.classList.add('playing'); video.play();
  video.onended = () => card.classList.remove('playing');
}));
// Live controls appear only when this page is served by loop.serve.
const live = document.getElementById('live');
const run = location.pathname.split('/')[2];
async function poll() {
  let status;
  try { status = await (await fetch('/api/status', { cache: 'no-store' })).json(); } catch { return; }
  live.hidden = false;
  const mine = Object.entries(status.running).filter(([, r]) => r === run).map(([m]) => m);
  const busy = Object.keys(status.running).length > 0;
  live.querySelector('span').textContent = mine.length ? `Running live: ${mine.join(' and ')}. This page updates as each round finishes.` : busy ? 'Another run is in progress.' : '';
  live.querySelector('button').disabled = busy;
  if (mine.length) setTimeout(() => location.reload(), 8000);
}
live.querySelector('button').addEventListener('click', async (event) => {
  event.target.disabled = true;
  const response = await fetch('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"rounds": 3}' });
  const body = await response.json();
  if (body.page) location.href = body.page; else { event.target.disabled = false; alert(body.error || 'Could not start'); }
});
poll();
// Crosshair readout on the forecast charts.
document.querySelectorAll('.fan svg').forEach(svg => {
  const s = JSON.parse(svg.dataset.series), tip = svg.parentElement.querySelector('.tip'), cross = svg.querySelector('.cross');
  const minutes = s.t.map(t => Date.parse(t.replace(' ', 'T').replace(/Z?$/, 'Z')) / 60000);
  const fmt = v => v.toFixed(s.d) + ' ' + s.unit;
  svg.addEventListener('pointermove', e => {
    const box = svg.getBoundingClientRect(), vx = (e.clientX - box.left) / box.width * s.W;
    const t = s.x0 + (vx - s.L) / (s.W - s.L - s.R) * (s.x1 - s.x0);
    let i = 0; while (i < minutes.length - 1 && minutes[i + 1] <= t) i++;
    if (t < s.x0 || t > s.x1) { tip.hidden = true; return; }
    cross.setAttribute('x1', vx); cross.setAttribute('x2', vx);
    const k = i - s.h.length, when = s.t[i].replace('T', ' ').replace('Z', '') + ' UTC';
    tip.innerHTML = k < 0 ? `${when}<br>observed ${fmt(s.h[i])}` : `${when}<br>P90 ${fmt(s.p90[k])}<br>P50 ${fmt(s.p50[k])}<br>P10 ${fmt(s.p10[k])}`;
    tip.hidden = false; tip.style.left = Math.min(box.width - 150, Math.max(0, e.clientX - box.left + 12)) + 'px';
  });
  svg.addEventListener('pointerleave', () => { tip.hidden = true; cross.setAttribute('x1', -10); cross.setAttribute('x2', -10); });
});
"""


def _epoch(t):
    """UTC minutes since the epoch for '2023-01-05T17:54Z' or '2023-01-05 17:54'."""
    return datetime.strptime(t.replace(" ", "T").rstrip("Z"), "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp() / 60


def fan_chart(title, unit, history_t, history_v, forecast_t, quantiles, reference=None, observed=None, decimals=2):
    """One measure per chart: observed history (neutral ink), forecast P10–P90 band and P50 (blue),
    optional reference line (dashed neutral) and observed outcome (ring). Hover shows the values."""
    W, H, L, R, T, B = 680, 220, 52, 12, 14, 30
    ht, ft = [_epoch(t) for t in history_t], [_epoch(t) for t in forecast_t]
    p10, p50, p90 = [q[0] for q in quantiles], [q[4] for q in quantiles], [q[8] for q in quantiles]
    values = history_v + p10 + p90 + (reference or []) + ([observed[1]] if observed else [])
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    t0, t1 = ht[0], ft[-1]
    x = lambda t: L + (t - t0) / (t1 - t0) * (W - L - R)
    y = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    line = lambda ts, vs: " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in zip(ts, vs))
    band = line(ft, p90) + " " + line(ft[::-1], p10[::-1])
    ticks = [lo + (hi - lo) * k / 4 for k in range(5)]
    grid = "".join(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                   f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end" class="tick">{v:.{decimals}f}</text>' for v in ticks)
    days = sorted({int(t // 1440) for t in ht + ft})
    xticks = "".join(f'<text x="{x(d * 1440):.1f}" y="{H - 10}" text-anchor="middle" class="tick">'
                     f'{datetime.fromtimestamp(d * 86400, timezone.utc).strftime("%b %d")}</text>'
                     for d in days if t0 <= d * 1440 <= t1)
    ref = f'<polyline points="{line(ft, reference)}" class="ref"/>' if reference else ""
    obs = ""
    if observed:
        ox, oy = x(_epoch(observed[0])), y(observed[1])
        obs = (f'<circle cx="{ox:.1f}" cy="{oy:.1f}" r="5" class="obs"/>'
               f'<text x="{ox + 8:.1f}" y="{oy - 6:.1f}" class="lbl">observed peak {observed[1]:.{decimals}f} {unit}</text>')
    data = json.dumps({"t": history_t + forecast_t, "h": history_v, "p10": p10, "p50": p50, "p90": p90,
                       "x0": t0, "x1": t1, "L": L, "R": R, "W": W, "unit": unit, "d": decimals})
    issue = x(ft[0])
    return (f'<figure class="fan"><figcaption>{html.escape(title)}</figcaption>'
            f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(title)}" data-series=\'{html.escape(data)}\'>'
            f'{grid}{xticks}<polygon points="{band}" class="band"/>{ref}'
            f'<polyline points="{line(ht, history_v)}" class="hist"/><polyline points="{line(ft, p50)}" class="p50"/>'
            f'<line x1="{issue:.1f}" x2="{issue:.1f}" y1="{T}" y2="{H - B}" class="issue"/>'
            f'<text x="{issue + 4:.1f}" y="{T + 10}" class="lbl">forecast issued</text>'
            f'<text x="{x(ft[-1]) - 4:.1f}" y="{y(p90[-1]) - 6:.1f}" text-anchor="end" class="lbl band-lbl">P10–P90</text>'
            f'<text x="{x(ht[len(ht) // 3]):.1f}" y="{y(max(history_v)) - 6:.1f}" class="lbl">observed</text>'
            f'{obs}<line class="cross" y1="{T}" y2="{H - B}" x1="-10" x2="-10"/></svg>'
            f'<div class="tip" hidden></div></figure>')


def forecast_section(forcing, log):
    if not forcing:
        return ""
    history, forecast = forcing["history"], forcing["forecast"]
    observed = forcing.get("observed")
    water = fan_chart("Bay water level at the San Francisco tide gauge (m NAVD88)", "m", history["t"], history["water_level"],
                      forecast["t"], forecast["water_level_q"], reference=forecast["tide_prediction"],
                      observed=(observed["peak_time"], observed["peak_water_level_navd88_m"]) if observed else None)
    gust = fan_chart("Wind gust at the gauge (m/s)", "m/s", history["t"], history["gust"], forecast["t"], forecast["gust_q"],
                     observed=None, decimals=1)
    control = {s["name"]: s["total_usd"] for s in log["control"].get("scenarios", [])}
    rows = "".join(f"<tr><td>{s['name']}</td><td class='num'>{s['weight']}</td><td class='num'>{s['peak_water_level_navd88_m']:.2f} m</td>"
                   f"<td class='num'>{s['gust_mps']:.1f} m/s</td><td class='num'>{usd(control.get(s['name'], 0))}</td></tr>"
                   for s in forcing["scenarios"])
    check = ""
    if observed:
        inside = "inside" if observed["water_level_within_p10_p90"] else "outside"
        gust_note = (f" Observed peak gust was {observed['peak_gust_mps']:.1f} m/s against a P90 of "
                     f"{forcing['scenarios'][-1]['gust_mps']:.1f} m/s: gauge history alone does not see an incoming storm's wind."
                     if observed.get("peak_gust_mps") else "")
        check = (f"<p class='plan'>Backtest: the observed peak, {observed['peak_water_level_navd88_m']:.2f} m at {observed['peak_time']} UTC, "
                 f"fell {inside} the P10–P90 band.{gust_note}</p>")
    return (f"<h2>Storm forecast <span class='plan'>{html.escape(forcing['model'])}, issued {forcing['issued']} UTC, "
            f"{forcing['horizon_hours']:g} h</span></h2><div class='fans'>{water}{gust}</div>"
            f"<p class='plan'>Dashed line: NOAA harmonic tide prediction (a TimesFM covariate). Each plan is simulated in all "
            f"three scenarios and scored on expected damage.</p>{check}"
            f"<table class='scen'><thead><tr><th>Scenario</th><th>Weight</th><th>Bay level</th><th>Peak gust</th>"
            f"<th>Damage, no preparation</th></tr></thead><tbody>{rows}</tbody></table>")


def usd(value):
    return f"{'−' if value < 0 else ''}${abs(value) / 1e6:,.1f}M"


def load(run, key):
    path = run / key / f"{key}.json"
    try:
        return json.loads(path.read_text()) if path.is_file() else None
    except json.JSONDecodeError:
        return None


def best(log):
    scored = [r for r in log["rounds"] if "ledger" in r] if log else []
    return min(scored, key=lambda r: r["ledger"]["total_usd"]) if scored else None


def card(entry, folder, previous_usd, heading):
    total = entry["ledger"]["total_usd"]
    delta = total - previous_usd
    tone = "better" if delta < 0 else "worse" if delta > 0 else ""
    plan, bags = entry["plan"], entry["sandbags"]
    walls = ", ".join(f"{w['length_m']:.0f} m × {w['height_ft']} ft" for w in bags["walls"]) or "none"
    video = f'<video src="{folder}/{entry["video"]}" muted playsinline controls></video>' if entry.get("video") else ""
    parts = [f'<article class="round"><header><b>{html.escape(heading)}</b>'
             f'<span class="num">{usd(total)} <span class="{tone}">({"+" if delta > 0 else ""}{usd(delta)} vs previous)</span></span></header>',
             f'<img src="{folder}/{entry["image"]}" alt="Simulated peak flood for {html.escape(plan["name"])}" loading="lazy">{video}',
             f'<p class="plan">{html.escape(plan["name"])} · {entry["ledger"]["structures_flooded"]} buildings flooded · '
             f'{entry["ledger"]["structures_kept_dry_by_doorway_sandbags"]} kept dry by doorway bags · '
             f'{bags["total"]:,} of {bags["stock"]:,} sandbags · walls: {html.escape(walls)} · '
             f'{len(entry["clearing"]["completed"])} drains cleared</p>']
    scenarios = entry.get("scenarios") or []
    if len(scenarios) > 1:
        parts.append("<div class='thumbs'>" + "".join(
            f"<figure><img src='{folder}/{x['image']}' alt='{x['name']} scenario' loading='lazy'>"
            f"<figcaption>{x['name']} · bay {x.get('peak_water_level_navd88_m', 0):.2f} m · {usd(x['total_usd'])}</figcaption></figure>"
            for x in scenarios) + "</div><p class='plan'>Expected damage weights the scenarios 0.3 / 0.4 / 0.3.</p>")
    if entry.get("critique"):
        parts.append(f'<p class="label">What it saw in the previous run</p><p class="text">{html.escape(entry["critique"])}</p>')
    if entry.get("rationale"):
        parts.append(f'<p class="label">Plan</p><p class="text">{html.escape(entry["rationale"])}</p>')
    if entry.get("model_call"):
        parts.append(f'<p class="plan">model {entry["model_call"]["seconds"]} s · simulate + render {entry["seconds"]} s</p>')
    parts.append("</article>")
    return "".join(parts)


def column(log, key, title):
    if not log:
        return f"<section><h2>{title}</h2><p class='error'>No run log.</p></section>"
    control = log["control"]["ledger"]["total_usd"]
    out = [f"<section><h2>{title} <span class='plan'>{html.escape(log['model'])}</span></h2>"]
    previous = control
    for entry in log["rounds"]:
        if "error" in entry:
            out.append(f"<article class='round'><p class='error'>Round {entry['round']} stopped: {html.escape(entry['error'])}</p></article>")
            continue
        out.append(card(entry, key, previous, f"Round {entry['round']}"))
        previous = entry["ledger"]["total_usd"]
    out.append("</section>")
    return "".join(out)


def build(run: Path):
    argon, flash = load(run, "argon"), load(run, "flash")
    reference = argon or flash
    if reference is None:
        live = "<div id='live' hidden><button type='button'>Run live</button><span></span></div>"
        page = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
                f"<title>Storm Prep Loop</title><style>{CSS}</style></head><body><main><h1>Setting up the storm</h1>"
                f"<p class='lede'>Running the no-preparation control and Codex's baseline before the models' first round "
                f"(about a minute).</p>{live}</main><script>{SCRIPT}</script></body></html>")
        (run / "index.html").write_text(page)
        return run / "index.html"
    control = reference["control"]
    heuristic = reference["codex_heuristic"]
    kind = "expected damage, " if reference.get("forcing") else ""
    rows = [(f"No preparation", control["ledger"]["total_usd"], "var(--muted)"),
            ("Codex heuristic (drains only)", heuristic["ledger"]["total_usd"], "var(--muted)")]
    for log, name, color in ((flash, f"Gemini 3.8 Flash, {kind}best round", "var(--c2)"), (argon, f"Gemini 4 Argon, {kind}best round", "var(--c1)")):
        b = best(log)
        if b:
            rows.append((name, b["ledger"]["total_usd"], color))
    top = max(r[1] for r in rows)
    bars = "".join(f'<div class="bar"><span>{html.escape(n)}</span><div class="track"><div class="fill" style="width:{v / top * 100:.1f}%;background:{c}"></div></div>'
                   f'<span class="usd">{usd(v)}</span></div>' for n, v, c in rows)
    p = reference["parameters"]
    forcing = None
    if reference.get("forcing"):
        for candidate in [run / "forcing.json", *sorted(run.parent.glob("forcing-*.json"))]:
            if candidate.is_file():
                loaded = json.loads(candidate.read_text())
                if loaded.get("issued") == reference["forcing"]["issued"]:
                    forcing = loaded
                    break
    a_best, f_best = best(argon), best(flash)
    if a_best:
        saved = control["ledger"]["total_usd"] - a_best["ledger"]["total_usd"]
        headline = (f"Argon cut {'expected ' if forcing else ''}simulated flood damage by {usd(saved)} "
                    f"({saved / control['ledger']['total_usd'] * 100:.0f}%) with {a_best['sandbags']['total']:,} sandbags")
        if f_best:
            headline += f"; Flash, with the same storm and stock, cut {usd(control['ledger']['total_usd'] - f_best['ledger']['total_usd'])}"
    else:
        headline = "Prep-plan loop"
    bay = ("bay held at each TimesFM scenario's forecast peak" if reference.get("forcing")
           else f'{p["coastal_rise_m"]:g} m coastal rise above 2.368 m NAVD88')
    sources = (f'Simulator: Codex Embarcadero storm model (<code>preparedness.simulate</code>), {p["rain_mm_h"]:g} mm/h rain, '
               f'{bay}, {p["blockage_pct"]:g}% initial drain blockage. '
               f'Buildings: <a href="{html.escape(reference["nsi_source"])}">USACE National Structure Inventory 2022</a> '
               f'({control["ledger"]["structures_in_domain"]:,} structures). Damage curves: <a href="{html.escape(reference["curves_source"])}">USACE go-consequences</a>. '
               f'Sandbags: {html.escape(reference["costs"]["levee_bags_per_100_ft"]["source"])} '
               f'Assumptions: {html.escape(" ".join(control["ledger"]["assumptions"]))} '
               f'{html.escape(reference["costs"]["height_scaling"]["source"])} {html.escape(reference["costs"]["doorway_protection"]["source"])} '
               f'{html.escape(reference["costs"]["sandbag_stock"]["source"])} Simulated outcomes, not forecasts.')
    page = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>Storm Prep Loop</title><style>{CSS}</style></head><body><main>"
            f"<h1>{html.escape(headline)}</h1>"
            f"<p class='lede'>Each model writes a pre-storm plan, the storm simulator runs it, the model watches the rendered result "
            f"and video, names what failed, and revises. Click a map to play that run's storm.</p>"
            f"<div id='live' hidden><button type='button'>Run live</button><span></span></div>"
            f"<div class='bars'>{bars}</div>{forecast_section(forcing, reference)}"
            f"<div class='cols'>{column(argon, 'argon', 'Gemini 4 Argon')}{column(flash, 'flash', 'Gemini 3.8 Flash (baseline)')}</div>"
            f"<footer>{sources}</footer></main><script>{SCRIPT}</script></body></html>")
    (run / "index.html").write_text(page)
    return run / "index.html"


if __name__ == "__main__":
    print(build(Path(sys.argv[1]).resolve()))
