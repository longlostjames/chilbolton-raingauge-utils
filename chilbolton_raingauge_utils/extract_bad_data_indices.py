#!/usr/bin/env python3
"""
Extract bad data indices from netCDF files and save to CSV.
"""

import xarray as xr
import pandas as pd
import argparse
import os
from pathlib import Path


def get_bad_data_intervals(qc_flag, flag_value=2):
    """Return list of (start_idx, end_idx) tuples where the specified QC flag value occurs."""
    bad_data_mask = (qc_flag == flag_value).values
    intervals = []

    start_idx = None
    for i, val in enumerate(bad_data_mask):
        if val and start_idx is None:
            start_idx = i
        elif not val and start_idx is not None:
            end_idx = i - 1
            intervals.append((start_idx, end_idx))
            start_idx = None
    if start_idx is not None:
        intervals.append((start_idx, len(bad_data_mask) - 1))
    return intervals


def extract_bad_data_indices_from_file(nc_file):
    """Extract bad data indices from a single netCDF file for rainfall amount."""
    try:
        ds = xr.open_dataset(nc_file)

        # Get date from filename
        filename = os.path.basename(nc_file)
        try:
            date_str = [s for s in filename.split('_') if s.isdigit() and len(s) == 8][0]
            date = pd.to_datetime(date_str, format="%Y%m%d")
        except (IndexError, ValueError):
            date = pd.to_datetime(ds['time'].values[0]).normalize()

        # Get bad data intervals (flag=2) for rainfall amount
        rainfall_bad_intervals = []
        if 'qc_flag_thickness_of_rainfall_amount' in ds:
            rainfall_bad_intervals = get_bad_data_intervals(ds['qc_flag_thickness_of_rainfall_amount'], flag_value=2)

        ds.close()

        # Build result dictionary dynamically for any number of bad data periods
        result = {'date': date}

        for i, (start, end) in enumerate(rainfall_bad_intervals, start=1):
            result[f'rainfall_bad{i}_start_idx'] = start
            result[f'rainfall_bad{i}_end_idx'] = end

        return result

    except Exception as e:
        print(f"Error processing {nc_file}: {e}")
        return None


def main():
    """CLI entry point for extract-raingauge-bad-data-indices command."""
    parser = argparse.ArgumentParser(description="Extract bad data indices from raingauge netCDF files to CSV.")
    parser.add_argument(
        "-i", "--input_dir",
        required=True,
        help="Directory containing netCDF files (or parent directory with year subdirectories)"
    )
    parser.add_argument(
        "-o", "--output_file",
        required=True,
        help="Output CSV file path"
    )
    parser.add_argument(
        "-y", "--year",
        type=int,
        default=None,
        help="Specific year to process (optional, will look in input_dir/YYYY/)"
    )

    args = parser.parse_args()

    # Determine input directory
    if args.year:
        input_path = Path(args.input_dir) / str(args.year)
    else:
        input_path = Path(args.input_dir)

    if not input_path.exists():
        print(f"Error: Input directory {input_path} does not exist")
        return

    # Find all netCDF files
    nc_files = sorted(input_path.glob("*.nc"))

    if not nc_files:
        print(f"No netCDF files found in {input_path}")
        return

    print(f"Processing {len(nc_files)} files from {input_path}")

    # Extract indices from all files
    results = []
    for nc_file in nc_files:
        result = extract_bad_data_indices_from_file(nc_file)
        if result:
            results.append(result)

    # Create DataFrame and save to CSV
    if results:
        df = pd.DataFrame(results)
        df = df.sort_values('date')

        for col in df.columns:
            if col != 'date' and ('_start_idx' in col or '_end_idx' in col):
                df[col] = df[col].astype('Int64')

        output_path = Path(args.output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        df.to_csv(args.output_file, index=False)
        print(f"Saved bad data indices to {args.output_file}")
        print(f"Processed {len(results)} days")
    else:
        print("No results to save")


if __name__ == "__main__":
    main()
