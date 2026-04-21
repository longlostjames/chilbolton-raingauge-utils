# chilbolton-raingauge-utils

Processing utilities for Chilbolton raingauge data.

## Overview

This package provides tools for processing rainfall data from the RAL Drop
Counting Gauge at Chilbolton Atmospheric Observatory (CAO), logged by a
Campbell Scientific CR1000X datalogger. It supports three data pipelines:

- **Format5** (legacy binary, pre-2020): `process-raingauge-f5`
- **CR1000X NCAS** (2020 onwards): `process-raingauge`
- **CR1000X STFC** (2024 onwards): `process-raingauge-stfc`

Output files follow CF-compliant NetCDF conventions using the
[NCAS AMoF NetCDF Template](https://github.com/ncasuk/ncas-amof-netcdf-template).

## Installation

See [INSTALL.md](INSTALL.md) for full installation instructions.

```bash
pip install chilbolton-raingauge-utils
```

## Quick Start

### Process a single day (CR1000X format)

```bash
process-raingauge CR1000XSeries_Chilbolton_Rxcabinmet1_20240115.dat \
    -o /output/dir/ -m metadata.json
```

### Process a full year

```bash
process-raingauge-year -y 2024 \
    --raw-data-base /path/to/raw/data \
    --output-base /path/to/output
```

### Process legacy Format5 data

```bash
process-raingauge-f5 chan240115.000 -o /output/dir/ -m metadata_f5.json
process-raingauge-year-f5 -y 2018 \
    --raw-data-base /path/to/format5/data \
    --output-base /path/to/output
```

### Generate quicklook plots

```bash
make-raingauge-quicklooks -y 2024 -i /path/to/netcdf/ -o /path/to/plots/
```

### Bad data management

```bash
# Extract bad data indices to CSV for review
extract-raingauge-bad-data-indices -i /path/to/netcdf/ -o bad_data_indices_2024.csv -y 2024

# Apply corrected bad data indices back to NetCDF files
apply-raingauge-bad-data-indices -c bad_data_indices_2024.csv -i /path/to/netcdf/ -y 2024
```

## Data Paths (JASMIN)

Default data paths assume the NCAS GWS on JASMIN:

| Data stream | Raw data | Output |
|---|---|---|
| NCAS CR1000X | `.../new_daily_split/{YYYY}/{YYYYMM}/CR1000XSeries_Chilbolton_Rxcabinmet1_{YYYYMMDD}.dat` | `.../ncas-rain-gauge-1/data/long-term/level1a/{YYYY}/` |
| Format5 | `.../format5/chan{YYMMDD}.000` | `.../ncas-rain-gauge-1/data/long-term/level1_f5/{YYYY}/` |
| STFC CR1000X | `.../long-term/{YYYY}/{YYYYMM}/CR1000XSeries_Chilbolton_Rxcabinmet1_{YYYYMMDD}.dat` | `.../ncas-rain-gauge-1/data/long-term/level1/{YYYY}/` |

## NetCDF Output

Files are named following the NCAS convention:

```
ncas-rain-gauge-1_cao_{YYYYMMDD}_precipitation_v1.0.nc
```

Key variables:
- `thickness_of_rainfall_amount` — rainfall amount (mm)
- `qc_flag_thickness_of_rainfall_amount` — QC flag (1 = good, 2 = bad data)

## Notes on Format5 Channel

The Format5 channel name for the raingauge in the `f5channelDB.chdb` database is
assumed to be `rg001dc_ch`. Verify this against the actual channel database before
processing Format5 data; update `process_raingauge_f5.py` if needed.

## Notes on Metadata

The instrument serial number is not currently known and is set to `"unknown"` in
all metadata files. Update `metadata.json`, `metadata_f5.json`, and
`metadata_stfc.json` once the serial number is confirmed.

## Author

Chris Walden (chris.walden@ncas.ac.uk)

## License

See [LICENSE](LICENSE).
