"""
# Process RAL Drop Counting Gauge raingauge data to netCDF
"""

import polars as pl
import numpy as np
from datetime import datetime
import ncas_amof_netcdf_template as nant
import datetime as dt
import re
import os
import sys
import argparse
import cftime
from datetime import timezone
from pathlib import Path

try:
    from . import __version__
except ImportError:
    __version__ = "unknown"

# --- Local AMF CV definitions (avoids GitHub rate-limiting on SLURM) -----------
# nant's _check_website_exists uses requests.get() even for local paths, so we
# patch it to handle filesystem paths directly.
_AMF_CVs_TAG = "v2.2.0"
_AMF_CVs_LOCAL = str(Path(__file__).parent / "amf_cvs_local")

def _patched_check_website_exists(self, url: str) -> bool:
    if not url.startswith("http"):
        return os.path.exists(url)
    import requests
    return requests.get(url).status_code == 200

nant.file_info.FileInfo._check_website_exists = _patched_check_website_exists
# -------------------------------------------------------------------------------

# ---- Pump cycle detection ----------------------------------------------------
# Every 12 hours the gauge automatically flushes its reservoir.  The controller
# suppresses counting during the flush, but 1–2 spurious drops are often seen
# in the sample immediately after the cycle completes.  These are identified by
# their regular ~12-hour periodicity and isolation (zero counts in the
# surrounding samples).
#
# Detection approach: find isolated 1–2 count spikes, then for each such spike
# look for a single best-matching partner ~12 hours later.  Using the closest
# match (rather than all matches within a tolerance window) avoids mistakenly
# pairing genuine drizzle drops with a real pump event.
PUMP_INTERVAL_SAMPLES = 4320   # 12 hours at 10 s per sample
PUMP_INTERVAL_TOLERANCE = 60   # ±10 minutes = ±60 samples
PUMP_MAX_DROPS = 2             # maximum drops considered a pump artefact


def detect_pump_cycles(drop_counts,
                       interval_samples=PUMP_INTERVAL_SAMPLES,
                       tolerance=PUMP_INTERVAL_TOLERANCE,
                       max_pump_drops=PUMP_MAX_DROPS):
    """Return a boolean array that is True for pump cycle artefacts.

    Each pump cycle produces 1–2 spurious drop counts in the sample immediately
    after the 12-hourly automatic flush completes.  The artefact is identified
    by two criteria:

    1. **Isolation** — the sample has 1–*max_pump_drops* counts and both its
       immediate neighbours are zero.
    2. **Periodicity** — a paired isolated spike exists approximately
       *interval_samples* samples (12 hours) earlier or later.  Where multiple
       candidates fall within the tolerance window, the one closest to exactly
       12 hours is chosen to avoid pairing genuine light-rain drops with a real
       pump event.

    Parameters
    ----------
    drop_counts : array-like of int
        Raw rg001dc_ch_Tot values (one element per 10-second interval).
    interval_samples : int
        Expected separation between pump cycles in samples (default 4320 = 12 h).
    tolerance : int
        Allowed deviation from *interval_samples* in samples (default 60 = ±10 min).
    max_pump_drops : int
        Maximum drop count treated as a potential pump artefact (default 2).

    Returns
    -------
    numpy.ndarray of bool
        Same length as *drop_counts*.  True where a pump cycle artefact is present.
    """
    counts = np.array(drop_counts, dtype=float)
    counts = np.where(np.isnan(counts), 0.0, counts)
    n = len(counts)

    # Isolated small spikes: value in [1, max_pump_drops] with zero neighbours
    candidates = np.array(
        [i for i in range(1, n - 1)
         if 1 <= counts[i] <= max_pump_drops
         and counts[i - 1] == 0 and counts[i + 1] == 0],
        dtype=int,
    )

    flag = np.zeros(n, dtype=bool)
    if len(candidates) < 2:
        return flag

    flagged: set[int] = set()
    for idx_a in candidates:
        # All candidates approximately one interval later
        lo = idx_a + interval_samples - tolerance
        hi = idx_a + interval_samples + tolerance
        window = candidates[(candidates > lo) & (candidates < hi)]
        if len(window) == 0:
            continue
        # Pick the single closest match to exactly 12 h
        best = int(window[np.argmin(np.abs(window - (idx_a + interval_samples)))])
        flagged.add(int(idx_a))
        flagged.add(best)

    for idx in flagged:
        flag[idx] = True
    return flag
# -------------------------------------------------------------------------------

DATE_REGEX = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{6}"
d = re.compile(DATE_REGEX)


def _drop_unused_count_var(filepath, count_var_name):
    """Remove whichever of number_of_drops/number_of_tips was not written."""
    import netCDF4 as _nc4
    other = 'number_of_tips' if count_var_name == 'number_of_drops' else 'number_of_drops'
    with _nc4.Dataset(filepath) as src:
        if other not in src.variables:
            return
        tmppath = filepath + '.tmp'
        with _nc4.Dataset(tmppath, 'w', format=src.file_format) as dst:
            dst.setncatts(src.__dict__)
            for name, dim in src.dimensions.items():
                dst.createDimension(name, None if dim.isunlimited() else len(dim))
            for vname, var in src.variables.items():
                if vname == other:
                    continue
                fv = var._FillValue if '_FillValue' in var.ncattrs() else False
                out = dst.createVariable(vname, var.datatype, var.dimensions, fill_value=fv)
                out.setncatts({k: var.getncattr(k) for k in var.ncattrs() if k != '_FillValue'})
                out[:] = var[:]
    os.replace(tmppath, filepath)


def preprocess_data(infile, column_name='rg001dc_ch_Tot', column_is_float=False):
    print(infile)

    # Step 1: Read the file
    with open(infile, "r") as f:
        data = f.readlines()

    # Step 2: Parse the header to get column names
    header_line = data[1].strip()  # Second line contains column names
    column_names = [col.strip('"') for col in header_line.split(",")]
    print(f"Parsed column names: {column_names}")

    # Step 3: Skip metadata lines (first 4 lines are metadata)
    data_lines = data[4:]  # Data starts after the first 4 lines

    # Step 4: Process the data, skipping any embedded header rows
    # The CR1000X logger re-inserts header lines after a restart mid-file.
    processed_data = []
    for line in data_lines:
        if line.strip():  # Skip empty lines
            fields = line.strip().split(",")
            # Skip repeated header rows (first field would be "TIMESTAMP")
            if fields[0].strip('"') == "TIMESTAMP":
                continue
            processed_data.append(fields)

    # Step 5: Create a Polars DataFrame with the parsed column names
    df = pl.DataFrame(processed_data, schema=column_names, orient="row")

    # Step 6: Ensure required columns exist
    required_columns = ["TIMESTAMP", column_name]
    for column in required_columns:
        if column not in df.columns:
            print(f"Column '{column}' is missing. Filling with null values.")
            df = df.with_columns(pl.lit(None).alias(column))

    # Step 7: Strip quotes from all string columns
    for column in df.columns:
        if df.schema[column] == pl.Utf8:
            df = df.with_columns(
                pl.col(column).str.strip_chars('"').alias(column)
            )

    # Step 8: Replace invalid values (e.g., "NAN") with null
    for column in [column_name]:
        if column in df.columns:
            df = df.with_columns(
                pl.when(pl.col(column) == "NAN")
                .then(None)
                .otherwise(pl.col(column))
                .alias(column)
            )

    # Step 9: Convert columns to appropriate data types
    type_conversions = {
        "TIMESTAMP": pl.Datetime,
        column_name: pl.Float64 if column_is_float else pl.Int64,
    }

    for column, dtype in type_conversions.items():
        if column in df.columns:
            if column == "TIMESTAMP":
                df = df.with_columns(
                    pl.col("TIMESTAMP").str.strptime(
                        pl.Datetime, format="%Y-%m-%d %H:%M:%S", strict=False
                    )
                )
            else:
                df = df.with_columns(pl.col(column).cast(dtype))

    # Step 10: Keep only the required columns
    df = df.select(["TIMESTAMP", column_name])

    # column_name is the drop count from the logger

    return df


def process_file(infile, outdir="./", metadata_file="metadata_rg1.json",
                 instrument_name="ncas-rain-gauge-1", column_name="rg001dc_ch_Tot",
                 count_var_name="number_of_drops", column_is_mm=False):
    df = preprocess_data(infile, column_name=column_name, column_is_float=column_is_mm)
    print(df)

    # Check if the year of the last timestamp is one greater than the previous timestamp
    if df["TIMESTAMP"][-1].year > df["TIMESTAMP"][-2].year:
        print("[INFO] Adjusting the last timestamp to be one year earlier temporarily.")
        original_last_timestamp = df["TIMESTAMP"][-1]
        adjusted_last_timestamp = original_last_timestamp.replace(year=original_last_timestamp.year - 1)
        df = df.with_columns(
            pl.when(pl.col("TIMESTAMP") == original_last_timestamp)
            .then(adjusted_last_timestamp)
            .otherwise(pl.col("TIMESTAMP"))
            .alias("TIMESTAMP")
        )

    # Get all the time formats
    unix_times, day_of_year, years, months, days, hours, minutes, seconds, time_coverage_start_unix, time_coverage_end_unix, file_date = nant.util.get_times(df["TIMESTAMP"])

    # Restore the original last timestamp for correction later
    if "adjusted_last_timestamp" in locals():
        print("[INFO] Restoring the original last timestamp for correction.")
        unix_times[-1] = int(original_last_timestamp.replace(tzinfo=timezone.utc).timestamp())
        day_of_year[-1] = original_last_timestamp.timetuple().tm_yday
        years[-1] = original_last_timestamp.year
        months[-1] = original_last_timestamp.month
        days[-1] = original_last_timestamp.day
        hours[-1] = original_last_timestamp.hour
        minutes[-1] = original_last_timestamp.minute
        seconds[-1] = original_last_timestamp.second
        time_coverage_end_unix = unix_times[-1]

    file_date = f"{str(years[0])}{str(months[0]).zfill(2)}{str(days[0]).zfill(2)}"

    # Read product_version from metadata file
    import json
    with open(metadata_file, 'r') as f:
        metadata = json.load(f)
    product_version = metadata.get('product_version', 'v1.0').lstrip('v')

    # Create NetCDF file using local AMF CV definitions to avoid GitHub rate-limiting
    nc = nant.create_netcdf.make_product_netcdf("precipitation", instrument_name, date=file_date,
                                 dimension_lengths={"time": len(unix_times)},
                                 file_location=outdir, platform="cao",
                                 product_version=product_version,
                                 use_local_files=_AMF_CVs_LOCAL,
                                 tag=_AMF_CVs_TAG)
    if isinstance(nc, list):
        print("[WARNING] Unexpectedly got multiple netCDFs returned from nant.create_netcdf.main, just using first file...")
        nc = nc[0]

    # Add time variable data to NetCDF file
    nant.util.update_variable(nc, "time", unix_times)
    nant.util.update_variable(nc, "day_of_year", day_of_year)
    nant.util.update_variable(nc, "year", years)
    nant.util.update_variable(nc, "month", months)
    nant.util.update_variable(nc, "day", days)
    nant.util.update_variable(nc, "hour", hours)
    nant.util.update_variable(nc, "minute", minutes)
    nant.util.update_variable(nc, "second", seconds)

    # Correct the last timestamp and year in the NetCDF file
    if "adjusted_last_timestamp" in locals():
        print("[INFO] Correcting the last timestamp and year in the NetCDF file.")
        corrected_last_unix_time = int(original_last_timestamp.replace(tzinfo=timezone.utc).timestamp())
        nc.variables["time"][-1] = corrected_last_unix_time

        if "valid_max" in nc.variables["time"].ncattrs():
            nc.variables["time"].setncattr("valid_max", max(corrected_last_unix_time, nc.variables["time"].getncattr("valid_max")))

        nc.variables["year"][-1] = original_last_timestamp.year

        if "valid_max" in nc.variables["year"].ncattrs():
            nc.variables["year"].setncattr("valid_max", max(original_last_timestamp.year, nc.variables["year"].getncattr("valid_max")))

    # Add drop count and derived rainfall data to NetCDF file
    accumulation_per_drop_mm = float(metadata.get("measurement_quanta", "0.00331 mm").split()[0])
    if column_is_mm:
        # Column already holds accumulated rainfall in mm (e.g. tipping-bucket
        # logger outputs tip_count * tip_size_mm directly).
        # Derive the tip count by rounding to the nearest integer tip, then
        # recompute rainfall_mm from tip_count * quanta so that
        # thickness_of_rainfall_amount and rainfall_rate are consistent with
        # the canonical measurement_quanta value in the metadata.
        rainfall_mm_raw = df[column_name]
        number_of_drops = (rainfall_mm_raw / accumulation_per_drop_mm).round(0).cast(pl.Int64)
        rainfall_mm = number_of_drops * accumulation_per_drop_mm
    else:
        number_of_drops = df[column_name]
        rainfall_mm = number_of_drops * accumulation_per_drop_mm
    nant.util.update_variable(nc, count_var_name, number_of_drops)
    nc.variables[count_var_name].long_name = "Number of pulses/drops counted in integration period"
    nant.util.update_variable(nc, "thickness_of_rainfall_amount", rainfall_mm)
    nc.variables["thickness_of_rainfall_amount"].long_name = "Rain accumulated in integration period"

    # Detect pump cycles and apply QC flags.
    # Every 12 hours the gauge automatically flushes its reservoir.  The
    # controller suppresses counting during the flush, but 1–2 spurious drops
    # are often seen immediately after.  These are flagged as instrument_error
    # (flag value 2) in the qc_flag variable.
    #
    # The AMF precipitation product defines a single 'qc_flag' variable.  nant
    # creates it but remove_empty_variables will strip it if it stays at its
    # fill value.  We always write qc_flag (1=good_data, 2=instrument_error).
    import netCDF4 as netCDF4_mod
    raw_counts = number_of_drops.fill_null(0).to_numpy()
    pump_mask = detect_pump_cycles(raw_counts)
    n_pump = int(pump_mask.sum())
    if n_pump > 0:
        print(f"[INFO] Flagging {n_pump} samples as pump cycle artefacts (QC flag = 2).")
    purge_qc = np.where(pump_mask, np.int8(2), np.int8(1))

    if "qc_flag" in nc.variables:
        nc.variables["qc_flag"][:] = purge_qc
    else:
        # nant may not have created qc_flag (e.g. older template); add it manually
        qc_var = nc.createVariable("qc_flag", "i1", ("time",), fill_value=-127)
        qc_var[:] = purge_qc
        qc_var.setncattr("units", "1")
        qc_var.setncattr("long_name", "Data Quality flag")
        qc_var.setncattr("flag_values", np.array([0, 1, 2, 3, 4], dtype=np.int8))
        qc_var.setncattr("flag_meanings",
            "not_used good_data instrument_error "
            "bad_data_precipitation_rate_less_than_0_mm_hr-1 "
            "suspect_data_precipitation_rate_greater_than_300_mm_hr-1")

    # Add time_coverage_start and time_coverage_end metadata
    nc.setncattr(
        "time_coverage_start",
        dt.datetime.fromtimestamp(time_coverage_start_unix, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    )
    nc.setncattr(
        "time_coverage_end",
        dt.datetime.fromtimestamp(time_coverage_end_unix, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    )

    # Add metadata from file
    nant.util.add_metadata_to_netcdf(nc, metadata_file)
    for _attr in ("laser_wavelength", "laser_sample_area"):
        if _attr in nc.ncattrs():
            nc.delncattr(_attr)

    # Set processing software version from package
    version_str = __version__ if __version__.startswith('v') else f"v{__version__}"
    nc.setncattr("processing_software_version", version_str)

    # Ensure the 'time' variable has the correct units and values
    if "time" in nc.variables:
        print("[INFO] Correcting the 'time' variable in the NetCDF file.")
        nc.variables["time"].setncattr("units", "seconds since 1970-01-01 00:00:00")
        nc.variables["time"].setncattr("standard_name", "time")
        nc.variables["time"].setncattr("long_name", "Time (seconds since 1970-01-01 00:00:00)")
        nc.variables["time"].setncattr("axis", "T")
        nc.variables["time"].setncattr("calendar", "standard")
        print(nc['time'])
        time_values = nc.variables["time"][:]
        corrected_time_values = [
            cftime.date2num(
                cftime.num2date(t, "seconds since 1970-01-01 00:00:00"),
                "seconds since 1970-01-01 00:00:00"
            )
            for t in time_values
        ]
        nc.variables["time"][:] = corrected_time_values
        if len(corrected_time_values) > 0:
            nc.variables["time"].setncattr("valid_min", float(min(corrected_time_values)))
            nc.variables["time"].setncattr("valid_max", float(max(corrected_time_values)))

    # Close file, remove empty variables
    file_name = nc.filepath()
    nc.close()
    try:
        nant.remove_empty_variables.main(file_name, tag=_AMF_CVs_TAG, skip_check=True)
    except Exception as e:
        print(f"[WARNING] Could not remove empty variables (non-critical): {e}", file=sys.stderr)
    _drop_unused_count_var(file_name, count_var_name)


def main():
    """CLI entry point for process-raingauge command."""
    parser = argparse.ArgumentParser(description="Process RAL Drop Counting Gauge raingauge data to netCDF")
    parser.add_argument("infile", type=str, help="Input CR1000X .dat file")
    parser.add_argument("-o", "--outdir", type=str, default="./", help="Output directory")
    parser.add_argument("-m", "--metadata_file", type=str, default="metadata_rg1.json", help="Metadata file")
    parser.add_argument("--instrument-name", type=str, default="ncas-rain-gauge-1",
                        help="NCAS instrument name (default: ncas-rain-gauge-1)")
    parser.add_argument("--column-name", type=str, default="rg001dc_ch_Tot",
                        help="Datalogger column name for drop counts (default: rg001dc_ch_Tot)")

    args = parser.parse_args()
    process_file(args.infile, outdir=args.outdir, metadata_file=args.metadata_file,
                 instrument_name=args.instrument_name, column_name=args.column_name)
