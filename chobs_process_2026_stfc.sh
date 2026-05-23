#!/bin/bash

# Process raingauge data for 2026 using STFC variant (local run)

# Activate conda environment
source /home/cw66/miniforge3/etc/profile.d/conda.sh
conda activate cao_3_11

# Create log directory if it doesn't exist
mkdir -p logs

# Set paths
RAW_DATA_BASE="/data/range/new_daily_met"

echo "Processing year 2026 (CR1000X STFC)"

OUTPUT_BASE="/data/range/new_netcdf/stfc-rain-gauge-1"
process-raingauge-year-stfc --gauge 1 -y 2026 \
    --raw-data-base ${RAW_DATA_BASE} \
    --output-base ${OUTPUT_BASE}

OUTPUT_BASE="/data/range/new_netcdf/stfc-rain-gauge-2"
process-raingauge-year-stfc --gauge 2 -y 2026 \
    --raw-data-base ${RAW_DATA_BASE} \
    --output-base ${OUTPUT_BASE}

OUTPUT_BASE="/data/range/new_netcdf/stfc-rain-gauge-3"
process-raingauge-year-stfc --gauge 3 -y 2026 \
    --raw-data-base ${RAW_DATA_BASE} \
    --output-base ${OUTPUT_BASE}

OUTPUT_BASE="/data/range/new_netcdf/stfc-rain-gauge-9"
process-raingauge-year-stfc --gauge 9 -y 2026 \
    --raw-data-base ${RAW_DATA_BASE} \
    --output-base ${OUTPUT_BASE}

OUTPUT_BASE="/data/range/new_netcdf/stfc-rain-gauge-5"
process-raingauge-year-stfc --gauge 5 -y 2026 \
    --raw-data-base ${RAW_DATA_BASE} \
    --output-base ${OUTPUT_BASE}

echo "Done year 2026"
