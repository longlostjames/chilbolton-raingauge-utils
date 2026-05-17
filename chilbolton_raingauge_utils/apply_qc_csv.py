"""
Apply QC flags from a CSV file (written by gauge-qc-web) to the corresponding NetCDF file.

The CSV format is:
    index,time,qc_flag

where ``index`` is the zero-based position in the NetCDF time dimension.
If no NetCDF path is given explicitly, the file with the same stem and a .nc
extension is used.
"""

import argparse
import csv
import datetime
import sys
from pathlib import Path

import netCDF4 as nc4
import numpy as np


def apply_csv(csv_path: str, nc_path: str | None = None) -> None:
    csv_p = Path(csv_path)
    if not csv_p.is_file():
        sys.exit(f"CSV file not found: {csv_path}")

    nc_p = Path(nc_path) if nc_path else csv_p.with_suffix('.nc')
    if not nc_p.is_file():
        sys.exit(f"NetCDF file not found: {nc_p}")

    # Read CSV
    indices, flags = [], []
    with open(csv_p, newline='') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            indices.append(int(row['index']))
            flags.append(int(row['qc_flag']))

    if not indices:
        sys.exit("CSV file contains no data rows.")

    flags_arr = np.array(flags, dtype=np.int8)

    # Apply to NetCDF
    timestamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    history_entry = (
        f"{timestamp} - QC flags applied from CSV: {csv_p.name} "
        f"using apply-raingauge-qc-csv"
    )

    with nc4.Dataset(str(nc_p), 'r+') as nc:
        n_times = len(nc.dimensions['time'])
        bad = [i for i in indices if i < 0 or i >= n_times]
        if bad:
            sys.exit(f"CSV contains {len(bad)} out-of-range index values (max allowed: {n_times - 1}).")

        if 'qc_flag' in nc.variables:
            nc.variables['qc_flag'][indices] = flags_arr
        else:
            qc_var = nc.createVariable('qc_flag', 'i1', ('time',), fill_value=-127)
            qc_var.units = '1'
            qc_var.long_name = 'Data Quality flag'
            # Initialise to 1 (good_data) then overwrite the CSV rows
            qc_var[:] = np.ones(n_times, dtype=np.int8)
            qc_var[indices] = flags_arr

        existing_history = getattr(nc, 'history', None)
        nc.history = (
            f"{history_entry}\n{existing_history}"
            if existing_history
            else history_entry
        )
        nc.last_revised_date = timestamp

    n_flagged = int((flags_arr != 1).sum())
    print(f"Applied {len(indices)} flag values ({n_flagged} non-good) to {nc_p}")


def main():
    parser = argparse.ArgumentParser(
        description="Apply QC flags from a gauge-qc-web CSV file to a NetCDF file."
    )
    parser.add_argument('csv_file', help="CSV file containing index,time,qc_flag columns")
    parser.add_argument(
        '-n', '--nc-file', default=None,
        help="NetCDF file to update (default: same path as CSV with .nc extension)"
    )
    args = parser.parse_args()
    apply_csv(args.csv_file, args.nc_file)


if __name__ == '__main__':
    main()
