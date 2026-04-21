#!/bin/bash
#SBATCH --job-name=raingauge_stfc
#SBATCH --partition=standard
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --time=24:00:00
#SBATCH --mem=16G
#SBATCH --array=2024-2025
#SBATCH --output=logs/raingauge_stfc_%A_%a.out
#SBATCH --error=logs/raingauge_stfc_%A_%a.err

YEAR=${SLURM_ARRAY_TASK_ID}

source activate cao_3_11

RAW_DATA_BASE=/gws/pw/j07/ncas_obs_vol2/cao/raw_data/met_cao/data/long-term
OUTPUT_BASE=/gws/pw/j07/ncas_obs_vol2/cao/processing/ncas-rain-gauge-1/data/long-term/level1

echo "Processing year ${YEAR} (CR1000X STFC)"
process-raingauge-year-stfc -y ${YEAR} \
    --raw-data-base ${RAW_DATA_BASE} \
    --output-base ${OUTPUT_BASE}

echo "Done year ${YEAR}"
