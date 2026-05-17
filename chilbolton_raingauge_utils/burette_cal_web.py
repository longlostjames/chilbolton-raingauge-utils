#!/usr/bin/env python3
"""
Browser-based burette calibration analysis tool, designed for access via SSH
local port forwarding.

Run on the remote machine (JASMIN / HPC):
    burette-cal-web  [--dir /path/to/nc/files]  [--port 8766]

or jump straight to a specific file:
    burette-cal-web  file.nc  [--port 8766]

Forward the port from your local machine in a separate terminal:
    ssh -L 8766:localhost:8766  <user>@<host>

Then open in your browser:
    http://localhost:8766

Calibration method
------------------
A burette containing a known volume of water (default 50 ml) is discharged
into the gauge funnel.  The expected equivalent rainfall thickness is:

    thickness_mm = volume_mm3 / collection_area_mm2
                 = (volume_ml * 1000) / (collection_area_m2 * 1e6)
                 = volume_ml / (collection_area_m2 * 1000)

The measured thickness is:

    measured_mm = total_drops * measurement_quanta_mm

The ratio measured/expected and the percentage error are displayed after the
user selects the calibration window by dragging across the chart.
"""

import argparse
import logging
import re
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


import netCDF4 as nc4
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

def _parse_value_units(s: str) -> tuple[float, str]:
    """Parse a string like '0.0182 m2' -> (0.0182, 'm2')."""
    m = re.match(r'([\d.eE+\-]+)\s*(.*)', s.strip())
    if m:
        return float(m.group(1)), m.group(2).strip()
    raise ValueError(f"Cannot parse value+units from: {s!r}")


def _collection_area_m2(raw: str) -> float:
    """Convert a collection_area attribute string to float m²."""
    val, unit = _parse_value_units(raw)
    unit = unit.lower().replace('^2', '2').replace('**2', '2')
    if unit in ('m2', 'm²'):
        return val
    if unit in ('cm2', 'cm²'):
        return val * 1e-4
    if unit in ('mm2', 'mm²'):
        return val * 1e-6
    # default: assume m²
    return val


def _quanta_mm(raw: str) -> float:
    """Convert a measurement_quanta attribute string to float mm."""
    val, unit = _parse_value_units(raw)
    unit = unit.lower()
    if unit == 'mm':
        return val
    if unit == 'm':
        return val * 1000.0
    if unit == 'cm':
        return val * 10.0
    return val


# ---------------------------------------------------------------------------
# Server state
# ---------------------------------------------------------------------------

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
  <title>Burette Calibration</title>
  <script src="/plotly.js"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body { font-family: system-ui, sans-serif; margin: 0; background: #f0f2f5;
           color: #333; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

    /* ── top bar ── */
    #topbar { background: #4a148c; color: #fff; padding: 7px 14px;
               display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
    #topbar h1 { margin: 0; font-size: 15px; font-weight: 600; flex: 1; }
    #btn-choose { background: rgba(255,255,255,.15); color: #fff; border: 1px solid rgba(255,255,255,.4);
                  border-radius: 4px; padding: 5px 13px; cursor: pointer; font-size: 12px; }
    #btn-choose:hover { background: rgba(255,255,255,.25); }

    /* ── main layout: sidebar + right panel ── */
    #main { display: flex; flex: 1; overflow: hidden; }

    /* ── file browser sidebar ── */
    #sidebar { width: 320px; min-width: 220px; max-width: 480px; background: #fff;
                border-right: 1px solid #ddd; display: flex; flex-direction: column;
                overflow: hidden; flex-shrink: 0; }
    #sidebar.hidden { display: none; }
    #browser-header { background: #ede7f6; padding: 7px 10px; font-size: 12px;
                       font-weight: 600; color: #4a148c; border-bottom: 1px solid #ddd;
                       flex-shrink: 0; }
    #breadcrumb { padding: 6px 10px; font-size: 11px; color: #555; background: #fafafa;
                   border-bottom: 1px solid #eee; word-break: break-all;
                   flex-shrink: 0; line-height: 1.6; }
    #breadcrumb span { cursor: pointer; color: #4a148c; }
    #breadcrumb span:hover { text-decoration: underline; }
    #file-list { overflow-y: auto; flex: 1; }
    .entry { display: flex; align-items: center; gap: 7px; padding: 6px 10px;
              cursor: pointer; font-size: 12px; border-bottom: 1px solid #f0f0f0;
              user-select: none; }
    .entry:hover { background: #ede7f6; }
    .entry.nc-file { color: #4a148c; }
    .entry.nc-file:hover { background: #f3e5f5; }
    .entry.dir::before     { content: "📁"; font-size: 14px; }
    .entry.nc-file::before { content: "📄"; font-size: 14px; }
    .entry.up::before      { content: "⬆️"; font-size: 14px; }

    /* ── right panel ── */
    #right { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

    #placeholder { flex: 1; display: flex; align-items: center; justify-content: center;
                    color: #aaa; font-size: 15px; flex-direction: column; gap: 10px; }

    #content-panel { flex: 1; display: none; flex-direction: column; overflow-y: auto;
                      padding: 10px 14px 6px; }
    #content-panel.visible { display: flex; }

    #file-label { font-size: 12px; color: #555; margin-bottom: 4px; word-break: break-all; }

    /* ── metadata strip ── */
    #meta-strip { display: flex; flex-wrap: wrap; gap: 6px 20px; font-size: 11px;
                   color: #666; margin-bottom: 6px; flex-shrink: 0; }
    .meta-item b { color: #4a148c; }

    /* ── controls bar ── */
    #controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px;
                margin-bottom: 6px; flex-shrink: 0; }
    .ctrl-group { display: flex; align-items: center; gap: 5px; }
    .ctrl-label { font-size: 12px; color: #555; font-weight: 500; }
    input.param-input { width: 100px; padding: 4px 6px; border: 1px solid #bbb;
                         border-radius: 4px; font-size: 12px; }
    input.param-input.overridden { border-color: #e65100; background: #fff8f5; }
    button.action { padding: 5px 13px; border: none; border-radius: 4px;
                    cursor: pointer; font-size: 13px; font-weight: 500; }
    #btn-clear-sel { background: #757575; color: #fff; }
    #btn-quit      { background: #c62828; color: #fff; }
    button.action:hover    { opacity: .85; }
    button.action:disabled { opacity: .4; cursor: default; }

    #hint { font-size: 11px; color: #999; margin-bottom: 6px; flex-shrink: 0; }

    /* ── chart ── */
    #chart { flex: 0 0 auto; height: 320px; min-height: 200px; }

    /* ── results panel ── */
    #results {
      flex-shrink: 0; background: #fff; border: 1px solid #ddd;
      border-radius: 6px; padding: 10px 14px; margin-top: 8px;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 6px 20px;
    }
    #results.empty {
      display: block; color: #aaa; font-style: italic;
      text-align: center; padding: 10px;
    }
    .res-item { display: flex; flex-direction: column; gap: 2px; }
    .res-label { color: #777; font-size: 11px; }
    .res-value { font-size: 14px; font-weight: 600; color: #333; }
    .res-value.good { color: #2e7d32; }
    .res-value.warn { color: #e65100; }
    .res-value.bad  { color: #c62828; }
  </style>
</head>
<body>

<div id="topbar">
  <h1>Burette Calibration Analysis</h1>
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
      <span style="font-size:48px;opacity:.3">🧪</span>
      <span>Select a NetCDF file from the browser to begin</span>
    </div>

    <div id="content-panel">
      <div id="file-label"></div>
      <div id="meta-strip"></div>

      <div id="controls">
        <div class="ctrl-group">
          <span class="ctrl-label">Burette volume (ml):</span>
          <input type="number" id="vol-input" class="param-input" value="50" min="0.1" step="0.1">
        </div>
        <div class="ctrl-group">
          <span class="ctrl-label">Collection area (m²):</span>
          <input type="number" id="area-input" class="param-input" value="" step="0.0001"
                 placeholder="from file">
        </div>
        <div class="ctrl-group">
          <span class="ctrl-label">Quanta (mm):</span>
          <input type="number" id="quanta-input" class="param-input" value="" step="0.00001"
                 placeholder="from file">
        </div>
        <button class="action" id="btn-clear-sel">Clear selection</button>
        <button class="action" id="btn-quit">Quit</button>
      </div>

      <div id="hint">
        <span style="display:inline-block;width:10px;height:10px;background:#ab47bc;border-radius:2px;vertical-align:middle"></span> Purple bars are pre-flagged as burette calibration in the NetCDF and are auto-selected.
        <span style="display:inline-block;width:10px;height:10px;background:#f06292;border-radius:2px;vertical-align:middle;margin-left:8px"></span> Pink bars are manually drag-selected.
        Drag to set or replace the selection window. Override collection area or quanta if needed (shown in orange).
      </div>

      <div id="chart"></div>
      <div id="results" class="empty">Drag to select a calibration window above</div>
    </div>
  </div>
</div>

<script>
'use strict';

// ── state ──────────────────────────────────────────────────────────────────
let times = [], drops = [];
let fileQuantaMm  = null;   // quanta from the NetCDF file
let fileAreaM2    = null;   // collection_area from the NetCDF file
let fileLoaded    = false;
let sidebarOpen   = true;
let countLabel    = 'Drops';
let selIndices    = [];   // currently selected indices (analysis window)
let qcCalIndices  = [];   // indices pre-flagged as burette_cal in the NetCDF

// ── colours ────────────────────────────────────────────────────────────────
const COLOR_NORMAL  = '#7986cb';   // unselected bars
const COLOR_QC_CAL  = '#ab47bc';   // pre-flagged burette-cal (QC flag)
const COLOR_SEL     = '#f06292';   // manually drag-selected

function barColors(selSet) {
  const qcSet = new Set(qcCalIndices);
  return drops.map((_, i) => {
    if (selSet.has(i))  return COLOR_SEL;
    if (qcSet.has(i))   return COLOR_QC_CAL;
    return COLOR_NORMAL;
  });
}

// ── helpers ────────────────────────────────────────────────────────────────
async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  return r.json();
}

function getVolMl() {
  const v = parseFloat(document.getElementById('vol-input').value);
  return (isNaN(v) || v <= 0) ? null : v;
}

function getAreaM2() {
  const inp = document.getElementById('area-input');
  const v   = parseFloat(inp.value);
  return (isNaN(v) || v <= 0) ? null : v;
}

function getQuantaMm() {
  const inp = document.getElementById('quanta-input');
  const v   = parseFloat(inp.value);
  return (isNaN(v) || v <= 0) ? null : v;
}

// Mark area/quanta fields orange when manually overridden
function updateOverrideStyles() {
  const areaInp   = document.getElementById('area-input');
  const quantaInp = document.getElementById('quanta-input');
  const areaVal   = parseFloat(areaInp.value);
  const quantaVal = parseFloat(quantaInp.value);
  areaInp.classList.toggle('overridden',
    fileAreaM2 !== null && !isNaN(areaVal) && Math.abs(areaVal - fileAreaM2) > 1e-9);
  quantaInp.classList.toggle('overridden',
    fileQuantaMm !== null && !isNaN(quantaVal) && Math.abs(quantaVal - fileQuantaMm) > 1e-10);
}

// ── sidebar toggle ─────────────────────────────────────────────────────────
function toggleSidebar() {
  sidebarOpen = !sidebarOpen;
  document.getElementById('sidebar').classList.toggle('hidden', !sidebarOpen);
  document.getElementById('btn-choose').textContent =
    sidebarOpen ? '✕ Close browser' : '📁 Browse files';
}

// ── file browser ───────────────────────────────────────────────────────────
async function browse(path) {
  const r = await fetch('/browse?path=' + encodeURIComponent(path));
  const d = await r.json();

  // breadcrumb
  const crumb = document.getElementById('breadcrumb');
  const parts  = d.path.split('/').filter(Boolean);
  crumb.innerHTML = '<span onclick="browse(\'/\')">/ </span>' +
    parts.map((p, i) => {
      const fp = '/' + parts.slice(0, i + 1).join('/');
      return `<span onclick="browse('${fp.replace(/'/g, "\\'")}')">${p}/</span>`;
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
    el.onclick = () => browse(dir.path);
    list.appendChild(el);
  }
  for (const f of d.files) {
    const el = document.createElement('div');
    el.className = 'entry nc-file';
    el.textContent = '  ' + f.name;
    el.onclick = () => openFile(f.path);
    list.appendChild(el);
  }
}

// ── open file ──────────────────────────────────────────────────────────────
async function openFile(path) {
  const d = await post('/open', { path });
  if (d.error) { alert('Error: ' + d.error); return; }

  times        = d.times;
  drops        = d.drops;
  fileQuantaMm = d.quanta_mm;
  fileAreaM2   = d.area_m2;
  countLabel   = d.count_label || 'Drops';
  qcCalIndices = d.qc_cal_indices || [];
  // Auto-select QC-flagged burette-cal samples as the initial analysis window
  selIndices   = [...qcCalIndices];
  fileLoaded   = true;

  document.getElementById('file-label').textContent = path;

  // Pre-populate parameter inputs from the file
  const areaInp   = document.getElementById('area-input');
  const quantaInp = document.getElementById('quanta-input');
  if (fileAreaM2   !== null) areaInp.value   = fileAreaM2.toFixed(6);
  else                        areaInp.value   = '';
  if (fileQuantaMm !== null) quantaInp.value = fileQuantaMm.toFixed(6);
  else                        quantaInp.value = '';
  updateOverrideStyles();

  // Metadata strip
  const ms = document.getElementById('meta-strip');
  ms.innerHTML = '';
  if (d.source)
    ms.insertAdjacentHTML('beforeend',
      `<span class="meta-item"><b>Source:</b> ${d.source}</span>`);
  if (fileQuantaMm !== null)
    ms.insertAdjacentHTML('beforeend',
      `<span class="meta-item"><b>Measurement quanta:</b> ${fileQuantaMm.toFixed(5)} mm/drop</span>`);
  if (fileAreaM2 !== null)
    ms.insertAdjacentHTML('beforeend',
      `<span class="meta-item"><b>Collection area:</b> ${fileAreaM2.toFixed(4)} m²</span>`);
  if (qcCalIndices.length)
    ms.insertAdjacentHTML('beforeend',
      `<span class="meta-item" style="color:#ab47bc"><b>QC burette-cal samples:</b> ${qcCalIndices.length}</span>`);

  document.getElementById('placeholder').style.display = 'none';
  document.getElementById('content-panel').classList.add('visible');
  initChart();
  // Run analysis immediately if QC-flagged samples were found
  if (selIndices.length) {
    highlightAndAnalyse();
  } else {
    resetResults();
  }

  if (window.innerWidth < 900 && sidebarOpen) toggleSidebar();
}

// ── chart ──────────────────────────────────────────────────────────────────
function initChart() {
  const trace = {
    x: times, y: drops, type: 'bar',
    marker: { color: barColors(new Set()), line: { width: 0 } },
    hovertemplate: `%{x|%H:%M:%S}<br>${countLabel}: %{y}<extra></extra>`,
    selected:   { marker: { opacity: 1 } },
    unselected: { marker: { opacity: 0.5 } },
  };

  const layout = {
    xaxis: {
      title: 'Time (UTC)', type: 'date', tickformat: '%H:%M',
      rangeslider: { visible: true, thickness: 0.05 },
    },
    yaxis: { title: `${countLabel} per 10 s` },
    bargap: 0,
    margin: { t: 6, r: 16, b: 80, l: 55 },
    dragmode: 'select',
    plot_bgcolor: '#fff', paper_bgcolor: '#fff',
  };

  Plotly.newPlot('chart', [trace], layout, { responsive: true });

  document.getElementById('chart').on('plotly_selected', (data) => {
    if (!data || !data.points.length) return;
    selIndices = data.points.map(p => p.pointIndex);
    highlightAndAnalyse();
  });

  document.getElementById('chart').on('plotly_deselect', () => {
    // Revert to QC-flagged selection (if any) rather than clearing everything
    selIndices = [...qcCalIndices];
    Plotly.restyle('chart', { 'marker.color': [barColors(new Set())] }, [0]);
    if (selIndices.length) runAnalysis(); else resetResults();
  });
}

function highlightAndAnalyse() {
  Plotly.restyle('chart', { 'marker.color': [barColors(new Set(selIndices))] }, [0]);
  runAnalysis();
}

// ── calibration analysis ───────────────────────────────────────────────────

// thickness_mm = volume_ml * 1000 mm³/ml  /  (area_m2 * 1e6 mm²/m²)
//              = volume_ml / (area_m2 * 1000)
function expectedMm(volume_ml, area_m2) {
  return (volume_ml * 1000.0) / (area_m2 * 1.0e6);
}

function ratioClass(ratio) {
  const pctErr = Math.abs(ratio - 1.0) * 100.0;
  if (pctErr < 2.0) return 'good';
  if (pctErr < 5.0) return 'warn';
  return 'bad';
}

function runAnalysis() {
  if (!selIndices.length) { resetResults(); return; }

  const sorted     = [...selIndices].sort((a, b) => a - b);
  const totalDrops = sorted.reduce((s, i) => s + drops[i], 0);
  const nSamples   = sorted.length;
  const tStart     = times[sorted[0]].replace('T', ' ');
  const tEnd       = times[sorted[sorted.length - 1]].replace('T', ' ');

  const quantaMm   = getQuantaMm();
  const areaM2     = getAreaM2();
  const volMl      = getVolMl();

  const measuredMm = (quantaMm !== null) ? totalDrops * quantaMm : null;
  const expMm      = (volMl !== null && areaM2 !== null) ? expectedMm(volMl, areaM2) : null;
  const ratio      = (measuredMm !== null && expMm !== null && expMm > 0)
                     ? measuredMm / expMm : null;
  const pctErr     = ratio !== null ? (ratio - 1.0) * 100.0 : null;
  const rc         = ratio !== null ? ratioClass(ratio) : '';

  // Drop volume derived directly from burette: V_drop = V_burette / N_drops
  // Derived quanta (mm): thickness per drop = V_drop_ml / (area_m2 * 1000)
  const dropVolMl      = (volMl !== null && totalDrops > 0) ? volMl / totalDrops : null;
  const dropVolUl      = dropVolMl !== null ? dropVolMl * 1000.0 : null;
  const derivedQuantaMm = (dropVolMl !== null && areaM2 !== null)
                          ? dropVolMl / (areaM2 * 1000.0) : null;
  // Compare derived quanta with the file quanta
  const quantaDiffPct  = (derivedQuantaMm !== null && quantaMm !== null && quantaMm > 0)
                         ? (derivedQuantaMm / quantaMm - 1.0) * 100.0 : null;
  const qdc            = quantaDiffPct !== null ? ratioClass(1.0 + quantaDiffPct / 100.0) : '';

  const fmt  = (v, dp, unit) => v !== null ? `${v.toFixed(dp)} ${unit}` : 'N/A';
  const fmtN = (v, dp)       => v !== null ? v.toFixed(dp) : 'N/A';

  const res = document.getElementById('results');
  res.className = '';
  res.innerHTML = `
    <div class="res-item" style="grid-column:1/-1;border-bottom:1px solid #eee;padding-bottom:4px;margin-bottom:2px">
      <span class="res-label" style="font-size:12px;font-weight:600;color:#4a148c">― Calibration result</span>
    </div>
    <div class="res-item">
      <span class="res-label">Drop volume (from burette)</span>
      <span class="res-value" style="color:#4a148c">${fmt(dropVolUl, 4, 'µl')}</span>
    </div>
    <div class="res-item">
      <span class="res-label">Derived quanta</span>
      <span class="res-value" style="color:#4a148c">${fmt(derivedQuantaMm, 6, 'mm')}</span>
    </div>
    <div class="res-item">
      <span class="res-label">File quanta</span>
      <span class="res-value">${quantaMm !== null ? quantaMm.toFixed(6) + ' mm' : 'N/A'}</span>
    </div>
    <div class="res-item">
      <span class="res-label">Quanta difference</span>
      <span class="res-value ${qdc}">${quantaDiffPct !== null ? quantaDiffPct.toFixed(2) + ' %' : 'N/A'}</span>
    </div>
    <div class="res-item" style="grid-column:1/-1;border-bottom:1px solid #eee;padding-bottom:4px;margin-bottom:2px;margin-top:4px">
      <span class="res-label" style="font-size:12px;font-weight:600;color:#555">― Detail</span>
    </div>
    <div class="res-item">
      <span class="res-label">Window start</span>
      <span class="res-value">${tStart}</span>
    </div>
    <div class="res-item">
      <span class="res-label">Window end</span>
      <span class="res-value">${tEnd}</span>
    </div>
    <div class="res-item">
      <span class="res-label">Samples selected</span>
      <span class="res-value">${nSamples}</span>
    </div>
    <div class="res-item">
      <span class="res-label">Total ${countLabel.toLowerCase()}</span>
      <span class="res-value">${totalDrops}</span>
    </div>
    <div class="res-item">
      <span class="res-label">Burette volume</span>
      <span class="res-value">${volMl !== null ? volMl.toFixed(1) + ' ml' : 'N/A'}</span>
    </div>
    <div class="res-item">
      <span class="res-label">Collection area</span>
      <span class="res-value">${areaM2 !== null ? areaM2.toFixed(6) + ' m²' : 'N/A'}</span>
    </div>
    <div class="res-item">
      <span class="res-label">Expected thickness</span>
      <span class="res-value">${fmt(expMm, 4, 'mm')}</span>
    </div>
    <div class="res-item">
      <span class="res-label">Measured thickness</span>
      <span class="res-value">${fmt(measuredMm, 4, 'mm')}</span>
    </div>
    <div class="res-item">
      <span class="res-label">Ratio (measured / expected)</span>
      <span class="res-value ${rc}">${fmtN(ratio, 5)}</span>
    </div>
    <div class="res-item">
      <span class="res-label">Error</span>
      <span class="res-value ${rc}">${pctErr !== null ? pctErr.toFixed(2) + ' %' : 'N/A'}</span>
    </div>
  `;
}

function resetResults() {
  const res = document.getElementById('results');
  res.className = 'empty';
  res.textContent = 'Drag to select a calibration window above';
}

// ── control listeners ──────────────────────────────────────────────────────
['vol-input', 'area-input', 'quanta-input'].forEach(id => {
  document.getElementById(id).addEventListener('input', () => {
    updateOverrideStyles();
    if (selIndices.length) runAnalysis();
  });
});

document.getElementById('btn-clear-sel').addEventListener('click', () => {
  selIndices = [];
  qcCalIndices = [];   // also clear the QC-derived auto-selection
  if (fileLoaded) {
    Plotly.restyle('chart', { 'marker.color': [barColors(new Set())] }, [0]);
    Plotly.relayout('chart', { selections: [] });
  }
  resetResults();
});

document.getElementById('btn-quit').addEventListener('click', async () => {
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  post('/quit');
});

// ── boot ───────────────────────────────────────────────────────────────────
(async () => {
  try {
    const init = await (await fetch('/init')).json();
    await browse(init.start_dir);
    if (init.preload) {
      await openFile(init.preload);
      if (sidebarOpen) toggleSidebar();
    }
  } catch (err) {
    console.error('Boot error:', err);
    const ph = document.getElementById('placeholder');
    ph.innerHTML = `<span style="color:#c62828;font-size:14px;padding:20px;text-align:center">
      &#9888; Error loading page:<br>
      <code style="font-size:12px">${err.message}</code><br><br>
      Check the browser console (F12) and the server terminal for details.</span>`;
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
        return jsonify({
            'start_dir': _state.get('start_dir', start_dir),
            'preload':   _state.get('nc_path'),
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
            with nc4.Dataset(str(p)) as ds:
                unix      = ds.variables['time'][:].data.copy()
                count_var = (
                    'number_of_tips'
                    if 'number_of_tips' in ds.variables
                    else 'number_of_drops'
                )
                drops = ds.variables[count_var][:].data.astype(float)

                # Read global attributes
                area_m2   = None
                quanta_mm = None
                source    = None

                raw_area   = getattr(ds, 'collection_area',    None)
                raw_quanta = getattr(ds, 'measurement_quanta', None)
                source     = getattr(ds, 'source',             None)

                if raw_area:
                    try:
                        area_m2 = _collection_area_m2(raw_area)
                    except ValueError:
                        pass
                if raw_quanta:
                    try:
                        quanta_mm = _quanta_mm(raw_quanta)
                    except ValueError:
                        pass

                # Find samples QC-flagged as burette calibration
                qc_cal_indices = []
                if 'qc_flag' in ds.variables:
                    qc_arr = ds.variables['qc_flag'][:].data.astype(int)
                    qc_var = ds.variables['qc_flag']
                    # Determine the flag value for burette calibration by
                    # inspecting flag_values / flag_meanings attributes
                    cal_flag_val = None
                    flag_vals     = getattr(qc_var, 'flag_values',   None)
                    flag_meanings = getattr(qc_var, 'flag_meanings',  None)
                    if flag_vals is not None and flag_meanings is not None:
                        meanings = str(flag_meanings).split()
                        vals     = np.asarray(flag_vals).flatten().tolist()
                        for val, meaning in zip(vals, meanings):
                            if 'calibration' in meaning.lower():
                                cal_flag_val = int(val)
                                break
                    # Fall back to the value used in gauge_qc_web FLAG_REGISTRY
                    if cal_flag_val is None:
                        cal_flag_val = 5
                    qc_cal_indices = [int(i) for i in np.where(qc_arr == cal_flag_val)[0]]
        except Exception as exc:
            return jsonify({'error': str(exc)})

        times_iso = pd.to_datetime(unix, unit='s').strftime('%Y-%m-%dT%H:%M:%S').tolist()
        _state.update({
            'loaded':    True,
            'nc_path':   str(p),
            'drops':     drops,
            'count_var': count_var,
            'times_iso': times_iso,
        })

        count_label = 'Tips' if count_var == 'number_of_tips' else 'Drops'
        return jsonify({
            'times':           times_iso,
            'drops':           drops.tolist(),
            'quanta_mm':       quanta_mm,
            'area_m2':         area_m2,
            'source':          source,
            'count_label':     count_label,
            'qc_cal_indices':  qc_cal_indices,
        })

    @app.route('/quit', methods=['POST'])
    def quit_server():
        import os
        import signal
        import time as _time

        def _stop():
            _time.sleep(0.3)
            os.kill(os.getpid(), signal.SIGINT)

        threading.Thread(target=_stop, daemon=True).start()
        return jsonify({'status': 'quitting'})

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Browser-based burette calibration analysis.\n\n"
            "Run on the remote machine:\n"
            "  burette-cal-web  [file.nc]  [--dir /path]  [--port 8766]\n\n"
            "Forward the port from your local machine:\n"
            "  ssh -L 8766:localhost:8766  <user>@<host>\n\n"
            "Then open:  http://localhost:8766"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('nc_file', nargs='?', default=None,
                        help='NetCDF file to open immediately (optional)')
    parser.add_argument('-d', '--dir', default=None,
                        help='Starting directory for the file browser '
                             '(default: directory of nc_file, or home directory)')
    parser.add_argument('-p', '--port', type=int, default=8766,
                        help='Local port to serve on (default: 8766)')
    args = parser.parse_args()

    if args.dir:
        start_dir = str(Path(args.dir).resolve())
    elif args.nc_file:
        start_dir = str(Path(args.nc_file).resolve().parent)
    else:
        start_dir = str(Path.home())

    _state['start_dir'] = start_dir

    _load_plotly()

    if args.nc_file:
        _state['nc_path'] = str(Path(args.nc_file).resolve())

    port = args.port
    print()
    print("=" * 62)
    print("  Burette Calibration Analysis — browser interface")
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
    print("  Press Ctrl-C to quit.")
    print()

    app = _make_app(start_dir)
    try:
        app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\nDone.")


if __name__ == '__main__':
    main()
