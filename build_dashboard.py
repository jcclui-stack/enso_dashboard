#!/usr/bin/env python3
"""
ENSO Dashboard — scheduled rebuild script
==========================================
Fetches the latest ONI, Niño 3.4, SOI and MEI.v2 values from their official
data files, recomputes the current value / trend / phase / 36-month history,
merges in the manually-maintained forecast probabilities, and regenerates
enso-dashboard.html.

Run it on a schedule (cron or GitHub Action). Numeric indices are auto-fetched;
the forecast probabilities live in forecast_config.json (no clean feed exists
for those — edit them when CPC issues a new monthly discussion).

Usage:
    python build_dashboard.py                 # fetch live + rebuild
    python build_dashboard.py --offline        # use cached data only (no network)

Dependencies: only the Python standard library (urllib). No pip install needed.
"""

import json, sys, re, datetime as dt, urllib.request, urllib.error, os

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE   = os.path.join(HERE, "data_cache.json")
CONFIG_FILE  = os.path.join(HERE, "forecast_config.json")
STYLE_FILE   = os.path.join(HERE, "_style_block.html")
OUTPUT_FILE  = os.path.join(HERE, "enso-dashboard.html")

# ---------------------------------------------------------------------------
# DATA SOURCES  — verify these URLs in your environment; adjust if an agency
# moves a file. Each fetcher is wrapped so a single broken source falls back
# to the last cached value rather than breaking the whole build.
# ---------------------------------------------------------------------------
SOURCES = {
    # NOAA CPC Oceanic Niño Index (seasonal, 3-month running anomaly)
    "oni":  "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt",
    # NOAA CPC monthly Niño SST anomalies (1991-2020 base); Niño3.4 anom = col 9
    "nino34": "https://www.cpc.ncep.noaa.gov/data/indices/ersst5.nino.mth.91-20.ascii",
    # Australian BoM Troup SOI (monthly, ±~30 scale)
    "soi":  "https://www.bom.gov.au/climate/enso/soi_monthly.txt",
    # NOAA PSL Multivariate ENSO Index v2 (bimonthly, sigma)
    "mei":  "https://psl.noaa.gov/enso/mei/data/meiv2.data",
}

TIMEOUT = 30
UA = {"User-Agent": "ENSO-dashboard-rebuild/1.0 (+https://example.org)"}

MONTHS_ABBR = {1:"J",2:"F",3:"M",4:"A",5:"M",6:"J",7:"J",8:"A",9:"S",10:"O",11:"N",12:"D"}
SEASON_ORDER = ["DJF","JFM","FMA","MAM","AMJ","MJJ","JJA","JAS","ASO","SON","OND","NDJ"]
# season -> the calendar month its center sits in (for ordering)
SEASON_CENTER = {"DJF":1,"JFM":2,"FMA":3,"MAM":4,"AMJ":5,"MJJ":6,
                 "JJA":7,"JAS":8,"ASO":9,"SON":10,"OND":11,"NDJ":12}


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------
def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def parse_oni(text):
    """oni.ascii.txt: 'SEAS YR TOTAL ANOM' -> list of (year, season, anom)."""
    out = []
    for line in text.splitlines():
        p = line.split()
        if len(p) == 4 and p[0] in SEASON_ORDER:
            try:
                out.append((int(p[1]), p[0], float(p[3])))
            except ValueError:
                pass
    return out  # chronological


def parse_nino34(text):
    """ersst5 monthly: YR MON N1+2 A N3 A N4 A N3.4 A -> (year, month, anom)."""
    out = []
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 10 and p[0].isdigit():
            try:
                out.append((int(p[0]), int(p[1]), float(p[9])))
            except ValueError:
                pass
    return out


def parse_soi(text):
    """BoM monthly SOI: 'YYYYMM value' or 'YYYY  jan feb ... dec'. Handle both."""
    out = []
    for line in text.splitlines():
        p = line.split()
        if not p:
            continue
        # Form A: YYYYMM<space>value
        if len(p) == 2 and re.fullmatch(r"\d{6}", p[0]):
            try:
                y, m = int(p[0][:4]), int(p[0][4:6])
                out.append((y, m, float(p[1])))
                continue
            except ValueError:
                pass
        # Form B: YYYY then 12 monthly columns
        if re.fullmatch(r"\d{4}", p[0]) and len(p) >= 13:
            y = int(p[0])
            for m in range(1, 13):
                try:
                    v = float(p[m])
                    if v > -90:  # skip missing flags like -999
                        out.append((y, m, v))
                except ValueError:
                    pass
    out.sort()
    return out


def parse_mei(text):
    """meiv2.data: YEAR then 12 bimonthly values (DJ, JF, ... ND)."""
    out = []
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 13 and re.fullmatch(r"\d{4}", p[0]):
            y = int(p[0])
            for m in range(1, 13):
                try:
                    v = float(p[m])
                    if v > -990:
                        out.append((y, m, v))
                except ValueError:
                    pass
    out.sort()
    return out


PARSERS = {"oni": parse_oni, "nino34": parse_nino34, "soi": parse_soi, "mei": parse_mei}


def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def get_series(offline=False):
    """Return {key: parsed_series}, fetching live unless offline, caching results."""
    cache = load_cache()
    series = {}
    for key, url in SOURCES.items():
        if not offline:
            try:
                raw = fetch(url)
                parsed = PARSERS[key](raw)
                if parsed:
                    series[key] = parsed
                    cache[key] = parsed          # refresh cache on success
                    print(f"  [ok]   {key}: {len(parsed)} records fetched")
                    continue
                print(f"  [warn] {key}: parsed 0 records, using cache")
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
                print(f"  [warn] {key}: fetch failed ({e}), using cache")
        if key in cache:
            series[key] = [tuple(x) for x in cache[key]]
            print(f"  [cache]{key}: {len(series[key])} records from cache")
        else:
            print(f"  [MISS] {key}: no live data and no cache — chart will be empty")
            series[key] = []
    save_cache(cache)
    return series


# ---------------------------------------------------------------------------
# Phase classification
# ---------------------------------------------------------------------------
def classify(value, el_thr, la_thr, soi=False):
    """Return ('warm'|'cool'|'neutral', label)."""
    if soi:  # SOI: negative -> El Niño, positive -> La Niña
        if value <= el_thr:  return "warm", "El Niño-leaning"
        if value >= la_thr:  return "cool", "La Niña-leaning"
        return "neutral", "Neutral"
    if value >= el_thr: return "warm", "El Niño"
    if value <= la_thr: return "cool", "La Niña"
    return "neutral", "Neutral"


def last36_monthly(series):
    """Take a (y,m,v) monthly series and return the last 36 (y,m,v)."""
    return series[-36:] if len(series) >= 36 else series


def last36_oni(series):
    """ONI is seasonal; return last 36 (year, season, v)."""
    return series[-36:] if len(series) >= 36 else series


# ---------------------------------------------------------------------------
# SVG chart builder (same geometry as the hand-built version)
# ---------------------------------------------------------------------------
W, H = 440, 220
ML, MR, MT, MB = 40, 14, 14, 26
PW, PH = W - ML - MR, H - MT - MB


def _sx(i, n): return ML + PW * i / (n - 1) if n > 1 else ML
def _sy(v, vmin, vmax): return MT + PH * (vmax - v) / (vmax - vmin)


def build_chart(title, sub, labelled_vals, color, vmin, vmax, thr_el, thr_la,
                unit, src, soi=False):
    """labelled_vals: list of (xlabel, value)."""
    n = len(labelled_vals)
    if n == 0:
        return f'<div class="chart-card"><div class="chart-title">{title}</div><div class="chart-sub">No data available</div></div>'
    vals = [v for _, v in labelled_vals]

    grid, ylabels = [], []
    ticks = 4
    for t in range(ticks + 1):
        v = vmax - (vmax - vmin) * t / ticks
        yy = _sy(v, vmin, vmax)
        grid.append(f'<line class="grid-line" x1="{ML}" y1="{yy:.1f}" x2="{W-MR}" y2="{yy:.1f}"/>')
        lbl = f'{v:+.0f}' if soi else f'{v:+.1f}'
        ylabels.append(f'<text class="axis-lbl" x="{ML-6}" y="{yy+3:.1f}" text-anchor="end">{lbl}</text>')

    yz = _sy(0, vmin, vmax)
    zero = f'<line class="zero-line" x1="{ML}" y1="{yz:.1f}" x2="{W-MR}" y2="{yz:.1f}"/>'
    ye, yl = _sy(thr_el, vmin, vmax), _sy(thr_la, vmin, vmax)
    thr = (f'<line class="thr-warm" x1="{ML}" y1="{ye:.1f}" x2="{W-MR}" y2="{ye:.1f}"/>'
           f'<line class="thr-cool" x1="{ML}" y1="{yl:.1f}" x2="{W-MR}" y2="{yl:.1f}"/>')

    pts = [(_sx(i, n), _sy(v, vmin, vmax)) for i, v in enumerate(vals)]
    line = "M " + " L ".join(f'{x:.1f} {y:.1f}' for x, y in pts)
    area = (f'M {pts[0][0]:.1f} {yz:.1f} L '
            + " L ".join(f'{x:.1f} {y:.1f}' for x, y in pts)
            + f' L {pts[-1][0]:.1f} {yz:.1f} Z')

    xlabels = []
    step = max(1, n // 12)
    for i, (xl, _) in enumerate(labelled_vals):
        if i % step == 0 or i == n - 1:
            xlabels.append(f'<text class="axis-lbl" x="{_sx(i,n):.1f}" y="{H-8}" text-anchor="middle">{xl}</text>')

    lx, ly = pts[-1]
    grad_id = "g_" + re.sub(r"[^a-z0-9]", "", title.lower())
    dot = f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="{color}" stroke="var(--panel)" stroke-width="1.5"/>'
    svg = f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{title} 36-month time series">
  <defs><linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="{color}" stop-opacity="0.28"/>
    <stop offset="100%" stop-color="{color}" stop-opacity="0"/>
  </linearGradient></defs>
  {''.join(grid)}{thr}{zero}
  <path d="{area}" fill="url(#{grad_id})"/>
  <path d="{line}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
  {dot}{''.join(ylabels)}{''.join(xlabels)}
</svg>'''

    if soi:
        el_lbl, la_lbl = f"El Niño ≤{thr_el:+g}", f"La Niña ≥{thr_la:+g}"
    else:
        el_lbl, la_lbl = f"El Niño ≥{thr_el:+g}{unit}", f"La Niña ≤{thr_la:+g}{unit}"
    return f'''    <div class="chart-card">
      <div class="chart-head"><span class="chart-title">{title}</span><span class="chart-meta">{src}</span></div>
      <div class="chart-sub">{sub}</div>
      {svg}
      <div class="legend"><span><i class="dash"></i> {el_lbl}</span><span><i class="dashc"></i> {la_lbl}</span></div>
    </div>'''


# ---------------------------------------------------------------------------
# Donut ring builder
# ---------------------------------------------------------------------------
import math
RING_R = 66
RING_C = 2 * math.pi * RING_R


def build_ring(pct, color, label, period, note, deep=False):
    dash = RING_C * pct / 100
    cls = "prob-card deep" if deep else "prob-card"
    return f'''    <div class="{cls}">
      <div class="glow"></div>
      <div class="prob-lbl">{label}</div>
      <div class="prob-period">{period}</div>
      <div class="ring">
        <svg viewBox="0 0 150 150" width="150" height="150">
          <circle cx="75" cy="75" r="{RING_R}" fill="none" stroke="var(--line)" stroke-width="11"/>
          <circle cx="75" cy="75" r="{RING_R}" fill="none" stroke="{color}" stroke-width="11"
            stroke-linecap="round" stroke-dasharray="{dash:.1f} {RING_C:.1f}"/>
        </svg>
        <div class="pct"><b style="color:{color}">{pct}%</b><s>probability</s></div>
      </div>
      <div class="prob-note">{note}</div>
    </div>'''


# ---------------------------------------------------------------------------
# Index card builder
# ---------------------------------------------------------------------------
def gauge_fill(value, vmin, vmax, grad):
    """Return inline style for the small card gauge fill (0 centered)."""
    span = vmax - vmin
    zero_pos = (0 - vmin) / span * 100
    val_pos = (value - vmin) / span * 100
    left = min(zero_pos, val_pos)
    width = abs(val_pos - zero_pos)
    return f'left:{left:.1f}%;width:{width:.1f}%;background:{grad}'


def build_card(name, full, value, unit, phase_cls, phase_lbl, trend, vmin, vmax,
               grad, data_date, src, updated, nxt, klass):
    pill = {"warm": "pill-warm", "cool": "pill-cool", "neutral": "pill-neutral"}[phase_cls]
    valstr = f'{value:+.2f}' if abs(value) < 10 else f'{value:+.1f}'
    return f'''    <div class="card {klass}">
      <div class="glow"></div>
      <div class="card-top">
        <div><div class="idx-name">{name}</div><div class="idx-full">{full}</div></div>
        <span class="phase-pill {pill}">{phase_lbl}</span>
      </div>
      <div class="value">{valstr}<span class="unit">{unit}</span></div>
      <div class="trend">{trend}</div>
      <div class="gauge"><div class="gauge-bar"><div class="gauge-fill" style="{gauge_fill(value,vmin,vmax,grad)}"></div><div class="gauge-zero"></div></div>
        <div class="gauge-scale"><span>{vmin:g}</span><span>0</span><span>+{vmax:g}</span></div></div>
      <div class="card-foot"><span>{data_date}</span><span>{src}</span></div>
      <div class="dates"><div class="date-cell"><span class="lbl">Updated</span><span class="val">{updated}</span></div>
        <div class="date-cell next"><span class="lbl">Next</span><span class="val">{nxt}</span></div></div>
    </div>'''


# ---------------------------------------------------------------------------
# Assemble the page
# ---------------------------------------------------------------------------
def main():
    offline = "--offline" in sys.argv
    print("ENSO dashboard rebuild —", "OFFLINE" if offline else "LIVE FETCH")
    cfg = json.load(open(CONFIG_FILE))
    style = open(STYLE_FILE).read()
    series = get_series(offline=offline)

    today = dt.date.today()
    updated_str = today.strftime("%d %b %Y")
    # BoM weekly products refresh ~weekly (Tue); CPC monthly (2nd Thu). Approximate:
    weekly_next = (today + dt.timedelta(days=7)).strftime("%d %b %Y")
    monthly_next = (today + dt.timedelta(days=28)).strftime("%d %b %Y")

    # ---- current values + trends -----------------------------------------
    def trend_str(vals):
        if len(vals) < 2:
            return "—"
        d = vals[-1] - vals[-2]
        arrow = '<span class="arrow up">▲</span>' if d >= 0 else '<span class="arrow down">▼</span>'
        return f'{arrow} {d:+.2f} since previous reading'

    # ONI (seasonal)
    oni = last36_oni(series.get("oni", []))
    oni_vals = [(f'{MONTHS_ABBR[SEASON_CENTER[s]]}{str(y)[2:]}', v) for y, s, v in oni]
    oni_cur = oni[-1][2] if oni else 0.0
    oni_cls, oni_lbl = classify(oni_cur, 0.5, -0.5)

    # Niño 3.4 (monthly)
    n34 = last36_monthly(series.get("nino34", []))
    n34_vals = [(f'{MONTHS_ABBR[m]}{str(y)[2:]}', v) for y, m, v in n34]
    n34_cur = n34[-1][2] if n34 else 0.0
    n34_cls, n34_lbl = classify(n34_cur, 0.8, -0.8)

    # SOI (monthly)
    soi = last36_monthly(series.get("soi", []))
    soi_vals = [(f'{MONTHS_ABBR[m]}{str(y)[2:]}', v) for y, m, v in soi]
    soi_cur = soi[-1][2] if soi else 0.0
    soi_cls, soi_lbl = classify(soi_cur, -7, 7, soi=True)

    # MEI.v2 (bimonthly)
    mei = last36_monthly(series.get("mei", []))
    mei_vals = [(f'{MONTHS_ABBR[m]}{str(y)[2:]}', v) for y, m, v in mei]
    mei_cur = mei[-1][2] if mei else 0.0
    mei_cls, mei_lbl = classify(mei_cur, 0.5, -0.5)

    # ---- cards ------------------------------------------------------------
    cards = "\n".join([
        build_card("Niño 3.4 SST", "Monthly SST anomaly", n34_cur, "°C", n34_cls, n34_lbl,
                   trend_str([v for _, _, v in n34]), -2.0, 2.0, "var(--grad-warm)",
                   f"Latest month", "NOAA CPC", updated_str, weekly_next, "warm"),
        build_card("ONI → RONI", "Oceanic Niño Index (3-mo)", oni_cur, "°C", oni_cls, oni_lbl,
                   trend_str([v for _, _, v in oni]), -2.0, 2.0, "var(--grad-warm)",
                   "RONI replaced ONI · Feb 2026", "NOAA CPC", updated_str, monthly_next, "warm"),
        build_card("SOI", "Southern Oscillation Index", soi_cur, "", soi_cls, soi_lbl,
                   trend_str([v for _, _, v in soi]), -30, 30, "var(--grad-warm)",
                   "Latest month", "BoM", updated_str, weekly_next, "warm"),
        build_card("MEI.v2", "Multivariate ENSO Index", mei_cur, "σ", mei_cls, mei_lbl,
                   trend_str([v for _, _, v in mei]), -2.0, 2.0,
                   "linear-gradient(135deg,#8ea88f,#ff9a3d)",
                   "Bimonthly · NOAA PSL", "NOAA PSL", updated_str, monthly_next, mei_cls),
    ])

    # ---- charts -----------------------------------------------------------
    charts = "\n".join([
        build_chart("Niño 3.4 SST", "Monthly SST anomaly · °C", n34_vals, "var(--warm)",
                    -2.0, 2.0, 0.8, -0.8, "°C", "NOAA CPC"),
        build_chart("ONI → RONI", "3-month running mean · °C", oni_vals, "var(--warm)",
                    -2.0, 2.0, 0.5, -0.5, "°C", "NOAA CPC"),
        build_chart("SOI", "Troup Southern Oscillation Index", soi_vals, "var(--cool)",
                    -30, 30, -7, 7, "", "BoM", soi=True),
        build_chart("MEI.v2", "Bimonthly standardised index · σ", mei_vals, "var(--gold)",
                    -2.0, 2.0, 0.5, -0.5, "σ", "NOAA PSL"),
    ])

    # ---- probability ------------------------------------------------------
    pr = cfg["probabilities"]
    rings = "\n".join([
        build_ring(pr["el_nino_onset"]["pct"], "var(--warm)", "El Niño Onset",
                   pr["el_nino_onset"]["period"], pr["el_nino_onset"]["note"]),
        build_ring(pr["el_nino_persist"]["pct"], "var(--warm)", "El Niño Persists",
                   pr["el_nino_persist"]["period"], pr["el_nino_persist"]["note"]),
        build_ring(pr["strong"]["pct"], "#ff7a3d", "Strong El Niño",
                   pr["strong"]["period"], pr["strong"]["note"]),
        build_ring(pr["super"]["pct"], "#d23b3b", "Super El Niño",
                   pr["super"]["period"], pr["super"]["note"], deep=True),
    ])
    prog_rows = "".join(
        f'<div class="prog-row"><span class="seas">{s}</span>'
        f'<div class="prog-track"><div class="prog-fill" style="width:{v}%"></div></div>'
        f'<span class="prog-val">{v}%</span></div>'
        for s, v in cfg["el_nino_by_season"])

    # ---- full document ----------------------------------------------------
    html = PAGE_TEMPLATE.format(
        style=style,
        alert=cfg["alert_status"],
        updated=updated_str, weekly_next=weekly_next,
        phase_head=cfg["phase_headline"], phase_badge=cfg["phase_subhead"],
        phase_desc=cfg["phase_desc"], needle=cfg["needle_pct"],
        rings=rings, prog_rows=prog_rows,
        cards=cards, charts=charts,
        gen_time=dt.datetime.now().strftime("%Y-%m-%d %H:%M UTC%z") or dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    )
    with open(OUTPUT_FILE, "w") as f:
        f.write(html)
    print(f"\nWrote {OUTPUT_FILE} ({len(html)} chars)")
    print("Current values:",
          f"Niño3.4={n34_cur:+.2f} ({n34_lbl}),",
          f"ONI={oni_cur:+.2f} ({oni_lbl}),",
          f"SOI={soi_cur:+.1f} ({soi_lbl}),",
          f"MEI={mei_cur:+.2f} ({mei_lbl})")


# ---------------------------------------------------------------------------
# Page template (CSS injected via {style}; dynamic blocks via .format)
# ---------------------------------------------------------------------------
PAGE_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ENSO Monitor — El Niño / Southern Oscillation Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800;900&family=IBM+Plex+Mono:wght@400;500;600&family=Spectral:ital,wght@0,400;0,600;1,400&display=swap" rel="stylesheet">
{style}
</head>
<body>
<div class="grain"></div>
<div class="wrap">
  <header>
    <div class="kicker">Climate Variability Monitor</div>
    <h1>ENSO<span class="o"> Dashboard</span></h1>
    <p class="sub">Real-time tracking of the El Niño–Southern <span style="font-style:normal">Oscillation</span> across the equatorial Pacific — oceanic and atmospheric indices, current phase, and the thresholds that define El Niño and La Niña.</p>
    <div class="meta-row">
      <span class="meta"><span class="dot live"></span> Status <b>{alert}</b></span>
      <span class="meta">Last updated <b>{updated}</b></span>
      <span class="meta">Next update <b>{weekly_next}</b></span>
      <span class="meta">Region <b>Niño 3.4 · 5°N–5°S, 170°W–120°W</b></span>
    </div>
  </header>

  <div class="status">
    <div class="status-inner">
      <div>
        <div class="status-tag">Current ENSO Phase</div>
        <div class="status-head">{phase_head} <span class="badge">{phase_badge}</span></div>
        <div class="status-desc">{phase_desc}</div>
      </div>
      <div class="status-meter">
        <div class="status-tag">La Niña ◄ ───── ► El Niño</div>
        <div class="track"><span class="needle" style="left:{needle}%"></span></div>
        <div class="track-labels"><span>Strong La Niña</span><span>Neutral</span><span>Strong El Niño</span></div>
      </div>
    </div>
  </div>

  <div class="section-title" style="margin-top:42px">Forecast Probability</div>
  <div class="prob-grid">
{rings}
  </div>
  <div class="prog-card">
    <div class="chart-head" style="margin-bottom:10px"><span class="chart-title">El Niño Probability by Season</span><span class="chart-meta">NOAA CPC</span></div>
    <div class="chart-sub" style="margin-bottom:16px">Official CPC probability that El Niño conditions are present each overlapping 3-month season</div>
    {prog_rows}
  </div>

  <div class="section-title" style="margin-top:42px">Primary Indices</div>
  <div class="grid">
{cards}
  </div>

  <div class="section-title" style="margin-top:42px">36-Month History</div>
  <div class="chart-grid">
{charts}
  </div>

  <div class="section-title" style="margin-top:42px">Phase Thresholds</div>
  <div class="thresh-wrap">
    <table>
      <thead><tr><th>Index</th><th><span class="chip la">La Niña</span></th><th><span class="chip nu">Neutral</span></th><th><span class="chip el">El Niño</span></th></tr></thead>
      <tbody>
        <tr><td class="idx">Niño 3.4 SST<span class="note">Relative weekly anomaly (BoM)</span></td><td>≤ −0.80 °C</td><td>−0.80 to +0.80 °C</td><td>≥ +0.80 °C</td></tr>
        <tr><td class="idx">ONI / RONI<span class="note">3-month running mean, ≥3 consecutive seasons</span></td><td>≤ −0.5 °C</td><td>−0.5 to +0.5 °C</td><td>≥ +0.5 °C</td></tr>
        <tr><td class="idx">SOI<span class="note">Sustained values define phase (Troup)</span></td><td>≥ +7</td><td>−7 to +7</td><td>≤ −7</td></tr>
        <tr><td class="idx">MEI.v2<span class="note">Standardised σ; ±0.5 commonly used as a guide</span></td><td>≤ −0.5</td><td>−0.5 to +0.5</td><td>≥ +0.5</td></tr>
      </tbody>
    </table>
  </div>

  <footer>
    <div style="color:var(--muted);letter-spacing:.14em">DATA SOURCES</div>
    <div class="src-grid">
      <span>↳ NOAA CPC — <a href="https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/" target="_blank" rel="noopener">ENSO Diagnostic Discussion</a></span>
      <span>↳ NOAA PSL — <a href="https://psl.noaa.gov/enso/mei/" target="_blank" rel="noopener">MEI.v2</a></span>
      <span>↳ Aust. BoM — <a href="https://www.bom.gov.au/climate/enso/" target="_blank" rel="noopener">ENSO Wrap-Up & SOI</a></span>
      <span>↳ IRI Columbia — <a href="https://iri.columbia.edu/our-expertise/climate/forecasts/enso/current/" target="_blank" rel="noopener">ENSO Forecast</a></span>
    </div>
    <p class="disclaimer">Index values auto-fetched from official sources; forecast probabilities updated from the CPC ENSO Diagnostic Discussion. NOAA replaced the ONI with the Relative ONI (RONI) in February 2026 to account for background ocean warming. Page generated {gen_time}. Always consult the official source links for operational use.</p>
  </footer>
</div>
</body>
</html>'''


if __name__ == "__main__":
    main()
