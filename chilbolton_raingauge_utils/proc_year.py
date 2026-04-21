#!/usr/bin/env python3
"""Process a full year of raingauge data from CR1000X format."""

import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta

from .process_raingauge import process_file


def main():
    """Process a full year of raingauge data."""
    parser = argparse.ArgumentParser(
        description="Process a full year of RAL Drop Counting Gauge raingauge data from CR1000X format.",
        epilog="""This command processes an entire year of RAL Drop Counting Gauge raingauge
data from Campbell Scientific CR1000X datalogger format (TOA5) to CF-compliant
NetCDF files."""
    )
    parser.add_argument("-y", "--year", required=True, type=int,
                        help="Year to process (e.g., 2024)")
    parser.add_argument("--raw-data-base", type=str,
                        default="/gws/pw/j07/ncas_obs_vol2/cao/raw_data/met_cao/data/long-term/new_daily_split",
                        help="Base directory for raw data")
    parser.add_argument("--output-base", type=str,
                        default="/gws/pw/j07/ncas_obs_vol2/cao/processing/ncas-rain-gauge-1/data/long-term/level1a",
                        help="Base directory for output NetCDF files")

    args = parser.parse_args()

    # Get metadata file from package installation
    script_dir = Path(__file__).parent
    metadata_file = script_dir / "metadata.json"

    if not metadata_file.exists():
        print(f"Error: Metadata file not found at {metadata_file}", file=sys.stderr)
        sys.exit(1)

    # Date range for the given year
    start_date = datetime(args.year, 1, 1)
    end_date = datetime(args.year, 12, 31)

    current_date = start_date

    while current_date <= end_date:
        year_month = current_date.strftime("%Y%m")
        date_str = current_date.strftime("%Y%m%d")

        # Create output directory
        outdir = Path(args.output_base) / str(args.year)
        outdir.mkdir(parents=True, exist_ok=True)

        # Construct input file path
        infile = Path(args.raw_data_base) / str(args.year) / year_month / f"CR1000XSeries_Chilbolton_Rxcabinmet1_{date_str}.dat"

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
