#!/usr/bin/env python3
"""
Apply bad data indices from a CSV file to raingauge netCDF files.
"""

import xarray as xr
import pandas as pd
import numpy as np
import argparse
import os
from pathlib import Path
import shutil
from netCDF4 import Dataset
from datetime import datetime


def set_time_units_to_seconds_since_epoch(nc_file):
    """
    Reopen the NetCDF file using netCDF4 and set the time units to 'seconds since 1970-01-01 00:00:00'.
    """
    with Dataset(nc_file, mode='r+') as ds:
        if 'time' in ds.variables:
            time_var = ds.variables['time']
            time_var.setncattr('units', 'seconds since 1970-01-01 00:00:00')
            time_var.setncattr('standard_name', 'time')
            time_var.setncattr('long_name', 'Time (seconds since 1970-01-01 00:00:00)')
            time_var.setncattr('axis', 'T')
            if len(time_var[:]) > 0:
                time_var.setncattr('valid_min', float(time_var[:].min()))
                time_var.setncattr('valid_max', float(time_var[:].max()))


def apply_bad_data_indices_to_file(nc_file, bad_data_indices_row):
    """
    Apply bad data flags to a netCDF file based on indices from CSV.

    Parameters:
        nc_file: Path to netCDF file
        bad_data_indices_row: Row from the CSV DataFrame with bad data indices
    """
    flag_good = 1
    flag_bad = 2

    with xr.open_dataset(nc_file, mode='r+') as ds:
        if 'qc_flag_thickness_of_rainfall_amount' not in ds:
            print(f"Warning: qc_flag_thickness_of_rainfall_amount not found in {nc_file}, skipping")
            return

        qc_rainfall = ds['qc_flag_thickness_of_rainfall_amount'].values.copy()

        # Reset all bad data flags to good (1)
        qc_rainfall[qc_rainfall == flag_bad] = flag_good

        # Handle variable number of rainfall bad data periods dynamically
        rainfall_bad_num = 1
        while True:
            start_col = f'rainfall_bad{rainfall_bad_num}_start_idx'
            end_col = f'rainfall_bad{rainfall_bad_num}_end_idx'

            if start_col not in bad_data_indices_row.index or end_col not in bad_data_indices_row.index:
                break

            if pd.notna(bad_data_indices_row.get(start_col)) and pd.notna(bad_data_indices_row.get(end_col)):
                start_idx = int(bad_data_indices_row[start_col])
                end_idx = int(bad_data_indices_row[end_col])
                qc_rainfall[start_idx:end_idx + 1] = flag_bad

            rainfall_bad_num += 1

        ds['qc_flag_thickness_of_rainfall_amount'].values[:] = qc_rainfall

        # Update history and last_modified attributes
        timestamp = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
        history_entry = f"{timestamp} - Applied bad data indices from CSV file using apply-raingauge-bad-data-indices"
        if 'history' in ds.attrs:
            ds.attrs['history'] = f"{history_entry}\n{ds.attrs['history']}"
        else:
            ds.attrs['history'] = history_entry

        ds.attrs['last_modified'] = timestamp

        temp_filename = str(nc_file) + '.tmp'
        ds.to_netcdf(temp_filename)

    shutil.move(temp_filename, nc_file)
    set_time_units_to_seconds_since_epoch(nc_file)


def find_nc_file_for_date(input_dir, date, year=None):
    """Find the netCDF file corresponding to a given date."""
    date_str = date.strftime('%Y%m%d')

    if year:
        search_dir = Path(input_dir) / str(year)
    else:
        search_dir = Path(input_dir)

    candidates = list(search_dir.glob(f"*{date_str}*.nc"))

    if candidates:
        return candidates[0]

    return None


def main():
    """CLI entry point for apply-raingauge-bad-data-indices command."""
    parser = argparse.ArgumentParser(
        description="Apply bad data indices from CSV to raingauge netCDF files."
    )
    parser.add_argument(
        "-c", "--csv_file",
        required=True,
        help="CSV file with bad data indices (created by extract-raingauge-bad-data-indices)"
    )
    parser.add_argument(
        "-i", "--input_dir",
        required=True,
        help="Directory containing netCDF files (or parent directory with year subdirectories)"
    )
    parser.add_argument(
        "-y", "--year",
        type=int,
        default=None,
        help="Specific year to process (optional, will look in input_dir/YYYY/)"
    )

    args = parser.parse_args()

    # Read raw lines to find maximum number of columns (handles variable-width CSV)
    max_cols = 0
    with open(args.csv_file, 'r') as f:
        for line in f:
            num_cols = len(line.strip().split(','))
            if num_cols > max_cols:
                max_cols = num_cols

    # Read CSV, padding rows with fewer columns using None
    rows = []
    with open(args.csv_file, 'r') as f:
        header = f.readline().strip().split(',')
        for line in f:
            fields = line.strip().split(',')
            # Pad with empty strings if fewer columns
            fields += [''] * (max_cols - len(fields))
            rows.append(fields[:max_cols])

    # Extend header if data rows have more columns
    if max_cols > len(header):
        extra = max_cols - len(header)
        for i in range(extra):
            header.append(f"extra_{i}")

    df = pd.DataFrame(rows, columns=header[:max_cols])
    df['date'] = pd.to_datetime(df['date'])

    # Convert index columns to numeric
    for col in df.columns:
        if col != 'date':
            df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int64')

    print(f"Loaded {len(df)} rows from {args.csv_file}")

    # Process each row
    processed = 0
    skipped = 0
    for _, row in df.iterrows():
        nc_file = find_nc_file_for_date(args.input_dir, row['date'], year=args.year)

        if nc_file is None:
            print(f"No netCDF file found for {row['date'].strftime('%Y-%m-%d')}, skipping")
            skipped += 1
            continue

        try:
            apply_bad_data_indices_to_file(nc_file, row)
            print(f"Updated {nc_file.name}")
            processed += 1
        except Exception as e:
            print(f"Error processing {nc_file}: {e}")
            skipped += 1

    print(f"\nDone. Processed: {processed}, skipped: {skipped}")


if __name__ == "__main__":
    main()
