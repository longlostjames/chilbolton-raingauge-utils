"""
# Split Campbell Scientific CR1000X data files into daily files
"""

import pandas as pd
from pathlib import Path
import argparse
import filecmp
import csv

try:
    from . import __version__
except ImportError:
    __version__ = "unknown"

def split_file(input_file, output_dir, delimiter, timestamp_column, verbose=False):
    # === READ THE FIRST 4 HEADER LINES (TOA5 METADATA) ===
    with open(input_file, 'r', encoding='utf-8') as f:
        header_lines = [next(f) for _ in range(4)]

    # === LOAD DATAFRAME ===
    df = pd.read_csv(
        input_file,
        skiprows=1,           # Only skip the first metadata line
        delimiter=delimiter,
        quotechar='"',
        low_memory=False
    )
    df.columns = df.columns.str.strip().str.replace('"', '')

    if timestamp_column not in df.columns:
        print(f"ERROR: Timestamp column '{timestamp_column}' not found in columns: {df.columns.tolist()}")
        return

    # Parse timestamps with explicit format (CR1000X typical format: YYYY-MM-DD HH:MM:SS)
    df[timestamp_column] = pd.to_datetime(df[timestamp_column], format='%Y-%m-%d %H:%M:%S', errors='coerce')
    df = df.dropna(subset=[timestamp_column])

    # === SHIFT MIDNIGHT TO PREVIOUS DAY ===
    timestamps = df[timestamp_column]
    adjusted_dates = timestamps.dt.date.where(
        timestamps.dt.time != pd.to_datetime("00:00:00").time(),
        timestamps.dt.date - pd.Timedelta(days=1)
    )
    df['group_date'] = adjusted_dates

    # === WRITE DAILY FILES WITH HEADER IN YYYY/YYYYMM SUBDIRS ===
    input_base = Path(input_file).stem
    for date, group in df.groupby('group_date'):
        date_obj = pd.to_datetime(date)
        year_str = date_obj.strftime('%Y')
        ym_str = date_obj.strftime('%Y%m')
        ymd_str = date_obj.strftime('%Y%m%d')
        out_subdir = Path(output_dir) / year_str / ym_str
        out_subdir.mkdir(parents=True, exist_ok=True)
        out_file = out_subdir / f"{input_base}_{ymd_str}.dat"

        # Convert timestamp column to string (no extra quotes)
        group = group.copy()
        group[timestamp_column] = group[timestamp_column].dt.strftime('%Y-%m-%d %H:%M:%S')

        # Write the 4-line TOA5 header
        with open(out_file, 'w', encoding='utf-8', newline='') as f:
            f.writelines(header_lines)
            group.drop(columns='group_date').to_csv(
                f,
                index=False,
                header=False,  # Don't write header - already in header_lines
                quoting=csv.QUOTE_MINIMAL,
                quotechar='"'
            )
        if verbose:
            print(f"Saved: {out_file}")

def deduplicate_daily_files(output_dir, verbose=False):
    # Walk through all YYYY/YYYYMM subdirs
    for year_dir in Path(output_dir).glob("*"):
        if not year_dir.is_dir():
            continue
        for month_dir in year_dir.glob("*"):
            if not month_dir.is_dir():
                continue
            # Group files by date
            files_by_date = {}
            for file in month_dir.glob("*.dat"):
                # Extract date string from filename (last 8 digits before .dat)
                parts = file.stem.split('_')
                if len(parts) < 2:
                    continue
                date_str = parts[-1]
                files_by_date.setdefault(date_str, []).append(file)
            # For each date, compare files and keep only one if identical
            for date_str, files in files_by_date.items():
                if len(files) <= 1:
                    continue
                # Compare all files for this date
                keep = files[0]
                for f in files[1:]:
                    if filecmp.cmp(keep, f, shallow=False):
                        if verbose:
                            print(f"Duplicate found for {date_str}: {f} is identical to {keep}, removing {f}")
                        f.unlink()
                    else:
                        print(f"WARNING: {f} and {keep} for {date_str} differ, keeping both.")

def main():
    """CLI entry point for split-cr1000x-data-daily command."""
    parser = argparse.ArgumentParser(description="Split all CR1000X_Chilbolton_Rxcabinmet1*.dat files in a directory into daily files in YYYY/YYYYMM subdirectories, deduplicating identical daily files.")
    parser.add_argument("-i", "--input_dir", required=True, help="Directory containing CR1000X_Chilbolton_Rxcabinmet1*.dat files")
    parser.add_argument("-o", "--output_dir", default="daily_files", help="Directory to write daily files (default: daily_files)")
    parser.add_argument("-d", "--delimiter", default=",", help="Delimiter for input file (default: ',')")
    parser.add_argument("-t", "--timestamp_column", default="TIMESTAMP", help="Name of timestamp column (default: TIMESTAMP)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = args.output_dir

    print(f"Input directory: {input_dir}")
    print(f"Output directory: {output_dir}")

    # Process all matching files
    for input_file in sorted(input_dir.glob("CR1000XSeries_Chilbolton_Rxcabinmet1*.dat")):
        print(f"Processing {input_file}")
        split_file(str(input_file), output_dir, args.delimiter, args.timestamp_column, args.verbose)

    # Deduplicate daily files
    deduplicate_daily_files(output_dir, args.verbose)


if __name__ == "__main__":
    main()