#!/usr/bin/env python3
"""Process a single month of raingauge data from Format5."""

import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta

from .process_raingauge_f5 import process_file

_GWS = "/gws/pw/j07/ncas_obs_vol2/cao"
_RAW_DATA_BASE = f"{_GWS}/raw_data/legacy/cao-analog-format5_chilbolton/data/long-term/format5"

GAUGE_CONFIGS = {
    1: {
        "instrument_name": "ncas-rain-gauge-1",
        "channel_name": "rg001dc_ch",
        "metadata_file": "metadata_rg1_f5.json",
        "output_base": f"{_GWS}/processing/ncas-rain-gauge-1/data/long-term/level1_f5",
    },
    2: {
        "instrument_name": "ncas-rain-gauge-2",
        "channel_name": "rg006dc_ch",
        "metadata_file": "metadata_rg2_f5.json",
        "output_base": f"{_GWS}/processing/ncas-rain-gauge-2/data/long-term/level1_f5",
    },
    3: {
        "instrument_name": "ncas-rain-gauge-3",
        "channel_name": "rg008dc_ch",
        "metadata_file": "metadata_rg3_f5.json",
        "output_base": f"{_GWS}/processing/ncas-rain-gauge-3/data/long-term/level1_f5",
    },
    9: {
        "instrument_name": "ncas-rain-gauge-9",
        "channel_name": "rg009dc_ch",
        "metadata_file": "metadata_rg9_f5.json",
        "output_base": f"{_GWS}/processing/ncas-rain-gauge-9/data/long-term/level1_f5",
    },
    5: {
        "instrument_name": "ncas-rain-gauge-5",
        "channel_name": "rg004tb_ch",
        "metadata_file": "metadata_rg5_f5.json",
        "output_base": f"{_GWS}/processing/ncas-rain-gauge-5/data/long-term/level1_f5",
    },
}


def main():
    """Process a single month of raingauge data from Format5."""
    parser = argparse.ArgumentParser(
        description="Process a single month of RAL Drop Counting Gauge raingauge data from legacy Format5.",
    )
    parser.add_argument("-y", "--year", required=True, type=int,
                        help="Year to process (e.g., 2018)")
    parser.add_argument("-m", "--month", required=True, type=int,
                        help="Month to process (1-12)")
    parser.add_argument("-g", "--gauge", required=True, type=int, choices=[1, 2, 3, 5, 9],
                        help="Gauge number to process (1, 2, 3, 5, or 9)")
    parser.add_argument("--raw-data-base", type=str, default=_RAW_DATA_BASE,
                        help="Base directory for raw Format5 data")
    parser.add_argument("--output-base", type=str, default=None,
                        help="Base directory for output NetCDF files (default: gauge-specific path)")
    args = parser.parse_args()

    if args.month < 1 or args.month > 12:
        print(f"Error: Month must be between 1 and 12, got {args.month}", file=sys.stderr)
        sys.exit(1)

    cfg = GAUGE_CONFIGS[args.gauge]
    output_base = args.output_base or cfg["output_base"]

    script_dir = Path(__file__).parent
    metadata_file = script_dir / cfg["metadata_file"]

    if not metadata_file.exists():
        print(f"Error: Metadata file not found at {metadata_file}", file=sys.stderr)
        sys.exit(1)

    start_date = datetime(args.year, args.month, 1)
    if args.month == 12:
        end_date = datetime(args.year, 12, 31)
    else:
        end_date = datetime(args.year, args.month + 1, 1) - timedelta(days=1)

    current_date = start_date

    while current_date <= end_date:
        date_str_short = current_date.strftime("%y%m%d")

        outdir = Path(output_base) / str(args.year)
        outdir.mkdir(parents=True, exist_ok=True)

        infile = Path(args.raw_data_base) / f"chan{date_str_short}.000"

        if not infile.exists():
            print(f"Warning: Input file not found: {infile}")
            current_date += timedelta(days=1)
            continue

        try:
            process_file(str(infile), str(outdir), str(metadata_file),
                         instrument_name=cfg["instrument_name"],
                         channel_name=cfg["channel_name"])
        except Exception as e:
            print(f"Error processing {infile}: {e}", file=sys.stderr)

        current_date += timedelta(days=1)

    print(f"\nProcessing complete for {args.year}-{args.month:02d}")


if __name__ == "__main__":
    main()
