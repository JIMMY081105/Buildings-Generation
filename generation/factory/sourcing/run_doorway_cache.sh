#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=/cephfs/shared/yixing/envs/isaac_prod/isaacsim/extscache/omni.usd.libs-1.0.1+69cbf6ad.lx64.r.cp311:/cephfs/shared/yixing/envs/main/lib/python3.11/site-packages
export LD_LIBRARY_PATH=/cephfs/shared/yixing/envs/isaac_prod/isaacsim/extscache/omni.usd.libs-1.0.1+69cbf6ad.lx64.r.cp311/bin:/cephfs/shared/yixing/envs/main/lib
DOOR_CACHE_LOG_SUFFIX=${DOOR_CACHE_LOG_SUFFIX:-main}

/cephfs/shared/yixing/envs/isaac_prod/env/bin/python3 \
  /cephfs/shared/yixing/outputs/factory10/scripts/augment_doorway_cache.py \
  --base-cache /cephfs/shared/yixing/outputs/factory10/work/official_scenes/cache_final76_complete_objects.json \
  --pairs /cephfs/shared/yixing/outputs/factory10/work/pair_catalog_final76_strict/pairs_h320.json \
  --pairs /cephfs/shared/yixing/outputs/factory10/work/pair_catalog_final76_strict/pairs_h350.json \
  --pairs /cephfs/shared/yixing/outputs/factory10/work/pair_catalog_final76_strict/pairs_h360.json \
  --scene-root /cephfs/shared/yixing/outputs/factory10/work/official_scenes/extracted \
  --door-scripts /cephfs/shared/yixing/outputs/building_poc/batch_20260831_50_h1_r1/post_h1_48_r1/scripts \
  --checkpoints /cephfs/shared/yixing/outputs/factory10/work/doorway_cache_checkpoints \
  --out /cephfs/shared/yixing/outputs/factory10/work/official_scenes/cache_strict49_doorway_v2.json \
  "$@" \
  > "/cephfs/shared/yixing/outputs/factory10/work/doorway_cache_v2_${DOOR_CACHE_LOG_SUFFIX}.log" 2>&1
