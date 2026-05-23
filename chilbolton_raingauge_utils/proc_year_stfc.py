#!/usr/bin/env python3
"""Process a full year of raingauge data from STFC variant."""

import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta

from .process_raingauge_stfc import process_file

_GWS = "/gws/pw/j07/ncas_obs_vol2/cao"
_RAW_DATA_BASE = f"{_GWS}/raw_data/met_cao/data/long-term"

GAUGE_CONFIGS = {
    1: {
        "instrument_name": "stfc-rain-gauge-1",
        "column_name": "rg001dc_ch_Tot",
        "metadata_file": "metadata_rg1_stfc.json",
        "output_base": f"{_GWS}/processing/ncas-rain-gauge-1/data/long-term/level1b",
    },
    2: {
        "instrument_name": "stfc-rain-gauge-2",
        "column_name": "rg006dc_ch_Tot",
        "metadata_file": "metadata_rg2_stfc.json",
        "output_base": f"{_GWS}/processing/ncas-rain-gauge-2/data/long-term/level1b",
    },
    3: {
        "instrument_name": "stfc-rain-gauge-3",
        "column_name": "rg008dc_ch_Tot",
        "metadata_file": "metadata_rg3_stfc.json",
        "output_base": f"{_GWS}/processing/ncas-rain-gauge-3/data/long-term/level1b",
    },
    9: {
        "instrument_name": "stfc-rain-gauge-9",
        "column_name": "rg009dc_ch_Tot",
        "metadata_file": "metadata_rg9_stfc.json",
        "output_base": f"{_GWS}/processing/ncas-rain-gauge-9/data/long-term/level1b",
    },
    5: {
        "instrument_name": "stfc-rain-gauge-5",
        "column_name": "rg004tb_ch_Tot",
        "count_var_name": "number_of_tips",
        "column_is_mm": True,
        "metadata_file": "metadata_rg5_stfc.json",
        "output_base": f"{_GWS}/processing/ncas-rain-gauge-5/data/long-term/level1b",
    },
}


def main():
    """Process a full year of raingauge data from STFC variant."""
    parser = argparse.ArgumentParser(
        description="Process a full year of RAL Drop Counting Gauge raingauge data from STFC variant.",
        epilog="""This command processes an entire year of RAL Drop Counting Gauge raingauge
data from Campbell Scientific CR1000X datalogger format (STFC variant) to
CF-compliant NetCDF files."""
    )
    parser.add_argument("-y", "--year", required=True, type=int,
                        help="Year to process (e.g., 2024)")
    parser.add_argument("-g", "--gauge", required=True, type=int, choices=[1, 2, 3, 5, 9],
                        help="Gauge number to process (1, 2, 3, 5, or 9)")
    parser.add_argument("--raw-data-base", type=str, default=_RAW_DATA_BASE,
                        help="Base directory for raw data")
    parser.add_argument("--output-base", type=str, default=None,
                        help="Base directory for output NetCDF files (default: gauge-specific path)")
    args = parser.parse_args()

    cfg = GAUGE_CONFIGS[args.gauge]
    output_base = args.output_base or cfg["output_base"]

    script_dir = Path(__file__).parent
    metadata_file = script_dir / cfg["metadata_file"]

    if not metadata_file.exists():
        print(f"Error: Metadata file not found at {metadata_file}", file=sys.stderr)
        sys.exit(1)

    start_date = datetime(args.year, 1, 1)
    end_date = datetime(args.year, 12, 31)
    current_date = start_date

    while current_date <= end_date:
        year_month = current_date.strftime("%Y%m")
        date_str = current_date.strftime("%Y%m%d")

        outdir = Path(output_base) / str(args.year)
        outdir.mkdir(parents=True, exist_ok=True)

        infile = Path(args.raw_data_base) / str(args.year) / year_month / f"CR1000XSeries_Chilbolton_Rxcabinmet1_{date_str}.dat"

        if not infile.exists():
            print(f"Warning: Input file not found: {infile}")
            current_date += timedelta(days=1)
            continue

        try:
            process_file(str(infile), str(outdir), str(metadata_file),
                         instrument_name=cfg["instrument_name"],
                         column_name=cfg["column_name"],
                         count_var_name=cfg.get("count_var_name", "number_of_drops"),
                         column_is_mm=cfg.get("column_is_mm", False))
        except Exception as e:
            print(f"Error processing {infile}: {e}", file=sys.stderr)

        current_date += timedelta(days=1)

    print(f"\nProcessing complete for year {args.year}")


if __name__ == "__main__":
    main()
