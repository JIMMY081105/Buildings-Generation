set -u
W=/cephfs/shared/yixing/outputs/diversity54_20260908
PY=/cephfs/shared/yixing/envs/isaac_prod/env/bin/python3
R=$W/scripts/render_orbit_isaac_demo_20261009.py
P=$W/evidence/signature_cap8_tracks_20261006/home_train96_train99_straight_v001_h270/material_MetalBasic_r2/MATERIAL_PREPARATION.json
O=/cephfs/shared/yixing/outputs/diversity54_20260908/evidence/orbit_final_20261010
rm -rf "$O"; mkdir -p "$O"
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader > "$O/gpu_before.txt" 2>&1
export PYTHONUNBUFFERED=1 CUDA_VISIBLE_DEVICES=0 ACCEPT_EULA=Y OMNI_KIT_ACCEPT_EULA=YES HF_HUB_OFFLINE=1
stdbuf -oL -eL "$PY" "$R" --preparation "$P" --output "$O" --frames 120 --total-spp 120 --settle 180
echo "RC=$?"
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader > "$O/gpu_after.txt" 2>&1
echo ORBIT_BATCH_DONE
