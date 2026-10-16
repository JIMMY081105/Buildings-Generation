#!/bin/bash
set -euo pipefail

ROOT=/data/run01/scvj260/building_poc/batch_20260826_a1_a5_r1/post_h1_remediation_r1/stage_b_assets_v2
PYTHON=/ssd_xs/scvj260/infinigen_staircase_poc/env/bin/python
RELEASE=/data/run01/scvj260/building_batch_20260819/release_r4/scripts
GENERATOR="$RELEASE/generate_staircase.py"
AUDITOR="$RELEASE/audit_staircase_usd.py"
RISE="$RELEASE/stair_rise_contract.py"
DOWNSCALE="$RELEASE/make_tex128.py"

[ ! -e "$ROOT" ] || { echo "refusing to overwrite $ROOT" >&2; exit 2; }
mkdir -p "$ROOT"

for SPEC in \
  '320 straight 3.2' \
  '320 l_shaped 3.2' \
  '320 u_shaped 3.2' \
  '360 l_shaped 3.6' \
  '360 u_shaped 3.6'; do
  read -r HEIGHT_MM KIND HEIGHT_M <<<"$SPEC"
  CELL="$ROOT/h${HEIGHT_MM}/$KIND"
  RAW="$CELL/raw"
  mkdir -p "$RAW"
  "$PYTHON" "$GENERATOR" --kind "$KIND" --height "$HEIGHT_M" \
    --seed 101 --output-dir "$RAW" --image-res 512 \
    --upper-floor-final-landing
  "$PYTHON" "$AUDITOR" "$RAW/staircase.usd" \
    --generation-metrics "$RAW/generation_metrics.json" \
    --output "$RAW/usd_audit.json"
  "$PYTHON" "$RISE" "$RAW/staircase.usd" \
    --upper-slab-top-m "$HEIGHT_M" --output "$RAW/stair_rise_contract.json"
  mkdir -p "$CELL/downscale_src" "$CELL/downscale_dst"
  cp -a "$RAW" "$CELL/downscale_src/$KIND"
  "$PYTHON" "$DOWNSCALE" --src "$CELL/downscale_src" \
    --dst "$CELL/downscale_dst" --size 128
  mv "$CELL/downscale_dst/$KIND" "$CELL/tex128"
  cp "$RAW/traversal_path.json" "$RAW/generation_metrics.json" \
    "$RAW/usd_audit.json" "$RAW/stair_rise_contract.json" "$CELL/tex128/"
  echo "DONE h${HEIGHT_MM}_${KIND}"
done

"$PYTHON" - "$ROOT" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1])
strict=("all_authored_tread_colliders_normalized",
        "source_tread_collision_prims_deactivated",
        "combined_tread_collider_contains_all_boxes",
        "combined_tread_collider_is_static_closed_triangle_mesh")
rows=[]
for cell in sorted(root.glob("h*/*/tex128")):
    audit=json.loads((cell/"usd_audit.json").read_text())
    rise=json.loads((cell/"stair_rise_contract.json").read_text())
    checks={name:audit.get("checks",{}).get(name) is True for name in strict}
    row={"cell":cell.parent.parent.name+"_"+cell.parent.name,
         "asset_root":str(cell),"strict_normalization_gates":checks,
         "strict_normalization_pass":all(checks.values()),
         "ground_to_first_tread_m":rise["first_rise_m"],
         "maximum_walking_surface_rise_m":rise["max_rise_m"],
         "max_support_step_m":rise["max_support_step_m"],
         "rise_contract_pass":rise["passes"],
         "staircase_usd_sha256":hashlib.sha256((cell/"staircase.usd").read_bytes()).hexdigest()}
    rows.append(row)
assert len(rows)==5 and all(r["strict_normalization_pass"] and r["rise_contract_pass"] for r in rows)
out={"schema":"RepairedWalkingSurfaceStageBV2","status":"PASS",
     "generator":str(pathlib.Path("/data/run01/scvj260/building_batch_20260819/release_r4/scripts/generate_staircase.py")),
     "generator_fix":"place tread top, not underside, on riser grid",
     "height_substitution_used":False,"cells":rows}
(root/"stage_b_summary.json").write_text(json.dumps(out,indent=2)+"\n")
print(json.dumps(out,indent=2))
PY
