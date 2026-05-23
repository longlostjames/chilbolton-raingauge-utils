"""
# Process RAL Drop Counting Gauge raingauge data to netCDF (STFC variant)
"""

import polars as pl
import numpy as np
import ncas_amof_netcdf_template as nant
import datetime as dt
from datetime import datetime
import cftime
import re
import os
from pathlib import Path
from datetime import datetime, timezone

try:
    from . import __version__
except ImportError:
    __version__ = "unknown"

# --- Local AMF CV definitions (avoids GitHub rate-limiting on SLURM) -----------
_AMF_CVs_TAG = "v2.2.0"
_AMF_CVs_LOCAL = str(Path(__file__).parent / "amf_cvs_local")

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
    header_line = data[1].strip()
    column_names = [col.strip('"') for col in header_line.split(",")]
    print(f"Parsed column names: {column_names}")

    # Step 3: Skip metadata lines (first 4 lines are metadata)
    data_lines = data[4:]

    # Step 4: Process the data, skipping any embedded header rows
    # The CR1000X logger re-inserts header lines after a restart mid-file.
    processed_data = []
    for line in data_lines:
        if line.strip():
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


def process_file(infile, outdir="./", metadata_file="metadata_rg1_stfc.json",
                 instrument_name="stfc-rain-gauge-1", column_name="rg001dc_ch_Tot",
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
        # thickness_of_rainfall_amount is consistent with
        # the canonical measurement_quanta value in the metadata.
        rainfall_mm_raw = df[column_name]
        number_of_drops = (rainfall_mm_raw / accumulation_per_drop_mm).round(0).cast(pl.Int64)
        rainfall_mm = number_of_drops * accumulation_per_drop_mm
    else:
        number_of_drops = df[column_name]
        rainfall_mm = number_of_drops * accumulation_per_drop_mm
    # Use the requested count variable name if the template created it, otherwise fall back to number_of_drops
    actual_count_var = count_var_name if count_var_name in nc.variables else "number_of_drops"
    if actual_count_var != count_var_name:
        print(f"[INFO] Variable '{count_var_name}' not in template; writing count to '{actual_count_var}'.")
    nant.util.update_variable(nc, actual_count_var, number_of_drops)
    nant.util.update_variable(nc, "thickness_of_rainfall_amount", rainfall_mm)

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
    nant.remove_empty_variables.main(file_name)
    _drop_unused_count_var(file_name, actual_count_var)


# Gauge configurations (shared with proc_month_stfc)
_GAUGE_CONFIGS = {
    1: {"instrument_name": "stfc-rain-gauge-1", "column_name": "rg001dc_ch_Tot",
        "count_var_name": "number_of_drops", "column_is_mm": False, "metadata_file": "metadata_rg1_stfc.json"},
    2: {"instrument_name": "stfc-rain-gauge-2", "column_name": "rg006dc_ch_Tot",
        "count_var_name": "number_of_drops", "column_is_mm": False, "metadata_file": "metadata_rg2_stfc.json"},
    3: {"instrument_name": "stfc-rain-gauge-3", "column_name": "rg008dc_ch_Tot",
        "count_var_name": "number_of_drops", "column_is_mm": False, "metadata_file": "metadata_rg3_stfc.json"},
    9: {"instrument_name": "stfc-rain-gauge-9", "column_name": "rg009dc_ch_Tot",
        "count_var_name": "number_of_drops", "column_is_mm": False, "metadata_file": "metadata_rg9_stfc.json"},
    5: {"instrument_name": "stfc-rain-gauge-5", "column_name": "rg004tb_ch_Tot",
        "count_var_name": "number_of_tips", "column_is_mm": True, "metadata_file": "metadata_rg5_stfc.json"},
}


def main():
    """CLI entry point for process-raingauge-stfc command."""
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description="Process RAL Drop Counting Gauge raingauge data (STFC variant) to netCDF")
    parser.add_argument("infile", type=str, help="Input CR1000X .dat file")
    parser.add_argument("-o", "--outdir", type=str, default="./", help="Output directory")
    parser.add_argument("-m", "--metadata_file", type=str, default=None,
                        help="Metadata JSON file (default: auto-selected from --gauge)")
    parser.add_argument("-g", "--gauge", type=int, choices=[1, 2, 3, 5, 9], default=None,
                        help="Gauge number (1, 2, 3, 5, 9) — sets instrument name, column, and metadata automatically")
    args = parser.parse_args()

    if args.gauge is not None:
        cfg = _GAUGE_CONFIGS[args.gauge]
        script_dir = Path(__file__).parent
        metadata_file = args.metadata_file or str(script_dir / cfg["metadata_file"])
        process_file(args.infile, outdir=args.outdir, metadata_file=metadata_file,
                     instrument_name=cfg["instrument_name"],
                     column_name=cfg["column_name"],
                     count_var_name=cfg["count_var_name"],
                     column_is_mm=cfg["column_is_mm"])
    else:
        metadata_file = args.metadata_file or "metadata_stfc.json"
        process_file(args.infile, outdir=args.outdir, metadata_file=metadata_file)
