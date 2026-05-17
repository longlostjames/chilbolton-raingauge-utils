#!/usr/bin/env python3
"""
Browser-based interactive multi-gauge comparison, with cross-correlation
analysis.  Designed for access via SSH local port forwarding.

Run on the remote machine (JASMIN / HPC):
    compare-gauges-web  [--dir /path/to/nc/files]  [--port 8766]

Forward the port from your local machine in a separate terminal:
    ssh -L 8766:localhost:8766  <user>@<host>

Then open in your browser:
    http://localhost:8766
"""

import argparse
import logging
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

# Gauge slot colours – visually distinct and accessible
_SLOT_COLORS = ["#1e88e5", "#e53935", "#43a047", "#fb8c00", "#8e24aa", "#00acc1"]
_MAX_GAUGES = len(_SLOT_COLORS)

# Server state: list of loaded gauge dicts (or None for empty slots)
_gauges: list[dict | None] = [None] * _MAX_GAUGES

# ---------------------------------------------------------------------------
# HTML / JS single-page application
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Gauge Comparator</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body { font-family: system-ui, sans-serif; margin: 0; background: #f0f2f5;
           color: #333; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

    /* ── top bar ── */
    #topbar { background: #2e7d32; color: #fff; padding: 7px 14px;
               display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
    #topbar h1 { margin: 0; font-size: 15px; font-weight: 600; flex: 1; }
    #btn-browse { background: rgba(255,255,255,.15); color: #fff; border: 1px solid rgba(255,255,255,.4);
                  border-radius: 4px; padding: 5px 13px; cursor: pointer; font-size: 12px; }
    #btn-browse:hover { background: rgba(255,255,255,.25); }

    /* ── main layout ── */
    #main { display: flex; flex: 1; overflow: hidden; }

    /* ── file browser sidebar ── */
    #sidebar { width: 320px; min-width: 220px; max-width: 480px; background: #fff;
                border-right: 1px solid #ddd; display: flex; flex-direction: column;
                overflow: hidden; flex-shrink: 0; transition: width 0.15s; }
    #sidebar.hidden { display: none; }
    #browser-header { background: #e8f5e9; padding: 7px 10px; font-size: 12px;
                       font-weight: 600; color: #2e7d32; border-bottom: 1px solid #ddd;
                       flex-shrink: 0; }
    #folder-actions { padding: 5px 10px; background: #f9fbe7; border-bottom: 1px solid #ddd;
                       flex-shrink: 0; display: none; }
    #btn-load-dir { background: #558b2f; color: #fff; border: none; border-radius: 4px;
                    padding: 4px 12px; cursor: pointer; font-size: 11px; font-weight: 600; }
    #btn-load-dir:hover { opacity: .85; }
    #load-dir-status { font-size: 11px; color: #777; margin-left: 8px; }
    #breadcrumb { padding: 6px 10px; font-size: 11px; color: #555; background: #fafafa;
                   border-bottom: 1px solid #eee; word-break: break-all;
                   flex-shrink: 0; line-height: 1.6; }
    #breadcrumb span { cursor: pointer; color: #2e7d32; }
    #breadcrumb span:hover { text-decoration: underline; }
    #file-list { overflow-y: auto; flex: 1; }
    .entry { display: flex; align-items: center; gap: 7px; padding: 6px 10px;
              cursor: pointer; font-size: 12px; border-bottom: 1px solid #f0f0f0;
              user-select: none; }
    .entry:hover { background: #e8f5e9; }
    .entry.nc-file { color: #2e7d32; }
    .entry.dir::before     { content: "📁"; font-size: 14px; }
    .entry.nc-file::before { content: "📄"; font-size: 14px; }
    .entry.up::before      { content: "⬆️"; font-size: 14px; }

    /* ── gauge slot selector (inside sidebar) ── */
    #slot-select { padding: 8px 10px; background: #f1f8e9; border-bottom: 1px solid #ddd;
                    flex-shrink: 0; font-size: 12px; }
    #slot-select label { font-weight: 600; display: block; margin-bottom: 5px; }
    .slot-btn { display: inline-flex; align-items: center; gap: 5px; margin: 2px;
                 padding: 4px 10px; border: 2px solid transparent; border-radius: 4px;
                 cursor: pointer; font-size: 11px; font-weight: 600; background: #eee; color: #333; }
    .slot-btn.active { border-color: #333; }
    .slot-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }

    /* ── right panel ── */
    #right { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

    /* gauge badge strip */
    #gauge-strip { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 12px;
                    background: #fff; border-bottom: 1px solid #e0e0e0; flex-shrink: 0; }
    .g-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px;
                border-radius: 12px; font-size: 11px; border: 2px solid transparent; }
    .g-badge .g-remove { cursor: pointer; font-weight: 700; margin-left: 4px; opacity: .6; }
    .g-badge .g-remove:hover { opacity: 1; }
    .g-empty { color: #bbb; font-size: 12px; font-style: italic; }

    /* chart tabs */
    #tab-bar { display: flex; padding: 0 12px; background: #fff;
                border-bottom: 1px solid #ddd; flex-shrink: 0; }
    .tab { padding: 8px 16px; cursor: pointer; font-size: 12px; font-weight: 500;
            color: #555; border-bottom: 3px solid transparent; }
    .tab.active { color: #2e7d32; border-bottom-color: #2e7d32; }
    .tab:hover:not(.active) { background: #f5f5f5; }

    #charts { flex: 1; overflow: hidden; display: flex; flex-direction: column; }
    .chart-pane { flex: 1; display: none; flex-direction: column; overflow: hidden; padding: 10px 12px 6px; }
    .chart-pane.active { display: flex; }
    #chart-ts, #chart-xcorr { flex: 1; min-height: 150px; }
    #xcorr-controls { flex-shrink: 0; }
    #ts-placeholder { flex: 1; display: flex; align-items: center; justify-content: center;
                       flex-direction: column; gap: 10px; color: #bbb; font-size: 14px; }

    /* stats table */
    #stats-pane { flex: 1; display: none; flex-direction: column; overflow: auto; padding: 12px; }
    #stats-pane.active { display: flex; }
    #stats-table { border-collapse: collapse; font-size: 12px; width: 100%; }
    #stats-table th { background: #e8f5e9; padding: 6px 10px; text-align: left;
                       border: 1px solid #c8e6c9; }
    #stats-table td { padding: 6px 10px; border: 1px solid #e0e0e0; }
    #stats-table tr:nth-child(even) td { background: #fafafa; }

    /* xcorr controls */
    #xcorr-controls { display: flex; align-items: center; gap: 10px; flex-shrink: 0;
                       padding: 6px 0; flex-wrap: wrap; font-size: 12px; }
    #xcorr-controls label { font-weight: 500; }
    #xcorr-controls select, #xcorr-controls input { font-size: 12px; padding: 3px 6px;
                                                      border: 1px solid #ccc; border-radius: 3px; }
    #btn-xcorr, #btn-scatter { background: #2e7d32; color: #fff; border: none; border-radius: 4px;
                  padding: 5px 14px; cursor: pointer; font-size: 12px; font-weight: 500; }
    #btn-xcorr:hover, #btn-scatter:hover { opacity: .85; }
    #xcorr-status, #scatter-status { font-size: 11px; color: #888; }
    #range-pill { display:none; font-size:11px; color:#fff; background:#e65100;
                  border-radius:10px; padding:2px 8px; margin-left:10px; align-items:center; gap:4px; }
    #range-pill.active { display:inline-flex; }
    #range-pill label { font-size:11px; color:#fff; white-space:nowrap; }
    #range-pill input[type=datetime-local] {
      font-size:11px; border:none; border-radius:4px; padding:1px 4px;
      background:rgba(255,255,255,0.25); color:#fff; outline:none; cursor:text;
      width:13em; colorscheme:dark; }
    #range-pill input[type=datetime-local]:focus { background:rgba(255,255,255,0.4); }
    #btn-clear-range { background:none; border:none; color:#fff; font-size:12px;
                       cursor:pointer; padding:0 2px; line-height:1; }

    #placeholder { flex: 1; display: flex; align-items: center; justify-content: center;
                    color: #aaa; font-size: 14px; flex-direction: column; gap: 10px; }
  </style>
</head>
<body>

<div id="topbar">
  <h1>Gauge Comparator</h1>
  <button id="btn-browse" onclick="toggleSidebar()">📁 Browse files</button>
</div>

<div id="main">

  <!-- sidebar -->
  <div id="sidebar">
    <div id="browser-header">File browser</div>
    <div id="slot-select">
      <label>Load file into slot:</label>
      <div id="slot-buttons"></div>
    </div>
    <div id="breadcrumb"></div>
    <div id="folder-actions">
      <button id="btn-load-dir" onclick="loadDir()">📅 Load all in folder into slot</button>
      <span id="load-dir-status"></span>
    </div>
    <div id="file-list"></div>
  </div>

  <!-- right panel -->
  <div id="right">
    <!-- loaded gauge badges -->
    <div id="gauge-strip">
      <span class="g-empty" id="strip-empty">No gauges loaded — browse to a NetCDF file and click to load it</span>
    </div>

    <!-- tab bar -->
    <div id="tab-bar">
      <div class="tab active" onclick="showTab('ts')">Time series</div>
      <div class="tab" onclick="showTab('scatter')">Scatter</div>
      <div class="tab" onclick="showTab('xcorr')">Cross-correlation</div>
      <div class="tab" onclick="showTab('cumul')">Cumulative</div>
      <div class="tab" onclick="showTab('dmass')">Double mass</div>
      <div class="tab" onclick="showTab('stats')">Statistics</div>
      <span id="range-pill">
        <label>Range:</label>
        <input type="datetime-local" id="range-t0" step="60" title="Range start (UTC)">
        <label>–</label>
        <input type="datetime-local" id="range-t1" step="60" title="Range end (UTC)">
        <label>UTC</label>
        <button id="btn-clear-range" title="Clear range selection" onclick="clearTsRange()">✕</button>
      </span>
    </div>

    <div id="charts">
      <!-- time series -->
      <div class="chart-pane active" id="pane-ts">
        <div id="ts-placeholder">
          <span style="font-size:48px;opacity:.25">📈</span>
          <span>Browse to a NetCDF file in the sidebar and click it to load a gauge</span>
        </div>
        <div id="chart-ts" style="display:none"></div>
      </div>

      <!-- scatter -->
      <div class="chart-pane" id="pane-scatter">
        <div id="xcorr-controls">
          <label>Gauge A (x):</label>
          <select id="scatter-a"></select>
          <label>Gauge B (y):</label>
          <select id="scatter-b"></select>
          <span style="font-size:12px;font-weight:500">Type:</span>
          <label class="fm-opt" style="--c:#2e7d32">
            <input type="radio" name="scattertype" value="scatter" checked> Scatter
          </label>
          <label class="fm-opt" style="--c:#2e7d32">
            <input type="radio" name="scattertype" value="hist2d"> 2D histogram
          </label>
          <label class="fm-opt" style="--c:#2e7d32">
            <input type="checkbox" id="scatter-wet-only" checked> Wet hours only
          </label>
          <span style="font-size:12px;font-weight:500">Fit:</span>
          <label class="fm-opt" style="--c:#2e7d32">
            <input type="radio" name="fitmethod" value="ols" checked> OLS
          </label>
          <label class="fm-opt" style="--c:#2e7d32">
            <input type="radio" name="fitmethod" value="theilsen"> Theil-Sen
          </label>
          <label style="font-size:12px;font-weight:500">Threshold (mm):</label>
          <input id="scatter-ols-thresh" type="number" value="0.2" min="0" step="0.01"
                 style="width:70px" title="Hours where both gauges are at or above this value are used for the regression">
          <button id="btn-scatter" onclick="runScatter()">Plot</button>
          <span id="scatter-status"></span>
        </div>
        <div id="chart-scatter" style="flex:1;min-height:150px"></div>
      </div>

      <!-- cross-correlation -->
      <div class="chart-pane" id="pane-xcorr">
        <div id="xcorr-controls">
          <label>Gauge A:</label>
          <select id="xcorr-a"></select>
          <label>Gauge B:</label>
          <select id="xcorr-b"></select>
          <label>Max lag (min):</label>
          <input id="xcorr-lag" type="number" value="60" min="1" max="1440" style="width:70px">
          <button id="btn-xcorr" onclick="runXcorr()">Compute</button>
          <span id="xcorr-status"></span>
        </div>
        <div id="chart-xcorr"></div>
      </div>

      <!-- cumulative rainfall -->
      <div class="chart-pane" id="pane-cumul">
        <div id="cumul-filter-row" style="display:none; padding:5px 12px; font-size:12px; color:#555; background:#f5f0fb; border-bottom:1px solid #d1c4e9; flex-shrink:0; flex-wrap:wrap; gap:10px; align-items:center;">
          <span style="font-weight:600; color:#4a148c">TB day filter:</span>
          <label style="cursor:pointer"><input type="checkbox" id="filt-missing" onchange="refreshCumul()"> Exclude days with missing TB data</label>
          <label style="cursor:pointer"><input type="checkbox" id="filt-few-tips" onchange="refreshCumul()"> Exclude days with &lt;4 TB tips</label>
          <label style="cursor:pointer"><input type="checkbox" id="filt-dc-rain" onchange="refreshCumul()"> Exclude days with 0 TB rain but &gt;1mm DC rain</label>
          <label style="cursor:pointer"><input type="checkbox" id="filt-dc-zero" onchange="refreshCumul()"> Exclude periods when all DC gauges read zero</label>
          <span id="cumul-excl-label" style="color:#7b1fa2; font-style:italic"></span>
        </div>
        <div id="chart-cumul" style="flex:1;min-height:150px"></div>
      </div>

      <!-- double mass curve -->
      <div class="chart-pane" id="pane-dmass">
        <div id="dmass-controls" style="display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:5px 12px;font-size:12px;flex-shrink:0;border-bottom:1px solid #eee">
          <label>TB gauge (x):</label>
          <select id="dmass-tb"></select>
          <label>DC gauge (y):</label>
          <select id="dmass-dc"></select>
          <label style="cursor:pointer" title="Only accumulate during periods when the DC gauge is recording rain (\u00b130 min window around each drop)"><input type="checkbox" id="dmass-rain-only" checked onchange="refreshDMass()"> Rain events only</label>
          <label>Event window (min):</label>
          <input id="dmass-window" type="number" value="30" min="0" max="360" step="10" style="width:60px" onchange="refreshDMass()">
          <label style="cursor:pointer" title="Exclude events where the per-event DC/TB ratio deviates from the median by more than the threshold"><input type="checkbox" id="dmass-filter-outliers" onchange="refreshDMass()"> Exclude outlier events</label>
          <input id="dmass-outlier-thresh" type="number" value="50" min="5" max="200" step="5" style="width:50px" title="% deviation from median event ratio" onchange="refreshDMass()">%
          <button onclick="refreshDMass()">Plot</button>
          <span id="dmass-status" style="color:#555"></span>
        </div>
        <div id="chart-dmass" style="flex:1;min-height:150px"></div>
      </div>

      <!-- stats -->
      <div id="stats-pane" class="chart-pane">
        <div id="stats-content">
          <span style="color:#aaa; font-size:13px">Load at least one gauge to see statistics.</span>
        </div>
      </div>
    </div>

  </div>
</div>

<script src="/plotly.js"></script>
<script>
'use strict';

const SLOT_COLORS = ["#1e88e5","#e53935","#43a047","#fb8c00","#8e24aa","#00acc1"];
const MAX_GAUGES = SLOT_COLORS.length;

// Gauge state: array of {slot, label, times, rainfall_rate} or null
let gauges = new Array(MAX_GAUGES).fill(null);
let activeSlot = 0;  // which slot the file browser will load into

// Selected time range from TS zoom (unix seconds, or null = full range)
let tsRange = null;  // {t0: number, t1: number} | null

function _isoToUnix(iso) {
  // Plotly relayout gives ISO strings like "2024-01-01 00:00:00" or "2024-01-01T00:00:00"
  return Date.parse(iso.replace(' ', 'T')) / 1000;
}

function _unixToDatetimeLocal(unix) {
  // Returns "YYYY-MM-DDTHH:MM" in UTC for use in datetime-local inputs
  const d = new Date(unix * 1000);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

function _datetimeLocalToUnix(val) {
  // datetime-local gives "YYYY-MM-DDTHH:MM" — treat as UTC
  return Date.parse(val + ':00Z') / 1000;
}

function setTsRange(t0iso, t1iso) {
  const t0 = _isoToUnix(t0iso);
  const t1 = _isoToUnix(t1iso);
  if (!isFinite(t0) || !isFinite(t1) || t1 <= t0) return;
  tsRange = { t0, t1 };
  document.getElementById('range-t0').value = _unixToDatetimeLocal(t0);
  document.getElementById('range-t1').value = _unixToDatetimeLocal(t1);
  document.getElementById('range-pill').classList.add('active');
}

function _onRangeInputChange() {
  const t0 = _datetimeLocalToUnix(document.getElementById('range-t0').value);
  const t1 = _datetimeLocalToUnix(document.getElementById('range-t1').value);
  if (!isFinite(t0) || !isFinite(t1) || t1 <= t0) return;
  tsRange = { t0, t1 };
  // Update the TS chart x-axis to match the edited range
  if (tsInitialised) {
    const t0iso = _unixToDatetimeLocal(t0).replace('T', ' ');
    const t1iso = _unixToDatetimeLocal(t1).replace('T', ' ');
    Plotly.relayout('chart-ts', { 'xaxis.range': [t0iso, t1iso] });
  }
  if (currentTab === 'stats') refreshStats();
}

function clearTsRange() {
  tsRange = null;
  document.getElementById('range-t0').value = '';
  document.getElementById('range-t1').value = '';
  document.getElementById('range-pill').classList.remove('active');
  if (currentTab === 'stats') refreshStats();
}

// ── helpers ────────────────────────────────────────────────────────────────
async function get(url) {
  const r = await fetch(url); return r.json();
}
async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json();
}

// ── tabs ───────────────────────────────────────────────────────────────────
let currentTab = 'ts';
function showTab(t) {
  currentTab = t;
  document.querySelectorAll('.tab').forEach((el, i) => {
    const tabs = ['ts', 'scatter', 'xcorr', 'cumul', 'dmass', 'stats'];
    el.classList.toggle('active', tabs[i] === t);
  });
  document.getElementById('pane-ts').classList.toggle('active', t === 'ts');
  document.getElementById('pane-scatter').classList.toggle('active', t === 'scatter');
  document.getElementById('pane-xcorr').classList.toggle('active', t === 'xcorr');
  document.getElementById('pane-cumul').classList.toggle('active', t === 'cumul');
  document.getElementById('pane-dmass').classList.toggle('active', t === 'dmass');
  document.getElementById('stats-pane').classList.toggle('active', t === 'stats');
  if (t === 'ts') refreshTS();
  if (t === 'cumul') refreshCumul();
  if (t === 'dmass') refreshDMass();
  if (t === 'stats') refreshStats();
  if (t === 'xcorr' && xcorrInitialised) runXcorr();
}

// ── sidebar toggle ─────────────────────────────────────────────────────────
let sidebarOpen = true;
function toggleSidebar() {
  sidebarOpen = !sidebarOpen;
  document.getElementById('sidebar').classList.toggle('hidden', !sidebarOpen);
  document.getElementById('btn-browse').textContent =
    sidebarOpen ? '✕ Close browser' : '📁 Browse files';
}

// ── gauge name helpers ────────────────────────────────────────────────────
// Extract the instrument name from a label (part before the first '_').
// Works for both file stems ("ncas-rain-gauge-1_cao_20240315_...") and
// directory names ("ncas-rain-gauge-1").
function gaugeShortName(label) {
  return label.split('_')[0] || label;
}

// ── slot selector buttons ──────────────────────────────────────────────────
function buildSlotButtons() {
  const container = document.getElementById('slot-buttons');
  container.innerHTML = '';
  for (let i = 0; i < MAX_GAUGES; i++) {
    const btn = document.createElement('button');
    btn.className = 'slot-btn' + (i === activeSlot ? ' active' : '');
    btn.innerHTML = `<span class="slot-dot" style="background:${SLOT_COLORS[i]}"></span>` +
                    `${gauges[i] ? gaugeShortName(gauges[i].label) : 'Slot ' + (i+1)}`;
    btn.onclick = () => { activeSlot = i; buildSlotButtons(); };
    container.appendChild(btn);
  }
}

// ── gauge badge strip ──────────────────────────────────────────────────────
function refreshBadges() {
  const strip = document.getElementById('gauge-strip');
  const empty = document.getElementById('strip-empty');
  strip.querySelectorAll('.g-badge').forEach(el => el.remove());

  const loaded = gauges.filter(Boolean);
  empty.style.display = loaded.length ? 'none' : '';

  gauges.forEach((g, i) => {
    if (!g) return;
    const badge = document.createElement('span');
    badge.className = 'g-badge';
    badge.style.background = SLOT_COLORS[i] + '22';
    badge.style.borderColor = SLOT_COLORS[i];
    const qStr = g.quanta ? ` <span style="font-size:10px;opacity:0.7">(${g.quanta})</span>` : '';
    badge.innerHTML =
      `<span class="slot-dot" style="background:${SLOT_COLORS[i]}"></span>` +
      `<span>${g.label}${qStr}</span>` +
      `<span class="g-remove" title="Remove" onclick="removeGauge(${i})">✕</span>`;
    strip.appendChild(badge);
  });
}

function removeGauge(i) {
  gauges[i] = null;
  refreshBadges();
  buildSlotButtons();
  refreshPairSelects();
  if (currentTab === 'ts') refreshTS();
  if (currentTab === 'cumul') refreshCumul();
  if (currentTab === 'dmass') refreshDMass();
  if (currentTab === 'stats') refreshStats();
}

let currentPath = '';

async function browse(path) {
  const d = await get('/browse?path=' + encodeURIComponent(path));
  currentPath = d.path;

  const crumb = document.getElementById('breadcrumb');
  const parts = d.path.split('/').filter(Boolean);
  crumb.innerHTML = '<span onclick="browse(\'/\')">/ </span>' +
    parts.map((p, i) => {
      const full = '/' + parts.slice(0, i + 1).join('/');
      return `<span onclick="browse('${full.replace(/'/g, "\\'")}')">${p}/</span>`;
    }).join(' ');

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
    el.onclick = () => loadFile(f.path);
    list.appendChild(el);
  }

  // Show "load all" button only when the folder (or its subfolders) has NC files
  const folderActions = document.getElementById('folder-actions');
  const status = document.getElementById('load-dir-status');
  folderActions.style.display = d.nc_count > 0 ? 'block' : 'none';
  status.textContent = d.nc_count > 0 ? `${d.nc_count} .nc file${d.nc_count === 1 ? '' : 's'} in folder` : '';
  document.getElementById('btn-load-dir').textContent =
    `\uD83D\uDCC5 Load all ${d.nc_count} file${d.nc_count === 1 ? '' : 's'} into slot`;
}

// ── load all NC files in current folder into active slot
async function loadDir() {
  const status = document.getElementById('load-dir-status');
  const btn = document.getElementById('btn-load-dir');
  btn.disabled = true;
  status.textContent = 'Loading...';
  const d = await post('/load_dir', { path: currentPath, slot: activeSlot });
  btn.disabled = false;
  if (d.error) { alert('Error: ' + d.error); status.textContent = ''; return; }
  gauges[activeSlot] = {
    slot:      activeSlot,
    label:     d.label,
    times:     d.times,
    rainfall_rate: d.rainfall_rate,
    quanta:    d.quanta    ?? null,
    technique: d.technique ?? null,
  };
  status.textContent = `Loaded ${d.file_count} files`;
  for (let i = 1; i <= MAX_GAUGES; i++) {
    const next = (activeSlot + i) % MAX_GAUGES;
    if (!gauges[next]) { activeSlot = next; break; }
  }
  refreshBadges();
  buildSlotButtons();
  refreshPairSelects();
  if (currentTab === 'ts') refreshTS();
  if (currentTab === 'cumul') refreshCumul();
  if (currentTab === 'dmass') refreshDMass();
  if (currentTab === 'stats') refreshStats();
}

// ── load NetCDF file into active slot ─────────────────────────────────────
async function loadFile(path) {
  const d = await post('/load', { path, slot: activeSlot });
  if (d.error) { alert('Error: ' + d.error); return; }
  gauges[activeSlot] = {
    slot:      activeSlot,
    label:     d.label,
    times:     d.times,
    rainfall_rate: d.rainfall_rate,
    quanta:    d.quanta    ?? null,
    technique: d.technique ?? null,
  };
  // Auto-advance to next empty slot
  for (let i = 1; i <= MAX_GAUGES; i++) {
    const next = (activeSlot + i) % MAX_GAUGES;
    if (!gauges[next]) { activeSlot = next; break; }
  }
  refreshBadges();
  buildSlotButtons();
  refreshPairSelects();
  if (currentTab === 'ts') refreshTS();
  if (currentTab === 'cumul') refreshCumul();
  if (currentTab === 'dmass') refreshDMass();
  if (currentTab === 'stats') refreshStats();
}

// ── time-series chart ──────────────────────────────────────────────────────
let tsInitialised = false;
function refreshTS() {
  const loaded = gauges.map((g, i) => g ? { ...g, color: SLOT_COLORS[i] } : null).filter(Boolean);
  const ph = document.getElementById('ts-placeholder');
  const chartDiv = document.getElementById('chart-ts');
  if (!loaded.length) {
    if (tsInitialised) Plotly.purge('chart-ts');
    tsInitialised = false;
    ph.style.display = 'flex';
    chartDiv.style.display = 'none';
    return;
  }
  ph.style.display = 'none';
  chartDiv.style.display = '';

  const traces = loaded.map(g => ({
    x: g.times,
    y: g.rainfall_rate,
    type: 'scatter',
    mode: 'lines',
    name: g.label,
    line: { color: g.color, width: 1.5 },
    hovertemplate: '%{x|%Y-%m-%d %H:%M}<br>%{y:.3f} mm hr⁻¹<extra>' + g.label + '</extra>',
  }));

  const layout = {
    xaxis: { title: 'Time (UTC)', type: 'date', rangeslider: { visible: true, thickness: 0.05 } },
    yaxis: { title: 'Rainfall rate (mm hr⁻¹)', rangemode: 'tozero', autorange: true },
    legend: { orientation: 'h', y: -0.18 },
    margin: { t: 10, r: 16, b: 90, l: 65 },
    plot_bgcolor: '#fff',
    paper_bgcolor: '#fff',
    hovermode: 'x unified',
  };
  // Restore the saved zoom range so it survives tab switches
  if (tsRange) {
    layout.xaxis.range = [
      new Date(tsRange.t0 * 1000).toISOString().slice(0, 19).replace('T', ' '),
      new Date(tsRange.t1 * 1000).toISOString().slice(0, 19).replace('T', ' '),
    ];
  }

  if (!tsInitialised) {
    Plotly.newPlot('chart-ts', traces, layout, { responsive: true });
    tsInitialised = true;
    // Track zoom / range-slider changes
    document.getElementById('chart-ts').on('plotly_relayout', ev => {
      const x0 = ev['xaxis.range[0]'] ?? ev['xaxis.range']?.[0];
      const x1 = ev['xaxis.range[1]'] ?? ev['xaxis.range']?.[1];
      if (x0 && x1) {
        setTsRange(x0, x1);
      } else if (ev['xaxis.autorange'] || ev['xaxis.range'] === undefined && !x0) {
        clearTsRange();
      }
    });
  } else {
    Plotly.react('chart-ts', traces, layout);
  }
}

// -- shared pair selects (scatter + xcorr) -----------------------------------
function refreshPairSelects() {
  const loaded = gauges.map((g, i) => g ? { ...g, slot: i } : null).filter(Boolean);
  ['scatter-a', 'scatter-b', 'xcorr-a', 'xcorr-b'].forEach((id, pos) => {
    const sel = document.getElementById(id);
    const prev = sel.value;
    sel.innerHTML = '';
    loaded.forEach(g => {
      const opt = document.createElement('option');
      opt.value = g.slot;
      opt.textContent = `${gaugeShortName(g.label)} (slot ${g.slot + 1})`;
      sel.appendChild(opt);
    });
    if (loaded.find(g => String(g.slot) === prev)) sel.value = prev;
    else if (pos % 2 === 1 && loaded.length > 1) sel.value = loaded[1].slot;
  });

  // Double-mass selects: TB gauge on x, DC gauge on y
  const tbLoaded = loaded.filter(g => g.technique === 'tipping_bucket');
  const dcLoaded = loaded.filter(g => g.technique === 'drop_counting');

  ['dmass-tb', 'dmass-dc'].forEach((id, idx) => {
    const sel = document.getElementById(id);
    const pool = idx === 0 ? (tbLoaded.length ? tbLoaded : loaded) : (dcLoaded.length ? dcLoaded : loaded);
    const prev = sel.value;
    sel.innerHTML = '';
    pool.forEach(g => {
      const opt = document.createElement('option');
      opt.value = g.slot;
      opt.textContent = `${gaugeShortName(g.label)} (slot ${g.slot + 1})`;
      sel.appendChild(opt);
    });
    if (pool.find(g => String(g.slot) === prev)) sel.value = prev;
  });
}

// -- scatter plot ------------------------------------------------------------
let scatterInitialised = false;
async function runScatter() {
  const slotA = parseInt(document.getElementById('scatter-a').value);
  const slotB = parseInt(document.getElementById('scatter-b').value);
  const plotType = document.querySelector('input[name="scattertype"]:checked').value;
  document.getElementById('scatter-status').textContent = 'Computing...';

  const rangePayload = tsRange ? { t_start: tsRange.t0, t_end: tsRange.t1 } : {};
  const olsThresh = parseFloat(document.getElementById('scatter-ols-thresh').value) || 0.0;
  const fitMethod = document.querySelector('input[name="fitmethod"]:checked').value;
  const d = await post('/scatter', { slot_a: slotA, slot_b: slotB, ols_threshold: olsThresh, fit_method: fitMethod, ...rangePayload });
  if (d.error) { alert('Error: ' + d.error); document.getElementById('scatter-status').textContent = ''; return; }

  const labelA = gauges[slotA]?.label ?? `Slot ${slotA+1}`;
  const labelB = gauges[slotB]?.label ?? `Slot ${slotB+1}`;
  const wetOnly = document.getElementById('scatter-wet-only').checked;

  // Step 1: wet-hours filter — exclude hours where both gauges are zero
  const wetIdx = d.x.map((x, i) => (!wetOnly || x > 0 || d.y[i] > 0) ? i : -1).filter(i => i >= 0);
  // Step 2: threshold filter — exclude hours where either gauge is below the OLS threshold
  const plotIdx = wetIdx.filter(i => d.x[i] >= olsThresh && d.y[i] >= olsThresh);
  const px = plotIdx.map(i => d.x[i]);
  const py = plotIdx.map(i => d.y[i]);
  const ptimes = plotIdx.map(i => d.times[i]);

  const axMax = Math.max(...px.filter(isFinite), ...py.filter(isFinite), 0.1) * 1.05;

  let dataTrace;
  let plotAxMax = axMax;  // may be overridden for hist2d

  if (plotType === 'hist2d') {
    plotAxMax = Math.max(...px.filter(isFinite), ...py.filter(isFinite), 0.1) * 1.05;
    const binSize = plotAxMax / 80;
    dataTrace = {
      x: px, y: py,
      type: 'histogram2d',
      colorscale: 'Hot',
      reversescale: true,
      showscale: true,
      zmin: 1,
      colorbar: { title: 'Count', thickness: 12, len: 0.6, titlefont: { size: 11 } },
      hovertemplate: `${labelA}: %{x:.3f} mm<br>${labelB}: %{y:.3f} mm<br>Count: %{z}<extra></extra>`,
      xbins: { start: 0, size: binSize },
      ybins: { start: 0, size: binSize },
      showlegend: false,
    };
  } else {
    dataTrace = {
      x: px, y: py,
      type: 'scatter', mode: 'markers',
      marker: {
        size: 5, opacity: 0.55,
        color: px.map((_, i) => i),
        colorscale: 'Viridis',
        colorbar: { title: 'Sample index', thickness: 12, len: 0.6, titlefont: { size: 11 } },
        line: { width: 0 },
      },
      hovertemplate:
        `${labelA}: %{x:.3f} mm<br>${labelB}: %{y:.3f} mm<br>%{customdata}<extra></extra>`,
      customdata: ptimes,
      showlegend: false,
    };
  }

  const ref = {
    x: [0, plotAxMax], y: [0, plotAxMax],
    type: 'scatter', mode: 'lines',
    line: { color: '#aaa', width: 1.5, dash: 'dot' },
    name: '1 : 1',
    hoverinfo: 'skip',
  };

  const reg = {
    x: [0, plotAxMax], y: [0, plotAxMax * d.slope],
    type: 'scatter', mode: 'lines',
    line: { color: '#e53935', width: 1.5 },
    name: `${d.fit_method}  y = ${d.slope.toFixed(3)}x  (R² = ${d.r2.toFixed(3)}, n=${d.n_fit})`,
    hoverinfo: 'skip',
  };

  const layout = {
    xaxis: { title: `${labelA} (mm hr\u207b\xb9 accumulation)`, rangemode: 'tozero', range: [0, plotAxMax], constrain: 'domain' },
    yaxis: { title: `${labelB} (mm hr\u207b\xb9 accumulation)`, rangemode: 'tozero', range: [0, plotAxMax],
              scaleanchor: 'x', scaleratio: 1, constrain: 'domain' },
    legend: { x: 0.02, y: 0.98, bgcolor: 'rgba(255,255,255,.8)', font: { size: 11 } },
    margin: { t: 20, r: 80, b: 60, l: 65 },
    plot_bgcolor: plotType === 'hist2d' ? '#e8e8e8' : '#fff', paper_bgcolor: '#fff',
  };

  if (!scatterInitialised) {
    Plotly.newPlot('chart-scatter', [dataTrace, ref, reg], layout, { responsive: true });
    scatterInitialised = true;
  } else {
    Plotly.react('chart-scatter', [dataTrace, ref, reg], layout);
  }
  document.getElementById('scatter-status').textContent =
    `${d.x.length} paired hours  |  fit: ${d.n_fit} hours \u2265${d.fit_threshold} mm both gauges  |  R\u00b2 = ${d.r2.toFixed(3)}`;
}

// -- double mass curve -------------------------------------------------------
let dmassInitialised = false;
let _dmassRev = 0;

function refreshDMass() {
  const tbSlot = parseInt(document.getElementById('dmass-tb').value);
  const dcSlot = parseInt(document.getElementById('dmass-dc').value);
  const tbG = isNaN(tbSlot) ? null : gauges[tbSlot];
  const dcG = isNaN(dcSlot) ? null : gauges[dcSlot];

  const status = document.getElementById('dmass-status');
  const chartDiv = document.getElementById('chart-dmass');

  if (!tbG || !dcG) {
    if (dmassInitialised) { Plotly.purge(chartDiv); dmassInitialised = false; }
    status.textContent = 'Load a TB gauge and a DC gauge to plot a double-mass curve.';
    return;
  }

  const rainOnly = document.getElementById('dmass-rain-only').checked;
  const winMin   = Math.max(0, parseInt(document.getElementById('dmass-window').value) || 30);
  const EXPAND   = winMin * 6;  // samples at 10 s per minute

  const SAMPLE_H = 10 / 3600;

  // Apply time range subset
  const { times: tbT, rates: tbR } = _subsetToRange(tbG.times, tbG.rainfall_rate);
  const { times: dcT, rates: dcR } = _subsetToRange(dcG.times, dcG.rainfall_rate);

  // Build set of "rain-active" DC timestamps (±window around each non-zero sample)
  const dcActiveTimes = new Set();
  if (rainOnly) {
    const n = dcT.length;
    const mask = new Uint8Array(n);
    let ctr = 0;
    for (let i = 0; i < n; i++) { if (dcR[i] > 0) ctr = EXPAND + 1; if (ctr > 0) { mask[i] = 1; ctr--; } }
    ctr = 0;
    for (let i = n - 1; i >= 0; i--) { if (dcR[i] > 0) ctr = EXPAND + 1; if (ctr > 0) { mask[i] = 1; ctr--; } }
    for (let i = 0; i < n; i++) { if (mask[i]) dcActiveTimes.add(dcT[i]); }
  }

  // Compute cumulative accumulations at each TB timestamp.
  // We need both gauges at the same timestamps — use TB's grid and look up DC
  // by building a map from ISO timestamp → rate.
  const dcMap = new Map();
  for (let i = 0; i < dcT.length; i++) dcMap.set(dcT[i], dcR[i]);

  // --- Outlier event detection ---
  const filterOutliers = document.getElementById('dmass-filter-outliers').checked;
  const outlierThresh = (parseFloat(document.getElementById('dmass-outlier-thresh').value) || 50) / 100;
  const MIN_EVT_MM = 0.2;  // ignore trace events when computing median ratio

  // First pass: group active samples into contiguous events, accumulate per-event totals
  const evGroups = [];
  { let inEvt = false;
    for (let i = 0; i < tbT.length; i++) {
      const active = !rainOnly || dcActiveTimes.has(tbT[i]);
      if (active) {
        if (!inEvt) { evGroups.push({ tbMm: 0, dcMm: 0, iStart: i, iEnd: i }); inEvt = true; }
        const ev = evGroups[evGroups.length - 1];
        ev.iEnd = i;
        const r = tbR[i]; if (r > 0 && !isNaN(r)) ev.tbMm += r * SAMPLE_H;
        const d = dcMap.has(tbT[i]) ? dcMap.get(tbT[i]) : 0;
        if (d > 0 && !isNaN(d)) ev.dcMm += d * SAMPLE_H;
      } else { inEvt = false; }
    }
  }

  // Median per-event DC/TB ratio (over events large enough to be meaningful)
  const qualEvs = evGroups.filter(e => e.tbMm >= MIN_EVT_MM);
  const sortedRatios = qualEvs.map(e => e.dcMm / e.tbMm).sort((a, b) => a - b);
  const medRatio = sortedRatios.length ? sortedRatios[Math.floor(sortedRatios.length / 2)] : 1;

  // Build outlier mask (1 = exclude this sample index)
  const outlierMask = new Uint8Array(tbT.length);
  let excludedEvtCount = 0;
  if (filterOutliers && medRatio > 0) {
    for (const ev of evGroups) {
      // Vertical kink: DC records significant rain but TB records almost nothing
      // → TB malfunction. Exclude regardless of ratio (can't divide by zero).
      if (ev.tbMm < MIN_EVT_MM) {
        if (ev.dcMm >= MIN_EVT_MM) {
          for (let i = ev.iStart; i <= ev.iEnd; i++) outlierMask[i] = 1;
          excludedEvtCount++;
        }
        continue;
      }
      // Horizontal kink or ratio outlier: DC/TB deviates too far from the median
      if (Math.abs((ev.dcMm / ev.tbMm) / medRatio - 1) > outlierThresh) {
        for (let i = ev.iStart; i <= ev.iEnd; i++) outlierMask[i] = 1;
        excludedEvtCount++;
      }
    }
  }

  let tbCum = 0, dcCum = 0;
  const xArr = [], yArr = [], tArr = [];
  let nEvents = 0;

  for (let i = 0; i < tbT.length; i++) {
    const t   = tbT[i];
    const tbr = tbR[i];
    const dcr = dcMap.has(t) ? dcMap.get(t) : null;

    const active = !rainOnly || dcActiveTimes.has(t);
    if (!active) continue;
    if (outlierMask[i]) continue;

    if (tbr !== null && !isNaN(tbr) && tbr > 0) tbCum += tbr * SAMPLE_H;
    if (dcr !== null && !isNaN(dcr) && dcr > 0) { dcCum += dcr * SAMPLE_H; nEvents++; }
    xArr.push(tbCum);
    yArr.push(dcCum);
    tArr.push(t);
  }

  const tbName = gaugeShortName(tbG.label);
  const dcName = gaugeShortName(dcG.label);

  if (!xArr.length) {
    if (dmassInitialised) { Plotly.purge(chartDiv); dmassInitialised = false; }
    status.textContent = rainOnly ? 'No rain events found in the selected period.' : 'No data in the selected period.';
    return;
  }

  // Safe max: both arrays are monotonically non-decreasing, so the last element is the max
  const axMax = Math.max(xArr[xArr.length - 1], yArr[yArr.length - 1], 0.1) * 1.05;

  const curve = {
    x: xArr, y: yArr,
    customdata: tArr,
    type: 'scatter', mode: 'lines',
    line: { color: '#1e88e5', width: 1.5 },
    name: 'Double mass',
    hovertemplate: `${tbName}: %{x:.2f} mm<br>${dcName}: %{y:.2f} mm<br>%{customdata}<extra></extra>`,
  };

  const ref = {
    x: [0, axMax], y: [0, axMax],
    type: 'scatter', mode: 'lines',
    line: { color: '#aaa', width: 1.5, dash: 'dot' },
    name: '1 : 1', hoverinfo: 'skip',
  };

  // Simple OLS slope through origin
  const sumX2 = xArr.reduce((s, v) => s + v * v, 0);
  const sumXY = xArr.reduce((s, v, i) => s + v * yArr[i], 0);
  const slope = sumX2 > 0 ? sumXY / sumX2 : 1;
  const reg = {
    x: [0, axMax], y: [0, axMax * slope],
    type: 'scatter', mode: 'lines',
    line: { color: '#e53935', width: 1.5 },
    name: `OLS slope = ${slope.toFixed(4)}`,
    hoverinfo: 'skip',
  };

  const layout = {
    xaxis: { title: `${tbName} cumulative (mm)`, rangemode: 'tozero', range: [0, axMax], constrain: 'domain' },
    yaxis: { title: `${dcName} cumulative (mm)`, rangemode: 'tozero', range: [0, axMax],
             scaleanchor: 'x', scaleratio: 1, constrain: 'domain' },
    legend: { x: 0.02, y: 0.98, bgcolor: 'rgba(255,255,255,.85)', font: { size: 11 } },
    margin: { t: 10, r: 20, b: 60, l: 65 },
    plot_bgcolor: '#fff', paper_bgcolor: '#fff',
    datarevision: ++_dmassRev,
  };

  if (!dmassInitialised) {
    Plotly.newPlot(chartDiv, [curve, ref, reg], layout, { responsive: true });
    dmassInitialised = true;
  } else {
    Plotly.react(chartDiv, [curve, ref, reg], layout);
  }

  const totTB = tbCum.toFixed(2), totDC = dcCum.toFixed(2);
  const pctDiff = tbCum > 0 ? ((dcCum / tbCum - 1) * 100).toFixed(1) : '—';
  const winStr = rainOnly ? ` | ±${winMin} min event window` : '';
  const outlierStr = (filterOutliers && excludedEvtCount > 0)
    ? ` | ${excludedEvtCount} outlier event${excludedEvtCount === 1 ? '' : 's'} excluded (median ratio ${medRatio.toFixed(3)})`
    : '';
  status.textContent = `TB total: ${totTB} mm  |  DC total: ${totDC} mm  |  DC/TB: ${(+totDC / +totTB || 0).toFixed(4)}  (${pctDiff > 0 ? '+' : ''}${pctDiff}%)${winStr}${outlierStr}`;
}

// -- cumulative rainfall -----------------------------------------------------
let cumulInitialised = false;
let _cumulRev = 0;

// Parse a quanta attribute string like "0.2 mm" → 0.2 (returns null if unparseable)
function parseQuantaStr(s) {
  if (!s) return null;
  const m = String(s).match(/([\d.eE+\-]+)/);
  return m ? parseFloat(m[1]) : null;
}

// Compute per-day totals (mm) and missing-data flag from a rate series.
// rates are in mm hr⁻¹ over 10-second samples; null/NaN ⇒ missing.
// Returns Map<'YYYY-MM-DD', {total_mm, has_missing}>.
function getDailyMm(times, rates) {
  const SAMPLE_H = 10 / 3600;
  const map = new Map();
  for (let i = 0; i < times.length; i++) {
    const day = times[i].slice(0, 10);
    if (!map.has(day)) map.set(day, { total_mm: 0, has_missing: false });
    const entry = map.get(day);
    const r = rates[i];
    if (r === null || r === undefined || (typeof r === 'number' && isNaN(r))) {
      entry.has_missing = true;
    } else {
      entry.total_mm += Math.max(0, r) * SAMPLE_H;
    }
  }
  return map;
}

// Subset a gauge's times/rates arrays to the current tsRange (returns same if no range).
function _subsetToRange(times, rates) {
  if (!tsRange) return { times, rates };
  const t0iso = new Date(tsRange.t0 * 1000).toISOString().slice(0, 19);
  const t1iso = new Date(tsRange.t1 * 1000).toISOString().slice(0, 19);
  const idx = times.map((t, i) => (t >= t0iso && t <= t1iso) ? i : -1).filter(i => i >= 0);
  return { times: idx.map(i => times[i]), rates: idx.map(i => rates[i]) };
}

function refreshCumul() {
  const loaded = gauges.map((g, i) => g ? { ...g, color: SLOT_COLORS[i] } : null).filter(Boolean);
  if (!loaded.length) {
    if (cumulInitialised) Plotly.purge('chart-cumul');
    cumulInitialised = false;
    document.getElementById('cumul-filter-row').style.display = 'none';
    return;
  }

  const SAMPLE_H = 10 / 3600;

  // Identify tipping-bucket and drop-counting gauges
  const tbGauges = loaded.filter(g => g.technique === 'tipping_bucket');
  const dcGauges = loaded.filter(g => g.technique === 'drop_counting');
  const hasTB    = tbGauges.length > 0;

  // Show / hide the filter row (visible when any gauge type that supports filtering is loaded)
  const filterRow = document.getElementById('cumul-filter-row');
  filterRow.style.display = (hasTB || dcGauges.length > 0) ? 'flex' : 'none';
  // Grey-out TB-specific checkboxes when no TB gauge is loaded
  ['filt-missing', 'filt-few-tips', 'filt-dc-rain'].forEach(id => {
    const el = document.getElementById(id);
    el.disabled = !hasTB;
    el.parentElement.style.opacity = hasTB ? '' : '0.4';
  });

  // Read checkboxes (only relevant when a TB gauge is present)
  const filterMissing = hasTB && document.getElementById('filt-missing').checked;
  const filterFewTips = hasTB && document.getElementById('filt-few-tips').checked;
  const filterDcRain  = hasTB && document.getElementById('filt-dc-rain').checked;
  const filterDcZero  = document.getElementById('filt-dc-zero').checked;
  const anyDayFilter  = filterMissing || filterFewTips || filterDcRain;
  const anyFilter     = anyDayFilter || filterDcZero;

  // Pre-compute DC daily totals (used for filter c)
  const dcDailyMaps = filterDcRain
    ? dcGauges.map(g => { const s = _subsetToRange(g.times, g.rainfall_rate); return getDailyMm(s.times, s.rates); })
    : [];

  // Build a set of "active" timestamps around DC rain observations (filter d).
  // Expand ±30 min around each rain sample so gaps between individual drops
  // within a rain event don't fragment the plot.
  const dcRainTimes = new Set();
  if (filterDcZero && dcGauges.length > 0) {
    const EXPAND = 180;  // ±30 min at 10-second sampling
    for (const g of dcGauges) {
      const { times: dct, rates: dcr } = _subsetToRange(g.times, g.rainfall_rate);
      const n = dct.length;
      const mask = new Uint8Array(n);
      // Forward pass: mark EXPAND samples after each rain observation
      let ctr = 0;
      for (let i = 0; i < n; i++) {
        if (dcr[i] > 0) ctr = EXPAND + 1;
        if (ctr > 0) { mask[i] = 1; ctr--; }
      }
      // Backward pass: mark EXPAND samples before each rain observation
      ctr = 0;
      for (let i = n - 1; i >= 0; i--) {
        if (dcr[i] > 0) ctr = EXPAND + 1;
        if (ctr > 0) { mask[i] = 1; ctr--; }
      }
      for (let i = 0; i < n; i++) { if (mask[i]) dcRainTimes.add(dct[i]); }
    }
  }

  // Build set of excluded calendar days (filters a, b, c — TB-specific, day-level)
  const excludedDays = new Set();
  if (anyDayFilter) {
    for (const tbg of tbGauges) {
      const { times: tbt, rates: tbr } = _subsetToRange(tbg.times, tbg.rainfall_rate);
      const tbDaily  = getDailyMm(tbt, tbr);
      const quantaMm = parseQuantaStr(tbg.quanta) ?? 0.2;

      for (const [day, { total_mm, has_missing }] of tbDaily) {
        if (excludedDays.has(day)) continue;
        // (a) Missing data
        if (filterMissing && has_missing) { excludedDays.add(day); continue; }
        // (b) Fewer than 4 tips (at least 1; purely dry days are suppressed by filter d)
        const tips = quantaMm > 0 ? total_mm / quantaMm : 0;
        if (filterFewTips && tips > 0 && tips < 4) { excludedDays.add(day); continue; }
        // (c) No TB rain but DC gauges show > 1 mm
        if (filterDcRain && total_mm < quantaMm * 0.5) {
          const dcHasRain = dcDailyMaps.some(m => (m.get(day)?.total_mm ?? 0) > 1.0);
          if (dcHasRain) excludedDays.add(day);
        }
      }
    }
  }

  // Update exclusion count label
  const exclLabel = document.getElementById('cumul-excl-label');
  const exclParts = [];
  if (excludedDays.size > 0) exclParts.push(`${excludedDays.size} day${excludedDays.size === 1 ? '' : 's'} excluded`);
  if (filterDcZero && dcGauges.length > 0) exclParts.push('DC-zero periods suppressed');
  exclLabel.textContent = exclParts.length ? `(${exclParts.join('; ')})` : '';

  const traces = loaded.map(g => {
    const { times, rates } = _subsetToRange(g.times, g.rainfall_rate);

    // Drop excluded / DC-zero-dry samples from the arrays entirely so the
    // cumulative line has no flat sections between rain events.
    let cumsum = 0;
    const filtTimes = [];
    const cumRain   = [];
    for (let i = 0; i < times.length; i++) {
      const t   = times[i];
      const day = t.slice(0, 10);
      const r   = rates[i];
      const dayOk = !excludedDays.has(day);
      const dcOk  = !filterDcZero || dcGauges.length === 0 || dcRainTimes.has(t);
      if (dayOk && dcOk) {
        if (r !== null && r !== undefined && !isNaN(r) && r > 0) cumsum += r * SAMPLE_H;
        filtTimes.push(t);
        cumRain.push(cumsum);
      }
    }

    const shortName = gaugeShortName(g.label);
    return {
      x: filtTimes,
      y: cumRain,
      type: 'scatter',
      mode: 'lines',
      name: shortName,
      line: { color: g.color, width: 1.5 },
      hovertemplate: '%{x|%Y-%m-%d %H:%M}<br>%{y:.2f} mm<extra>' + shortName + '</extra>',
    };
  });

  const layout = {
    xaxis: { title: 'Time (UTC)', type: 'date' },
    yaxis: { title: 'Cumulative rainfall (mm)', rangemode: 'tozero' },
    legend: { orientation: 'h', y: -0.18 },
    margin: { t: 10, r: 16, b: 90, l: 65 },
    plot_bgcolor: '#fff',
    paper_bgcolor: '#fff',
    hovermode: 'x unified',
    datarevision: ++_cumulRev,
  };

  if (!cumulInitialised) {
    Plotly.newPlot('chart-cumul', traces, layout, { responsive: true });
    cumulInitialised = true;
  } else {
    Plotly.react('chart-cumul', traces, layout);
  }
}

let xcorrInitialised = false;
async function runXcorr() {
  const slotA = parseInt(document.getElementById('xcorr-a').value);
  const slotB = parseInt(document.getElementById('xcorr-b').value);
  const maxLag = parseInt(document.getElementById('xcorr-lag').value) || 60;
  document.getElementById('xcorr-status').textContent = 'Computing…';

  const rangePayload = tsRange ? { t_start: tsRange.t0, t_end: tsRange.t1 } : {};
  const d = await post('/xcorr', { slot_a: slotA, slot_b: slotB, max_lag_minutes: maxLag, ...rangePayload });
  if (d.error) { alert('Error: ' + d.error); return; }

  const labelA = gauges[slotA]?.label ?? `Slot ${slotA+1}`;
  const labelB = gauges[slotB]?.label ?? `Slot ${slotB+1}`;

  const trace = {
    x: d.lags,
    y: d.xcorr,
    type: 'scatter',
    mode: 'lines',
    line: { color: '#2e7d32', width: 1.5 },
    hovertemplate: 'Lag: %{x} s<br>r = %{y:.4f}<extra></extra>',
  };

  // Mark the peak
  const peakIdx = d.xcorr.indexOf(Math.max(...d.xcorr));
  const peakTrace = {
    x: [d.lags[peakIdx]],
    y: [d.xcorr[peakIdx]],
    type: 'scatter',
    mode: 'markers+text',
    marker: { color: '#e53935', size: 10 },
    text: [`peak r=${d.xcorr[peakIdx].toFixed(4)}\nlag=${d.lags[peakIdx]}s`],
    textposition: 'top center',
    hoverinfo: 'skip',
    showlegend: false,
  };

  const layout = {
    title: { text: `Cross-correlation: ${labelA} vs ${labelB}`, font: { size: 13 } },
    xaxis: { title: 'Lag (seconds)', zeroline: true, zerolinecolor: '#aaa' },
    yaxis: { title: 'Pearson r', range: [-1, 1] },
    margin: { t: 40, r: 16, b: 60, l: 65 },
    shapes: [{ type: 'line', x0: 0, x1: 0, y0: -1, y1: 1,
                line: { color: '#aaa', width: 1, dash: 'dot' } }],
    plot_bgcolor: '#fff', paper_bgcolor: '#fff',
  };

  if (!xcorrInitialised) {
    Plotly.newPlot('chart-xcorr', [trace, peakTrace], layout, { responsive: true });
    xcorrInitialised = true;
  } else {
    Plotly.react('chart-xcorr', [trace, peakTrace], layout);
  }
  document.getElementById('xcorr-status').textContent =
    `Peak r = ${d.xcorr[peakIdx].toFixed(4)} at lag ${d.lags[peakIdx]} s`;
}

// ── statistics panel ───────────────────────────────────────────────────────
function refreshStats() {
  const allLoaded = gauges.map((g, i) => g ? { ...g, color: SLOT_COLORS[i] } : null).filter(Boolean);
  const content = document.getElementById('stats-content');
  if (!allLoaded.length) {
    content.innerHTML = '<span style="color:#aaa;font-size:13px">Load at least one gauge to see statistics.</span>';
    return;
  }

  // Apply TS range subset to each gauge
  const loaded = allLoaded.map(g => {
    if (!tsRange) return g;
    const mask = g.times.map(t => {
      const u = Date.parse(t.replace(' ', 'T') + 'Z') / 1000;
      return u >= tsRange.t0 && u <= tsRange.t1;
    });
    return {
      ...g,
      times:         g.times.filter((_, i) => mask[i]),
      rainfall_rate: g.rainfall_rate.filter((_, i) => mask[i]),
    };
  });

  const rangeNote = tsRange
    ? `<div style="font-size:11px;color:#e65100;margin-bottom:6px">⚠ Showing subset: ${document.getElementById('range-t0').value.replace('T',' ')} – ${document.getElementById('range-t1').value.replace('T',' ')} UTC</div>`
    : '';

  let html = rangeNote + `<table id="stats-table">
    <tr>
      <th>Gauge</th>
      <th>N samples</th>
      <th>Mean (mm hr⁻¹)</th>
      <th>Std dev</th>
      <th>Max</th>
      <th>Total accumulation (mm)</th>
      <th>% rainy (>0.01 mm hr⁻¹)</th>
    </tr>`;

  for (const g of loaded) {
    const arr = g.rainfall_rate.filter(v => v !== null && !isNaN(v));
    const n = arr.length;
    const mean = n ? arr.reduce((a, b) => a + b, 0) / n : NaN;
    const std  = n ? Math.sqrt(arr.reduce((a, b) => a + (b - mean) ** 2, 0) / n) : NaN;
    const max  = n ? arr.reduce((a, b) => b > a ? b : a, -Infinity) : NaN;
    // Each sample is 10 s → rainfall rate in mm/hr → accumulation in mm:
    // mm = (mm/hr) × (10s / 3600s/hr)
    const accum = arr.reduce((a, b) => a + b * 10 / 3600, 0);
    const rainy = n ? (arr.filter(v => v > 0.01).length / n * 100) : NaN;

    const fmt = (v, dp=3) => isNaN(v) ? '—' : v.toFixed(dp);

    html += `<tr>
      <td><span style="display:inline-block;width:10px;height:10px;border-radius:50%;
                       background:${g.color};margin-right:6px"></span>${g.label}</td>
      <td>${n}</td>
      <td>${fmt(mean)}</td>
      <td>${fmt(std)}</td>
      <td>${fmt(max)}</td>
      <td>${fmt(accum, 2)}</td>
      <td>${fmt(rainy, 1)}</td>
    </tr>`;
  }
  html += '</table>';
  content.innerHTML = html;
}

// ── boot ───────────────────────────────────────────────────────────────────
buildSlotButtons();
document.getElementById('range-t0').addEventListener('change', _onRangeInputChange);
document.getElementById('range-t1').addEventListener('change', _onRangeInputChange);
['filt-missing', 'filt-few-tips', 'filt-dc-rain', 'filt-dc-zero'].forEach(id => {
  const el = document.getElementById(id);
  if (el && !el.onchange) el.addEventListener('change', refreshCumul);
});
(async () => {
  try {
    const init = await get('/init');
    await browse(init.start_dir);
  } catch (err) {
    console.error('Boot error:', err);
    document.getElementById('gauge-strip').innerHTML =
      `<span style="color:#c62828">⚠️ Server error: ${err.message}</span>`;
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
        return jsonify({'start_dir': start_dir})

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
        # Count NC files in this directory and one level of subdirectories only
        # (avoids slow recursive walk on large filesystems)
        nc_count = len(files)
        for d in dirs:
            try:
                nc_count += sum(1 for c in Path(d['path']).iterdir()
                                if c.suffix == '.nc')
            except PermissionError:
                pass
        return jsonify({'path': str(p), 'dirs': dirs, 'files': files,
                        'parent': parent, 'nc_count': nc_count})

    @app.route('/load', methods=['POST'])
    def load_file():
        path = request.json.get('path', '')
        slot = int(request.json.get('slot', 0))
        if slot < 0 or slot >= _MAX_GAUGES:
            return jsonify({'error': f'Invalid slot {slot}'})
        p = Path(path).resolve()
        if not p.is_file():
            return jsonify({'error': f'File not found: {path}'})
        try:
            with nc4.Dataset(str(p)) as nc:
                unix = nc.variables['time'][:].data.copy()
                quanta = getattr(nc, 'measurement_quanta', None)
                technique = getattr(nc, 'measurement_technique', None)
                if technique is None:
                    if 'number_of_tips' in nc.variables:
                        technique = 'tipping_bucket'
                    elif 'number_of_drops' in nc.variables:
                        technique = 'drop_counting'
                if 'rainfall_rate' in nc.variables:
                    rr = nc.variables['rainfall_rate'][:].data.astype(float).copy()
                    # Replace fill/missing values with NaN
                    fill = getattr(nc.variables['rainfall_rate'], '_FillValue', None)
                    if fill is not None:
                        rr[rr == fill] = np.nan
                elif 'thickness_of_rainfall_amount' in nc.variables:
                    # Tipping-bucket gauge: mm per 10-second sample → mm hr⁻¹
                    v = nc.variables['thickness_of_rainfall_amount']
                    rr = v[:].data.astype(float).copy()
                    fill = getattr(v, '_FillValue', None)
                    if fill is not None:
                        rr[rr == fill] = np.nan
                    rr *= 360.0  # mm/10s → mm/hr
                elif 'number_of_drops' in nc.variables:
                    drops = nc.variables['number_of_drops'][:].data.astype(float).copy()
                    rr = drops
                else:
                    return jsonify({'error': 'No rainfall_rate, thickness_of_rainfall_amount, or number_of_drops variable found'})
        except Exception as exc:
            return jsonify({'error': str(exc)})

        times_iso = pd.to_datetime(unix, unit='s', utc=True).strftime('%Y-%m-%dT%H:%M:%S').tolist()
        label = p.stem  # e.g. ncas-rain-gauge-1_cao_20240315_rainfall-rate_1min_v1

        _gauges[slot] = {
            'label': label,
            'unix':  unix,
            'rainfall_rate': rr,
            'quanta': quanta,
            'technique': technique,
        }
        return jsonify({'label': label, 'times': times_iso, 'rainfall_rate': rr.tolist(), 'quanta': quanta, 'technique': technique})

    @app.route('/load_dir', methods=['POST'])
    def load_dir():
        path = request.json.get('path', '')
        slot = int(request.json.get('slot', 0))
        if slot < 0 or slot >= _MAX_GAUGES:
            return jsonify({'error': f'Invalid slot {slot}'})
        p = Path(path).resolve()
        if not p.is_dir():
            return jsonify({'error': f'Not a directory: {path}'})

        nc_files = sorted(p.rglob('*.nc'))
        if not nc_files:
            return jsonify({'error': f'No .nc files found under {path}'})

        all_unix, all_rr = [], []
        failed = 0
        for nc_path in nc_files:
            try:
                with nc4.Dataset(str(nc_path)) as nc:
                    unix = nc.variables['time'][:].data.copy()
                    if 'rainfall_rate' in nc.variables:
                        rr = nc.variables['rainfall_rate'][:].data.astype(float).copy()
                        fill = getattr(nc.variables['rainfall_rate'], '_FillValue', None)
                        if fill is not None:
                            rr[rr == fill] = np.nan
                    elif 'thickness_of_rainfall_amount' in nc.variables:
                        v = nc.variables['thickness_of_rainfall_amount']
                        rr = v[:].data.astype(float).copy()
                        fill = getattr(v, '_FillValue', None)
                        if fill is not None:
                            rr[rr == fill] = np.nan
                        rr *= 360.0  # mm/10s → mm/hr
                    elif 'number_of_drops' in nc.variables:
                        rr = nc.variables['number_of_drops'][:].data.astype(float).copy()
                    else:
                        failed += 1
                        continue
                all_unix.append(unix)
                all_rr.append(rr)
            except Exception:
                failed += 1

        if not all_unix:
            return jsonify({'error': 'Could not read any files in the directory'})

        unix_cat = np.concatenate(all_unix)
        rr_cat   = np.concatenate(all_rr)

        # Sort by time and deduplicate
        order    = np.argsort(unix_cat, kind='stable')
        unix_cat = unix_cat[order]
        rr_cat   = rr_cat[order]
        _, unique = np.unique(unix_cat, return_index=True)
        unix_cat = unix_cat[unique]
        rr_cat   = rr_cat[unique]

        times_iso = pd.to_datetime(unix_cat, unit='s', utc=True).strftime('%Y-%m-%dT%H:%M:%S').tolist()
        # Label: extract the instrument name from the first NC file stem
        # (e.g. "ncas-rain-gauge-1_cao_20210315_..." → "ncas-rain-gauge-1").
        # Fall back to the directory name if the stem has no underscores.
        first_stem = nc_files[0].stem
        label = first_stem.split('_')[0] if '_' in first_stem else p.name
        # Use measurement_quanta and measurement_technique from first successfully-read file
        quanta = None
        technique = None
        for nc_path in nc_files:
            try:
                with nc4.Dataset(str(nc_path)) as nc:
                    if quanta is None:
                        quanta = getattr(nc, 'measurement_quanta', None)
                    if technique is None:
                        technique = getattr(nc, 'measurement_technique', None)
                        if technique is None:
                            if 'number_of_tips' in nc.variables:
                                technique = 'tipping_bucket'
                            elif 'number_of_drops' in nc.variables:
                                technique = 'drop_counting'
                if quanta is not None and technique is not None:
                    break
            except Exception:
                pass

        _gauges[slot] = {
            'label': label,
            'unix':  unix_cat,
            'rainfall_rate': rr_cat,
            'quanta': quanta,
            'technique': technique,
        }
        return jsonify({
            'label': label,
            'times': times_iso,
            'quanta': quanta,
            'technique': technique,
            'rainfall_rate': rr_cat.tolist(),
            'file_count': len(all_unix),
        })

    @app.route('/xcorr', methods=['POST'])
    def xcorr():
        slot_a = int(request.json.get('slot_a', 0))
        slot_b = int(request.json.get('slot_b', 1))
        max_lag_minutes = float(request.json.get('max_lag_minutes', 60))
        t_sel_start = request.json.get('t_start', None)
        t_sel_end   = request.json.get('t_end',   None)

        g_a = _gauges[slot_a]
        g_b = _gauges[slot_b]
        if g_a is None or g_b is None:
            return jsonify({'error': 'Both slots must have a gauge loaded'})

        # Align the two time series onto a common 10-s grid by interpolation
        t_a = g_a['unix'].astype(float)
        t_b = g_b['unix'].astype(float)
        rr_a = g_a['rainfall_rate']
        rr_b = g_b['rainfall_rate']

        # Common time grid: union of both, 10-second step
        dt = 10.0  # seconds (nominal sample interval)
        t_start = max(t_a.min(), t_b.min())
        t_end   = min(t_a.max(), t_b.max())
        # Apply user-selected range subset
        if t_sel_start is not None:
            t_start = max(t_start, float(t_sel_start))
        if t_sel_end is not None:
            t_end = min(t_end, float(t_sel_end))
        if t_end <= t_start:
            return jsonify({'error': 'No overlapping time period between the two gauges'})

        t_common = np.arange(t_start, t_end + dt, dt)
        a_interp = np.interp(t_common, t_a, rr_a)
        b_interp = np.interp(t_common, t_b, rr_b)

        # Replace NaNs with series mean before correlation
        a_interp = np.where(np.isnan(a_interp), np.nanmean(a_interp), a_interp)
        b_interp = np.where(np.isnan(b_interp), np.nanmean(b_interp), b_interp)

        # Normalise
        a_norm = a_interp - a_interp.mean()
        b_norm = b_interp - b_interp.mean()

        max_lag_samples = int(max_lag_minutes * 60 / dt)

        n = len(a_norm)
        # Full cross-correlation via FFT
        full_xcorr = np.correlate(a_norm, b_norm, mode='full')
        # Normalise so that zero-lag autocorrelation == 1
        norm_factor = np.sqrt((a_norm ** 2).sum() * (b_norm ** 2).sum())
        if norm_factor == 0:
            return jsonify({'error': 'One of the series has zero variance'})
        full_xcorr /= norm_factor

        # Centre index
        mid = n - 1
        lag_range = range(
            max(0, mid - max_lag_samples),
            min(len(full_xcorr), mid + max_lag_samples + 1)
        )
        lags_sec = [(i - mid) * dt for i in lag_range]
        corr_vals = [float(full_xcorr[i]) for i in lag_range]

        return jsonify({'lags': lags_sec, 'xcorr': corr_vals})

    @app.route('/scatter', methods=['POST'])
    def scatter():
        slot_a = int(request.json.get('slot_a', 0))
        slot_b = int(request.json.get('slot_b', 1))
        t_start = request.json.get('t_start', None)
        t_end   = request.json.get('t_end',   None)

        g_a = _gauges[slot_a]
        g_b = _gauges[slot_b]
        if g_a is None or g_b is None:
            return jsonify({'error': 'Both slots must have a gauge loaded'})

        # Convert 10-second rainfall_rate (mm hr⁻¹) → hourly accumulation (mm)
        # rainfall_mm per 10-s sample = rr_mm_hr * (10/3600)
        _SAMPLE_S = 10.0

        def _hourly_mm(unix_arr, rr_arr):
            """Return (hour_unix, hourly_mm) arrays, NaN hours excluded."""
            # Apply time-range subset if provided
            if t_start is not None and t_end is not None:
                mask = (unix_arr >= float(t_start)) & (unix_arr <= float(t_end))
                unix_arr = unix_arr[mask]
                rr_arr   = rr_arr[mask]
            mm_10s = rr_arr * (_SAMPLE_S / 3600.0)
            idx = pd.to_datetime(unix_arr, unit='s', utc=True)
            s = pd.Series(mm_10s, index=idx)
            # Sum over each complete hour; require at least 80% coverage (288 samples/hr)
            hourly = s.resample('1h').agg(
                lambda g: g.sum() if g.notna().sum() >= 240 else np.nan
            )
            hourly = hourly.dropna()
            return hourly.index.astype(np.int64) // 10**9, hourly.values

        t_a_hr, mm_a = _hourly_mm(g_a['unix'].astype(float), g_a['rainfall_rate'])
        t_b_hr, mm_b = _hourly_mm(g_b['unix'].astype(float), g_b['rainfall_rate'])

        # Inner join on hour timestamps
        t_b_map = {t: mm_b[i] for i, t in enumerate(t_b_hr)}
        x_vals, y_vals, times_iso = [], [], []
        for i, t in enumerate(t_a_hr):
            if t in t_b_map:
                xa = float(mm_a[i])
                yb = float(t_b_map[t])
                if np.isfinite(xa) and np.isfinite(yb):
                    x_vals.append(xa)
                    y_vals.append(yb)
                    times_iso.append(
                        pd.Timestamp(int(t), unit='s', tz='UTC').strftime('%Y-%m-%d %H:%M')
                    )

        if len(x_vals) < 2:
            return jsonify({'error': 'Fewer than 2 overlapping hours between the two gauges'})

        x_arr = np.array(x_vals)
        y_arr = np.array(y_vals)

        # Regression: only hours where both gauges are at or above the threshold
        FIT_THRESHOLD = float(request.json.get('ols_threshold', 0.2))
        fit_method = request.json.get('fit_method', 'ols')
        fit_mask = (x_arr >= FIT_THRESHOLD) & (y_arr >= FIT_THRESHOLD)
        x_fit = x_arr[fit_mask]
        y_fit = y_arr[fit_mask]

        if len(x_fit) >= 2:
            n_fit = int(fit_mask.sum())
            # R² relative to origin-constrained fit: 1 - SS_res / SS_tot
            def _r2_origin(xf, yf, s):
                ss_res = ((yf - s * xf) ** 2).sum()
                ss_tot = (yf ** 2).sum()  # total SS about origin
                return float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0

            if fit_method == 'theilsen':
                # Theil-Sen through origin: median of y_i / x_i ratios
                ratios = y_fit / x_fit
                slope = float(np.median(ratios))
                fit_label = 'Theil-Sen'
            else:
                # OLS through origin: slope = sum(x*y) / sum(x^2)
                slope = float(np.dot(x_fit, y_fit) / np.dot(x_fit, x_fit)) if np.dot(x_fit, x_fit) > 0 else 1.0
                fit_label = 'OLS'
            intercept = 0.0
            r2 = _r2_origin(x_fit, y_fit, slope)
        else:
            slope, intercept, r2, n_fit, fit_label = 1.0, 0.0, 0.0, 0, fit_method.upper()

        return jsonify({
            'x': x_vals,
            'y': y_vals,
            'times': times_iso,
            'slope': slope,
            'intercept': intercept,
            'r2': r2,
            'n_fit': n_fit,
            'fit_method': fit_label,
            'fit_threshold': FIT_THRESHOLD,
        })

    @app.route('/quit', methods=['POST'])
    def quit_server():
        import os, signal, time as _time
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
            "Browser-based interactive multi-gauge comparator with cross-correlation.\n\n"
            "Run on the remote machine:\n"
            "  compare-gauges-web  [--dir /path/to/nc/files]  [--port 8766]\n\n"
            "Forward the port from your local machine:\n"
            "  ssh -L 8766:localhost:8766  <user>@<host>\n\n"
            "Then open:  http://localhost:8766"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('-d', '--dir', default=None,
                        help='Starting directory for the file browser (default: home directory)')
    parser.add_argument('-p', '--port', type=int, default=8766,
                        help='Local port to serve on (default: 8766)')
    args = parser.parse_args()

    start_dir = str(Path(args.dir).resolve()) if args.dir else str(Path.home())

    _load_plotly()

    port = args.port
    print()
    print("=" * 62)
    print("  Gauge Comparator — browser interface")
    print("=" * 62)
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
