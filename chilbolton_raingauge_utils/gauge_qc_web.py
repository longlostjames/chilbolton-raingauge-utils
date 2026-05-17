#!/usr/bin/env python3
"""
Browser-based interactive QC flag picker, designed for access via SSH
local port forwarding.

Run on the remote machine (JASMIN / HPC):
    gauge-qc-web  [--dir /path/to/nc/files]  [--port 8765]

or jump straight to a specific file:
    gauge-qc-web  file.nc  [--port 8765]

Forward the port from your local machine in a separate terminal:
    ssh -L 8765:localhost:8765  <user>@<host>

Then open in your browser:
    http://localhost:8765
"""

import argparse
import logging
import os
import threading
import urllib.request
from pathlib import Path

_PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"
_PLOTLY_CACHE = Path.home() / ".cache" / "chilbolton_raingauge_utils" / "plotly.min.js"
_plotly_js_bytes: bytes | None = None


def _load_plotly() -> None:
    """Download Plotly JS once and cache it; serve locally to avoid CDN issues."""
    global _plotly_js_bytes
    if _PLOTLY_CACHE.exists():
        _plotly_js_bytes = _PLOTLY_CACHE.read_bytes()
        print(f"  Plotly JS loaded from cache ({len(_plotly_js_bytes)//1024} KB)")
        return
    try:
        print("  Downloading Plotly JS (one-time, ~3 MB) ", end='', flush=True)
        with urllib.request.urlopen(_PLOTLY_CDN, timeout=30) as resp:
            _plotly_js_bytes = resp.read()
        _PLOTLY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _PLOTLY_CACHE.write_bytes(_plotly_js_bytes)
        print(f"OK ({len(_plotly_js_bytes)//1024} KB, cached to {_PLOTLY_CACHE})")
    except Exception as exc:
        print(f"\n  Warning: could not download Plotly locally ({exc}).")
        print(f"  Will redirect browser to CDN: {_PLOTLY_CDN}")

import calendar
import glob
import re
import netCDF4 as nc4
import numpy as np
import pandas as pd

try:
    from .process_raingauge import detect_pump_cycles
except ImportError:
    from chilbolton_raingauge_utils.process_raingauge import detect_pump_cycles

_FLAG_GOOD = np.int8(1)
_FLAG_PUMP = np.int8(2)   # kept for backwards compat with detect_pump_cycles

# Met instrument configuration (mirrors raingauge_click_plots_web)
_GWS_DATA_PATH  = '/gws/pw/j07/ncas_obs_vol2/cao/processing'
_TEMP_INST      = 'ncas-temperature-rh-1'
_TEMP_PREFIXES  = ('ncas-temperature-rh-1', 'stfc-temperature-rh-1')
_TEMP_THRESH_C  = 7.0   # shown as reference line on T subplot


def _read_met(yyyymmdd: str, data_path: str) -> dict | None:
    """Load air_temperature (°C) and relative_humidity (%) for *yyyymmdd*.

    Returns {'times': [...ISO...], 'temp_c': [...], 'rh': [...]} or None.
    Bad-QC samples are replaced with None so Plotly renders gaps.
    """
    year   = yyyymmdd[:4]
    level1 = os.path.join(data_path, _TEMP_INST, 'data', 'long-term', 'level1', year)
    fpath  = None
    for prefix in _TEMP_PREFIXES:
        pattern = os.path.join(level1, f'{prefix}_cao_{yyyymmdd}_surface-met_*.nc')
        matches = glob.glob(pattern)
        if matches:
            fpath = matches[0]
            break
    if fpath is None:
        return None
    try:
        with nc4.Dataset(fpath, 'r') as ds:
            unix = np.asarray(ds.variables['time'][:], dtype=float)
            temp = np.asarray(ds.variables['air_temperature'][:], dtype=float)
            if 'qc_flag_air_temperature' in ds.variables:
                qc_t = np.asarray(ds.variables['qc_flag_air_temperature'][:])
                temp = np.where(qc_t == 1, temp, np.nan)
            temp_c = temp - 273.15
            rh = np.asarray(ds.variables['relative_humidity'][:], dtype=float)
            if 'qc_flag_relative_humidity' in ds.variables:
                qc_r = np.asarray(ds.variables['qc_flag_relative_humidity'][:])
                rh = np.where(qc_r == 1, rh, np.nan)
        times_iso = pd.to_datetime(unix, unit='s').strftime('%Y-%m-%dT%H:%M:%S').tolist()
        def _to_list(arr):
            return [None if np.isnan(v) else float(v) for v in arr]
        return {'times': times_iso, 'temp_c': _to_list(temp_c), 'rh': _to_list(rh)}
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Flag registry — add or edit flag types here.  Each entry:
#   value       : int8 flag value written to the NetCDF qc_flag variable
#   label       : human-readable label shown in the UI
#   color       : CSS hex colour for the bar chart and legend
#   selectable  : True  → appears in the "Flag as:" radio group
#                 False → shown in legend only (e.g. good data)
#   nc_meaning  : token written to the CF flag_meanings attribute
# ---------------------------------------------------------------------------
FLAG_REGISTRY = [
    {'value': 0, 'label': 'not used',     'color': '#9e9e9e', 'selectable': False,
     'nc_meaning': 'not_used'},
    {'value': 1, 'label': 'good data',    'color': '#1e88e5', 'selectable': True,
     'nc_meaning': 'good_data'},
    {'value': 2, 'label': 'pump cycle',   'color': '#f06292', 'selectable': True,
     'nc_meaning': 'instrument_error'},
    {'value': 3, 'label': 'bad data',     'color': '#e53935', 'selectable': True,
     'nc_meaning': 'bad_data_precipitation_rate_less_than_0_mm_hr-1'},
    {'value': 4, 'label': 'suspect data', 'color': '#f9a825', 'selectable': True,
     'nc_meaning': 'suspect_data_precipitation_rate_greater_than_300_mm_hr-1'},
    {'value': 5, 'label': 'burette cal',  'color': '#ab47bc', 'selectable': True,
     'nc_meaning': 'calibration_burette'},
]

_state: dict = {
    'loaded': False,
    'root':   str(Path.home()),
}

# ---------------------------------------------------------------------------
# HTML / JS single-page application
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Gauge QC</title>
  <script src="/plotly.js"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body { font-family: system-ui, sans-serif; margin: 0; background: #f0f2f5;
           color: #333; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

    /* ── top bar ── */
    #topbar { background: #1565c0; color: #fff; padding: 7px 14px;
               display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
    #topbar h1 { margin: 0; font-size: 15px; font-weight: 600; flex: 1; }
    #btn-choose { background: rgba(255,255,255,.15); color: #fff; border: 1px solid rgba(255,255,255,.4);
                  border-radius: 4px; padding: 5px 13px; cursor: pointer; font-size: 12px; }
    #btn-choose:hover { background: rgba(255,255,255,.25); }

    /* ── main layout: sidebar + chart area ── */
    #main { display: flex; flex: 1; overflow: hidden; }

    /* ── file browser sidebar ── */
    #sidebar { width: 320px; min-width: 220px; max-width: 480px; background: #fff;
                border-right: 1px solid #ddd; display: flex; flex-direction: column;
                overflow: hidden; flex-shrink: 0; transition: width 0.15s; }
    #sidebar.hidden { display: none; }
    #browser-header { background: #e8eaf6; padding: 7px 10px; font-size: 12px;
                       font-weight: 600; color: #3949ab; border-bottom: 1px solid #ddd;
                       flex-shrink: 0; }
    #breadcrumb { padding: 6px 10px; font-size: 11px; color: #555; background: #fafafa;
                   border-bottom: 1px solid #eee; word-break: break-all;
                   flex-shrink: 0; line-height: 1.6; }
    #breadcrumb span { cursor: pointer; color: #1565c0; }
    #breadcrumb span:hover { text-decoration: underline; }
    #file-list { overflow-y: auto; flex: 1; }
    .entry { display: flex; align-items: center; gap: 7px; padding: 6px 10px;
              cursor: pointer; font-size: 12px; border-bottom: 1px solid #f0f0f0;
              user-select: none; }
    .entry:hover { background: #e8eaf6; }
    .entry.nc-file { color: #1565c0; }
    .entry.nc-file:hover { background: #e3f2fd; }
    .entry.dir::before      { content: "📁"; font-size: 14px; }
    .entry.nc-file::before  { content: "📄"; font-size: 14px; }
    .entry.up::before       { content: "⬆️"; font-size: 14px; }

    /* ── right panel (chart + controls) ── */
    #right { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

    /* placeholder when no file loaded */
    #placeholder { flex: 1; display: flex; align-items: center; justify-content: center;
                    color: #aaa; font-size: 15px; flex-direction: column; gap: 10px; }

    /* chart panel (hidden until file loaded) */
    #chart-panel { flex: 1; display: none; flex-direction: column; overflow: hidden;
                    padding: 10px 14px 6px; }
    #chart-panel.visible { display: flex; }

    #file-label { font-size: 12px; color: #555; margin-bottom: 6px; word-break: break-all; }
    #flagmode { display: flex; align-items: center; gap: 6px; margin-bottom: 6px;
                flex-shrink: 0; flex-wrap: wrap; }
    #intmode  { display: flex; align-items: center; gap: 6px; margin-bottom: 6px;
                flex-shrink: 0; flex-wrap: wrap; }
    .fm-label { font-size: 12px; color: #555; font-weight: 500; margin-right: 2px; }
    .fm-opt { display: inline-flex; align-items: center; padding: 3px 10px;
               border: 1.5px solid var(--c); border-radius: 4px; cursor: pointer;
               font-size: 12px; font-weight: 500; color: var(--c); user-select: none; }
    .fm-opt:has(input:checked) { background: var(--c); color: #fff; }
    .fm-opt input { display: none; }
    #controls { display: flex; flex-wrap: wrap; align-items: center;
                gap: 8px; margin-bottom: 8px; flex-shrink: 0; }
    button.action { padding: 6px 14px; border: none; border-radius: 4px;
                    cursor: pointer; font-size: 13px; font-weight: 500; }
    #btn-auto      { background: #1e88e5; color: #fff; }
    #btn-clear     { background: #757575; color: #fff; }
    #btn-load-csv  { background: #6a1b9a; color: #fff; }
    #btn-load-corr { background: #00695c; color: #fff; }
    #btn-save      { background: #2e7d32; color: #fff; }
    #btn-discard   { background: #e65100; color: #fff; }
    #btn-quit      { background: #c62828; color: #fff; }
    button.action:hover    { opacity: .85; }
    button.action:disabled { opacity: .4; cursor: default; }
    #status   { font-size: 12px; color: #555; margin-left: 4px; }
    #save-msg { color: #2e7d32; font-weight: bold; font-size: 12px;
                display: none; margin-left: 6px; }
    #hint { font-size: 11px; color: #999; margin-bottom: 6px; flex-shrink: 0; }
    #colour-legend { display: flex; gap: 14px; flex-wrap: wrap; align-items: center;
                     font-size: 11px; color: #555; margin-bottom: 6px; flex-shrink: 0; }
    .legend-item { display: flex; align-items: center; gap: 4px; }
    .legend-swatch { width: 14px; height: 14px; border-radius: 2px; flex-shrink: 0; }
    #chart { flex: 1; min-height: 200px; }
  </style>
</head>
<body>

<div id="topbar">
  <h1>Gauge QC</h1>
  <button id="btn-choose" onclick="toggleSidebar()">📁 Browse files</button>
</div>

<div id="main">
  <!-- file browser sidebar -->
  <div id="sidebar">
    <div id="browser-header">File browser</div>
    <div id="breadcrumb"></div>
    <div id="file-list"></div>
  </div>

  <!-- right panel -->
  <div id="right">
    <div id="placeholder">
      <span style="font-size:48px;opacity:.3">📂</span>
      <span>Select a NetCDF file from the browser on the left</span>
    </div>

    <div id="chart-panel">
      <div id="file-label"></div>
      <div id="flagmode">
        <span class="fm-label">Flag as:</span>
        <!-- populated dynamically from FLAG_REGISTRY via /init -->
      </div>
      <div id="intmode">
        <span class="fm-label">Interaction:</span>
        <label class="fm-opt" style="--c:#1565c0">
          <input type="radio" name="intmode" value="click" checked> Click to flag
        </label>
        <label class="fm-opt" style="--c:#6a1b9a">
          <input type="radio" name="intmode" value="select"> Select range
        </label>
      </div>
      <div id="controls">
        <button class="action" id="btn-auto">Auto pump cycles</button>
        <button class="action" id="btn-load-csv">Load CSV</button>
        <button class="action" id="btn-load-corr">Load .corr</button>
        <button class="action" id="btn-clear">Clear all flags</button>
        <span class="fm-label">Save to:</span>
        <label class="fm-opt" style="--c:#2e7d32">
          <input type="radio" name="savemode" value="netcdf" checked> NetCDF
        </label>
        <label class="fm-opt" style="--c:#2e7d32">
          <input type="radio" name="savemode" value="csv"> CSV
        </label>
        <button class="action" id="btn-save">Save</button>
        <button class="action" id="btn-discard">Discard</button>
        <button class="action" id="btn-quit">Quit</button>
        <span id="status"></span>
        <span id="save-msg">&#10003; Saved</span>
      </div>
      <div id="hint">Select a flag type, then <b>click</b> a bar to flag/unflag it, or switch to <b>Select range</b> and drag across multiple bars to flag them all. Use the range-slider to zoom.</div>
      <div id="colour-legend"></div>
      <div id="chart"></div>
    </div>
  </div>
</div>

<script>
'use strict';

// ── state ──────────────────────────────────────────────────────────────────
let qc = [], times = [], drops = [], fileLoaded = false;
let metData = null;   // {times, temp_c, rh} from ncas-temperature-rh-1, or null
let sidebarOpen = true;
let countLabel = 'Drops';  // updated from /open response

// ── flag registry (populated from server via /init) ────────────────────────
let FLAG_COLORS = {};
let FLAG_LABELS = {};
const barColors = () => qc.map(v => FLAG_COLORS[v] ?? '#9e9e9e');

const statusText = () => {
  const counts = {};
  qc.forEach(v => { if (v !== 1) counts[v] = (counts[v] || 0) + 1; });
  if (Object.keys(counts).length === 0) return 'No samples flagged';
  return Object.entries(counts)
    .map(([v, n]) => `${n}× flag ${v} (${FLAG_LABELS[v] ?? '?'})`)
    .join('  |  ');
};

let selectedFlagValue = 2;

function initFlagRegistry(flags) {
  FLAG_COLORS = Object.fromEntries(flags.map(f => [f.value, f.color]));
  FLAG_LABELS = Object.fromEntries(flags.map(f => [f.value, f.label]));

  // Build flag-mode radio buttons (selectable flags only)
  const fmDiv = document.getElementById('flagmode');
  const selectable = flags.filter(f => f.selectable);
  selectable.forEach((f, i) => {
    const lbl = document.createElement('label');
    lbl.className = 'fm-opt';
    lbl.style.setProperty('--c', f.color);
    lbl.innerHTML =
      `<input type="radio" name="flagmode" value="${f.value}"${i === 0 ? ' checked' : ''}> ${f.label} (${f.value})`;
    fmDiv.appendChild(lbl);
  });
  if (selectable.length) selectedFlagValue = selectable[0].value;
  document.querySelectorAll('input[name="flagmode"]').forEach(r => {
    r.addEventListener('change', () => { selectedFlagValue = parseInt(r.value); });
  });

  // Build colour legend
  const el = document.getElementById('colour-legend');
  flags.forEach(f => {
    el.insertAdjacentHTML('beforeend',
      `<span class="legend-item">
        <span class="legend-swatch" style="background:${f.color}"></span>
        <span>${f.label} (${f.value})</span>
      </span>`);
  });
}
let interactionMode = 'click';
document.querySelectorAll('input[name="intmode"]').forEach(r => {
  r.addEventListener('change', () => {
    interactionMode = r.value;
    if (fileLoaded) {
      Plotly.relayout('chart', { dragmode: interactionMode === 'select' ? 'select' : 'zoom' });
    }
  });
});
document.querySelectorAll('input[name="savemode"]').forEach(r => {
  r.addEventListener('change', () => { post('/save_mode', { mode: r.value }); });
});
async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  return r.json();
}

// ── sidebar toggle ─────────────────────────────────────────────────────────
function toggleSidebar() {
  sidebarOpen = !sidebarOpen;
  document.getElementById('sidebar').classList.toggle('hidden', !sidebarOpen);
  document.getElementById('btn-choose').textContent =
    sidebarOpen ? '✕ Close browser' : '📁 Browse files';
}

// ── file browser ───────────────────────────────────────────────────────────
let currentPath = '';

async function browse(path) {
  const r = await fetch('/browse?path=' + encodeURIComponent(path));
  const d = await r.json();
  currentPath = d.path;

  // breadcrumb
  const crumb = document.getElementById('breadcrumb');
  const parts = d.path.split('/').filter(Boolean);
  crumb.innerHTML = '<span onclick="browse(\'/\')">/ </span>' +
    parts.map((p, i) => {
      const fullPath = '/' + parts.slice(0, i + 1).join('/');
      return `<span onclick="browse('${fullPath.replace(/'/g, "\\'")}')">${p}/</span>`;
    }).join(' ');

  // entries
  const list = document.getElementById('file-list');
  list.innerHTML = '';

  if (d.parent !== null) {
    const up = document.createElement('div');
    up.className = 'entry up';
    up.textContent = '  ..  (parent)';
    up.onclick = () => browse(d.parent);
    list.appendChild(up);
  }

  for (const dir of d.dirs) {
    const el = document.createElement('div');
    el.className = 'entry dir';
    el.textContent = '  ' + dir.name;
    el.title = dir.path;
    el.onclick = () => browse(dir.path);
    list.appendChild(el);
  }

  for (const f of d.files) {
    const el = document.createElement('div');
    el.className = 'entry nc-file';
    el.textContent = '  ' + f.name;
    el.title = f.path;
    el.onclick = () => openFile(f.path);
    list.appendChild(el);
  }
}

// ── open file ──────────────────────────────────────────────────────────────
async function openFile(path) {
  document.getElementById('status').textContent = 'Loading\u2026';
  const d = await post('/open', { path });
  if (d.error) { alert('Error: ' + d.error); return; }
  times = d.times;
  drops = d.drops;
  qc    = d.qc;
  countLabel = d.count_label || 'Drops';
  metData = (d.met && d.met.times && d.met.times.length) ? d.met : null;
  fileLoaded = true;
  document.getElementById('file-label').textContent = path;
  document.getElementById('placeholder').style.display = 'none';
  document.getElementById('chart-panel').classList.add('visible');
  initChart();
  document.getElementById('status').textContent = statusText();
  // auto-close sidebar on narrow screens
  if (window.innerWidth < 900 && sidebarOpen) toggleSidebar();
}

// ── chart ──────────────────────────────────────────────────────────────────
function initChart() {
  const hasMet = !!(metData && metData.times.length);

  // ── traces ─────────────────────────────────────────────────────────────
  const traceDrops = {
    x: times, y: drops, type: 'bar',
    marker: { color: barColors(), line: { width: 0 } },
    hovertemplate: `%{x|%H:%M:%S}<br>${countLabel}: %{y}<extra></extra>`,
    unselected: { marker: { opacity: 1 } },
    selected:   { marker: { opacity: 1 } },
    xaxis: 'x', yaxis: 'y',
  };
  // Flag indicator trace (index 1) — coloured triangle markers at y=0 so that
  // flagged zero-drop samples are always visible regardless of bar height.
  const flagIndicatorTrace = () => {
    const idx = qc.map((v, i) => v !== 1 ? i : -1).filter(i => i >= 0);
    return {
      x: idx.map(i => times[i]),
      y: idx.map(() => 0),
      mode: 'markers',
      type: 'scatter',
      marker: {
        symbol: 'triangle-up', size: 6,
        color: idx.map(i => FLAG_COLORS[qc[i]] ?? '#9e9e9e'),
        line: { color: '#fff', width: 0.5 },
      },
      hoverinfo: 'skip',
      xaxis: 'x', yaxis: 'y',
      showlegend: false,
    };
  };
  const plotlyTraces = [traceDrops, flagIndicatorTrace()];

  if (hasMet) {
    plotlyTraces.push({
      x: metData.times, y: metData.temp_c,
      mode: 'lines', line: { width: 1.0, color: '#e65100' },
      name: 'Temp (°C)', xaxis: 'x2', yaxis: 'y2',
      hovertemplate: '%{x|%H:%M:%S}  %{y:.2f} °C<extra></extra>',
    });
    plotlyTraces.push({
      x: metData.times, y: metData.rh,
      mode: 'lines', line: { width: 1.0, color: '#0288d1' },
      name: 'RH (%)', xaxis: 'x2', yaxis: 'y3',
      opacity: 0.7,
      hovertemplate: '%{x|%H:%M:%S}  %{y:.1f} %<extra></extra>',
    });
  }

  // ── layout ─────────────────────────────────────────────────────────────
  const dropsDomainY = hasMet ? [0.40, 1.0] : [0, 1];
  const metDomainY   = [0, 0.28];

  const layout = {
    xaxis: {
      title: hasMet ? '' : 'Time (UTC)',
      type: 'date', tickformat: '%H:%M',
      domain: [0, 1], anchor: 'y',
      rangeslider: { visible: true, thickness: 0.05 },
    },
    yaxis: {
      title: `${countLabel} per 10 s`,
      domain: dropsDomainY, anchor: 'x',
    },
    bargap: 0,
    margin: { t: 6, r: hasMet ? 55 : 16, b: 80, l: 55 },
    showlegend: false,
    plot_bgcolor: '#fff', paper_bgcolor: '#fff',
  };

  if (hasMet) {
    layout.xaxis2 = {
      title: 'Time (UTC)', type: 'date', tickformat: '%H:%M',
      domain: [0, 1], anchor: 'y2',
      matches: 'x',   // zoom in sync with range slider
    };
    layout.yaxis2 = {
      title: { text: 'Temp (°C)', font: { color: '#e65100' } },
      tickfont: { color: '#e65100' },
      domain: metDomainY, anchor: 'x2',
    };
    layout.yaxis3 = {
      title: { text: 'RH (%)', font: { color: '#0288d1' } },
      tickfont: { color: '#0288d1' },
      overlaying: 'y2', side: 'right',
      range: [0, 100], showgrid: false,
      anchor: 'x2',
    };
    // 7°C threshold line
    layout.shapes = [{
      type: 'line', xref: 'x2', yref: 'y2',
      x0: times[0], x1: times[times.length - 1],
      y0: 7, y1: 7,
      line: { color: 'rgba(33,150,243,0.6)', width: 1, dash: 'dash' },
    }];
  }

  Plotly.newPlot('chart', plotlyTraces, layout, { responsive: true });

  // ── click handler (drops trace only) ──────────────────────────────────
  document.getElementById('chart').on('plotly_click', async (data) => {
    if (!fileLoaded || interactionMode !== 'click') return;
    const pt = data.points[0];
    if (pt.curveNumber !== 0) return;   // ignore T/RH and flag indicator traces
    const d = await post('/flag', { idx: pt.pointIndex, flag_value: selectedFlagValue });
    qc = d.qc;
    updateColors();
  });
  document.getElementById('chart').on('plotly_selected', async (data) => {
    if (!fileLoaded || interactionMode !== 'select' || !data || !data.points.length) return;
    const indices = data.points
      .filter(p => p.curveNumber === 0)
      .map(p => p.pointIndex);
    if (!indices.length) return;
    const d = await post('/flag_indices', { indices, flag_value: selectedFlagValue });
    qc = d.qc;
    updateColors();
    Plotly.relayout('chart', { selections: [] });
  });
}

function updateColors() {
  if (!fileLoaded) return;
  Plotly.restyle('chart', { 'marker.color': [barColors()] }, [0]);
  // Update flag indicator markers (trace 1)
  const nonGoodIdx = qc.map((v, i) => v !== 1 ? i : -1).filter(i => i >= 0);
  Plotly.restyle('chart', {
    x: [nonGoodIdx.map(i => times[i])],
    y: [nonGoodIdx.map(() => 0)],
    'marker.color': [nonGoodIdx.map(i => FLAG_COLORS[qc[i]] ?? '#9e9e9e')],
  }, [1]);
  document.getElementById('status').textContent = statusText();
}

// ── button handlers ────────────────────────────────────────────────────────
document.getElementById('btn-auto').addEventListener('click', async () => {
  const d = await post('/auto');
  qc = d.qc; updateColors();
});
document.getElementById('btn-load-csv').addEventListener('click', async () => {
  if (!fileLoaded) { alert('Open a NetCDF file first.'); return; }
  const currentNc = document.getElementById('file-label').textContent;
  const suggested = currentNc.replace(/\.nc$/, '.csv');
  const csvPath = window.prompt('Path to QC CSV file:', suggested);
  if (!csvPath) return;
  const d = await post('/load_csv', { path: csvPath });
  if (d.error) { alert('Error: ' + d.error); return; }
  qc = d.qc;
  updateColors();
  document.getElementById('status').textContent = d.message || 'CSV flags loaded';
});
document.getElementById('btn-load-corr').addEventListener('click', async () => {
  if (!fileLoaded) { alert('Open a NetCDF file first.'); return; }
  const corrPath = window.prompt('Path to .corr corrections file:');
  if (!corrPath) return;
  const d = await post('/load_corr', { path: corrPath });
  console.log('/load_corr response:', d);
  if (d.error) { alert('Error: ' + d.error); return; }
  qc = d.qc;
  updateColors();
  document.getElementById('status').textContent = d.message || '.corr flags applied';
});
document.getElementById('btn-clear').addEventListener('click', async () => {
  const d = await post('/clear');
  qc = d.qc; updateColors();
});
document.getElementById('btn-save').addEventListener('click', async () => {
  const btns = document.querySelectorAll('button');
  btns.forEach(b => b.disabled = true);
  const d = await post('/save');
  qc = d.qc;
  updateColors();
  Plotly.relayout('chart', { selections: [] });
  const msg = document.getElementById('save-msg');
  msg.textContent = d.save_mode === 'csv' ? '\u2713 Saved to CSV' : '\u2713 Saved to NetCDF';
  msg.style.display = 'inline';
  setTimeout(() => { msg.style.display = 'none'; btns.forEach(b => b.disabled = false); }, 1500);
});
document.getElementById('btn-discard').addEventListener('click', async () => {
  if (!fileLoaded) return;
  const d = await post('/discard');
  qc = d.qc; updateColors();
  document.getElementById('save-msg').style.display = 'none';
});
document.getElementById('btn-quit').addEventListener('click', async () => {
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  post('/quit');
});

// ── boot ───────────────────────────────────────────────────────────────────
(async () => {
  try {
    const init = await (await fetch('/init')).json();    initFlagRegistry(init.flags || []);
    if (init.save_mode) {
      const r = document.querySelector(`input[name="savemode"][value="${init.save_mode}"]`);
      if (r) r.checked = true;
    }
    if (init.count_label) countLabel = init.count_label;
    await browse(init.start_dir);
    if (init.preload) {
      await openFile(init.preload);
      // hide browser once a file is pre-loaded
      if (sidebarOpen) toggleSidebar();
    }
  } catch (err) {
    console.error('Boot error:', err);
    const ph = document.getElementById('placeholder');
    ph.innerHTML = `<span style="color:#c62828;font-size:14px;padding:20px;text-align:center">⚠️ Error loading page:<br><code style="font-size:12px">${err.message}</code><br><br>Check the browser console (F12) and the server terminal for details.</span>`;
    ph.style.display = 'flex';
  }
})();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------

def _make_app(start_dir: str):
    try:
        from flask import Flask, jsonify, request, Response
    except ImportError:
        import sys
        print("ERROR: Flask is required.  pip install flask", file=sys.stderr)
        sys.exit(1)

    app = Flask(__name__)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    @app.route('/')
    def index():
        return Response(_HTML, mimetype='text/html')

    @app.route('/plotly.js')
    def serve_plotly():
        if _plotly_js_bytes is not None:
            return Response(_plotly_js_bytes, mimetype='application/javascript')
        from flask import redirect
        return redirect(_PLOTLY_CDN, code=302)

    @app.route('/init')
    def init():
        cv = _state.get('count_var', 'number_of_drops')
        return jsonify({
            'start_dir':   _state.get('start_dir', start_dir),
            'preload':     _state.get('nc_path'),
            'save_mode':   _state.get('save_mode', 'netcdf'),
            'count_label': 'Tips' if cv == 'number_of_tips' else 'Drops',
            'flags': [
                {'value': int(f['value']), 'label': f['label'],
                 'color': f['color'], 'selectable': f['selectable']}
                for f in FLAG_REGISTRY
            ],
        })

    @app.route('/browse')
    def browse():
        raw = request.args.get('path', start_dir)
        p = Path(raw).resolve()
        if not p.is_dir():
            p = p.parent

        dirs, files = [], []
        try:
            for child in sorted(p.iterdir()):
                if child.name.startswith('.'):
                    continue
                if child.is_dir():
                    dirs.append({'name': child.name, 'path': str(child)})
                elif child.suffix == '.nc':
                    files.append({'name': child.name, 'path': str(child)})
        except PermissionError:
            pass

        parent = str(p.parent) if p.parent != p else None
        return jsonify({'path': str(p), 'dirs': dirs, 'files': files, 'parent': parent})

    @app.route('/open', methods=['POST'])
    def open_file():
        path = request.json.get('path', '')
        p = Path(path).resolve()
        if not p.is_file():
            return jsonify({'error': f'File not found: {path}'})
        try:
            with nc4.Dataset(str(p)) as nc:
                unix  = nc.variables['time'][:].data.copy()
                count_var = 'number_of_tips' if 'number_of_tips' in nc.variables else 'number_of_drops'
                drops = nc.variables[count_var][:].data.astype(float)
                qc = (
                    nc.variables['qc_flag'][:].data.astype(np.int8).copy()
                    if 'qc_flag' in nc.variables
                    else np.ones(len(unix), dtype=np.int8)
                )
        except Exception as exc:
            return jsonify({'error': str(exc)})

        times_iso = pd.to_datetime(unix, unit='s').strftime('%Y-%m-%dT%H:%M:%S').tolist()
        _state.update({
            'loaded':    True,
            'nc_path':   str(p),
            'out_path':  str(p),
            'drops':     drops,
            'count_var': count_var,
            'qc':        qc,
            'orig_qc':   qc.copy(),
            'times_iso': times_iso,
        })
        if _state.get('cli_out_path') and str(p) == _state.get('cli_nc_path'):
            _state['out_path'] = _state['cli_out_path']

        # Try to load met data for this date
        met = None
        m = re.search(r'_(\d{8})_', p.name)
        if m:
            met = _read_met(m.group(1), _state.get('data_path', _GWS_DATA_PATH))

        count_label = 'Tips' if count_var == 'number_of_tips' else 'Drops'
        return jsonify({'times': times_iso, 'drops': drops.tolist(),
                        'qc': qc.tolist(), 'met': met, 'count_label': count_label})

    @app.route('/flag', methods=['POST'])
    def toggle_flag():
        idx = int(request.json['idx'])
        flag_val = np.int8(request.json.get('flag_value', int(_FLAG_PUMP)))
        # Toggle: clicking the same flag again reverts to good
        _state['qc'][idx] = (
            _FLAG_GOOD if _state['qc'][idx] == flag_val else flag_val
        )
        return jsonify({'qc': _state['qc'].tolist()})

    @app.route('/flag_indices', methods=['POST'])
    def flag_indices():
        indices = request.json['indices']
        flag_val = np.int8(request.json.get('flag_value', int(_FLAG_PUMP)))
        for idx in indices:
            _state['qc'][int(idx)] = flag_val
        return jsonify({'qc': _state['qc'].tolist()})

    @app.route('/auto', methods=['POST'])
    def auto_detect():
        mask = detect_pump_cycles(_state['drops'])
        _state['qc'][mask]  = _FLAG_PUMP
        _state['qc'][~mask & (_state['qc'] == _FLAG_PUMP)] = _FLAG_GOOD
        return jsonify({'qc': _state['qc'].tolist()})

    @app.route('/load_csv', methods=['POST'])
    def load_csv():
        if not _state.get('loaded'):
            return jsonify({'error': 'No file loaded'})
        path = request.json.get('path', '')
        p = Path(path).resolve()
        if not p.is_file():
            return jsonify({'error': f'File not found: {path}'})
        try:
            import csv as _csv
            n_times = len(_state['times_iso'])
            new_qc = _state['qc'].copy()
            applied = 0
            with open(p, newline='') as fh:
                reader = _csv.DictReader(fh)
                for row in reader:
                    idx = int(row['index'])
                    flag = np.int8(int(row['qc_flag']))
                    if 0 <= idx < n_times:
                        new_qc[idx] = flag
                        applied += 1
            _state['qc'] = new_qc
            n_flagged = int((new_qc != _FLAG_GOOD).sum())
            msg = f'Loaded {applied} flags from {p.name} ({n_flagged} non-good)'
            print(msg)
            return jsonify({'qc': new_qc.tolist(), 'message': msg})
        except Exception as exc:
            return jsonify({'error': str(exc)})

    @app.route('/load_corr', methods=['POST'])
    def load_corr():
        if not _state.get('loaded'):
            return jsonify({'error': 'No file loaded'})
        path = request.json.get('path', '')
        p = Path(path).resolve()
        if not p.is_file():
            return jsonify({'error': f'File not found: {path}'})
        # Map .corr flag keywords to QC flag values
        CORR_FLAG_MAP = {
            'HOLDCAL': np.int8(4),   # suspect_data
            'BADDATA': np.int8(3),   # bad_data
        }
        try:
            import datetime as _dt
            # Build a numpy array of file times as seconds-since-epoch for fast comparison
            file_unix = np.array(
                [int(_dt.datetime.fromisoformat(t).replace(
                    tzinfo=_dt.timezone.utc).timestamp())
                 for t in _state['times_iso']], dtype=np.int64
            )
            new_qc = _state['qc'].copy()
            applied = 0
            skipped = 0
            with open(p) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split()
                    if len(parts) < 4:
                        continue
                    date_s, start_s, end_s, flag_kw = parts[0], parts[1], parts[2], parts[3]
                    flag_val = CORR_FLAG_MAP.get(flag_kw.upper())
                    if flag_val is None:
                        skipped += 1
                        continue
                    try:
                        start_dt = _dt.datetime.strptime(
                            date_s + start_s, '%Y%m%d%H%M%S').replace(
                            tzinfo=_dt.timezone.utc)
                        end_dt = _dt.datetime.strptime(
                            date_s + end_s, '%Y%m%d%H%M%S').replace(
                            tzinfo=_dt.timezone.utc)
                    except ValueError:
                        skipped += 1
                        continue
                    t0 = int(start_dt.timestamp())
                    t1 = int(end_dt.timestamp())
                    mask = (file_unix >= t0) & (file_unix <= t1)
                    n = int(mask.sum())
                    if n:
                        new_qc[mask] = flag_val
                        applied += n
            _state['qc'] = new_qc
            n_flagged = int((new_qc != _FLAG_GOOD).sum())
            msg = (f'Applied {applied} flags from {p.name} '
                   f'({n_flagged} non-good total'
                   + (f'; {skipped} unrecognised flag types skipped' if skipped else '') + ')')
            print(msg)
            return jsonify({'qc': new_qc.tolist(), 'message': msg})
        except Exception as exc:
            return jsonify({'error': str(exc)})

    @app.route('/clear', methods=['POST'])
    def clear_flags():
        # Reset all flagged samples (value > 1) back to good data (1)
        _state['qc'][_state['qc'] > _FLAG_GOOD] = _FLAG_GOOD
        return jsonify({'qc': _state['qc'].tolist()})

    @app.route('/discard', methods=['POST'])
    def discard_flags():
        _state['qc'] = _state['orig_qc'].copy()
        return jsonify({'qc': _state['qc'].tolist()})

    @app.route('/save', methods=['POST'])
    def save_flags():
        _do_save()
        return jsonify({'status': 'saved', 'qc': _state['qc'].tolist(),
                        'save_mode': _state.get('save_mode', 'netcdf')})

    @app.route('/save_mode', methods=['POST'])
    def set_save_mode():
        mode = request.json.get('mode', 'netcdf')
        if mode in ('netcdf', 'csv'):
            _state['save_mode'] = mode
        return jsonify({'save_mode': _state.get('save_mode', 'netcdf')})

    @app.route('/quit', methods=['POST'])
    def quit_server():
        import os, signal, time
        def _stop():
            time.sleep(0.3)
            os.kill(os.getpid(), signal.SIGINT)
        threading.Thread(target=_stop, daemon=True).start()
        return jsonify({'status': 'quitting'})

    return app


def _do_save():
    if _state.get('save_mode', 'netcdf') == 'csv':
        _do_save_csv()
    else:
        _do_save_netcdf()


def _flag_summary() -> str:
    parts = []
    for fl in FLAG_REGISTRY:
        if not fl['selectable']:
            continue
        n = int((_state['qc'] == fl['value']).sum())
        if n:
            parts.append(f"{n}\u00d7 {fl['label']}")
    return ', '.join(parts) if parts else 'no samples flagged'


def _do_save_netcdf():
    import datetime
    out_path = _state['out_path']
    timestamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    history_entry = (
        f"{timestamp} - QC flags edited interactively using gauge-qc-web"
    )
    # Derive CF attributes from FLAG_REGISTRY
    _reg_vals     = np.array([fl['value'] for fl in FLAG_REGISTRY], dtype=np.int8)
    _reg_meanings = ' '.join(fl['nc_meaning'] for fl in FLAG_REGISTRY)
    with nc4.Dataset(out_path, 'r+') as nc:
        if 'qc_flag' in nc.variables:
            nc.variables['qc_flag'][:] = _state['qc']
        else:
            v = nc.createVariable('qc_flag', 'i1', ('time',), fill_value=-127)
            v.units = '1'
            v.long_name = 'Data Quality flag'
            v.flag_values = _reg_vals
            v.flag_meanings = _reg_meanings
            v[:] = _state['qc']
        existing_history = getattr(nc, 'history', None)
        nc.history = (
            f"{history_entry}\n{existing_history}"
            if existing_history
            else history_entry
        )
        nc.last_revised_date = timestamp
    _state['orig_qc'] = _state['qc'].copy()
    print(f"Saved \u2192 {out_path}  ({_flag_summary()})")


def _do_save_csv():
    nc_path  = Path(_state['nc_path'])
    out_path = Path(_state.get('out_path', str(nc_path)))
    csv_path = out_path.with_suffix('.csv') if out_path.suffix != '.csv' else out_path
    with open(csv_path, 'w', newline='') as fh:
        fh.write('index,time,qc_flag\n')
        for i, (t, q) in enumerate(zip(_state['times_iso'], _state['qc'])):
            fh.write(f'{i},{t},{int(q)}\n')
    _state['orig_qc'] = _state['qc'].copy()
    print(f"Saved \u2192 {csv_path}  ({_flag_summary()})")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Browser-based interactive QC flag picker.\n\n"
            "Run on the remote machine:\n"
            "  identify-pump-cycles-web  [file.nc]  [--dir /path]  [--port 8765]\n\n"
            "Forward the port from your local machine:\n"
            "  ssh -L 8765:localhost:8765  <user>@<host>\n\n"
            "Then open:  http://localhost:8765"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('nc_file', nargs='?', default=None,
                        help='NetCDF file to open immediately (optional)')
    parser.add_argument('-d', '--dir', default=None,
                        help='Starting directory for the file browser '
                             '(default: directory of nc_file, or home directory)')
    parser.add_argument('-o', '--output', default=None,
                        help='Output NetCDF path when saving (default: overwrite input)')
    parser.add_argument('--csv', action='store_true', default=False,
                        help='Save QC flags to a CSV file instead of writing to the NetCDF '
                             '(CSV path is derived from the NetCDF filename)')
    parser.add_argument('-p', '--port', type=int, default=8765,
                        help='Local port to serve on (default: 8765)')
    parser.add_argument('--data-path', default=_GWS_DATA_PATH,
                        help='Root data path used to locate ncas-temperature-rh-1 '
                             'met files (default: %(default)s)')
    args = parser.parse_args()

    # Determine start directory for the browser
    if args.dir:
        start_dir = str(Path(args.dir).resolve())
    elif args.nc_file:
        start_dir = str(Path(args.nc_file).resolve().parent)
    else:
        start_dir = str(Path.home())

    _state['start_dir'] = start_dir
    _state['data_path']  = args.data_path
    _state['save_mode']  = 'csv' if args.csv else 'netcdf'

    _load_plotly()

    # Pre-load a file if given on the command line
    if args.nc_file:
        nc_path  = str(Path(args.nc_file).resolve())
        out_path = str(Path(args.output).resolve()) if args.output else nc_path
        with nc4.Dataset(nc_path) as nc:
            unix  = nc.variables['time'][:].data.copy()
            count_var = 'number_of_tips' if 'number_of_tips' in nc.variables else 'number_of_drops'
            drops = nc.variables[count_var][:].data.astype(float)
            qc = (
                nc.variables['qc_flag'][:].data.astype(np.int8).copy()
                if 'qc_flag' in nc.variables
                else np.ones(len(unix), dtype=np.int8)
            )
        times_iso = pd.to_datetime(unix, unit='s').strftime('%Y-%m-%dT%H:%M:%S').tolist()
        _state.update({
            'loaded':       True,
            'nc_path':      nc_path,
            'out_path':     out_path,
            'cli_nc_path':  nc_path,
            'cli_out_path': out_path,
            'drops':        drops,
            'count_var':    count_var,
            'qc':           qc,
            'orig_qc':      qc.copy(),
            'times_iso':    times_iso,
        })

    port = args.port
    print()
    print("=" * 62)
    print("  Gauge QC — browser interface")
    print("=" * 62)
    if args.nc_file:
        print(f"  File : {args.nc_file}")
    print(f"  Dir  : {start_dir}")
    print()
    print("  SSH port-forward from your local machine:")
    print(f"    ssh -L {port}:localhost:{port}  <user>@<host>")
    print()
    print(f"  Then open:  http://localhost:{port}")
    print("=" * 62)
    print("  Press Ctrl-C to quit without saving.")
    print()

    app = _make_app(start_dir)
    try:
        app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\nDone.")


if __name__ == '__main__':
    main()
