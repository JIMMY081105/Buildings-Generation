#!/usr/bin/env bash
set -euo pipefail

if (( $# != 2 )); then
  echo "usage: $0 HEIGHT_MM MAX_PAIRS" >&2
  exit 2
fi
height="$1"
max_pairs="$2"
case "$height" in
  320|350|360) ;;
  *) echo "unsupported genuine stair height: $height" >&2; exit 2 ;;
esac
if (( max_pairs < 1 )); then
  echo "MAX_PAIRS must be positive" >&2
  exit 2
fi

ROOT=/cephfs/shared/yixing/outputs/factory10
PY=/cephfs/shared/yixing/envs/isaac_prod/env/bin/python3
PICK=/cephfs/shared/yixing/code/Scenesmith-code-study/final_training_validation/pick_placement_v2.py
PAIR="$ROOT/work/pair_catalog_combined184_strict/pairs_h${height}.json"
CACHE="$ROOT/work/official_scenes/cache_combined184_strict_doorway_v2.json"
OUTDIR="$ROOT/work/a4_combined184_exact/a4"
LOGDIR="$ROOT/logs/a4_combined184_exact"
export PYTHONPATH=/cephfs/shared/yixing/outputs/building_poc/batch_20260831_50_h1_r1/scripts:/cephfs/shared/yixing/envs/isaac_prod/isaacsim/extscache/omni.usd.libs-1.0.1+69cbf6ad.lx64.r.cp311:/cephfs/shared/yixing/envs/main/lib/python3.11/site-packages
export LD_LIBRARY_PATH=/cephfs/shared/yixing/envs/isaac_prod/isaacsim/extscache/omni.usd.libs-1.0.1+69cbf6ad.lx64.r.cp311/bin:/cephfs/shared/yixing/envs/main/lib
mkdir -p "$OUTDIR" "$LOGDIR"

pids=()
for kind in straight l_shaped u_shaped; do
  out="$OUTDIR/placement_h${height}_${kind}_grid001.json"
  log="$LOGDIR/h${height}_${kind}.log"
  if [[ -e "$out" ]]; then
    echo "refusing to overwrite A4 output: $out" >&2
    exit 2
  fi
  "$PY" "$PICK" \
    --pairs "$PAIR" \
    --scene-cache "$CACHE" \
    --stair "$ROOT/assets/stairs/h${height}/${kind}/tex128/staircase.usd" \
    --stair-path "$ROOT/assets/stairs/h${height}/${kind}/tex128/traversal_path.json" \
    --kind "$kind" \
    --out "$out" \
    --max-pairs "$max_pairs" \
    --max-candidates "$max_pairs" \
    --grid-step 0.01 \
    > "$log" 2>&1 &
  pids+=("$!")
done

status=0
for pid in "${pids[@]}"; do
  wait "$pid" || status=1
done
if (( status != 0 )); then
  echo "one or more exact A4 processes failed" >&2
  exit "$status"
fi

for kind in straight l_shaped u_shaped; do
  "$PY" - "$OUTDIR/placement_h${height}_${kind}_grid001.json" "$kind" <<'PY'
import json, sys
p, kind = sys.argv[1:]
d = json.load(open(p))
print(json.dumps({
    "kind": kind,
    "eligible": d["pairs_height_and_kind_eligible"],
    "evaluated": d["pairs_evaluated_count"],
    "door_rejected": d["doorway_clearance_rejection_count"],
    "placement_rejected": d["strict_placement_rejection_count"],
    "candidates": len(d["candidates"]),
    "grid_step_m": d["grid_step_m"],
    "search_exhaustive": d["search_exhaustive"],
}, sort_keys=True))
PY
done
