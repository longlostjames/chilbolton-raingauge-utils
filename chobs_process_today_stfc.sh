#!/bin/bash

# Process today's and yesterday's raingauge data to NetCDF (local run)
# Assumes daily split files have already been produced by the met data split step.

# Activate conda environment (needed when run from cron, which has a minimal PATH)
source /home/cw66/miniforge3/etc/profile.d/conda.sh
conda activate cao_3_11

TODAY=$(date +%Y-%m-%d)
YEAR=$(date +%Y)
YM=$(date +%Y%m)
YMD=$(date +%Y%m%d)

YESTERDAY_YEAR=$(date -d "yesterday" +%Y)
YESTERDAY_YM=$(date -d "yesterday" +%Y%m)
YESTERDAY_YMD=$(date -d "yesterday" +%Y%m%d)

# Daily split output / raw data base for processing
RAW_DATA_BASE="/data/range/new_daily_met"

# NetCDF output base
OUTPUT_BASE_ROOT="/data/range/new_netcdf"

echo "Processing raingauge data for ${TODAY} (CR1000X STFC)"

for GAUGE in 1 2 3 9 5; do
    OUTPUT_BASE="${OUTPUT_BASE_ROOT}/stfc-rain-gauge-${GAUGE}"

    YESTERDAY_INFILE="${RAW_DATA_BASE}/${YESTERDAY_YEAR}/${YESTERDAY_YM}/CR1000XSeries_Chilbolton_Rxcabinmet1_${YESTERDAY_YMD}.dat"
    INFILE="${RAW_DATA_BASE}/${YEAR}/${YM}/CR1000XSeries_Chilbolton_Rxcabinmet1_${YMD}.dat"

    YESTERDAY_OUTDIR="${OUTPUT_BASE}/${YESTERDAY_YEAR}"
    OUTDIR="${OUTPUT_BASE}/${YEAR}"

    # Always process yesterday (picks up the midnight record and handles month rollover)
    if [ -f "${YESTERDAY_INFILE}" ]; then
        echo "Processing gauge ${GAUGE} for yesterday (${YESTERDAY_YMD})..."
        mkdir -p "${YESTERDAY_OUTDIR}"
        process-raingauge-stfc "${YESTERDAY_INFILE}" -o "${YESTERDAY_OUTDIR}" --gauge ${GAUGE}
    else
        echo "WARNING: Yesterday's input file not found: ${YESTERDAY_INFILE}" >&2
    fi

    # Process today
    if [ ! -f "${INFILE}" ]; then
        echo "ERROR: Today's input file not found: ${INFILE}" >&2
        continue
    fi
    echo "Processing gauge ${GAUGE} for today (${YMD})..."
    mkdir -p "${OUTDIR}"
    process-raingauge-stfc "${INFILE}" -o "${OUTDIR}" --gauge ${GAUGE}
done

echo "Done."
