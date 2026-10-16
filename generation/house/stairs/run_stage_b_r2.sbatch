#!/bin/bash
#SBATCH --job-name=bldg_b_stepfix2
#SBATCH --partition=gpu
#SBATCH --gpus=1
#SBATCH --cpus-per-task=8
#SBATCH --time=00:10:00
#SBATCH --output=/data/run01/scvj260/building_batch_20260819/slurm/bldg_b_stepfix_%j.out
#SBATCH --error=/data/run01/scvj260/building_batch_20260819/slurm/bldg_b_stepfix_%j.err

set -euo pipefail

ROOT=/data/run01/scvj260/building_batch_20260819
RELEASE="$ROOT/release_r4"
OUTPUT="$ROOT/stage_b_assets_r3"
PYTHON=/ssd_xs/scvj260/infinigen_staircase_poc/env/bin/python
export TMPDIR=/ssd_xs/scvj260/tmp
export PYTHONNOUSERSITE=1
export HF_HUB_OFFLINE=1
export OMP_NUM_THREADS=8

[ ! -e "$OUTPUT" ] || {
  echo "refusing to overwrite Stage-B r3 output: $OUTPUT" >&2
  exit 4
}
gpu_info="$(nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv,noheader)"
printf '%s\n' "$gpu_info"
[ "$(printf '%s\n' "$gpu_info" | sed '/^[[:space:]]*$/d' | wc -l)" -eq 1 ] \
  && printf '%s\n' "$gpu_info" | grep -q 'RTX 5090' || {
    echo "FATAL: Stage B requires exactly one visible RTX 5090" >&2
    exit 42
  }

"$PYTHON" "$RELEASE/scripts/run_stage_b_assets.py" \
  --frozen-manifest "$RELEASE/contracts/frozen_h1_authorized_batch.json" \
  --approval "$RELEASE/contracts/h1_decisions.json" \
  --execution-plan "$RELEASE/contracts/post_h1_execution_plan.json" \
  --output-root "$OUTPUT" \
  --python "$PYTHON" \
  --generator "$RELEASE/scripts/generate_staircase.py" \
  --auditor "$RELEASE/scripts/audit_staircase_usd.py" \
  --downscaler "$RELEASE/scripts/make_tex128.py"
