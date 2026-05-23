#!/usr/bin/env python
# =========================================================================
# raingauge_click_plots_web
# Web-based equivalent of raingauge_click_plots.py.
# Serves a Plotly-based 2×5 subplot page on localhost:8767.
# Use via SSH local port forwarding:
#   ssh -L 8767:localhost:8767 <user>@<host>
# then open http://localhost:8767 in your browser.
#
# Workflow:
#   1. Load a date.  Ten subplots appear (same layout as original).
#   2. Click-drag (or click twice) on any subplot to mark a suspect time
#      window.  Drawn rectangles accumulate in a pending list.
#   3. Press "Write corrections" to append HOLDCAL records to the .corr
#      files and redraw confirmed regions in red.
#   4. Press "Quit" to shut the server down.
#
# Original author: Judith Jeffery, RAL (Matplotlib version, 2020)
# Web refactor:    2026
# =========================================================================

import argparse
import glob
import os
import shutil
import sys
import time
import calendar
import threading
from datetime import date, datetime
from pathlib import Path

import numpy as np
import netCDF4 as nc4
from flask import Flask, jsonify, request, redirect

# ── Plotly JS cache (shared with compare_gauges_web) ─────────────────────────
_PLOTLY_CDN  = 'https://cdn.plot.ly/plotly-2.35.2.min.js'
_PLOTLY_CACHE = Path.home() / '.cache' / 'chilbolton_raingauge_utils' / 'plotly.min.js'

def _ensure_plotly():
    if _PLOTLY_CACHE.is_file():
        return
    _PLOTLY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    import urllib.request
    print(f'Downloading Plotly JS to {_PLOTLY_CACHE} …')
    urllib.request.urlretrieve(_PLOTLY_CDN, _PLOTLY_CACHE)
    print('Done.')

# ── Instrument configuration ──────────────────────────────────────────────────
# Gauges processed by this package (rg1=rg001dc_ch, rg2=rg006dc_ch, rg9=rg009dc_ch)

GAUGES = [
    {'instrument': 'ncas-rain-gauge-1', 'corr_id': 'rg001dc_ch', 'title': 'ncas-rain-gauge-1  rg001dc_ch'},
    {'instrument': 'ncas-rain-gauge-2', 'corr_id': 'rg006dc_ch', 'title': 'ncas-rain-gauge-2  rg006dc_ch'},
    {'instrument': 'ncas-rain-gauge-9', 'corr_id': 'rg009dc_ch', 'title': 'ncas-rain-gauge-9  rg009dc_ch'},
]

PLOT_TITLES  = [g['title']   for g in GAUGES]
CORR_FILE_IDS = [g['corr_id'] for g in GAUGES]
N_GAUGES = len(GAUGES)

# ── Data loading ──────────────────────────────────────────────────────────────

def _day_start_unix(yyyymmdd: str) -> float:
    tt = (int(yyyymmdd[0:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]), 0, 0, 0, 0, 0, 0)
    return float(calendar.timegm(tt))


def _hours(unix_arr, day_start: float):
    return (np.asarray(unix_arr, dtype=float) - day_start) / 3600.0


def read_day(yyyymmdd: str, data_path: str) -> list:
    """Load all gauges for *yyyymmdd*.  Returns list of {t, y, condensation} dicts."""
    day_start = _day_start_unix(yyyymmdd)
    year      = yyyymmdd[:4]
    missing   = 0.0
    n_default = 8640
    time_def  = (1.0 + np.arange(n_default)) / 360.0

    temp_K, rh_pct, t_met = read_temperature(yyyymmdd, data_path)

    # ── First pass: load all gauge data ──────────────────────────────────────
    # Level subdirectories searched in preference order.
    # Different processing eras write to different dirs:
    #   level1a  – original QC'd data
    #   level1b  – 2020-2024 CR1000X NCAS main processing
    #   level1   – 2024-2025 CR1000X STFC processing
    #   level1_f5 – 2015-2020 Format5 processing
    _LEVEL_DIRS = ('level1a', 'level1b', 'level1', 'level1_f5')

    gauge_data = []   # list of {'t', 'rate', 'drops', 'ok'}
    for g in GAUGES:
        inst  = g['instrument']
        fpath = None
        # File prefix is 'ncas-rain-gauge-X' for older data and
        # 'stfc-rain-gauge-X' for data after 2024-03-31; use a wildcard.
        inst_body = '-'.join(inst.split('-')[1:])   # e.g. 'rain-gauge-1'
        for level in _LEVEL_DIRS:
            pattern = os.path.join(data_path, inst, 'data', 'long-term', level,
                                   year, f'*-{inst_body}_cao_{yyyymmdd}_precipitation_*.nc')
            matches = glob.glob(pattern)
            if matches:
                fpath = sorted(matches)[-1]  # highest version if multiple
                break
        if fpath is None:
            print(f'[read_day] No file found for {inst} {yyyymmdd} in any of {_LEVEL_DIRS}',
                  flush=True)
            gauge_data.append({'t': time_def, 'rate': np.full(n_default, -1.0),
                               'drops': np.zeros(n_default), 'ok': False})
            continue
        print(f'[read_day] Loading {fpath}', flush=True)
        with nc4.Dataset(fpath, 'r') as ds:
            t     = _hours(ds.variables['time'][:], day_start)
            rate  = np.ma.filled(np.asarray(ds.variables['rainfall_rate'][:]),  fill_value=-1.0)
            qc    = np.asarray(ds.variables['qc_flag'][:])
            drops = np.ma.filled(np.asarray(ds.variables['number_of_drops'][:]), fill_value=0.0)
            rate  = np.where(qc == 1, rate, missing)
        gauge_data.append({'t': t, 'rate': rate, 'drops': drops, 'ok': True})

    # ── Condensation detection with cross-gauge suppression ───────────────────
    # Step 1: per-gauge raw boolean flags
    raw_flags = [_detect_condensation_flag(gd['drops'], gd['t'], temp_K)
                 for gd in gauge_data]

    # Step 2: suppress samples where ANY other gauge's 1-h rolling mean
    # exceeds _COND_XGAUGE — rain falls on all gauges simultaneously;
    # condensation is gauge-specific.
    dt_h   = float(time_def[1] - time_def[0]) if n_default > 1 else 1 / 360.0
    half_w = max(1, int(round(1.0 / dt_h))) // 2

    # Pre-compute each gauge's 1-hour rolling mean for cross-gauge check
    gauge_roll_means = []
    for gd in gauge_data:
        n = len(gd['drops'])
        if n < 2:
            gauge_roll_means.append(np.zeros(n))
            continue
        rm, _ = _rolling_stats(gd['drops'].astype(float), half_w)
        gauge_roll_means.append(rm)

    final_flags = []
    for i, flag in enumerate(raw_flags):
        n = len(flag)
        if flag.any() and n > 1:
            # Suppress where ANY other gauge's rolling mean exceeds _COND_XGAUGE.
            # Rain falls on all gauges; condensation is gauge-specific.
            # Using per-gauge MAX (not sum) so a single active gauge suppresses.
            other_max_mean = np.zeros(n, dtype=float)
            for j, rm in enumerate(gauge_roll_means):
                if j != i and len(rm) == n:
                    other_max_mean = np.maximum(other_max_mean, rm)
            flag = flag & ~(other_max_mean > _COND_XGAUGE)
        final_flags.append(flag)

    # ── Build traces ─────────────────────────────────────────────────────────
    traces = []
    for i, gd in enumerate(gauge_data):
        cond = _flag_to_ranges(final_flags[i], gd['t'],
                               min_duration_h=0.5, gap_fill_h=0.25)
        traces.append({'t': gd['t'].tolist(), 'y': gd['rate'].tolist(),
                       'condensation': cond})

    # ── Met traces (T in °C, RH in %) ────────────────────────────────────
    met = {'t': [], 'temp_c': [], 'rh': []}
    if t_met is not None and temp_K is not None:
        temp_c = np.where(np.isnan(temp_K), None, temp_K - 273.15)
        rh_out = np.where(np.isnan(rh_pct), None, rh_pct)
        met = {'t': t_met.tolist(),
               'temp_c': [None if v is None or (hasattr(v,'__float__') and np.isnan(v)) else float(v)
                          for v in temp_c],
               'rh':     [None if v is None or (hasattr(v,'__float__') and np.isnan(v)) else float(v)
                          for v in rh_out]}
    return {'traces': traces, 'met': met}


# ── Temperature data ─────────────────────────────────────────────────────────

_TEMP_INST     = 'ncas-temperature-rh-1'
_TEMP_PREFIXES = ('ncas-temperature-rh-1', 'stfc-temperature-rh-1')
_TEMP_THRESH_K  = 273.15 + 7.0  # condensation unlikely above 7 °C
_COND_MAX_MEAN  = 0.20           # drops/sample; 1-h rolling mean above this is rain-like
_COND_FANO_MAX  = 1.5            # Fano factor (Var/Mean) threshold; rain is super-Poisson
_COND_XGAUGE   = 0.06           # per-gauge rolling mean threshold for cross-gauge suppression


def read_temperature(yyyymmdd: str, data_path: str) -> tuple:
    """Load air_temperature (K) and relative_humidity (%) from ncas-temperature-rh-1.

    Returns (temp_K, rh_pct, t_hours) where temp_K and rh_pct are arrays with
    bad-QC samples set to NaN, and t_hours is hours since midnight.  Returns
    (None, None, None) if no file is found.
    """
    year   = yyyymmdd[:4]
    level1 = os.path.join(data_path, _TEMP_INST, 'data', 'long-term', 'level1', year)
    day_start = _day_start_unix(yyyymmdd)
    fpath = None
    for prefix in _TEMP_PREFIXES:
        pattern = os.path.join(level1, f'{prefix}_cao_{yyyymmdd}_surface-met_*.nc')
        matches = glob.glob(pattern)
        if matches:
            fpath = matches[0]
            break
    if fpath is None:
        return None, None, None
    try:
        with nc4.Dataset(fpath, 'r') as ds:
            t_unix = np.asarray(ds.variables['time'][:], dtype=float)
            t_hrs  = _hours(t_unix, day_start)
            temp = np.ma.filled(np.asarray(ds.variables['air_temperature'][:],
                                           dtype=float), fill_value=np.nan)
            if 'qc_flag_air_temperature' in ds.variables:
                qc = np.asarray(ds.variables['qc_flag_air_temperature'][:])
                temp = np.where(qc == 1, temp, np.nan)
            rh = np.ma.filled(np.asarray(ds.variables['relative_humidity'][:],
                                         dtype=float), fill_value=np.nan)
            if 'qc_flag_relative_humidity' in ds.variables:
                qc_rh = np.asarray(ds.variables['qc_flag_relative_humidity'][:])
                rh = np.where(qc_rh == 1, rh, np.nan)
        return temp, rh, t_hrs
    except Exception:
        return None, None, None


# ── Condensation detection ────────────────────────────────────────────────────

def _flag_to_ranges(flag: np.ndarray, t_hours: np.ndarray,
                    min_duration_h: float, gap_fill_h: float) -> list:
    """Convert a boolean flag array into a list of [t0, t1] time-range pairs."""
    n = len(flag)
    if n == 0 or not flag.any():
        return []

    dt_h = float(t_hours[1] - t_hours[0]) if n > 1 else 1 / 360.0
    gap_samples = max(1, int(round(gap_fill_h / dt_h)))

    changes = np.diff(flag.astype(np.int8))
    starts  = list(np.where(changes == 1)[0] + 1)
    ends    = list(np.where(changes == -1)[0] + 1)
    if flag[0]:
        starts = [0] + starts
    if flag[-1]:
        ends = ends + [n]
    if not starts:
        return []

    # Merge short gaps
    merged = [[starts[0], ends[0]]]
    for s, e in zip(starts[1:], ends[1:]):
        if s - merged[-1][1] <= gap_samples:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    # Filter by minimum duration
    result = []
    for s, e in merged:
        if t_hours[min(e - 1, n - 1)] - t_hours[s] >= min_duration_h:
            result.append([float(t_hours[s]), float(t_hours[min(e - 1, n - 1)])])
    return result


def _rolling_stats(drops: np.ndarray, half: int) -> tuple:
    """Return (rolling_mean, rolling_var) over a centred window of 2*half+1 samples."""
    n   = len(drops)
    idx = np.arange(n)
    s_  = np.maximum(0, idx - half)
    e_  = np.minimum(n, idx + half + 1)
    cnt = (e_ - s_).astype(float)
    cs      = np.cumsum(np.concatenate([[0.0], drops]))
    cs_sq   = np.cumsum(np.concatenate([[0.0], drops ** 2]))
    r_mean  = (cs[e_]    - cs[s_])    / cnt
    r_mean_sq = (cs_sq[e_] - cs_sq[s_]) / cnt
    r_var   = np.maximum(0.0, r_mean_sq - r_mean ** 2)
    return r_mean, r_var


def _detect_condensation_flag(drops: np.ndarray, t_hours: np.ndarray,
                              temp_K: np.ndarray | None = None,
                              min_mean: float = 0.03,
                              max_mean: float = _COND_MAX_MEAN,
                              max_block_count: int = 2,
                              fano_max: float = _COND_FANO_MAX) -> np.ndarray:
    """Return a per-sample boolean condensation flag (no duration filtering).

    This is the inner kernel used by both ``detect_condensation`` and the
    cross-gauge suppression logic in ``read_day``.
    """
    drops   = np.asarray(drops,   dtype=float)
    t_hours = np.asarray(t_hours, dtype=float)
    n = len(drops)
    if n < 2:
        return np.zeros(n, dtype=bool)

    dt_h = float(t_hours[1] - t_hours[0]) if n > 1 else 1 / 360.0
    if dt_h <= 0:
        dt_h = 1 / 360.0

    # 1-hour centred rolling mean and variance
    half = max(1, int(round(1.0 / dt_h))) // 2
    roll_mean, roll_var = _rolling_stats(drops, half)

    # Fano factor (index of dispersion = Var/Mean).
    # Condensation is sub/near-Poisson (Fano ≤ 1); bursty rain is
    # super-Poisson (Fano > 1).  Where the mean is negligible the
    # Fano factor is undefined — set to 0 so those samples pass.
    with np.errstate(divide='ignore', invalid='ignore'):
        fano = np.where(roll_mean > 0.01, roll_var / roll_mean, 0.0)

    # 15-minute non-overlapping block max (ensures no single sample has > max_block_count drops)
    block = max(1, int(round(0.25 / dt_h)))
    n_blk = (n + block - 1) // block
    idx   = np.arange(n)
    blk_max = np.array([drops[i * block:(i + 1) * block].max()
                        for i in range(n_blk)])
    block_max = blk_max[np.minimum(idx // block, n_blk - 1)]

    flag = ((roll_mean  >= min_mean) &
            (roll_mean  <= max_mean) &
            (block_max  <= max_block_count) &
            (fano       <  fano_max))

    # Temperature gate: suppress where T > threshold.
    # NaN temperature values leave the flag unchanged (conservative).
    if temp_K is not None:
        t_arr = np.asarray(temp_K, dtype=float)
        if len(t_arr) == n:
            too_warm = np.where(np.isnan(t_arr), False, t_arr > _TEMP_THRESH_K)
            flag = flag & ~too_warm

    return flag


def detect_condensation(drops: np.ndarray, t_hours: np.ndarray,
                        temp_K: np.ndarray | None = None,
                        min_mean: float = 0.03,
                        max_mean: float = _COND_MAX_MEAN,
                        max_block_count: int = 2,
                        min_duration_h: float = 0.50,
                        gap_fill_h: float = 0.25) -> list:
    """Heuristic detector for condensation periods in drop-count data.

    Condensation on the gauge funnel produces sparse (1–2 drops per 10 s
    sample), sustained dripping quite unlike actual rainfall which arrives in
    higher-count bursts.  Three criteria must all be met:

    * **1-hour rolling mean** in [min_mean, max_mean] — excludes true zeros
      and rain (0.30 drops/sample ≈ 0.36 mm/h).
    * **15-minute block maximum** ≤ max_block_count — any sample with ≥ 3
      drops disqualifies the block.
    * **Temperature gate** — samples where air temperature > 7 °C are excluded
      (condensation unlikely when warm).

    Note: ``read_day`` additionally applies cross-gauge suppression: periods
    where the other gauges' combined rolling mean exceeds _COND_MAX_MEAN are
    treated as rain.

    Returns
    -------
    list of [t0, t1] pairs (hours since midnight)
    """
    flag = _detect_condensation_flag(drops, t_hours, temp_K,
                                     min_mean, max_mean, max_block_count)
    return _flag_to_ranges(flag, np.asarray(t_hours, dtype=float),
                           min_duration_h, gap_fill_h)


# ── Correction file helpers ───────────────────────────────────────────────────

def _hours_to_hhmmss(h: float) -> str:
    h = max(0.0, min(h, 23.9999))
    hh = int(h)
    mm = int((h * 60) % 60)
    ss = int((h * 3600) % 60)
    return f'{hh:02d}{mm:02d}{ss:02d}'


def write_corrections(yyyymmdd: str, data_path: str, corrections: list) -> list:
    """Write correction records.  corrections is a list of {plot, t0, t1, flag} dicts.
    flag is 'HOLDCAL' (default) or 'CAL'.
    Each gauge writes to {data_path}/{instrument}/corrections/{corr_id}.corr.
    A monthly backup copy is kept alongside.
    Returns list of messages."""
    msgs = []
    for c in corrections:
        idx  = int(c['plot'])
        t0   = float(c['t0'])
        t1   = float(c['t1'])
        flag = str(c.get('flag', 'HOLDCAL')).strip()
        if flag not in ('HOLDCAL', 'CAL'):
            flag = 'HOLDCAL'   # reject unexpected values
        g    = GAUGES[idx]
        fid  = g['corr_id']
        corr_dir  = os.path.join(data_path, g['instrument'], 'corrections')
        corr_file = os.path.join(corr_dir, fid + '.corr')
        corr_copy = os.path.join(corr_dir, f'{fid}_{yyyymmdd[:6]}.corr')
        os.makedirs(corr_dir, exist_ok=True)
        if os.path.isfile(corr_file) and not os.path.isfile(corr_copy):
            shutil.copy2(corr_file, corr_copy)
        line = f'{yyyymmdd} {_hours_to_hhmmss(t0)} {_hours_to_hhmmss(t1)} {flag}\n'
        with open(corr_file, 'a') as f:
            f.write(line)
        msgs.append(f'Written to {corr_file}: {line.strip()}')
    return msgs


# ── HTML / JS SPA ─────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Raingauge click QC</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: sans-serif; font-size: 13px; background: #f5f5f5;
         display: flex; flex-direction: column; height: 100vh; }
  #topbar { display: flex; align-items: center; gap: 10px; padding: 6px 12px;
            background: #1565c0; color: #fff; flex-shrink: 0; }
  #topbar h1 { font-size: 15px; font-weight: 700; }
  #topbar input[type=text] { width: 100px; padding: 3px 6px; border-radius: 3px;
                              border: none; font-size: 13px; }
  #paths-bar { display: none; align-items: center; gap: 8px; padding: 5px 12px;
               background: #0d47a1; color: #bbdefb; font-size: 12px; flex-shrink: 0; }
  #paths-bar input { flex: 1; padding: 3px 6px; border-radius: 3px; border: none;
                     font-size: 12px; font-family: monospace; }
  #paths-bar button { padding: 3px 10px; border: none; border-radius: 3px;
                      background: #42a5f5; color: #fff; font-weight: 600;
                      font-size: 12px; cursor: pointer; }
  #paths-bar button:hover { opacity:.85; }
  #topbar button { padding: 4px 12px; border: none; border-radius: 4px;
                   cursor: pointer; font-weight: 600; font-size: 12px; }
  #btn-load    { background: #43a047; color: #fff; }
  #btn-load:hover { opacity:.85; }
  #btn-prev, #btn-next { background: #1565c0; color: #fff; }
  #btn-prev:hover, #btn-next:hover { opacity:.85; }
  #btn-write   { background: #fb8c00; color: #fff; }
  #btn-write:hover { opacity:.85; }
  #btn-undo    { background: #8e24aa; color: #fff; }
  #btn-undo:hover { opacity:.85; }
  #btn-clear   { background: #e53935; color: #fff; }
  #btn-clear:hover { opacity:.85; }
  #btn-cond    { background: #f9a825; color: #fff; }
  #btn-cond:hover  { opacity:.85; }
  #sel-flag    { padding: 4px 6px; border-radius: 4px; border: 1px solid #90caf9;
                 font-size: 12px; font-weight: 600; background: #e3f2fd; cursor: pointer; }
  #btn-quit    { background: #546e7a; color: #fff; margin-left: auto; }
  #btn-quit:hover { opacity:.85; }
  #status { font-size: 12px; color: #ffe082; margin-left: 4px; }
  #plots  { flex: 1; overflow: hidden; }
  #pending-list { padding: 4px 12px; background: #fff3e0; font-size: 11px;
                  border-top: 1px solid #ffe0b2; flex-shrink: 0; min-height: 22px; }
</style>
</head>
<body>
<div id="topbar">
  <h1>Raingauge QC</h1>
  <label>Date (yyyymmdd):
    <input type="text" id="inp-date" placeholder="20210601" maxlength="8">
  </label>
  <button id="btn-prev"  onclick="stepDay(-1)">◄ Prev</button>
  <button id="btn-load"  onclick="loadDay()">Load</button>
  <button id="btn-next"  onclick="stepDay(+1)">Next ►</button>
  <button id="btn-write" onclick="writeCorr()">Write corrections</button>
  <button id="btn-undo"  onclick="undoLast()">Undo last</button>
  <button id="btn-clear" onclick="clearPending()">Clear pending</button>
  <button id="btn-cond"  onclick="flagCondensation()">Flag condensation</button>  <label style="color:#bbdefb;font-size:12px">Flag type:
    <select id="sel-flag">
      <option value="HOLDCAL" selected>HOLDCAL</option>
      <option value="CAL">CAL (burette)</option>
    </select>
  </label>  <button id="btn-paths" onclick="togglePaths()">⚙ Paths</button>
  <button id="btn-quit"  onclick="quit()">Quit</button>
  <span id="status"></span>
</div>
<div id="paths-bar">
  <label>Data path: <input id="inp-data-path" type="text" size="80">
    <span style="font-size:11px;color:#bbdefb">&nbsp;(corrections written to {data_path}/{instrument}/corrections/)</span>
  </label>
  <button onclick="applyPaths()">Apply</button>
  <span id="paths-status" style="color:#ffe082"></span>
</div>
<div id="plots"></div>
<div id="pending-list">No pending corrections.</div>

<script src="/plotly.js"></script>
<script>
// ── state ────────────────────────────────────────────────────────────────────
let currentDate = '';
let traces      = [];          // raw data from server
let confirmed   = [];          // [{plot,t0,t1}] written to files
let pending     = [];          // [{plot,t0,t1}] not yet written
let clickFirst  = null;        // first click of a pair: {plot, t}
let condensation = [[], [], []]; // auto-detected condensation ranges per gauge
let metData     = null;          // {t, temp_c, rh} from ncas-temperature-rh-1
const CLICK_OFFSET = -0.08333; // hours (≈5 min), matches original

function getFlagType() {
  return document.getElementById('sel-flag').value;
}

const TITLES = [
  'ncas-rain-gauge-1  rg001dc_ch',
  'ncas-rain-gauge-2  rg006dc_ch',
  'ncas-rain-gauge-9  rg009dc_ch',
];
const N_GAUGES = TITLES.length;
const COLORS = ['#1565c0', '#e53935', '#2e7d32'];

// ── helpers ──────────────────────────────────────────────────────────────────
function status(msg) { document.getElementById('status').textContent = msg; }

async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json();
}

function hoursToHHMMSS(h) {
  h = Math.max(0, Math.min(h, 23.9999));
  const hh = Math.floor(h);
  const mm = Math.floor((h * 60) % 60);
  const ss = Math.floor((h * 3600) % 60);
  return `${String(hh).padStart(2,'0')}:${String(mm).padStart(2,'0')}:${String(ss).padStart(2,'0')}`;
}

// ── helpers ──────────────────────────────────────────────────────────────────
// Compute cumulative rainfall (mm) from rate (mm/h) and time (h).
// Treats null/NaN samples as zero contribution.
function computeCumulative(t, rate) {
  const cum = new Array(t.length).fill(0);
  for (let j = 1; j < t.length; j++) {
    const r0 = (rate[j-1] != null && isFinite(rate[j-1])) ? rate[j-1] : 0;
    const r1 = (rate[j]   != null && isFinite(rate[j]))   ? rate[j]   : 0;
    cum[j] = cum[j-1] + (r0 + r1) / 2 * (t[j] - t[j-1]);
  }
  return cum;
}

// ── rendering ────────────────────────────────────────────────────────────────
function buildPlots() {
  if (!traces.length) return;

  const plotDiv = document.getElementById('plots');
  plotDiv.innerHTML = '';

  const hasMet  = metData && metData.t && metData.t.length > 0;
  // Row 1: N_GAUGES rain panels; Row 2 (if met data): T (cols 1+2) and RH (col 3)
  const N_ROWS  = hasMet ? 2 : 1;
  const plotlyTraces = [];
  const shapes       = [];

  // ── Row 1: rain gauges ─────────────────────────────────────────────────────
  for (let i = 0; i < N_GAUGES; i++) {
    const xref = i === 0 ? 'x'  : `x${i+1}`;
    const tr   = traces[i];

    plotlyTraces.push({
      x: tr.t, y: tr.y,
      mode: 'lines',
      line: { width: 0.8, color: COLORS[i] },
      name: TITLES[i],
      xaxis: i === 0 ? 'x' : `x${i+1}`,
      yaxis: i === 0 ? 'y' : `y${i+1}`,
      hovertemplate: '%{x:.3f} h  %{y:.4f} mm/h<extra></extra>',
    });

    // cumulative rainfall trace (right-hand axis y6/y7/y8)
    plotlyTraces.push({
      x: tr.t, y: computeCumulative(tr.t, tr.y),
      mode: 'lines',
      line: { width: 1.2, color: COLORS[i], dash: 'dot' },
      name: TITLES[i] + ' cumul.',
      xaxis: i === 0 ? 'x' : `x${i+1}`,
      yaxis: `y${6+i}`,
      opacity: 0.55,
      hovertemplate: '%{x:.3f} h  %{y:.2f} mm<extra></extra>',
    });

    // auto-detected condensation regions (light yellow)
    for (const [t0, t1] of (condensation[i] || [])) {
      shapes.push({ type:'rect', xref, yref:'y domain',
        x0:t0, x1:t1, y0:0, y1:1,
        line:{color:'rgba(255,193,7,0.5)',width:1,dash:'dot'},
        fillcolor:'rgba(255,235,59,0.15)' });
    }
    // confirmed (red = HOLDCAL, purple = CAL)
    for (const c of confirmed) {
      if (c.plot === i) {
        const isCal = c.flag === 'CAL';
        shapes.push({ type:'rect', xref, yref:'y domain',
          x0:c.t0, x1:c.t1, y0:0, y1:0.5,
          line:{color: isCal ? '#6a1b9a' : '#b71c1c', width:1},
          fillcolor: isCal ? 'rgba(106,27,154,0.15)' : 'rgba(183,28,28,0.15)' });
      }
    }
    // pending (orange = HOLDCAL, violet dashed = CAL)
    for (const p of pending) {
      if (p.plot === i) {
        const isCal = p.flag === 'CAL';
        shapes.push({ type:'rect', xref, yref:'y domain',
          x0:p.t0, x1:p.t1, y0:0, y1:0.5,
          line:{color: isCal ? '#8e24aa' : '#e65100', width:1, dash:'dot'},
          fillcolor: isCal ? 'rgba(142,36,170,0.12)' : 'rgba(230,81,0,0.12)' });
      }
    }
    // first-click marker
    if (clickFirst !== null && clickFirst.plot === i) {
      shapes.push({ type:'line', xref, yref:'y domain',
        x0:clickFirst.t, x1:clickFirst.t, y0:0, y1:1,
        line:{color:'#fb8c00',width:1.5,dash:'dash'} });
    }
  }

  // ── Row 2: temperature (x4/y4) + RH (y5 overlaying y4) ──────────────────
  // T and RH share one full-width panel: left axis = T (°C), right axis = RH (%)
  if (hasMet) {
    // Temperature trace (°C) — left axis
    plotlyTraces.push({
      x: metData.t, y: metData.temp_c,
      mode: 'lines', line: { width: 1.0, color: '#e65100' },
      name: 'Air temp (°C)',
      xaxis: 'x4', yaxis: 'y4',
      hovertemplate: '%{x:.3f} h  %{y:.2f} °C<extra></extra>',
    });
    // 7 °C threshold line on T axis
    shapes.push({ type:'line', xref:'x4', yref:'y4',
      x0: -1, x1: 25, y0: 7, y1: 7,
      line: { color:'rgba(33,150,243,0.6)', width:1, dash:'dash' } });
    // RH trace (%) — right axis (y5 overlays y4)
    plotlyTraces.push({
      x: metData.t, y: metData.rh,
      mode: 'lines', line: { width: 1.0, color: '#0288d1' },
      name: 'RH (%)',
      xaxis: 'x4', yaxis: 'y5',
      opacity: 0.7,
      hovertemplate: '%{x:.3f} h  %{y:.1f} %<extra></extra>',
    });
  }

  // ── Layout ─────────────────────────────────────────────────────────────────
  const rowFrac = hasMet ? [0.42, 0.38] : [1.0];   // fraction of height per row
  const gap     = 0.08;
  // Row 1 domain: [rowFrac[1]+gap, 1.0]; Row 2 domain: [0, rowFrac[1]]
  const row1y = hasMet ? [rowFrac[1] + gap, 1.0] : [0, 1];
  const row2y = [0, rowFrac[1]];

  const layout = {
    title: { text: `Raingauge intercomparison ${currentDate}`, font:{size:13} },
    margin: { l:55, r:10, t:50, b:50 },
    showlegend: false,
    shapes,
    height: plotDiv.clientHeight || 500,
  };

  // Rain gauge axes — 3 equal columns, row 1
  const colW = 1 / N_GAUGES;
  const colGap = 0.03;
  for (let i = 0; i < N_GAUGES; i++) {
    const xa = i === 0 ? 'xaxis'  : `xaxis${i+1}`;
    const ya = i === 0 ? 'yaxis'  : `yaxis${i+1}`;
    const x0 = i * colW + (i > 0 ? colGap/2 : 0);
    const x1 = (i+1) * colW - (i < N_GAUGES-1 ? colGap/2 : 0);
    const primaryX = i === 0 ? 'x' : `x${i+1}`;
    const primaryY = i === 0 ? 'y' : `y${i+1}`;
    layout[xa] = { domain:[x0, x1], range:[-1,25],
                   title:{text: hasMet ? '' : 'Time (h)'},
                   tickvals:[0,4,8,12,16,20,24], anchor: primaryY };
    layout[ya] = { domain: row1y, title:{text: i===0?'Rain rate (mm/h)':''},
                   anchor: primaryX };
    const yvals = traces[i].y.filter(v => v !== null && v > 0);
    if (yvals.length === 0) layout[ya].range = [0, 0.01];
    // cumulative right-hand axis
    layout[`yaxis${6+i}`] = {
      overlaying: primaryY,
      side: 'right',
      title: { text: i === N_GAUGES-1 ? 'Cumul. (mm)' : '' },
      showgrid: false,
      rangemode: 'tozero',
      tickfont: { color:'#999', size:9 },
      titlefont: { color:'#999', size:10 },
      anchor: primaryX,
    };
  }

  // Met axes — single full-width panel, T left / RH right
  if (hasMet) {
    layout['xaxis4'] = { domain:[0, 1], range:[-1,25],
                         title:{text:'Time (h)'}, tickvals:[0,4,8,12,16,20,24],
                         anchor:'y4' };
    layout['yaxis4'] = { domain: row2y,
                         title:{ text:'Temp (°C)', font:{ color:'#e65100' } },
                         tickfont:{ color:'#e65100' },
                         anchor:'x4' };
    layout['yaxis5'] = { overlaying:'y4', side:'right',
                         range:[0,100],
                         title:{ text:'RH (%)', font:{ color:'#0288d1' } },
                         tickfont:{ color:'#0288d1' },
                         showgrid: false,
                         anchor:'x4' };
  }

  Plotly.newPlot(plotDiv, plotlyTraces, layout, { responsive:true, displayModeBar:false });

  // ── click handler (rain gauges only, curves 0–N_GAUGES-1) ─────────────────
  plotDiv.on('plotly_click', data => {
    if (!data.points.length) return;
    const pt = data.points[0];
    // Trace order: rate₀, cumul₀, rate₁, cumul₁, rate₂, cumul₂, T, RH
    // Rain-rate traces are at even curveNumbers 0, 2, 4 (i.e. 2*i for gauge i).
    const curveN = pt.curveNumber;
    if (curveN % 2 !== 0) return;          // cumulative trace — ignore
    const plotIdx = curveN / 2;
    if (plotIdx >= N_GAUGES) return;       // met traces — ignore
    const rawT = pt.x + CLICK_OFFSET;

    if (clickFirst === null) {
      clickFirst = { plot: plotIdx, t: rawT };
      status(`First click on ${TITLES[plotIdx]} at ${hoursToHHMMSS(rawT)} — click again to set end of window`);
      const xref = plotIdx === 0 ? 'x' : `x${plotIdx+1}`;
      const existingShapes = (plotDiv.layout && plotDiv.layout.shapes) ? [...plotDiv.layout.shapes] : [];
      Plotly.relayout(plotDiv, { shapes: [...existingShapes, {
        type:'line', xref, yref:'y domain',
        x0:rawT, x1:rawT, y0:0, y1:1,
        line:{color:'#fb8c00', width:1.5, dash:'dash'}
      }]});
    } else {
      const t0 = Math.min(clickFirst.t, rawT);
      const t1 = Math.max(clickFirst.t, rawT);
      if (clickFirst.plot !== plotIdx) {
        status('⚠ Second click was on a different subplot — ignored. Click again on the same subplot.');
        clickFirst = null;
        buildPlots();
        return;
      }
      if (t1 - t0 < 1/360) {
        status('⚠ Clicks too close together — ignored.');
        clickFirst = null;
        buildPlots();
        return;
      }
      pending.push({ plot: plotIdx, t0, t1, flag: getFlagType() });
      clickFirst = null;
      updatePendingList();
      buildPlots();
      status(`Pending: ${TITLES[plotIdx]}  ${hoursToHHMMSS(t0)} – ${hoursToHHMMSS(t1)}`);
    }
  });
}

function updatePendingList() {
  const el = document.getElementById('pending-list');
  if (!pending.length && !confirmed.length) {
    el.textContent = 'No pending corrections.';
    return;
  }
  const parts = [];
  for (const p of pending) {
    parts.push(`[pending/${p.flag||'HOLDCAL'}] ${TITLES[p.plot]} ${hoursToHHMMSS(p.t0)}\u2013${hoursToHHMMSS(p.t1)}`);
  }
  for (const c of confirmed) {
    parts.push(`[written/${c.flag||'HOLDCAL'}] ${TITLES[c.plot]} ${hoursToHHMMSS(c.t0)}\u2013${hoursToHHMMSS(c.t1)}`);
  }
  el.textContent = parts.join('   |   ');
}

// ── actions ──────────────────────────────────────────────────────────────────
function stepDay(delta) {
  const inp = document.getElementById('inp-date');
  const d = inp.value.trim();
  if (!/^\d{8}$/.test(d)) { alert('Enter a valid date first (yyyymmdd)'); return; }
  const dt = new Date(Date.UTC(
    parseInt(d.slice(0,4)), parseInt(d.slice(4,6)) - 1, parseInt(d.slice(6,8))));
  dt.setUTCDate(dt.getUTCDate() + delta);
  const yyyy = dt.getUTCFullYear();
  const mm   = String(dt.getUTCMonth() + 1).padStart(2, '0');
  const dd   = String(dt.getUTCDate()).padStart(2, '0');
  inp.value = `${yyyy}${mm}${dd}`;
  loadDay();
}

async function loadDay() {
  const d = document.getElementById('inp-date').value.trim();
  if (!/^\d{8}$/.test(d)) { alert('Enter date as yyyymmdd'); return; }
  status('Loading…');
  pending = []; confirmed = []; clickFirst = null;
  const res = await post('/load_day', { date: d });
  if (res.error) { alert('Error: ' + res.error); status(''); return; }
  currentDate = d;
  traces = res.traces;
  metData = (res.met && res.met.t && res.met.t.length) ? res.met : null;
  condensation = res.traces.map(tr => tr.condensation || []);
  const nCond = condensation.reduce((s, c) => s + c.length, 0);
  const condNote = nCond ? `  (${nCond} likely-condensation region${nCond>1?'s':''} auto-detected — see yellow shading)` : '';
  updatePendingList();
  buildPlots();
  status(`Loaded ${d}${condNote}`);
}

async function writeCorr() {
  if (!pending.length) { status('Nothing pending.'); return; }
  status('Writing…');
  const res = await post('/write_corrections', { date: currentDate, corrections: pending });
  if (res.error) { alert('Error: ' + res.error); status(''); return; }
  confirmed = confirmed.concat(pending);
  pending = [];
  clickFirst = null;
  updatePendingList();
  buildPlots();
  status('✓ Corrections written: ' + res.messages.join(' | '));
}

function undoLast() {
  if (!pending.length) { status('Nothing to undo.'); return; }
  pending.pop();
  updatePendingList();
  buildPlots();
  status('Last pending correction removed.');
}

function clearPending() {
  pending = [];
  clickFirst = null;
  updatePendingList();
  buildPlots();
  status('Pending list cleared.');
}

function flagCondensation() {
  if (!condensation.some(c => c.length)) { status('No condensation auto-detected for this day.'); return; }
  let added = 0;
  for (let i = 0; i < N_GAUGES; i++) {
    for (const [t0, t1] of condensation[i]) {
      // Skip if already pending or confirmed
      const dup = [...pending, ...confirmed].some(
        x => x.plot === i && Math.abs(x.t0 - t0) < 0.01 && Math.abs(x.t1 - t1) < 0.01);
      if (!dup) { pending.push({ plot: i, t0, t1 }); added++; }
    }
  }
  updatePendingList();
  buildPlots();
  status(added ? `Added ${added} condensation range${added>1?'s':''} to pending.` : 'All condensation ranges already pending/confirmed.');
}

async function quit() {
  await post('/quit', {});
  document.body.innerHTML = '<p style="padding:20px;font-size:16px">Server stopped. You can close this tab.</p>';
}

// ── resize ───────────────────────────────────────────────────────────────────
window.addEventListener('resize', () => { if (traces.length) buildPlots(); });

// ── paths panel ─────────────────────────────────────────────────────────────
function togglePaths() {
  const bar = document.getElementById('paths-bar');
  bar.style.display = bar.style.display === 'flex' ? 'none' : 'flex';
}

async function applyPaths() {
  const dp = document.getElementById('inp-data-path').value.trim();
  const res = await post('/set_paths', { data_path: dp });
  const ps = document.getElementById('paths-status');
  if (res.error) { ps.textContent = '⚠ ' + res.error; return; }
  ps.textContent = '✓ Applied';
  setTimeout(() => { ps.textContent = ''; }, 3000);
}

// ── init ─────────────────────────────────────────────────────────────────────
// Keyboard shortcuts: [ = prev day, ] = next day (only when not typing in an input)
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (e.key === '[') stepDay(-1);
  if (e.key === ']') stepDay(+1);
});

(async () => {
  const r = await fetch('/init');
  const d = await r.json();
  if (d.default_date) document.getElementById('inp-date').value = d.default_date;
  if (d.data_path)    document.getElementById('inp-data-path').value = d.data_path;
})();
</script>
</body>
</html>
"""


# ── Flask app factory ─────────────────────────────────────────────────────────

def create_app(data_path: str, default_date: str | None):
    app = Flask(__name__)
    # Mutable config — can be changed at runtime via /set_paths
    cfg = {'data_path': data_path}

    @app.route('/')
    def index():
        return _HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}

    @app.route('/plotly.js')
    def plotly_js():
        if _PLOTLY_CACHE.is_file():
            return _PLOTLY_CACHE.read_bytes(), 200, {'Content-Type': 'application/javascript'}
        return redirect(_PLOTLY_CDN, code=302)

    @app.route('/init')
    def init():
        dd = default_date or date.today().strftime('%Y%m%d')
        return jsonify({'default_date': dd, 'data_path': cfg['data_path']})

    @app.route('/set_paths', methods=['POST'])
    def set_paths():
        dp = request.json.get('data_path', '').strip()
        if dp and not os.path.isdir(dp):
            return jsonify({'error': f'data_path does not exist: {dp}'}), 400
        if dp:
            cfg['data_path'] = dp
        return jsonify({'data_path': cfg['data_path']})

    @app.route('/load_day', methods=['POST'])
    def load_day():
        yyyymmdd = request.json.get('date', '')
        if not (len(yyyymmdd) == 8 and yyyymmdd.isdigit()):
            return jsonify({'error': 'Invalid date format'}), 400
        try:
            result = read_day(yyyymmdd, cfg['data_path'])
            return jsonify(result)
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.route('/write_corrections', methods=['POST'])
    def write_corr():
        yyyymmdd    = request.json.get('date', '')
        corrections = request.json.get('corrections', [])
        if not yyyymmdd or not corrections:
            return jsonify({'error': 'Missing date or corrections'}), 400
        try:
            msgs = write_corrections(yyyymmdd, cfg['data_path'], corrections)
            return jsonify({'messages': msgs})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.route('/quit', methods=['POST'])
    def quit_server():
        threading.Thread(target=lambda: (time.sleep(0.5),
                                         os._exit(0))).start()
        return jsonify({'ok': True})

    return app


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Web-based raingauge QC click tool (port 8767)')
    parser.add_argument('-s', '--date', metavar='YYYYMMDD',
                        help='Pre-load this date on startup')
    parser.add_argument('-d', '--data-path', default='/gws/pw/j07/ncas_obs_vol2/cao/processing',
                        help='Root of amof-netCDF instrument tree (default: %(default)s)')
    parser.add_argument('-p', '--port', type=int, default=8767,
                        help='Port to serve on (default: %(default)s)')
    args = parser.parse_args()

    _ensure_plotly()

    app = create_app(args.data_path, args.date)
    print(f'Starting raingauge-click-plots-web on http://localhost:{args.port}')
    print('Use SSH port forwarding:')
    print(f'  ssh -L {args.port}:localhost:{args.port} <user>@<host>')
    print('then open http://localhost:{} in your browser.'.format(args.port))
    print('Press Ctrl-C or click Quit in the browser to stop.')
    app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
