#!/bin/bash
#SBATCH --job-name=raingauge_main
#SBATCH --partition=standard
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --time=24:00:00
#SBATCH --mem=16G
#SBATCH --array=2020-2024
#SBATCH --output=logs/raingauge_main_%A_%a.out
#SBATCH --error=logs/raingauge_main_%A_%a.err

YEAR=${SLURM_ARRAY_TASK_ID}

# Load conda environment
source /home/users/cjwalden/miniforge3/etc/profile.d/conda.sh
conda activate cao_3_11

echo "Python version: $(python --version)"
echo "chilbolton-raingauge-utils version: $(python -c 'import chilbolton_raingauge_utils; print(chilbolton_raingauge_utils.__version__)')"
echo "Working directory: $(pwd)"

RAW_DATA_BASE=/gws/pw/j07/ncas_obs_vol2/cao/raw_data/met_cao/data/long-term/new_daily_split
OUTPUT_BASE=/gws/pw/j07/ncas_obs_vol2/cao/processing/ncas-rain-gauge-1/data/long-term/level1b

echo "Processing year ${YEAR} (CR1000X NCAS)"
process-raingauge-year --gauge 1 -y ${YEAR} \
    --raw-data-base ${RAW_DATA_BASE} \
    --output-base ${OUTPUT_BASE}

OUTPUT_BASE=/gws/pw/j07/ncas_obs_vol2/cao/processing/ncas-rain-gauge-2/data/long-term/level1b

process-raingauge-year --gauge 2 -y ${YEAR} \
    --raw-data-base ${RAW_DATA_BASE} \
    --output-base ${OUTPUT_BASE}

OUTPUT_BASE=/gws/pw/j07/ncas_obs_vol2/cao/processing/ncas-rain-gauge-3/data/long-term/level1b

process-raingauge-year --gauge 3 -y ${YEAR} \
    --raw-data-base ${RAW_DATA_BASE} \
    --output-base ${OUTPUT_BASE}

OUTPUT_BASE=/gws/pw/j07/ncas_obs_vol2/cao/processing/ncas-rain-gauge-9/data/long-term/level1b

process-raingauge-year --gauge 9 -y ${YEAR} \
    --raw-data-base ${RAW_DATA_BASE} \
    --output-base ${OUTPUT_BASE}

OUTPUT_BASE=/gws/pw/j07/ncas_obs_vol2/cao/processing/ncas-rain-gauge-5/data/long-term/level1b

process-raingauge-year --gauge 5 -y ${YEAR} \
    --raw-data-base ${RAW_DATA_BASE} \
    --output-base ${OUTPUT_BASE}

echo "Done year ${YEAR}"
