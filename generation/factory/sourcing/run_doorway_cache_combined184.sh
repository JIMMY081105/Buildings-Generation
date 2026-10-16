#!/usr/bin/env bash
set -euo pipefail

ROOT=/cephfs/shared/yixing/outputs/factory10
PY=/cephfs/shared/yixing/envs/isaac_prod/env/bin/python3
export PYTHONPATH=/cephfs/shared/yixing/envs/isaac_prod/isaacsim/extscache/omni.usd.libs-1.0.1+69cbf6ad.lx64.r.cp311:/cephfs/shared/yixing/envs/main/lib/python3.11/site-packages
export LD_LIBRARY_PATH=/cephfs/shared/yixing/envs/isaac_prod/isaacsim/extscache/omni.usd.libs-1.0.1+69cbf6ad.lx64.r.cp311/bin:/cephfs/shared/yixing/envs/main/lib

COMMON=(
  "$ROOT/scripts/augment_doorway_cache.py"
  --base-cache "$ROOT/work/official_scenes/cache_combined184_full_dependencies.json"
  --pairs "$ROOT/work/pair_catalog_combined184_strict/pairs_h320.json"
  --pairs "$ROOT/work/pair_catalog_combined184_strict/pairs_h350.json"
  --pairs "$ROOT/work/pair_catalog_combined184_strict/pairs_h360.json"
  --scene-root "$ROOT/work/official_scenes/complete_union184"
  --door-scripts /cephfs/shared/yixing/outputs/building_poc/batch_20260831_50_h1_r1/post_h1_48_r1/scripts
  --checkpoints "$ROOT/work/doorway_cache_checkpoints"
  --out "$ROOT/work/official_scenes/cache_combined184_strict_doorway_v2.json"
)

if [[ -e "$ROOT/work/official_scenes/cache_combined184_strict_doorway_v2.json" ]]; then
  echo "refusing to overwrite completed doorway cache" >&2
  exit 2
fi

pids=()
for shard in 0 1 2 3 4 5 6 7; do
  "$PY" "${COMMON[@]}" --shard-count 8 --shard-index "$shard" --scan-only \
    > "$ROOT/logs/doorway_combined184_shard${shard}.log" 2>&1 &
  pids+=("$!")
done

status=0
for pid in "${pids[@]}"; do
  wait "$pid" || status=1
done
if (( status != 0 )); then
  echo "one or more doorway scan shards failed" >&2
  exit "$status"
fi

"$PY" "${COMMON[@]}" > "$ROOT/logs/doorway_combined184_finalize.log" 2>&1
tail -20 "$ROOT/logs/doorway_combined184_finalize.log"
