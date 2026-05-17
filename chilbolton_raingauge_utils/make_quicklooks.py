#!/usr/bin/env python3
"""
Generate quicklook plots for raingauge data with QC flags.
"""

import xarray as xr
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import os
import argparse

try:
    from . import __version__
except ImportError:
    __version__ = "unknown"


def plot_day(ds, nc_filename, outdir):
    """Plot rainfall_rate with QC flags, shading bad data regions."""
    if ds.time.size == 0:
        print(f"Skipping {nc_filename}: no data")
        return

    try:
        date_str = [s for s in nc_filename.split('_') if s.isdigit() and len(s) == 8][0]
        date_label = pd.to_datetime(date_str, format="%Y%m%d").strftime('%Y-%m-%d')
    except IndexError:
        date_label = "unknown_date"

    day_start = pd.to_datetime(date_label)
    day_end = day_start + pd.Timedelta(days=1)

    time = ds['time'].values

    fig, ax = plt.subplots(figsize=(14, 5))

    ax.plot(time, ds['rainfall_rate'], color='steelblue', label='Rainfall rate')

    # Mark QC-flagged points (flag=2) with red crosses
    if 'qc_flag' in ds:
        flag_mask = (ds['qc_flag'].values == 2)
        if flag_mask.any():
            flagged_time = time[flag_mask]
            flagged_rate = ds['rainfall_rate'].values[flag_mask]
            ax.scatter(flagged_time, flagged_rate, color='red', marker='x',
                       s=60, linewidths=1.5, zorder=5, label='Pump cycle (QC flag=2)')

    ax.set_ylabel('Rainfall rate (mm hr$^{-1}$)')
    ax.set_xlabel('Time (UTC)')
    ax.set_title(f'Rainfall rate with QC flags — {date_label}')
    ax.legend()
    ax.grid(True)
    ax.set_xlim(day_start, day_end)

    plt.tight_layout()

    outfile = os.path.join(outdir, f"{nc_filename.replace('.nc', '.png')}")
    plt.savefig(outfile, dpi=200)
    plt.close()
    print(f"Saved {outfile}")


def main():
    """CLI entry point for make-raingauge-quicklooks command."""
    parser = argparse.ArgumentParser(description="Generate daily QC flag plots for raingauge NetCDF files.")
    parser.add_argument(
        "-i", "--input_dir",
        default="/gws/pw/j07/ncas_obs_vol2/cao/processing/ncas-rain-gauge-1/data/long-term/level1a/",
        help="Base directory containing yearly subdirectories of NetCDF files"
    )
    parser.add_argument(
        "-o", "--output_dir",
        default="/gws/pw/j07/ncas_obs_vol2/cao/processing/ncas-rain-gauge-1/data/long-term/level1a/quicklooks/",
        help="Base directory to save yearly subdirectories of PNG plots"
    )
    parser.add_argument(
        "-y", "--year",
        required=True,
        help="Year to process (e.g., 2024)"
    )
    parser.add_argument(
        "-d", "--day",
        help="Specific day to process (format: YYYYMMDD). If not provided, all days in the year will be processed."
    )
    args = parser.parse_args()

    # Construct year-specific input and output directories
    input_dir = os.path.join(args.input_dir, args.year)
    output_dir = os.path.join(args.output_dir, args.year)

    os.makedirs(output_dir, exist_ok=True)

    from pathlib import Path
    input_path = Path(input_dir)

    if not input_path.exists():
        print(f"Input directory does not exist: {input_dir}")
        return

    nc_files = sorted([f for f in input_path.rglob("*.nc")])

    if not nc_files:
        print(f"No .nc files found in input directory: {input_dir}")
        return

    if args.day:
        nc_files = [f for f in nc_files if args.day in f.name]
        if not nc_files:
            print(f"No .nc files found for the specified day: {args.day}")
            return

    for nc_file in nc_files:
        try:
            ds = xr.open_dataset(nc_file)
            ds = ds.sortby('time')
            if 'time' in ds:
                plot_day(ds, nc_file.name, output_dir)
            else:
                print(f"{nc_file.name}: no 'time' variable")
        except Exception as e:
            print(f"Failed to process {nc_file.name}: {e}")


if __name__ == "__main__":
    main()
