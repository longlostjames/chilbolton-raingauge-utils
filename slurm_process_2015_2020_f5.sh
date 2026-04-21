#!/bin/bash
#SBATCH --job-name=raingauge_f5
#SBATCH --partition=standard
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --time=24:00:00
#SBATCH --mem=16G
#SBATCH --array=2015-2020
#SBATCH --output=logs/raingauge_f5_%A_%a.out
#SBATCH --error=logs/raingauge_f5_%A_%a.err

YEAR=${SLURM_ARRAY_TASK_ID}

source activate cao_3_11

RAW_DATA_BASE=/gws/pw/j07/ncas_obs_vol2/cao/raw_data/legacy/cao-analog-format5_chilbolton/data/long-term/format5
OUTPUT_BASE=/gws/pw/j07/ncas_obs_vol2/cao/processing/ncas-rain-gauge-1/data/long-term/level1_f5

echo "Processing year ${YEAR} (Format5)"
process-raingauge-year-f5 -y ${YEAR} \
    --raw-data-base ${RAW_DATA_BASE} \
    --output-base ${OUTPUT_BASE}

echo "Done year ${YEAR}"
