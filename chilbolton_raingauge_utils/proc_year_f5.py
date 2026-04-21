#!/usr/bin/env python3
"""Process a full year of raingauge data from Format5."""

import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta

from .process_raingauge_f5 import process_file


def main():
    """Process a full year of raingauge data from Format5."""
    parser = argparse.ArgumentParser(
        description="Process a full year of RAL Drop Counting Gauge raingauge data from legacy Format5.",
        epilog="""This command processes an entire year of RAL Drop Counting Gauge raingauge
data from legacy Format5 binary files to CF-compliant NetCDF files."""
    )
    parser.add_argument("-y", "--year", required=True, type=int,
                        help="Year to process (e.g., 2018)")
    parser.add_argument("--raw-data-base", type=str,
                        default="/gws/pw/j07/ncas_obs_vol2/cao/raw_data/legacy/cao-analog-format5_chilbolton/data/long-term/format5",
                        help="Base directory for raw Format5 data")
    parser.add_argument("--output-base", type=str,
                        default="/gws/pw/j07/ncas_obs_vol2/cao/processing/ncas-rain-gauge-1/data/long-term/level1_f5",
                        help="Base directory for output NetCDF files")

    args = parser.parse_args()

    # Get metadata file from package installation
    script_dir = Path(__file__).parent
    metadata_file = script_dir / "metadata_f5.json"

    if not metadata_file.exists():
        print(f"Error: Metadata file not found at {metadata_file}", file=sys.stderr)
        sys.exit(1)

    # Date range for the given year
    start_date = datetime(args.year, 1, 1)
    end_date = datetime(args.year, 12, 31)

    current_date = start_date

    while current_date <= end_date:
        date_str_short = current_date.strftime("%y%m%d")

        # Create output directory
        outdir = Path(args.output_base) / str(args.year)
        outdir.mkdir(parents=True, exist_ok=True)

        # Construct input file path (Format5 uses YYMMDD format)
        infile = Path(args.raw_data_base) / f"chan{date_str_short}.000"

        if not infile.exists():
            print(f"Warning: Input file not found: {infile}")
            current_date += timedelta(days=1)
            continue

        # Generate NetCDF file
        try:
            process_file(str(infile), str(outdir), str(metadata_file))
        except Exception as e:
            print(f"Error processing {infile}: {e}", file=sys.stderr)

        current_date += timedelta(days=1)

    print(f"\nProcessing complete for year {args.year}")


if __name__ == "__main__":
    main()
