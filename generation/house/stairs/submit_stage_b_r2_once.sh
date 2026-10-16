#!/bin/bash
# Submit exactly one repaired Stage-B job, and only into real public capacity.
set -euo pipefail

ROOT=/data/run01/scvj260/building_batch_20260819
SBATCH_SCRIPT="$ROOT/release_r4/scripts/run_stage_b_r2.sbatch"
OUTPUT="$ROOT/stage_b_assets_r3"
USER_NAME="$(id -un)"

[ -s "$SBATCH_SCRIPT" ] || { echo "missing Stage-B r3 sbatch script" >&2; exit 4; }
[ ! -e "$OUTPUT" ] || { echo "Stage-B r3 output already exists" >&2; exit 5; }
existing="$(squeue -u "$USER_NAME" -h -n bldg_b_stepfix2 -o '%A|%T' | head -n1)"
[ -z "$existing" ] || { echo "Stage-B r3 job already exists: $existing" >&2; exit 5; }

capacity_text="$(/usr/bin/sinfo 2>&1)"
public_free="$(printf '%s\n' "$capacity_text" \
  | awk '$1=="gpu" && $2 ~ /^[0-9]+$/ {print $2; exit}')"
public_free="${public_free:-0}"
printf 'capacity public_free=%s\n' "$public_free"
[ "$public_free" -ge 1 ] || {
  echo "refusing Stage B: public gpu capacity is zero" >&2
  exit 6
}

existing="$(squeue -u "$USER_NAME" -h -n bldg_b_stepfix2 -o '%A|%T' | head -n1)"
[ -z "$existing" ] || { echo "Stage-B r3 job appeared during recheck" >&2; exit 5; }
job_id="$(sbatch --parsable "$SBATCH_SCRIPT")"
printf 'submitted_stage_b_r3=%s\n' "$job_id"
