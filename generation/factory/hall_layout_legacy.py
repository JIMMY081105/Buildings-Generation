"""Factory80 v4 deterministic function/bay planning and source-bound placement.

The plan is a CPU authoring input, never a C1-C5 or READY verdict.  Old source,
asset qualification, canonical config and v32 evidence are read only.
"""
from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

VERSION = "factory80_layout@v4.18"
LAYOUT_ONLY_STATE = "FOR_LAYOUT_ONLY_NO_PHYSICS_CANDIDATE"
FUNCTIONS = ("WAREHOUSE", "WELDING", "AUTO_SERVICE", "PAINT_CLEAN", "POWER", "MACHINING")
EXCLUDED_SCI_FI = {"M_01858d5edc344ff4ab11d0edce645223", "M_347d16f0dab4421d835492adb4956d05"}
VISUAL_EXCLUSIONS = {"SR_E_machineGuard_a01":"EXCLUDED_V4_SOURCE_IMAGES_SHOW_ARTICULATED_ARM_CASING_NOT_FLOORSTANDING_GUARD_OR_WASHING_SHIELD",
    "SR_E_machineGuard_a02":"EXCLUDED_V4_SOURCE_IMAGES_SHOW_ARTICULATED_ARM_CASING_NOT_FLOORSTANDING_GUARD_OR_WASHING_SHIELD"}
DEFAULT_ROOT = Path("/cephfs/shared/yixing/outputs/factory80_20261011")
HASH_CACHE = {}
CONFIG_PATH = None


def config_file(project_root):
    return Path(CONFIG_PATH) if CONFIG_PATH else Path(project_root)/"pipeline/config.yaml"


def layout_policy(config):
    policy=config.get("v4",{})
    count=int(policy.get("layout_seed_count",20))
    fraction=float(policy.get("warehouse_fill_count_fraction_max",.25))
    if count!=20 or fraction!=.25:
        raise ValueError("Approved batch policy requires20 layout seeds and25% warehouse fill equipment COUNT")
    return count,fraction


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_name(path.name+f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary,path)


def write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("x",encoding="utf-8") as stream:
        json.dump(value,stream,indent=2,ensure_ascii=False,allow_nan=False)
        stream.write("\n")


def digest(path):
    path = Path(path)
    st = path.stat()
    key = (str(path.resolve()),st.st_size,st.st_mtime_ns)
    if key in HASH_CACHE:
        return HASH_CACHE[key]
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):
            h.update(block)
    HASH_CACHE[key] = h.hexdigest()
    return HASH_CACHE[key]


def ref(path):
    return {"path": str(path), "sha256": digest(path)}


def rank(*parts):
    return int.from_bytes(hashlib.sha256("/".join(map(str, ("Factory80V4_20261031", *parts))).encode()).digest(), "big")


def neighbors(cell, cells):
    i, j = cell
    return [p for p in ((i-1,j),(i+1,j),(i,j-1),(i,j+1)) if p in cells]


def connected(cells):
    cells = set(map(tuple, cells))
    if not cells:
        return False
    reached = {min(cells)}
    stack = list(reached)
    while stack:
        for q in neighbors(stack.pop(), cells):
            if q not in reached:
                reached.add(q)
                stack.append(q)
    return reached == cells


def grow(cells, seed, count, key):
    """Grow one contiguous bay region; caller verifies its complement."""
    cells = set(map(tuple, cells))
    chosen = {seed}
    order = [seed]
    while len(chosen) < count:
        edge = {n for c in chosen for n in neighbors(c, cells)} - chosen
        if not edge:
            raise ValueError("Contiguous bay growth exhausted")
        q = min(edge, key=key)
        chosen.add(q)
        order.append(q)
    return chosen, order


def mezz_cells(n, shape, seed):
    count = {3:4, 4:7, 5:11, 6:15, 7:21}[n]
    all_cells = {(i,j) for i in range(n) for j in range(n)}
    if shape == "STRIP":
        # A column-aligned edge band, shortened only by complete end bays.
        cells = {(i,j) for j in range(n) for i in range(n) if j*n+i < count}
    elif shape == "L_SHAPED":
        ordered = sorted(all_cells, key=lambda p: (min(p), sum(p), p))
        cells = set(ordered[:count])
    elif shape == "CENTRAL_ISLAND":
        if n < 5:
            raise ValueError("Column-aligned island cannot fit this grid at40-44% net")
        interior = {(i,j) for i,j in all_cells if 0<i<n-1 and 0<j<n-1}
        center = min(interior, key=lambda p: ((p[0]-(n-1)/2)**2+(p[1]-(n-1)/2)**2, p))
        # grid5 has only9 interior bays: the explicit >=2 free sides contract
        # permits one wall contact, so grow two adjoining bays on one side.
        allowed = interior | ({(0,j) for j in range(1,n-1)} if n == 5 else set())
        cells, _ = grow(allowed, center, count, key=lambda p: (p not in interior, (p[0]-(n-1)/2)**2+(p[1]-(n-1)/2)**2, rank(seed,"island",*p)))
    else:
        raise ValueError(shape)
    # Quarter-turns preserve bay topology. Physical x/y pitches remain20/21m.
    quarter = rank(seed,"mezz_rotation") % 4
    for _ in range(quarter):
        cells = {(n-1-j,i) for i,j in cells}
    if not connected(cells) or len(cells) != count:
        raise ValueError("Invalid mezzanine bay polygon")
    touch = [any(i == 0 for i,j in cells), any(i == n-1 for i,j in cells), any(j == 0 for i,j in cells), any(j == n-1 for i,j in cells)]
    if shape == "CENTRAL_ISLAND" and sum(not t for t in touch) < 2:
        raise ValueError("Island does not leave two sides clear of external walls")
    return sorted(cells), quarter*90, touch


def building_plan(config):
    grids = {bid:int(g) for g, bids in config["batch_grid_groups"].items() for bid in bids}
    bids = sorted(config["buildings"], key=int)
    quota = {f:5 for f in FUNCTIONS}
    quota[min(FUNCTIONS,key=lambda f:rank("four_primary",f))] = 4
    primary = {}
    previous = None
    for bid in bids:
        choices = [f for f in FUNCTIONS if quota[f] and f != previous]
        if not choices:
            raise ValueError("Function quota cannot avoid adjacent repetition")
        chosen = min(choices, key=lambda f:(-quota[f],rank(bid,"primary",f)))
        primary[bid] = chosen
        quota[chosen] -= 1
        previous = chosen
    if any(quota.values()):
        raise ValueError("Incomplete function allocation")
    secondary_bids = set(sorted(bids, key=lambda b:rank(b,"secondary_present"))[:17])
    island_bids = set(sorted((b for b in bids if grids[b]>=5),key=lambda b:rank(b,"island_quota"))[:9])
    l_bids = set(sorted((b for b in bids if b not in island_bids),key=lambda b:rank(b,"L_quota"))[:10])
    out = []
    for bid in config["buildings"]:
        n = grids[bid]
        seed = rank(bid,"building") % 2147483647
        f = primary[bid]
        secondary = min((v for v in FUNCTIONS if v != f),key=lambda v:rank(bid,"secondary",v)) if bid in secondary_bids else None
        shape = "CENTRAL_ISLAND" if bid in island_bids else "L_SHAPED" if bid in l_bids else "STRIP"
        cells, yaw, touch = mezz_cells(n,shape,seed)
        all_cells = {(i,j) for i in range(n) for j in range(n)}
        function_cells = {f:sorted(all_cells)}
        growth = {}
        if secondary:
            start = min(((0,0),(0,n-1),(n-1,0),(n-1,n-1)),key=lambda p:rank(seed,"secondary_seed",*p))
            part, order = grow(all_cells,start,max(1,round(n*n*.35)),key=lambda p:(abs(p[0]-start[0])+abs(p[1]-start[1]),rank(seed,"zone",*p)))
            if not connected(all_cells-part):
                raise ValueError("Primary function region disconnected")
            function_cells = {f:sorted(all_cells-part),secondary:sorted(part)}
            growth = {secondary:[list(p) for p in order]}
        ground = n*n*420.0
        gross = len(cells)*420.0
        stairs = 2 + rank(seed,"stair_count")%2
        out.append(dict(building=bid,grid=n,building_seed=seed,primary_function=f,secondary_function=secondary,
            function_bays={k:[list(p) for p in v] for k,v in function_cells.items()},function_growth=growth,
            mezzanine_shape=shape,mezzanine_requested_shape=shape,mezzanine_fallback=None,
            mezzanine_bays=[list(p) for p in cells],mezzanine_rotation_deg=yaw,mezzanine_touches_shell_sides=touch,
            bay_pitch_xy_m=[20.,21.],ground_bounds_xy_m=[0.,0.,20.*n,21.*n],ground_area_m2=ground,
            gross_mezzanine_area_m2=gross,gross_mezzanine_fraction=gross/ground,
            opening_union_area_allowed_m2=[max(0.,gross-.44*ground),gross-.40*ground],
            net_mezzanine_area_m2=None,net_mezzanine_fraction=None,net_area_status="PENDING_ACTUAL_STAIR_OPENINGS",
            stair_count=stairs,stair_kinds=[("straight","l_shaped","u_shaped")[rank(seed,"kind",i)%3] for i in range(1,stairs+1)],
            handrail_type=("vertical-post","horizontal-post")[rank(seed,"rail")%2],material=("M1","M2","M3")[rank(seed,"material")%3],
            machine_fraction_min=.2 if f in ("WAREHOUSE","POWER") else .5,
            placement_status="NOT_EXECUTED",checks_status="NOT_EXECUTED"))
    counts = collections.Counter(r["primary_function"] for r in out)
    shapes = collections.Counter(r["mezzanine_shape"] for r in out)
    if len(out)!=29 or set(counts)!=set(FUNCTIONS) or not all(4<=v<=6 for v in counts.values()) or not all(v>=7 for v in shapes.values()):
        raise ValueError("Batch quota mismatch")
    return dict(schema="Factory80V4BuildingPlanV1",tool_version=VERSION,buildings=out,
        primary_counts=dict(counts),shape_counts=dict(shapes),secondary_buildings=17,secondary_fraction=17/29,
        dimensions_source="Existing batch grid membership and fixed20x21m bay pitch; actual retained floor readback required at assembly",
        status="DETERMINISTIC_AUTHORING_PLAN_NOT_SCENE_OR_CHECK_PASS",batch_release=False)


def function_roles(asset, existing):
    key = asset["id"]
    old = set(existing.get("roles", []))
    roles = set(old)
    if key in EXCLUDED_SCI_FI:
        return {}, "EXCLUDED_SCI_FI_PER_V4"
    if key in VISUAL_EXCLUSIONS:
        return {}, VISUAL_EXCLUSIONS[key]
    specific = {
        "B040":"machining_center", "B045":"machining_center", "B047":"machining_center", "B107":"drill_press",
        "B148":"flatbed_machine", "B155":"machining_center", "B160":"machining_center", "B044":"hopper",
    }
    if asset.get("review_id") in specific:
        roles.add(specific[asset["review_id"]])
    patterns = {
        "vehicleSupport_":"auto_lift", "testingTool_brake_":"brake_test", "testingTool_wheel_balancing_":"wheel_balancer",
        "vehicleSupport_frame_straightening_":"frame_straightener", "tire_rack":"tire_rack", "rack_tyres":"tire_rack",
        "paintPumpSystem_":"paint_pump", "paintPressurePot_":"pressure_pot", "sprayGunCleaningStation_":"spray_gun_cleaner",
        "parts_washer_":"parts_washer", "airCompressor_":"air_compressor", "robot_trolley_":"AMR_trolley",
        "suppressionCylinder_":"fire_suppression_cylinder", "eyewash":"eyewash_station", "eyeWash":"eyewash_station",
        "machineGuard":"machine_shield", "barrier_fence_metal_":"machine_guard_segment", "powerTool_welding_station_":"welding_station",
        "equipment_3d_printer_":"additive_printer", "mechanic_tool_cart_":"tool_cart", "tools_trolley_":"tool_cart",
        "rolling_tool_":"tool_cart", "rolling_service_":"tool_cart", "mechanic_work_tray_":"tool_cart",
        "storage_locker_":"storage_cabinet", "ibcTank":"IBC", "conveyor_belt_":"straight_conveyor",
    }
    for pattern, role in patterns.items():
        if pattern.lower() in key.lower():
            roles.add(role)
    if key.startswith("SR_B_rack_"):
        roles.add("high_rack_group" if asset["dimensions_m"][2] > 2.5 else "light_rack_group")
        if "tire" in key.lower():
            roles.discard("high_rack_group"); roles.discard("light_rack_group"); roles.add("tire_rack")
    if key.startswith("SR_C_pallet_"):
        roles.add("pallet_load" if "assembly" in key else "pallet")
    functions = {
        "WAREHOUSE":{"high_rack_group","light_rack_group","pallet_load","wrapped_pallet_load","pallet","forklift_or_stacker","AMR_trolley","straight_conveyor","straight_conveyor_line","curved_conveyor"},
        "WELDING":{"body_fixture","robot_arm","machine_guard_segment","welding_station","electrical_cabinet"},
        "AUTO_SERVICE":{"auto_lift","brake_test","wheel_balancer","frame_straightener","tire_rack","tool_cart"},
        "PAINT_CLEAN":{"paint_pump","pressure_pot","spray_gun_cleaner","parts_washer","IBC","fire_suppression_cylinder","eyewash_station","machine_shield"},
        "POWER":{"non_flat_top_tank","generator_set","air_compressor","IBC","column_side_pipe_gallery"},
        "MACHINING":{"machining_center","drill_press","flatbed_machine","hopper","workbench","tool_cart","storage_cabinet"},
        "MEZZANINE":{"light_rack_group","bin","pallet","storage_cabinet","workbench","additive_printer"},
    }
    result = {f:sorted(roles & allowed) for f,allowed in functions.items() if roles & allowed}
    return result, None


TEMPLATES = {
    "WAREHOUSE": [dict(kind="warehouse_row",members={"high_rack_group":4,"pallet_load":4,"forklift_or_stacker":1,"AMR_trolley":1}),dict(kind="warehouse_conveyor",members={"straight_conveyor":1,"pallet_load":2,"AMR_trolley":1}),dict(kind="warehouse_rack_only",members={"high_rack_group":4})],
    "WELDING": [dict(kind="welding_cell_controlled",members={"straight_conveyor_line":1,"body_fixture":1,"robot_arm":3,"machine_guard_segment":4,"electrical_cabinet":1},reuse="V32_BATCH_WELDING_PLACEMENT_QUALIFICATION_V1"),dict(kind="manual_welding",members={"welding_station":3,"electrical_cabinet":1})],
    "AUTO_SERVICE": [dict(kind="service_bay",members={"auto_lift":1,"brake_test":1,"tire_rack":1,"tool_cart":1}),dict(kind="wheel_service",members={"auto_lift":1,"wheel_balancer":1,"tire_rack":1,"tool_cart":1}),dict(kind="frame_service",members={"frame_straightener":1,"wheel_balancer":1,"tire_rack":1,"tool_cart":1})],
    "PAINT_CLEAN": [dict(kind="paint_cell",members={"paint_pump":2,"pressure_pot":1,"spray_gun_cleaner":1,"IBC":1}),dict(kind="clean_cell",members={"parts_washer":2,"spray_gun_cleaner":1,"IBC":1}),dict(kind="pump_wash_row",members={"paint_pump":3,"parts_washer":1})],
    "POWER": [dict(kind="utility_cell",members={"generator_set":2,"air_compressor":2,"IBC":2}),dict(kind="tank_cell",members={"non_flat_top_tank":2,"generator_set":1,"IBC":3})],
    "MACHINING": [dict(kind="machining_cell",members={"machining_center":3,"workbench":1,"tool_cart":1}),dict(kind="drill_cell",members={"drill_press":2,"flatbed_machine":1,"hopper":1,"tool_cart":1})],
    "MEZZANINE": [dict(kind="upper_storage",members={"light_rack_group":4,"bin":4,"pallet":4,"storage_cabinet":1}),dict(kind="upper_additive",members={"additive_printer":2,"workbench":2,"storage_cabinet":1}),dict(kind="upper_rack_row",members={"light_rack_group":4})],
}


def make_pools(root, library, original):
    library_root = root/"asset_library"
    old = {r["key"]:r for r in original["pools"]}
    qualified_path = root/"runs/001/v32_batch/layout_inputs/ROLE_POOLS.json"
    qualified = {r["key"]:r for r in read(qualified_path)["pools"]} if qualified_path.is_file() else {}
    eligible = set(library["v3_current_scene_eligible_asset_ids"])
    rows = []
    patches = []
    omitted = []
    for a in library["assets"]:
        if a["id"] not in eligible:
            continue
        key = a["id"]
        before = old.get(key,qualified.get(key,{}))
        bindings, rejection = function_roles(a,before)
        if rejection:
            omitted.append(dict(asset_id=key,reason=rejection,source_views=a.get("actual_visual_review",{}).get("actual_reviewed_views",[]),
                authority="FACTORY80_GOAL_V4_20261031 section5 discard visually unsuitable props; root actual two-view inspection20261031")); continue
        if not bindings and not before:
            continue
        if not a.get("license"):
            raise ValueError("Eligible asset lacks recorded license: "+key)
        if before:
            row = copy.deepcopy(before)
            row.setdefault("technical_tag",a.get("technical_tag",a.get("V1_static",{}).get("status")))
        else:
            prepared = Path(a.get("sample_prepared_root") or str(library_root/"prepared"/key/"root.usda"))
            if not prepared.is_absolute():
                prepared = library_root/prepared
            if not prepared.is_file():
                omitted.append(dict(asset_id=key,reason="NO_EXISTING_PREPARED_ROOT"));continue
            technical_path = prepared.parent/"TECHNICAL_ASSET.json"
            if not technical_path.is_file():
                technical_path = prepared.parent/"ASSET.json"
                if not technical_path.is_file():
                    omitted.append(dict(asset_id=key,reason="NO_EXISTING_ACTUAL_BOUNDS_METADATA"));continue
                # Placement dimensions only, using the existing normalized asset.
                # This is not a visual mesh quality or re-admission check.
                from pxr import Usd, UsdGeom
                stage = Usd.Stage.Open(str(prepared))
                cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),["default","render","proxy"],useExtentsHint=False)
                bound = cache.ComputeWorldBound(stage.GetDefaultPrim()).ComputeAlignedRange()
                vb = [list(bound.GetMin()),list(bound.GetMax())]
                cb = vb
            else:
                t = read(technical_path)
                vb, cb = t["visual_bounds_m"],t["collision_bounds_m"]
            union = [[min(vb[0][k],cb[0][k]) for k in range(3)],[max(vb[1][k],cb[1][k]) for k in range(3)]]
            row = dict(key=key,review_id=a.get("review_id",key),**{"class":a["user_class"]},root={"path":str(prepared),"sha256":a.get("sample_prepared_root_sha256")},
                actual_visible_bounds_m=vb,actual_collision_bounds_m=cb,actual_union_bounds_m=union,dimensions_m=a["dimensions_m"],
                enabled_static_colliders=a["collision_proxy"]["enabled_colliders"],proxy=a["collision_proxy"],source_sha256=a["source_sha256"],
                technical_tag=a.get("technical_tag",a.get("V1_static",{}).get("status")),heavy_machine=a.get("actual_functional_dimension_review",{}).get("heavy_machine",True),
                roles=[],domains=[],allowed_recipe_role_domain_bindings=[],license=a["license"],existing_technical_metadata=str(technical_path))
            row["root"]["sha256"] = a.get("sample_prepared_root_sha256") or digest(prepared)
        union = row.get("actual_collision_and_visual_bounds_m") or row.get("actual_union_bounds_m") or row.get("actual_collision_bounds_m") or row.get("actual_visible_bounds_m")
        if union is None:
            raise ValueError("No existing actual placement envelope: "+key)
        row["actual_union_bounds_m"] = union
        row["actual_collision_and_visual_bounds_m"] = union
        row.setdefault("actual_visible_bounds_m",union)
        row.setdefault("review_id",a.get("review_id",key))
        row["v4_function_roles"] = bindings
        if "additive_printer" in bindings.get("MEZZANINE",[]):
            row["heavy_machine"] = False
            row["upper_printer_authority"] = "FACTORY80_GOAL_V4_20261031 section5 mezzanine additive zone"
        new_roles = sorted(set(r for rs in bindings.values() for r in rs)-set(row.get("roles",[])))
        row["roles"] = sorted(set(row.get("roles",[])) | {r for rs in bindings.values() for r in rs})
        domains = set(row.get("domains",[]))
        h = row["actual_union_bounds_m"][1][2]-row["actual_union_bounds_m"][0][2]
        if row["technical_tag"] == "PASS":
            domains.add("double_height")
            if h < 3.88: domains.add("under_mezz")
        elif row["technical_tag"] == "UNDER_MEZZ_ONLY":
            domains.add("under_mezz")
        if "MEZZANINE" in bindings:
            if not row.get("heavy_machine",False) or "additive_printer" in bindings["MEZZANINE"]:
                domains.add("upper_storage_control")
        row["domains"] = sorted(domains)
        previous_bindings = row.get("allowed_recipe_role_domain_bindings",[])
        additions = [dict(role=r,domain=d) for r in row["roles"] for d in domains if dict(role=r,domain=d) not in previous_bindings]
        row["allowed_recipe_role_domain_bindings"] = previous_bindings+additions
        rows.append(row)
        patches.append(dict(asset_id=key,existing_pool_entry=key in old,append_roles=new_roles,append_role_domain_bindings=additions,function_roles=bindings,
            class_unchanged=row["class"],proxy_unchanged=row["proxy"],root_unchanged=row["root"]))
    return dict(schema="Factory80V4AdditiveRolePoolsV1",pools=rows,unchanged_original_pool_count=len(old),excluded=omitted,
        source_library=ref(library_root/"LIBRARY.json"),source_role_pools=ref(root/"pipeline/ASSET_ROLE_POOLS_V3.json"),old_role_file_modified=False),patches


def prepare(root, output=None):
    out = output or root/"pipeline/v4"
    cfg = read(config_file(root))
    plan = building_plan(cfg)
    roles,patches = make_pools(root,read(root/"asset_library/LIBRARY.json"),read(root/"pipeline/ASSET_ROLE_POOLS_V3.json"))
    shell_rows = []
    input_index = {r["building"].removeprefix("factory80_"):r for r in read(root/"pipeline/BATCH_INPUT_INDEX.json")["buildings"]}
    for row in plan["buildings"]:
        bid = row["building"]
        base = root/"runs"/bid/"v32_batch"
        retained = base/"retained_architecture/architecture_v2.usda"
        sample_retained = root/"runs"/bid/"v3_sample/retained_architecture/architecture_v2.usda"
        if not retained.is_file() and sample_retained.is_file():
            retained = sample_retained
        domain = base/"layout_inputs/SPATIAL_DOMAINS.json"
        index = input_index[bid]
        alternatives = [index["preferred_historical_shell"],*reversed(index["other_historical_roots"])]
        historical = next((p for p in alternatives if Path(p).is_file()),None)
        shell_rows.append(dict(building=bid,retained_architecture=str(retained),retained_architecture_exists=retained.is_file(),
            retained_readback=str(retained.parent/"RETENTION_READBACK_V2.json"),prior_spatial_domains=str(domain),prior_spatial_domains_exists=domain.is_file(),
            prior_scene_root=str(base/"scene/root.usda"),historical_shell=historical,source_root_for_structure=str(retained) if retained.is_file() else historical))
    plan["shell_sources"] = shell_rows
    staged = copy.deepcopy(cfg)
    staged["phase"] = "FACTORY80_V4_PILOT_DEVELOPMENT"
    staged["v4"] = dict(tool_version=VERSION,primary_functions=list(FUNCTIONS),function_templates=TEMPLATES,
        plan_path=str(root/"pipeline/v4/V4_BUILDING_PLAN.json"),role_pool_path=str(root/"pipeline/v4/ROLE_POOLS_V4.json"),
        output_namespace="runs/<building>/v4",batch_release=False,checkpoint="USER_REVIEW_AFTER_FULL_GRID3_PILOT_AND_FIVE_NEGATIVE_CONTROLS",
        resources=dict(cpu_cores_max=64,cpu_buildings_max=4,gpu_serial=True,memory_gib_max=200,oom_score_adj=1000),
        checks=["C1","C2","C3","C4","C5"],photos=dict(rgb_fixed=6,collision_gray=1,automatic_visual_scoring=False),
        assets=dict(existing_admission_only=True,source_scale=1.,simplify_source_collision=False),
        mezzanine=dict(net_fraction=[.40,.44],exterior_edges="COLUMN_LINES",openings="ACTUAL_TREAD_ENVELOPE_PLUS_LATERAL_AND_REAR_0.3M"))
    write(out/"V4_BUILDING_PLAN.json",plan)
    write(out/"FUNCTION_TEMPLATES.json",dict(schema="Factory80V4FunctionTemplatesV1",templates=TEMPLATES,status="DRAFT_BEFORE_PILOT_REVIEW_BATCH_FROZEN_AFTER_REVIEW"))
    write(out/"ROLE_POOLS_V4.json",roles)
    write(out/"ASSET_FUNCTION_BINDINGS_V4_PATCH.json",dict(schema="Factory80V4AdditiveRolePatchV1",source=roles["source_role_pools"],append_only=True,patches=patches))
    write(out/"config.yaml",staged)
    return dict(primary_counts=plan["primary_counts"],shape_counts=plan["shape_counts"],secondary_fraction=17/29,pool_assets=len(roles["pools"]),
        roles_by_function={f:sum(f in r["v4_function_roles"] for r in roles["pools"]) for f in (*FUNCTIONS,"MEZZANINE")},
        shell_sources_found=sum(s["retained_architecture_exists"] for s in shell_rows),status="PREPARED_NOT_PHYSICS_CHECKED")


def make_domain(plan, prior, placed_stairs):
    """Reuse measured shell data; replace only mezzanine/aisles/stair reservations.

    placed_stairs require opening_rect or opening_polygon_xy, entry_floor_xyz_m,
    exit_floor_xyz_m and actual_full_stair_bounds_xy. These are actual generated
    geometry measurements after rigid placement, never nominal stair dimensions.
    """
    from shapely.geometry import box, Point, Polygon, shape, mapping
    from shapely.ops import unary_union
    d = copy.deepcopy(prior)
    n = plan["grid"]
    ground = shape(prior["floors"]["Floor1"]["support_geometry"])
    if abs(ground.area-plan["ground_area_m2"])>1e-3 or any(abs(a-b)>1e-3 for a,b in zip(ground.bounds,plan["ground_bounds_xy_m"])):
        raise ValueError("Actual retained floor differs from existing fixed shell grid")
    gross = unary_union([box(i*20,j*21,(i+1)*20,(j+1)*21) for i,j in plan["mezzanine_bays"]])
    openings = [Polygon(s["opening_polygon_xy"]) if "opening_polygon_xy" in s else box(*s["opening_rect"]) for s in placed_stairs]
    if len(openings) != plan["stair_count"]:
        raise ValueError("Missing actual generated stair opening geometry")
    if any(not gross.covers(p) for p in openings):
        raise ValueError("Actual staircase opening outside selected bay-aligned mezzanine")
    holes = unary_union(openings)
    upper = gross.difference(holes)
    if upper.geom_type != "Polygon" or not upper.is_valid or not .40<=upper.area/ground.area<=.44:
        raise ValueError("Actual net mezzanine geometry violates40-44% or connectivity")
    d.update(schema="Factory80V4ActualSpatialDomainsV1",building=plan["building"],theme="C" if plan["machine_fraction_min"]==.2 else "A",
        primary_function=plan["primary_function"],secondary_function=plan["secondary_function"],mezzanine_shape=plan["mezzanine_shape"],
        actual_mezzanine_gross_geometry=mapping(gross),actual_stair_opening_union_geometry=mapping(holes),
        opening_polygons_xy_m=[dict(stair_id=s.get("id",s.get("stair_id",str(i+1))),polygon_xy_m=[list(p) for p in o.exterior.coords]) for i,(s,o) in enumerate(zip(placed_stairs,openings))],
        actual_net_mezzanine_fraction=upper.area/ground.area,actual_net_mezzanine_area_m2=upper.area,
        batch_release=False,equipment_layout="V4_PENDING",formal_scene_quick_checks="C1_C5_NOT_EXECUTED")
    d["floors"]["Floor2"].update(top_z_m=4.,actual_full_slab_area_m2=upper.area,support_geometry=mapping(upper))
    d["regions"] = {"double_height":dict(floor="Floor1",geometry=mapping(ground.difference(gross)),actual_area_m2=ground.difference(gross).area),
        "under_mezz":dict(floor="Floor1",geometry=mapping(gross),actual_area_m2=gross.area),
        "upper_storage_control":dict(floor="Floor2",geometry=mapping(upper),actual_area_m2=upper.area)}
    aisles=[]
    for floor,support in (("Floor1",ground),("Floor2",upper)):
        x0,y0,x1,y1=ground.bounds
        if floor=="Floor1" and any(a["floor"]=="Floor1" for a in prior["reserved_aisles"]):
            aisles.extend(copy.deepcopy([a for a in prior["reserved_aisles"] if a["floor"]=="Floor1"]))
            continue
        col,row=main_axes(plan)
        mx,my=(col+.5)*20,(row+.5)*21
        candidates=[("main_X",box(x0,my-1.5,x1,my+1.5)),("main_Y",box(mx-1.5,y0,mx+1.5,y1))]
        for i in range(1,n):
            candidates.extend([("cross_Y",box(i*20+.5,y0,i*20+2.5,y1)),("cross_X",box(x0,i*21+.5,x1,i*21+2.5))])
        for kind,poly in candidates:
            clipped=support.intersection(poly)
            for part in [clipped] if clipped.geom_type=="Polygon" else getattr(clipped,"geoms",[]):
                if part.area>0:
                    # Rectangle pieces preserve existing audit's bounds schema.
                    if abs(box(*part.bounds).area-part.area)>1e-7:
                        cuts=sorted(set([part.bounds[0],part.bounds[2]]+[float(x) for x,y in part.exterior.coords]))
                        for a,b in zip(cuts,cuts[1:]):
                            q=part.intersection(box(a,y0,b,y1))
                            for r in [q] if q.geom_type=="Polygon" else getattr(q,"geoms",[]):
                                if r.area>0:aisles.append(dict(floor=floor,kind=kind,bounds=list(r.bounds)))
                    else:aisles.append(dict(floor=floor,kind=kind,bounds=list(part.bounds)))
    d["reserved_aisles"]=aisles
    d["stair_entry_exit_keepouts"]=[dict(floor=f,center_xy=s[key][:2],radius_m=3.) for s in placed_stairs for f,key in (("Floor1","entry_floor_xyz_m"),("Floor2","exit_floor_xyz_m"))]
    d["static_collider_world_aabbs"]=[r for r in prior["static_collider_world_aabbs"] if not any(p in r["prim_path"] for p in ("/Stair/","/Floor2Slab/","/SampleEquipmentV3/"))]
    for s in placed_stairs:
        x0,y0,x1,y1=s["actual_full_stair_bounds_xy"]
        z0=s["native_actual_bounds_m"][0][2]+s["offset_m"][2]
        z1=s["native_actual_bounds_m"][1][2]+s["offset_m"][2]
        d["static_collider_world_aabbs"].append(dict(prim_path=s["prim_path"],bounds_xyz_m=[[x0,y0,z0],[x1,y1,z1]],
            category="stair",stair_id=s["id"],bound_source="Actual generated visual/collision union envelope after rigid placement"))
    d["area_scaled_equipment_repeat_cap"]=8*math.ceil((ground.area+upper.area)/5500)
    d["layout_only"]=any(s.get("layout_only",False) for s in placed_stairs)
    d["physics_candidate_created"]=False if d["layout_only"] else None
    d["collision_qualification"]=LAYOUT_ONLY_STATE if d["layout_only"] else "PENDING_USER_EXCEPTION_NOT_READY" if any(s.get("collision_qualification")=="PENDING_USER_EXCEPTION_NOT_READY" for s in placed_stairs) else "C1_C5_NOT_EXECUTED"
    return d


def main_axes(plan):
    cells=list(map(tuple,plan["mezzanine_bays"]))
    columns=collections.Counter(i for i,j in cells);rows=collections.Counter(j for i,j in cells)
    # Both3m main aisles meet inside an actual occupied mezzanine bay.
    return min(cells,key=lambda p:(-columns[p[0]]-rows[p[1]],rank(plan["building_seed"],"main_axes",*p)))


def measured_base_domain(project_root,plan):
    """Read retained actual shell and original slab with existing mesh helpers."""
    import numpy as np
    from pxr import Usd,UsdGeom
    from shapely.geometry import Polygon,mapping
    from shapely.ops import unary_union
    sys.path.insert(0,str(project_root/"pipeline"))
    from floor_placement_audit import mesh_arrays,top_union,enabled
    master=read(project_root/"pipeline/v4/V4_BUILDING_PLAN.json")
    original=next(r for r in master["shell_sources"] if r["building"]==plan["building"])
    shell=next(r for r in read(project_root/"pipeline/v4/RETAINED_SHELLS.json")["buildings"] if r["building"]==plan["building"])
    retained=Path(shell["retained_shell"])
    mask=Usd.StagePopulationMask(["/World/Cut/Floor1Slab","/World/FactoryStructure","/World/Floor1/Geometry"])
    stage=Usd.Stage.OpenMasked(str(retained),mask)
    if not stage:raise ValueError("Actual retained shell cannot be opened")
    visual=stage.GetPrimAtPath("/World/Cut/Floor1Slab/Visual")
    gv,_=mesh_arrays(visual);ground_visual=top_union(visual);ground_top=float(gv[:,2].max())
    pieces=[];physical_tiles=[];obstacles=[]
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not enabled(prim):continue
        vertices,faces=mesh_arrays(prim)
        obstacles.append(dict(prim_path=str(prim.GetPath()),bounds_xyz_m=[vertices.min(0).tolist(),vertices.max(0).tolist()]))
        if str(prim.GetPath()).startswith("/World/Cut/Floor1Slab/"):
            tri=vertices[faces];normal=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])
            chosen=tri[(np.max(abs(tri[:,:,2]-ground_top),axis=1)<1e-6)&(normal[:,2]>0)]
            pieces.extend(Polygon(t[:,:2]) for t in chosen)
            physical_tiles.append(dict(prim_path=str(prim.GetPath()),actual_upward_top_triangles=len(chosen)))
    if not pieces:raise ValueError("No enabled actual ground collision top")
    ground=unary_union(pieces)
    if ground.symmetric_difference(ground_visual).area>1e-8:raise ValueError("Actual ground visual/physical support differs")
    # Historical files retain the original upper slab; the new retained shell
    # intentionally excludes it. Mask only this source subtree, not equipment.
    upper_stage=None;upper_source=None;upper_prim=None
    for source in dict.fromkeys([shell["source"],original["historical_shell"],original["prior_scene_root"]]):
        if not Path(source).is_file():continue
        candidate=Usd.Stage.OpenMasked(str(source),Usd.StagePopulationMask(["/World/Cut/Floor2Slab"]))
        for path in ("/World/Cut/Floor2Slab/Collision","/World/Cut/Floor2Slab/Visual"):
            prim=candidate.GetPrimAtPath(path)
            if prim and prim.IsA(UsdGeom.Mesh):upper_stage,upper_source,upper_prim=candidate,Path(source),prim;break
        if upper_prim:break
    if not upper_prim:raise ValueError("Actual historical upper slab missing; cannot guess its thickness")
    uv,_=mesh_arrays(upper_prim);upper=top_union(upper_prim);upper_top=float(uv[:,2].max());upper_bottom=float(uv[:,2].min())
    if abs(upper_top-4.)>2e-5 or upper_bottom>=upper_top:raise ValueError("Actual original slab is not the retained four metre floor")
    config=read(config_file(project_root))
    return dict(schema="Factory80MeasuredRetainedShellDomainV4",building=plan["building"],
        floors={"Floor1":dict(top_z_m=ground_top,actual_full_slab_area_m2=ground.area,support_geometry=mapping(ground)),
            "Floor2":dict(top_z_m=upper_top,actual_full_slab_area_m2=upper.area,support_geometry=mapping(upper))},
        upper_slab_bottom_z_m=upper_bottom,roof_z_m=config["physical_v3"]["roof_z_m"],
        frozen_body_height_m=config["standability_pinned"]["body_height_m"],existing_geometry_readback_tolerance_m=config["sample_mezz_stair_integration_v3"]["geometry_readback_tolerance_m"],
        static_collider_world_aabbs=obstacles,reserved_aisles=[],stair_entry_exit_keepouts=[],exterior_door_keepouts=[],
        actual_ground_physical_tiles=physical_tiles,ground_source="ACTUAL_ENABLED_COLLISION_UPWARD_TRIANGLE_UNION",
        retained_shell=ref(retained),actual_upper_slab_measurement=dict(source=ref(upper_source),prim_path=str(upper_prim.GetPath()),bottom_z_m=upper_bottom,top_z_m=upper_top),
        collider_AABBs_are_conservative_proposals_not_runtime_queries=True,formal_scene_quick_checks="C1_C5_NOT_EXECUTED",batch_release=False)


def measured_exterior_doors(project_root,plan,domain):
    """Reuse the v3.1 actual triangle cross-section algorithm on the new floor."""
    import numpy as np
    from pxr import Usd
    from shapely.geometry import shape,LineString,box,mapping
    from shapely.ops import unary_union
    sys.path.insert(0,str(project_root/"pipeline"))
    from floor_placement_audit import mesh_arrays,enabled
    shell=next(r for r in read(project_root/"pipeline/v4/RETAINED_SHELLS.json")["buildings"] if r["building"]==plan["building"])
    stage=Usd.Stage.OpenMasked(shell["retained_shell"],Usd.StagePopulationMask(["/World/Floor1/Geometry"]))
    config=read(config_file(project_root));xmin,ymin,xmax,ymax=shape(domain["floors"]["Floor1"]["support_geometry"]).bounds
    walls=[];output=[];measurements=[]
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if "/wall_0_" not in str(prim.GetPath()) or not enabled(prim):continue
        v,f=mesh_arrays(prim);walls.append((str(prim.GetPath()),v[f],v.min(0),v.max(0)))
    if len(walls)<6:raise ValueError("Actual retained exterior wall collision sections missing")
    for floor,floordoc in domain["floors"].items():
        z0=floordoc["top_z_m"];body=config["standability_pinned"]["body_height_m"];levels=[z0+.05,z0+body/2,z0+body-.05]
        for side,axis,coord,start,end in [("south",1,ymin,xmin,xmax),("north",1,ymax,xmin,xmax),("west",0,xmin,ymin,ymax),("east",0,xmax,ymin,ymax)]:
            tangent=1-axis;blocked=[];sources=[]
            for path,triangles,lo,hi in walls:
                if min(abs(lo[axis]-coord),abs(hi[axis]-coord))>1e-4:continue
                sources.append(dict(prim_path=path,bounds_xyz_m=[lo.tolist(),hi.tolist()]))
                for z in levels:
                    for tri in triangles:
                        if z<tri[:,2].min() or z>tri[:,2].max():continue
                        points=[]
                        for a,b in zip(tri,np.roll(tri,-1,axis=0)):
                            dz=b[2]-a[2]
                            if abs(dz)<1e-10:continue
                            t=(z-a[2])/dz
                            if 0<=t<=1:points.append(a+t*(b-a))
                        if len(points)>1:
                            a=min(p[tangent] for p in points);b=max(p[tangent] for p in points)
                            if b-a>1e-8:blocked.append(LineString([(a,0),(b,0)]))
            if not sources:raise ValueError("Missing expected actual exterior wall side: "+side)
            gaps=LineString([(start,0),(end,0)]).difference(unary_union(blocked));parts=list(gaps.geoms) if hasattr(gaps,"geoms") else [gaps]
            for part in parts:
                if part.is_empty or part.length<1e-6:continue
                a,_,b,_=part.bounds;k=config["layout_rules"]["exterior_door_interior_keepout_m"]
                rect={"south":[a,ymin,b,ymin+k],"north":[a,ymax-k,b,ymax],"west":[xmin,a,xmin+k,b],"east":[xmax-k,a,xmax,b]}[side]
                strip=box(*rect).intersection(shape(floordoc["support_geometry"]))
                if strip.area>1e-7:output.append(dict(floor=floor,side=side,opening_interval_m=[a,b],inward_depth_m=k,geometry=mapping(strip),actual_wall_sources=sources,actual_cross_section_heights_m=levels,
                    method="Complement of actual wall triangle cross sections at unchanged v3.1 body heights"))
            measurements.append(dict(floor=floor,side=side,wall_sources=sources,sample_levels_m=levels,opening_intervals=[list(p.bounds[::2]) for p in parts if not p.is_empty and p.length>=1e-6]))
    if not any(d["floor"]=="Floor1" for d in output):raise ValueError("Actual exterior ground opening missing")
    domain["exterior_door_keepouts"]=output;domain["v31_door_measurements"]=measurements
    domain["exterior_door_geometry_source"]=ref(shell["retained_shell"])
    return domain


def _measured_walk_surfaces(horizontal_faces,metrics,traversal,tolerance_m=2e-5):
    """Select actual measured face unions supporting the native path, not hulls.

    Heights and polygons come from exported vertex coordinates. The native
    riser count labels those measurements and detects missing source levels; it
    never creates a tread, nominal rectangle or physics representation.
    """
    import numpy as np
    from shapely.geometry import Polygon,Point,mapping
    from shapely.ops import unary_union
    path=np.asarray(traversal["points_xyz_m"],dtype=float)
    n=int(metrics["floor_integration"]["number_of_risers"])
    height=float(metrics["adapted_dimensions"]["target_height_m"]);rise=height/n
    if path.ndim!=2 or path.shape[1]!=3 or not np.isfinite(path).all():raise ValueError("Invalid actual native traversal")
    groups=collections.defaultdict(list)
    for face in horizontal_faces:
        level=round(face["top_z_m"]/rise)
        if 1<=level<n and abs(face["top_z_m"]-level*rise)<=tolerance_m:groups[level].append(face)
    treads=[];missing=[]
    for level in range(1,n):
        seeds=path[np.abs(path[:,2]-level*rise)<=tolerance_m]
        candidates=groups.get(level,[])
        if not len(seeds) or not candidates:missing.append(level);continue
        union=unary_union([Polygon(f["polygon_xy_m"]) for f in candidates])
        parts=[union] if union.geom_type=="Polygon" else [p for p in getattr(union,"geoms",[]) if p.geom_type=="Polygon"]
        selected=[p for p in parts if any(p.buffer(tolerance_m).covers(Point(*s[:2])) for s in seeds)]
        if not selected or any(not any(p.buffer(tolerance_m).covers(Point(*s[:2])) for p in selected) for s in seeds):
            missing.append(level);continue
        for index,part in enumerate(selected):
            contributors=[f for f in candidates if Polygon(f["polygon_xy_m"]).intersection(part).area>1e-10]
            actual_z=sum(f["top_z_m"]*Polygon(f["polygon_xy_m"]).area for f in contributors)/sum(Polygon(f["polygon_xy_m"]).area for f in contributors)
            treads.append(dict(tread_id=f"native_level{level:02d}_surface{index:02d}",level_index=level,top_z_m=actual_z,
                polygon_xy_m=[list(p) for p in part.exterior.coords],xy_polygon_m=[list(p) for p in part.exterior.coords],
                surface_geometry=mapping(part),xy_bounds_m=[list(part.bounds[:2]),list(part.bounds[2:])],
                source_faces=[dict(prim_path=f["prim_path"],face_index=f["face_index"]) for f in contributors],
                collider_ids=[],scope="ACTUAL_SOURCE_SURFACE_FOR_LAYOUT_ONLY_NOT_C4_OR_PHYSICS"))
    if missing:raise ValueError("ACTUAL_EXPORTED_TREAD_LEVELS_MISSING:"+",".join(map(str,missing)))
    envelope=unary_union([Polygon(t["polygon_xy_m"]) for t in treads])
    polygons=[envelope] if envelope.geom_type=="Polygon" else list(envelope.geoms)
    ascent=path[-1,:2]-path[-2,:2];ascent/=np.linalg.norm(ascent)
    side=np.array([-ascent[1],ascent[0]])
    iface=np.asarray(metrics["floor_integration"]["final_riser_interface_xy_m"])
    corners=np.array([p for t in treads for p in t["polygon_xy_m"]]);frame=(corners-iface)@np.stack([side,ascent]).T
    first_axis=path[1,:2]-path[0,:2];first_axis/=np.linalg.norm(first_axis)
    entry=path[0].copy();entry[:2]-=first_axis*metrics["adapted_dimensions"]["tread_depth_m"]/2;entry[2]=0.
    result=dict(treads=treads,tread_boxes=treads,actual_measured_tread_levels=sorted({t["level_index"] for t in treads}),missing_tread_levels=[],
        walking_envelope_geometry=mapping(envelope),walking_envelope_polygons_xy_m=[[list(v) for v in p.exterior.coords] for p in polygons],
        walking_envelope_is_single_polygon=len(polygons)==1,tread_exit_frame_min=frame.min(0).tolist(),tread_exit_frame_max=frame.max(0).tolist(),
        ascent_xy=ascent.tolist(),side_xy=side.tolist(),entry_floor_xyz_m=entry.tolist(),exit_floor_xyz_m=path[-1].tolist(),path_points=path.tolist(),platforms=[])
    if len(polygons)==1:result["walking_envelope_polygon_xy_m"]=[list(v) for v in polygons[0].exterior.coords]
    return result


def raw_stair_layout_metadata(row,native_summary_path,presentation_receipt=None,recovery_summary_path=None):
    """Measure an actual raw USD; never construct collision meshes or hulls."""
    import numpy as np
    from pxr import Usd,UsdGeom,UsdPhysics
    from shapely.geometry import Polygon
    metrics_path=Path(row["metrics_path"]);traversal_path=Path(row["traversal_path"]);native_receipt=Path(row["receipt"])
    metrics=read(metrics_path);traversal=read(traversal_path);receipt=read(native_receipt)
    if metrics["kind"]!=row["kind"] or metrics["seed"]!=row["actual_native_seed"]:raise ValueError("Native metadata identity differs from unchanged pregeneration map")
    if receipt["exit_code"]!=row["native_exit_code"]:raise ValueError("Actual native exit receipt differs from pregeneration map")
    inputs=[Path(native_summary_path),metrics_path,traversal_path,native_receipt,Path(__file__)]
    recovery_status=None
    if receipt["exit_code"]!=0:
        if row.get("metadata_recovery_exit_code")!=0 or not recovery_summary_path:raise ValueError("Failed native postprocess lacks a verified recovery map")
        recovery=read(recovery_summary_path)
        match=[r for r in recovery["recovered"] if r["building"]==row["building"] and r["stair_index"]==row["stair_index"] and r["actual_native_seed"]==row["actual_native_seed"]]
        if len(match)!=1 or match[0]["metadata_recovery_exit_code"]!=0 or match[0]["missing_tread_levels"]:raise ValueError("Actual native metadata recovery is incomplete")
        recovery_receipt=Path(match[0]["recovery_receipt"])
        if read(recovery_receipt)["exit_code"]!=0:raise ValueError("Recovery receipt did not complete")
        inputs.extend([Path(recovery_summary_path),recovery_receipt]);recovery_status=metrics["status"]
    source=Path(metrics.get("deliverable_usd") or metrics.get("actual_existing_visual_stage") or Path(row["asset_dir"])/"staircase.usd")
    if not source.is_file():raise FileNotFoundError(source)
    inputs.append(source)
    stage=Usd.Stage.Open(str(source));xc=UsdGeom.XformCache()
    if UsdGeom.GetStageUpAxis(stage)!="Z" or abs(UsdGeom.GetStageMetersPerUnit(stage)-1.)>1e-9:raise ValueError("Actual raw stair is not Z-up metre geometry")
    horizontal=[];low=np.full(3,np.inf);high=np.full(3,-np.inf);mesh_count=0;point_count=0
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh) or prim.HasAPI(UsdPhysics.CollisionAPI) or "/collision" in str(prim.GetPath()).lower():continue
        mesh=UsdGeom.Mesh(prim);raw=mesh.GetPointsAttr().Get()
        if not raw:continue
        transform=np.asarray(xc.GetLocalToWorldTransform(prim));points=np.asarray(raw,dtype=float)@transform[:3,:3]+transform[3,:3]
        low=np.minimum(low,points.min(0));high=np.maximum(high,points.max(0));mesh_count+=1;point_count+=len(points)
        counts=list(mesh.GetFaceVertexCountsAttr().Get() or []);indices=np.asarray(mesh.GetFaceVertexIndicesAttr().Get() or [],dtype=int);cursor=0
        for face_index,count in enumerate(counts):
            ids=indices[cursor:cursor+count];cursor+=count;face=points[ids]
            if count<3 or np.ptp(face[:,2])>2e-5:continue
            polygon=Polygon(face[:,:2])
            if not polygon.is_valid or polygon.area<=1e-10:continue
            horizontal.append(dict(prim_path=str(prim.GetPath()),face_index=face_index,top_z_m=float(face[:,2].mean()),polygon_xy_m=face[:,:2].tolist()))
    if not mesh_count or not horizontal:raise ValueError("Actual raw visual mesh/horizontal surfaces missing")
    measured=_measured_walk_surfaces(horizontal,metrics,traversal)
    output=dict(schema="Factory80StairLayoutMetadataV1",version=VERSION,layout_only=True,physics_candidate_created=False,
        collision_qualification=LAYOUT_ONLY_STATE,pending_user_decision="V4_SECTION4_NONBOX_COLLIDER_REPRESENTATION",checks={f"C{i}":"NOT_EXECUTED" for i in range(1,6)},READY=False,
        building=row["building"],stair_index=row["stair_index"],kind=metrics["kind"],seed=metrics["seed"],height_m=metrics["adapted_dimensions"]["target_height_m"],
        initial_native_seed=row["initial_native_seed"],actual_native_seed=row["actual_native_seed"],native_exit_code=receipt["exit_code"],
        metadata_recovery_exit_code=row.get("metadata_recovery_exit_code"),native_recovery_state=recovery_status,
        visual_source_stage=ref(source),visual_aabb_min_m=low.tolist(),visual_aabb_max_m=high.tolist(),
        actual_visual_mesh_count=mesh_count,actual_visual_vertex_count=point_count,actual_horizontal_source_face_count=len(horizontal),
        actual_face_measurement_tolerance_m=2e-5,face_selection="Actual exported horizontal-face unions supporting every actual native traversal level; no collision proxy construction",
        floor_integration=metrics["floor_integration"],style=metrics["style"],traversal=traversal,**measured)
    if presentation_receipt:
        visual=read(presentation_receipt)
        if not visual.get("source_only") or visual.get("collision_representation_created") is not False or visual["exit_code"]!=0:raise ValueError("Presentation input is not a completed source-only visual export")
        if visual["style"]!=row["planned_material"]:raise ValueError("Presentation style differs from unchanged building plan")
        visual_stage=Path(visual["asset_stage"])
        if digest(visual_stage)!=visual["output_sha"]:raise ValueError("Source-only presentation USD bytes changed")
        actual=Usd.Stage.Open(str(visual_stage));actual_xc=UsdGeom.XformCache();actual_lo=np.full(3,np.inf);actual_hi=np.full(3,-np.inf)
        for prim in actual.Traverse(Usd.TraverseInstanceProxies()):
            if not prim.IsA(UsdGeom.Mesh) or prim.HasAPI(UsdPhysics.CollisionAPI) or "/collision" in str(prim.GetPath()).lower():continue
            points=np.asarray(UsdGeom.Mesh(prim).GetPointsAttr().Get(),dtype=float)
            if not len(points):continue
            matrix=np.asarray(actual_xc.GetLocalToWorldTransform(prim));points=points@matrix[:3,:3]+matrix[3,:3]
            actual_lo=np.minimum(actual_lo,points.min(0));actual_hi=np.maximum(actual_hi,points.max(0))
        error=float(max(np.max(np.abs(actual_lo-low)),np.max(np.abs(actual_hi-high))))
        if not np.isfinite(error) or error>2e-5:raise ValueError("Actual source-only presentation changed measured raw-stair envelope: "+str(error))
        output["presentation_actual_bounds_m"]=[actual_lo.tolist(),actual_hi.tolist()]
        output["presentation_raw_bounds_max_error_m"]=error
        output["asset_stage"]=ref(visual_stage);inputs.extend([Path(presentation_receipt),visual_stage])
    output["input_sha"]={str(p):digest(p) for p in inputs}
    return output


def place_stairs(plan, records, prior, draft_stairs_for_layout=False, layout_only=False):
    """Place actual qualified stair geometry at a column-aligned mezzanine edge.

    Each records entry is {metadata_path, metadata}. No generator is patched or
    invoked here; this only applies rigid90degree transforms to its real output.
    """
    import numpy as np
    from shapely.geometry import box,Polygon,Point
    from shapely.ops import unary_union
    gross=unary_union([box(i*20,j*21,(i+1)*20,(j+1)*21) for i,j in plan["mezzanine_bays"]])
    if len(records)!=plan["stair_count"]:
        raise ValueError("Expected exactly the deterministic building stair count")
    static=np.asarray([r["bounds_xyz_m"] for r in prior["static_collider_world_aabbs"] if not any(p in r["prim_path"] for p in ("/Stair/","/Floor2Slab/","/SampleEquipmentV3/"))],dtype=float)
    n=plan["grid"];w,h=n*20.,n*21.
    aisles=[box(*a["bounds"]) for a in prior["reserved_aisles"] if a["floor"]=="Floor1"]
    col,row=main_axes(plan);mx,my=(col+.5)*20,(row+.5)*21
    aisles.extend([box(0,my-1.5,w,my+1.5),box(mx-1.5,0,mx+1.5,h)])
    for i in range(1,n):aisles.extend([box(i*20+.5,0,i*20+2.5,h),box(0,i*21+.5,w,i*21+2.5)])
    all_candidates=[]
    for index,record in enumerate(records,1):
        s=record["metadata"]
        if s.get("layout_only") and not layout_only:
            raise ValueError("Actual layout-only metadata requires explicit --layout-only; no physics candidate exists")
        if layout_only and not s.get("layout_only"):
            raise ValueError("Layout-only mode requires independently measured source-only metadata")
        valid_collision=s.get("exit_code")==0 and s.get("collision_checks",{}).get("PASS") is True
        if not valid_collision and not draft_stairs_for_layout and not layout_only:
            raise ValueError("Precise stair collider is not a valid positive candidate: "+str(record["metadata_path"]))
        if s["kind"]!=plan["stair_kinds"][index-1]:
            raise ValueError("Actual stair kind differs from deterministic requested kind")
        metrics_path=next((Path(p) for p in s.get("input_sha",{}) if Path(p).name=="generation_metrics.json"),None)
        metrics=read(metrics_path) if metrics_path and metrics_path.is_file() else {}
        effective=s.get("style",{}) if layout_only else metrics.get("style_override_trace",{}).get("effective",metrics.get("style",{}))
        actual_rail=effective.get("handrail_type","UNVERIFIED")
        rail_mismatch=actual_rail not in ("UNVERIFIED",plan["handrail_type"])
        if rail_mismatch and not draft_stairs_for_layout:
            raise ValueError("Actual handrail type differs from building-wide style")
        lo=np.asarray(s["visual_aabb_min_m"]);hi=np.asarray(s["visual_aabb_max_m"])
        iface=np.asarray(s["floor_integration"]["final_riser_interface_xy_m"]);axis=np.asarray(s["ascent_xy"]);side=np.asarray(s["side_xy"])
        qlo,qhi=np.asarray(s["tread_exit_frame_min"]),np.asarray(s["tread_exit_frame_max"])
        corners=iface+np.array([[qlo[0]-.3,qlo[1]-.3],[qhi[0]+.3,qlo[1]-.3],[qhi[0]+.3,0.],[qlo[0]-.3,0.]])@np.stack([side,axis])
        highest=max(s["treads"],key=lambda t:t["top_z_m"])
        topcoords=np.asarray(highest.get("polygon_xy_m") or highest["xy_polygon_m"])
        width=float(np.ptp((topcoords-iface)@side))
        local_patch=iface+np.array([[-width/2,.005],[width/2,.005],[width/2,3.],[-width/2,3.]])@np.stack([side,axis])
        candidates=[]
        for bay in map(tuple,plan["mezzanine_bays"]):
            for fx in (.25,.5,.75):
                for fy in (.25,.5,.75):
                    center=np.array([(bay[0]+fx)*20,(bay[1]+fy)*21])
                    for yaw in (0,90,180,270):
                        a=math.radians(yaw);rot=np.array([[math.cos(a),-math.sin(a)],[math.sin(a),math.cos(a)]])
                        offset=np.r_[center-rot@((lo+hi)[:2]/2),0.]
                        pts=np.array([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])]);pts[:,:2]=pts[:,:2]@rot.T+offset[:2]
                        foot=box(*pts[:,:2].min(0),*pts[:,:2].max(0))
                        if not gross.buffer(-.75).covers(foot) or any(foot.intersects(p) for p in aisles):continue
                        blo,bhi=pts.min(0),pts.max(0)
                        if len(static) and np.any(np.all(np.minimum(bhi,static[:,1])-np.maximum(blo,static[:,0])>2e-5,axis=1)):continue
                        opening=Polygon(corners@rot.T+offset[:2]);patch=Polygon(local_patch@rot.T+offset[:2])
                        if not gross.covers(opening) or not gross.covers(patch) or opening.intersection(patch).area>1e-7:continue
                        if any(opening.intersects(p) for p in aisles):continue
                        path=np.asarray(s["path_points"]).copy();path[:,:2]=path[:,:2]@rot.T+offset[:2]
                        entry=np.asarray(s["entry_floor_xyz_m"]).copy();entry[:2]=rot@entry[:2]+offset[:2]
                        exit=np.asarray(s["exit_floor_xyz_m"]).copy();exit[:2]=rot@exit[:2]+offset[:2]
                        if not gross.difference(opening).covers(Point(*exit[:2])):continue
                        metadata_path=Path(record["metadata_path"])
                        stage=Path(record.get("asset_stage") or (s.get("asset_stage") or {}).get("path") or metadata_path.parent/"staircase_v4.usda");collision=metadata_path.parent/"collision.usda"
                        row=dict(id=f"S{index:02d}",stair_id=f"S{index:02d}",stair_index=index,kind=s["kind"],seed=s["seed"],
                            asset_stage=ref(stage),collision_proxy=None if layout_only else ref(collision),metadata_path=str(metadata_path),collision_metadata=None if layout_only else str(metadata_path),
                            layout_metadata=str(metadata_path) if layout_only else None,layout_only=layout_only,physics_candidate_created=False if layout_only else None,
                            prim_path=f"/World/Stair/S{index:02d}",rotation_deg=yaw,offset_m=offset.tolist(),opening_bay_xy=list(bay),
                            actual_full_stair_bounds_xy=list(foot.bounds),opening_polygon_xy=[list(v) for v in opening.exterior.coords],opening_rect=list(opening.bounds),
                            upper_forward_landing_patch_xy=[list(v) for v in patch.exterior.coords],entry_floor_xyz_m=entry.tolist(),exit_floor_xyz_m=exit.tolist(),world_path=path.tolist(),
                            native_actual_bounds_m=[lo.tolist(),hi.tolist()],source_collision_checks=s.get("collision_checks"),
                            collision_qualification=LAYOUT_ONLY_STATE if layout_only else "C1_C5_NOT_EXECUTED" if valid_collision else "PENDING_USER_EXCEPTION_NOT_READY",
                            requested_handrail_type=plan["handrail_type"],actual_handrail_type=actual_rail,handrail_plan_mismatch=rail_mismatch,
                            actual_support_type=effective.get("support_type","UNVERIFIED"),requested_material=plan["material"],material_verification="VISUAL_USER_REVIEW_PENDING",
                            equipment_keepouts=[dict(floor="Floor1",center_xy=entry[:2].tolist(),radius_m=3.),dict(floor="Floor2",center_xy=exit[:2].tolist(),radius_m=3.)])
                        score=(foot.distance(gross.boundary),rank(plan["building_seed"],index,*bay,fx,fy,yaw))
                        candidates.append((score,row,foot,opening,patch))
        if not candidates:
            raise ValueError(f"NO_ACTUAL_STAIR_FIT:S{index:02d}:{s['kind']}")
        all_candidates.append(sorted(candidates,key=lambda q:q[0]))
    searched=0
    def search(chosen):
        nonlocal searched
        if len(chosen)==len(all_candidates):
            holes=unary_union([q[3] for q in chosen]);net=gross.difference(holes)
            if not .40<=net.area/(n*n*420.)<=.44:return None
            if any(not net.covers(q[4]) for q in chosen):return None
            return chosen
        for candidate in all_candidates[len(chosen)]:
            searched+=1
            if searched>20000:raise ValueError("ACTUAL_STAIR_PLACEMENT_SEARCH_EXHAUSTED")
            if any(candidate[2].distance(p[2])<.5 or candidate[3].intersects(p[3]) or candidate[3].intersects(p[4]) or candidate[4].intersects(p[3]) for p in chosen):continue
            result=search(chosen+[candidate])
            if result is not None:return result
        return None
    chosen=search([])
    if chosen is None:raise ValueError("NO_MUTUALLY_COMPATIBLE_ACTUAL_STAIR_LAYOUT")
    return dict(schema="Factory80V4PlacedActualStairsV1",building=plan["building"],stairs=[q[1] for q in chosen],
        actual_candidate_counts=[len(v) for v in all_candidates],searched_candidates=searched,
        source="Actual generated tread/rail bounds, actual traversal, existing0.3m opening rule",C1_C5="NOT_EXECUTED",
        draft_stairs_for_layout=draft_stairs_for_layout,
        layout_only=layout_only,physics_candidate_created=False if layout_only else None,
        collision_qualification=LAYOUT_ONLY_STATE if layout_only else "PENDING_USER_EXCEPTION_NOT_READY" if draft_stairs_for_layout else "C1_C5_NOT_EXECUTED")


def place_instances(config, domain, pools, plan, attempt=0):
    """Prioritize the assigned function; bounded warehouse fill; old audit reused.

    Ordinary imports reuse the existing v3.2 count/geometry audit and the
    already-qualified welding blueprint. Only superseded A/C recipe rules are
    replaced with the explicitly authorized six-function unit definitions.
    """
    import numpy as np
    from shapely.geometry import box,Point,shape,mapping
    from shapely.ops import unary_union
    sys.path.insert(0,str(Path(config["source_root"])/"pipeline"))
    from unit_geometry import turned_bounds
    from layout_unit_audit import audit as old_audit
    from welding_units import blueprint as qualified_blueprint
    from area_count_rules import placeable_regions
    if "v4" in config and config["v4"]["function_templates"] != TEMPLATES:
        raise ValueError("Authoring templates differ from the staged config; update draft config before pilot, freeze before bulk")
    seed=rank(plan["building_seed"],"layout",attempt)
    seed_count,warehouse_fill_max=layout_policy(config)
    if attempt not in range(seed_count):raise ValueError("Layout attempt must be a fixed index0..19")
    required_functions={plan["primary_function"]}|({plan["secondary_function"]} if plan["secondary_function"] else set())
    warehouse_is_function="WAREHOUSE" in required_functions
    areas=placeable_regions(config,domain)
    cap=8*math.ceil(sum(f["actual_full_slab_area_m2"] for f in domain["floors"].values())/5500)
    lookup={r["key"]:r for r in pools}
    # Existing conditional admission is a placement condition, not a new V1
    # verdict: re-evaluate the actual new net slab and standing gap at each
    # candidate. The old003/004 allowed booleans are never copied to this house.
    library_path=Path(config["source_root"])/"asset_library/LIBRARY.json"
    library={r["id"]:r for r in read(library_path)["assets"]}
    conditional_support={}
    for asset in pools:
        if asset.get("technical_tag")=="UNDER_MEZZ_ONLY":
            values={float(r["conservative_asset_support_lower_z_m"]) for r in library[asset["key"]]["conditional_placement"].values()}
            if len(values)!=1:raise ValueError("Conditional source standing-height evidence is inconsistent")
            conditional_support[asset["key"]]=next(iter(values))
    actual_upper=shape(domain["floors"]["Floor2"]["support_geometry"])
    used=collections.Counter()
    placed=[];units=[];occupied=collections.defaultdict(list);unplaced=[]
    static=np.asarray([r["bounds_xyz_m"] for r in domain["static_collider_world_aabbs"]],dtype=float)
    function_cells={tuple(c):f for f,cells in plan["function_bays"].items() for c in cells}
    serial=0
    def choose(template,dname,uid,variant=0):
        temporary=used.copy();ingredients={}
        for role,count in template["members"].items():
            ingredients[role]=[]
            for i in range(count):
                candidates=[r for r in pools if role in r["roles"] and dname in r["domains"] and (r["class"]=="STRUCTURE" or temporary[r["key"]]<cap)
                    and (dname!="upper_storage_control" or not r.get("heavy_machine",False))]
                if role=="high_rack_group":
                    candidates=[r for r in candidates if r["actual_union_bounds_m"][1][2]-r["actual_union_bounds_m"][0][2]<=6.5]
                    if dname=="under_mezz":
                        actual_clear_height=domain["upper_slab_bottom_z_m"]-domain["floors"]["Floor1"]["top_z_m"]
                        candidates=[r for r in candidates if r["actual_union_bounds_m"][1][2]-r["actual_union_bounds_m"][0][2]<=actual_clear_height]
                if role=="machine_guard_segment":
                    candidates=[r for r in candidates if "barrier_fence" in r["key"]]
                if not candidates:raise ValueError("ROLE_CAPACITY_EXHAUSTED:"+role)
                def key(r):
                    lo,hi=np.asarray(r["actual_union_bounds_m"]);area=np.prod((hi-lo)[:2])
                    # Rack fill needs real footprint; smallest service equipment
                    # is not preferred just to increase counts.
                    if role in ("high_rack_group","light_rack_group"):
                        return ((-area if variant==0 else area),temporary[r["key"]],rank(seed,uid,role,i,variant,r["key"])) if variant<2 else (temporary[r["key"]],rank(seed,uid,role,i,variant,r["key"]))
                    return (temporary[r["key"]],area,rank(seed,uid,role,i,r["key"]))
                r=min(candidates,key=key);ingredients[role].append(r);temporary[r["key"]]+=1
        return ingredients
    def blueprint(template,dname,uid,variant=0):
        ingredients=choose(template,dname,uid,variant)
        unit=dict(id=uid,kind=template["kind"],zone=template["kind"],domain=dname,floor="Floor2" if dname=="upper_storage_control" else "Floor1",ingredients=ingredients)
        if template.get("reuse"):
            unit["zone"]="double_height_welding"
            return qualified_blueprint(unit)
        members=[];y=0.;row=[];x=0.;depth=0.
        for role,assets in ingredients.items():
            for source in assets:
                if len(row)==2:
                    x=0.;y+=depth+.5;depth=0.;row=[]
                lo,hi=np.asarray(source["actual_union_bounds_m"]);yaw=0 if hi[0]-lo[0]>=hi[1]-lo[1] else 90
                lo,hi=np.asarray(turned_bounds(source["actual_union_bounds_m"],yaw));dims=hi-lo
                t=np.array([x-lo[0],y-lo[1],-lo[2]])
                members.append(dict(asset_id=source["key"],asset_class=source["class"],role=role,source_root=source["root"],collision_proxy=source["proxy"],
                    artistic_scale=1.,yaw_deg=yaw,translation_m=t.tolist(),local_union_bounds_m=[(lo+t).tolist(),(hi+t).tolist()]))
                x+=dims[0]+.5;depth=max(depth,dims[1]);row.append(source)
        vertices=np.asarray([v for m in members for v in m["local_union_bounds_m"]])
        return {k:v for k,v in unit.items() if k!="ingredients"}|dict(members=members,bounds_m=[vertices.min(0).tolist(),vertices.max(0).tolist()])
    def try_place(template,dname,bay,f,variant=0):
        nonlocal serial
        serial+=1;uid=f"{f}_{template['kind']}_{serial:05d}"
        try:u=blueprint(template,dname,uid,variant)
        except (ValueError,AssertionError) as e:
            unplaced.append(dict(unit_id=uid,reason=str(e)));return False
        floor=u["floor"];floorz=domain["floors"][floor]["top_z_m"]
        if floor=="Floor1" and f=="WAREHOUSE" and not warehouse_is_function:
            current=[m for m in placed if m["floor"]=="Floor1" and m["asset_class"]!="STRUCTURE"]
            added=sum(m["asset_class"]!="STRUCTURE" for m in u["members"])
            warehouse=sum(m["function"]=="WAREHOUSE" for m in current)
            if warehouse+added>warehouse_fill_max*(len(current)+added):
                unplaced.append(dict(unit_id=uid,reason="WAREHOUSE_FILL_EQUIPMENT_COUNT_CAP",current_warehouse_count=warehouse,
                    current_floor1_equipment_count=len(current),proposed_warehouse_count=warehouse+added,
                    proposed_floor1_equipment_count=len(current)+added,maximum_fraction=warehouse_fill_max))
                return False
        region=shape(domain["regions"][dname]["geometry"])
        baybox=box(bay[0]*20,bay[1]*21,(bay[0]+1)*20,(bay[1]+1)*21)
        free=areas[dname]["available"].intersection(baybox)
        # Every candidate footprint is covered by free, which is a subset of
        # this bay. An occupied polygon disjoint from the bay cannot intersect
        # any candidate. Keep the same exact predicates for all possible pairs.
        local_occupied=[o for o in occupied[floor] if o.intersects(baybox)]
        candidates=[]
        for yaw in (0,90,180,270):
            lo,hi=np.asarray(turned_bounds(u["bounds_m"],yaw));size=hi-lo
            xmin,ymin,xmax,ymax=free.bounds if not free.is_empty else (0,0,0,0)
            xs=np.arange(xmin+.25,xmax-size[0]-.25+1e-8,1.)
            ys=np.arange(ymin+.25,ymax-size[1]-.25+1e-8,1.)
            for x in xs:
                for y in ys:
                    foot=box(x,y,x+size[0],y+size[1])
                    if free.covers(foot) and not any(foot.intersects(o) for o in local_occupied):
                        center=np.array([(bay[0]+.5)*20,(bay[1]+.5)*21]);distance=np.sum((np.array(foot.centroid.coords[0])-center)**2)
                        candidates.append((distance,rank(seed,uid,yaw,float(x),float(y)),yaw,np.array([x-lo[0],y-lo[1],floorz-lo[2]]),foot))
        for _,__,yaw,shift,foot in sorted(candidates,key=lambda r:r[:2]):
            angle=math.radians(yaw);rot=np.array([[math.cos(angle),-math.sin(angle)],[math.sin(angle),math.cos(angle)]])
            members=[]
            for m in u["members"]:
                lo,hi=np.asarray(turned_bounds(m["local_union_bounds_m"],yaw))+shift
                if len(static) and np.any(np.all(np.minimum(hi,static[:,1])-np.maximum(lo,static[:,0])>2e-5,axis=1)):break
                source=lookup[m["asset_id"]];t=np.asarray(m["translation_m"]).copy();t[:2]=rot@t[:2];t+=shift
                if source["key"] in conditional_support:
                    support_lower=lo[2]+conditional_support[source["key"]]-source["actual_union_bounds_m"][0][2]
                    gap=domain["upper_slab_bottom_z_m"]-support_lower
                    if floor!="Floor1" or not actual_upper.covers(box(lo[0],lo[1],hi[0],hi[1])) or not gap<config["standability_pinned"]["body_height_m"]:break
                members.append(dict(m,unit_id=uid,unit_kind=u["kind"],function=f,domain=dname,floor=floor,
                    translation_m=t.tolist(),yaw_deg=(m["yaw_deg"]+yaw)%360,world_union_bounds_m=[lo.tolist(),hi.tolist()],
                    collision_proxy=source["proxy"],prim_path=f"/World/V4Equipment/{floor}/{uid}/I{len(members):03d}"))
            else:
                placed.extend(members);occupied[floor].append(foot.buffer(.15))
                for m in members:used[m["asset_id"]]+=1
                units.append(dict(id=uid,kind=u["kind"],function=f,domain=dname,floor=floor,bay_xy=list(bay),expected_roles=template["members"],world_footprint=mapping(foot)))
                return True
        unplaced.append(dict(unit_id=uid,reason="NO_WHOLE_UNIT_FIT",asset_variant_search=variant))
        return try_place(template,dname,bay,f,variant+1) if variant<2 else False
    def eligible_count(dname,bay=None):
        result=[]
        for r in placed:
            if r["domain"]!=dname or r["asset_class"]=="STRUCTURE" or r["world_union_bounds_m"][1][2]-r["world_union_bounds_m"][0][2]<.5:continue
            center=np.mean(np.asarray(r["world_union_bounds_m"]),axis=0)
            if bay is None or (int(center[0]//20),int(center[1]//21))==bay:result.append(r)
        return len(result)
    region_bays={};functional_queue=[]
    for dname in ("double_height","under_mezz","upper_storage_control"):
        region=shape(domain["regions"][dname]["geometry"])
        bays=[(i,j) for i in range(plan["grid"]) for j in range(plan["grid"]) if region.intersection(box(i*20,j*21,(i+1)*20,(j+1)*21)).area>=1.]
        region_bays[dname]=bays
        for bay in bays:
            freearea=areas[dname]["available"].intersection(box(bay[0]*20,bay[1]*21,(bay[0]+1)*20,(bay[1]+1)*21)).area
            if freearea<10:continue
            functional_queue.append((dname,bay,"MEZZANINE" if dname=="upper_storage_control" else function_cells[bay],math.ceil(freearea/16.)))
    # One complete assigned-function unit per bay per round. A first region
    # cannot consume every shared source's building repeat budget before the
    # other ground bays receive their own primary/secondary equipment.
    exhausted=set()
    for iteration in range(12):
        for dname,bay,f,target in sorted(functional_queue,key=lambda q:rank(seed,"function_round",iteration,q[0],*q[1])):
            if (dname,bay) in exhausted or eligible_count(dname,bay)>=target:continue
            choices=sorted(TEMPLATES[f],key=lambda t:rank(seed,dname,bay,iteration,t["kind"]))
            if not any(try_place(template,dname,bay,f) for template in choices):exhausted.add((dname,bay))
    for dname,bays in region_bays.items():
        required_footprint=areas[dname]["placeable_area_m2"]*config["layout_rules"]["region_footprint_fraction_min"]
        max_count=math.floor(areas[dname]["placeable_area_m2"]/config["layout_rules"]["equipment_density_m2_per_instance"]["minimum"]+1e-9)
        def footprint_sum():
            return sum((r["world_union_bounds_m"][1][0]-r["world_union_bounds_m"][0][0])*(r["world_union_bounds_m"][1][1]-r["world_union_bounds_m"][0][1])
                for r in placed if r["domain"]==dname and r["asset_class"]!="STRUCTURE" and r["world_union_bounds_m"][1][2]-r["world_union_bounds_m"][0][2]>=.5)
        for bay in sorted(bays,key=lambda c:rank(seed,"footprint_fill",dname,*c)):
            if footprint_sum()>=required_footprint:break
            f="MEZZANINE" if dname=="upper_storage_control" else function_cells[bay]
            for template in sorted(TEMPLATES[f],key=lambda t:rank(seed,"function_footprint_fill",dname,bay,t["kind"])):
                if eligible_count(dname)+sum(template["members"].values())<=max_count and try_place(template,dname,bay,f):break
    # Only after all Floor1 function regions have had first choice of space
    # may WAREHOUSE fill remaining density/footprint. The actual instance cap
    # is checked by try_place before any complete warehouse unit is committed.
    for dname in ("double_height","under_mezz"):
        region=shape(domain["regions"][dname]["geometry"])
        bays=[(i,j) for i in range(plan["grid"]) for j in range(plan["grid"]) if region.intersection(box(i*20,j*21,(i+1)*20,(j+1)*21)).area>=1.]
        max_count=math.floor(areas[dname]["placeable_area_m2"]/config["layout_rules"]["equipment_density_m2_per_instance"]["minimum"]+1e-9)
        required_footprint=areas[dname]["placeable_area_m2"]*config["layout_rules"]["region_footprint_fraction_min"]
        for bay in sorted(bays,key=lambda c:rank(seed,"bounded_warehouse_fill",dname,*c)):
            freearea=areas[dname]["available"].intersection(box(bay[0]*20,bay[1]*21,(bay[0]+1)*20,(bay[1]+1)*21)).area
            target=math.ceil(freearea/16.)
            for iteration in range(12):
                footprint=sum((m["world_union_bounds_m"][1][0]-m["world_union_bounds_m"][0][0])*(m["world_union_bounds_m"][1][1]-m["world_union_bounds_m"][0][1]) for m in placed if m["domain"]==dname and m["asset_class"]!="STRUCTURE" and m["world_union_bounds_m"][1][2]-m["world_union_bounds_m"][0][2]>=.5)
                if eligible_count(dname,bay)>=target and footprint>=required_footprint:break
                success=False
                for template in sorted(TEMPLATES["WAREHOUSE"],key=lambda t:rank(seed,"warehouse_fill",dname,bay,iteration,t["kind"])):
                    if eligible_count(dname)+sum(template["members"].values())<=max_count and try_place(template,dname,bay,"WAREHOUSE"):
                        success=True;break
                if not success:break
    result=audit_instances(config,domain,pools,plan,placed,units)
    return dict(schema="Factory80V4ActualEquipmentLayoutV1",tool_version=VERSION,building=plan["building"],seed=plan["building_seed"],layout_attempt=attempt,
        instances=placed,units=units,audit=result,unplaced_units=unplaced,repeat_cap=cap,actual_instance_count=len(placed),
        status="LAYOUT_PASS" if result["status"]=="PASS" else "LAYOUT_FAIL",batch_release=False,
        layout_only=domain.get("layout_only",False),physics_candidate_created=domain.get("physics_candidate_created"),
        collision_qualification=domain.get("collision_qualification","C1_C5_NOT_EXECUTED"))


def audit_instances(config,domain,pools,plan,placed,units):
    """Reuse the original audit on fixed existing placements without rerolling."""
    sys.path.insert(0,str(Path(config["source_root"])/"pipeline"))
    from layout_unit_audit import audit as old_audit
    function_cells={tuple(c):f for f,cells in plan["function_bays"].items() for c in cells}
    purposes={}
    # Functions are assigned before placement. Warehouse labels additionally
    # need the retained old audit's actual complete four-rack-row condition.
    for bay,f in function_cells.items():
        purposes[f"Floor1:{bay[0]}:{bay[1]}"]="WAREHOUSE" if f=="WAREHOUSE" else "PRODUCTION"
    # The retained audit asserts its historical API identity before reading
    # unchanged rule values. The batch config is immutable and remains the
    # recorded input; only this local call's two identity labels are adapted.
    audit_config=dict(config,schema="Factory80PipelineConfig20261021V32",version="3.2.0")
    result=old_audit(audit_config,domain,placed,pools,[],bay_purposes=purposes)
    retired={"required_theme_functional_groups","complete_or_coherent_partial_units"}
    rules=[r for r in result["rules"] if r["rule"] not in retired]
    unit_errors=[]
    for u in units:
        actual=collections.Counter(r["role"] for r in placed if r["unit_id"]==u["id"])
        if dict(actual)!=u["expected_roles"]:unit_errors.append(dict(unit=u["id"],expected=u["expected_roles"],actual=dict(actual)))
    rules.append(dict(rule="v4_complete_function_templates",status="PASS" if not unit_errors else "FAIL",failures=unit_errors))
    function_counts=collections.Counter(u["function"] for u in units)
    required=[plan["primary_function"]]+([plan["secondary_function"]] if plan["secondary_function"] else [])
    missing=[f for f in required if not function_counts[f]]
    rules.append(dict(rule="v4_required_functions_present",status="PASS" if not missing else "FAIL",missing=missing,unit_counts=dict(function_counts)))
    _,warehouse_max=layout_policy(config)
    ground=[r for r in placed if r["floor"]=="Floor1" and r["asset_class"]!="STRUCTURE"]
    counts=collections.Counter(r["function"] for r in ground)
    ground_units=collections.Counter(u["function"] for u in units if u["floor"]=="Floor1")
    warehouse_count=counts["WAREHOUSE"];total=len(ground);fraction=warehouse_count/total if total else 0.
    warehouse_is_function="WAREHOUSE" in required
    unexpected=sorted(set(counts)-set(required)-{"WAREHOUSE"})
    rules.append(dict(rule="v4_floor1_warehouse_fill_equipment_count_fraction",status="PASS" if warehouse_is_function or warehouse_count<=warehouse_max*total else "FAIL",
        count_basis="all Floor1 nonSTRUCTURE equipment instances; no height/area weighting",warehouse_is_primary_or_secondary=warehouse_is_function,
        cap_applies=not warehouse_is_function,warehouse_equipment_count=warehouse_count,floor1_equipment_count=total,
        actual_fraction=fraction,maximum_fraction=warehouse_max))
    rules.append(dict(rule="v4_floor1_primary_secondary_function_identity",status="PASS" if not unexpected else "FAIL",unexpected_functions=unexpected,
        primary_function=plan["primary_function"],secondary_function=plan["secondary_function"],
        primary_equipment_count=counts[plan["primary_function"]],secondary_equipment_count=counts[plan["secondary_function"]] if plan["secondary_function"] else 0,
        primary_unit_count=ground_units[plan["primary_function"]],secondary_unit_count=ground_units[plan["secondary_function"]] if plan["secondary_function"] else 0,
        floor1_function_equipment_counts=dict(counts),floor1_function_unit_counts=dict(ground_units)))
    result.update(schema="Factory80V4LayoutAuditV1",rules=rules,status="PASS" if all(r["status"]=="PASS" for r in rules) else "FAIL",
        reused_implementation="layout_unit_audit.audit ordinary import; superseded A/C recipe rules replaced by v4 templates",
        physics="C1_C5_NOT_EXECUTED",floor1_function_equipment_counts=dict(counts),floor1_function_unit_counts=dict(ground_units),
        floor1_warehouse_equipment_fraction=fraction,warehouse_fill_cap_applies=not warehouse_is_function)
    return result


def precompute_building(project_root,building,output=None,domain_path=None,metadata_only=False):
    """Actual raw geometry and ordinary layout audit, with immutable attempts."""
    import resource
    started=time.monotonic();project_root=Path(project_root)
    output=Path(output) if output else project_root/"runs"/building/"v4/batch_v1/layout"
    plan_path=project_root/"pipeline/v4/V4_BUILDING_PLAN.json"
    summary_path=project_root/"pipeline/v4/NATIVE_STAIR_PREGEN_V1/RESULT.json"
    recovery_path=summary_path.parent/"METADATA_RECOVERY_SUMMARY_V1.json"
    master=read(plan_path);plan=next(r for r in master["buildings"] if r["building"]==building)
    rows=sorted([r for r in read(summary_path)["assets"] if r["building"]==building],key=lambda r:r["stair_index"])
    if len(rows)!=plan["stair_count"]:raise ValueError("Actual raw-source map has wrong stair count")
    records=[]
    for row in rows:
        presentation=project_root/"runs"/building/f"v4/stairs/S{row['stair_index']:02d}/visual_v1/PRESENTATION_RECEIPT.json"
        data=raw_stair_layout_metadata(row,summary_path,presentation,recovery_path)
        data["input_sha"][str(plan_path)]=digest(plan_path)
        identity=hashlib.sha256(json.dumps(data["input_sha"],sort_keys=True).encode()).hexdigest()[:12]
        path=output/f"S{row['stair_index']:02d}_LAYOUT_METADATA_{VERSION.replace('@','_').replace('.','_')}_{identity}.json"
        if path.exists():
            if read(path)!=data:raise ValueError("Existing immutable metadata differs: "+str(path))
        else:write_new(path,data)
        records.append(dict(metadata_path=path,metadata=data,asset_stage=data["asset_stage"]["path"]))
    result=dict(status="ACTUAL_SOURCE_LAYOUT_METADATA_ONLY",building=building,stairs=len(records),layout_only=True,physics_candidate_created=False,collision_qualification=LAYOUT_ONLY_STATE,
        metadata_paths=[str(r["metadata_path"]) for r in records])
    if not metadata_only:
        if domain_path:domain_path=Path(domain_path)
        else:
            source=next(r for r in master["shell_sources"] if r["building"]==building)
            candidate=Path(source["prior_spatial_domains"])
            domain_path=candidate if candidate.is_file() else None
        if domain_path:prior=read(domain_path)
        else:
            prior=measured_base_domain(project_root,plan)
            domain_path=output/"MEASURED_BASE_DOMAIN.json";write(domain_path,prior)
        spec=place_stairs(plan,records,prior,layout_only=True)
        domain=make_domain(plan,prior,spec["stairs"])
        domain=measured_exterior_doors(project_root,plan,domain)
        write(output/"stairs_spec.json",spec);write(output/"SPATIAL_DOMAINS.json",domain)
        config_path=config_file(project_root);layout_policy(read(config_path));pools_path=project_root/"pipeline/v4/ROLE_POOLS_V4.json"
        saved=[]
        for path in output.glob("LAYOUT_ATTEMPT_*.json"):
            if path.stem.endswith("_RECEIPT"):continue
            data=read(path)
            if data.get("building")==building and data.get("layout_attempt") in range(20):
                if data.get("input_sha",{}).get(str(config_path))!=digest(config_path):raise ValueError("Saved layout attempts use different immutable config bytes")
                saved.append((data["layout_attempt"],path,data))
        completed_indices=sorted({s[0] for s in saved})
        if completed_indices and completed_indices!=list(range(max(completed_indices)+1)):raise ValueError("Saved fixed layout-seed history has a gap")
        next_attempt=max(completed_indices)+1 if completed_indices else 0
        result["preserved_completed_layout_seed_indices"]=completed_indices
        # A terminated process can leave a fully written immutable attempt but
        # no current result. Reuse it and never reset its seed budget.
        finished=next((s for s in saved if s[2]["status"]=="LAYOUT_PASS"),None)
        if not finished and next_attempt>=20:finished=max(saved,key=lambda s:s[0])
        if finished:
            attempt,path,layout=finished
            result.update(status=layout["status"],actual_instance_count=layout["actual_instance_count"],unit_count=len(layout["units"]),layout_seed_index=attempt,
                failed_rules=[r for r in layout["audit"]["rules"] if r["status"]!="PASS"],net_mezzanine_fraction=domain["actual_net_mezzanine_fraction"],reused_immutable_attempt=ref(path))
            write(output/"LAYOUT.json",layout)
        for attempt in range(next_attempt,20) if not finished else []:
            layout=place_instances(read(config_path),domain,read(pools_path)["pools"],plan,attempt)
            inputs={str(p):digest(p) for p in (config_path,plan_path,pools_path,domain_path,output/"stairs_spec.json",output/"SPATIAL_DOMAINS.json",Path(__file__))}
            layout["input_sha"]=inputs
            identity=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()[:12]
            path=output/f"LAYOUT_ATTEMPT_{attempt}_{VERSION.replace('@','_').replace('.','_')}_{identity}.json"
            if path.exists():
                if read(path)!=layout:raise ValueError("Existing immutable layout attempt differs")
            else:write_new(path,layout)
            write(output/"LAYOUT.json",layout)
            receipt_path=path.with_name(path.stem+"_RECEIPT.json")
            if not receipt_path.exists():write_new(receipt_path,dict(input_sha=inputs,output_sha={str(path):digest(path)},exit_code=0 if layout["status"]=="LAYOUT_PASS" else 1,
                peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,elapsed_s=time.monotonic()-started,
                status=layout["status"],layout_only=True,physics_candidate_created=False,collision_qualification=LAYOUT_ONLY_STATE,
                measured=dict(actual_instance_count=layout["actual_instance_count"],unit_count=len(layout["units"]),layout_seed_index=attempt)))
            result.update(status=layout["status"],actual_instance_count=layout["actual_instance_count"],unit_count=len(layout["units"]),layout_seed_index=attempt,
                failed_rules=[r for r in layout["audit"]["rules"] if r["status"]!="PASS"],net_mezzanine_fraction=domain["actual_net_mezzanine_fraction"])
            if layout["status"]=="LAYOUT_PASS":break
        if layout["status"]!="LAYOUT_PASS":result["disposition"]="EXCLUDED_LAYOUT"
    result.update(layout_seed_count=20,layout_seed_indices_used=sorted({s[0] for s in saved}|set(range(next_attempt,attempt+1))) if not metadata_only else [],
        layout_seed_budget_scope="CPU_LAYOUT_ONLY_0_TO_19",postC2_C4_layout_repair_count=3,postC2_C4_layout_repairs_used=0,
        elapsed_s=time.monotonic()-started,peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
        checks={f"C{i}":"NOT_EXECUTED" for i in range(1,6)},READY=False)
    write(output/"PRECOMPUTE_RESULT.json",result)
    return result


def _layout_worker_init():
    """At most8CPU each; four workers, actual RSS is measured, no AS guard."""
    import multiprocessing
    Path("/proc/self/oom_score_adj").write_text("1000")
    available=sorted(os.sched_getaffinity(0))[:32]
    index=(multiprocessing.current_process()._identity[0]-1)%4
    selected=available[index*8:(index+1)*8] or available[:8]
    os.sched_setaffinity(0,selected)
    try:
        from pxr import Work
        Work.SetConcurrencyLimit(8)
    except ImportError:pass


def _layout_worker(payload):
    global CONFIG_PATH
    project_root,building,config_path=payload
    CONFIG_PATH=Path(config_path)
    existing=Path(project_root)/"runs"/building/"v4/batch_v1/layout/PRECOMPUTE_RESULT.json"
    if existing.is_file():
        row=read(existing)
        row.update(reuse_existing=True,current_tool_not_used=True,original_result=ref(existing))
        layout=existing.with_name("LAYOUT.json")
        if layout.is_file():
            old=read(layout);row["original_tool_version"]=old["tool_version"]
            original_hash=old.get("input_sha",{}).get(str(Path(project_root)/"pipeline/v4/hall_layout_legacy.py"))
            row["original_tool_sha256"]=original_hash
            current=Path(project_root)/"pipeline/v4/hall_layout_legacy.py"
            matches=([current] if original_hash and digest(current)==original_hash else list((Path(project_root)/"pipeline/v4/source_history").glob(f"layout_v2_*_{original_hash}.py"))) if original_hash else []
            row["original_tool_source_history"]=ref(matches[0]) if matches and digest(matches[0])==original_hash else None
            row["original_source_bytes_status"]="VERIFIED_PRESERVED" if row["original_tool_source_history"] else "UNVERIFIED_OLD_BYTES_NOT_YET_RECOVERED"
        return row
    try:return precompute_building(project_root,building)
    except Exception as error:
        import traceback,datetime
        output=Path(project_root)/"runs"/building/"v4/batch_v1/layout"
        result=dict(building=building,status="PRECOMPUTE_IMPLEMENTATION_OR_SOURCE_FAILURE",error_type=type(error).__name__,error=str(error),traceback=traceback.format_exc(),
            tool_version=VERSION,source=ref(Path(__file__)),layout_only=True,physics_candidate_created=False,C1_C5="NOT_EXECUTED",READY=False)
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        write_new(output/f"PRECOMPUTE_FAILURE_{stamp}.json",result);write(output/"PRECOMPUTE_RESULT.json",result)
        return result


def repair_routing_building(project_root,building,last_seed_index=3):
    """Replay existing fixed indices with corrected domain routing, new output."""
    import resource,shutil
    project_root=Path(project_root);started=time.monotonic()
    olddir=project_root/"runs"/building/"v4/batch_v1/layout"
    output=olddir.parent/"layout_routing_repair_v1"
    oldresult=read(olddir/"PRECOMPUTE_RESULT.json")
    if oldresult["status"]!="LAYOUT_FAIL":raise ValueError("Routing replay is only for existing failed layouts")
    original={}
    for path in olddir.glob("LAYOUT_ATTEMPT_*.json"):
        if path.stem.endswith("_RECEIPT"):continue
        row=read(path);original[row["layout_attempt"]]=path
    if set(original)!={0,1,2,3}:raise ValueError("Existing fixed seed history must remain complete0..3")
    config_path=config_file(project_root);plan_path=project_root/"pipeline/v4/V4_BUILDING_PLAN.json";pools_path=project_root/"pipeline/v4/ROLE_POOLS_V4.json"
    config=read(config_path);plan=next(r for r in read(plan_path)["buildings"] if r["building"]==building);domain=read(olddir/"SPATIAL_DOMAINS.json")
    for original_path in original.values():
        old_inputs=read(original_path)["input_sha"]
        for path in (config_path,plan_path,pools_path):
            if old_inputs.get(str(path))!=digest(path):raise ValueError("Original fixed source/config/pool bytes changed during routing-only repair: "+str(path))
    if not domain.get("layout_only"):raise ValueError("Routing repair remains source-only layout work")
    output.mkdir(parents=True,exist_ok=True)
    for name in ("SPATIAL_DOMAINS.json","stairs_spec.json"):
        target=output/name
        if target.exists() and digest(target)!=digest(olddir/name):raise ValueError("Prior repair source domain/stairs changed")
        if not target.exists():shutil.copyfile(olddir/name,target)
    result=dict(building=building,tool_version=VERSION,layout_only=True,physics_candidate_created=False,collision_qualification=LAYOUT_ONLY_STATE,
        original_failed_result=ref(olddir/"PRECOMPUTE_RESULT.json"),original_fixed_seed_indices=[0,1,2,3],
        implementation_change="Remove unconditional under-mezzanine high-to-light rack substitution; choose allowed high racks within actual measured clear height",
        same_plan_and_fixed_seed_values=True,thresholds_templates_classes_roles_scale_unchanged=True,actual_under_mezz_clear_height_m=domain["upper_slab_bottom_z_m"]-domain["floors"]["Floor1"]["top_z_m"])
    saved={}
    for path in output.glob("LAYOUT_ATTEMPT_*.json"):
        if not path.stem.endswith("_RECEIPT"):
            row=read(path);saved[row["layout_attempt"]]=(path,row)
    for attempt in range(last_seed_index+1):
        if attempt in saved:path,layout=saved[attempt]
        else:
            one_started=time.monotonic();layout=place_instances(config,domain,read(pools_path)["pools"],plan,attempt)
            inputs={str(p):digest(p) for p in (config_path,plan_path,pools_path,olddir/"SPATIAL_DOMAINS.json",original[attempt],Path(__file__))}
            layout.update(input_sha=inputs,replayed_original_attempt=ref(original[attempt]),fixed_layout_seed_value_decimal=str(rank(plan["building_seed"],"layout",attempt)),routing_implementation_repair=True)
            identity=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()[:12]
            path=output/f"LAYOUT_ATTEMPT_{attempt}_{VERSION.replace('@','_').replace('.','_')}_{identity}.json";write_new(path,layout)
            write_new(path.with_name(path.stem+"_RECEIPT.json"),dict(input_sha=inputs,output_sha={str(path):digest(path)},exit_code=0 if layout["status"]=="LAYOUT_PASS" else 1,
                peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,elapsed_s=time.monotonic()-one_started,
                status=layout["status"],layout_seed_index=attempt,fixed_layout_seed_value_decimal=layout["fixed_layout_seed_value_decimal"],replayed_original_attempt=ref(original[attempt]),
                layout_only=True,physics_candidate_created=False,C1_C5="NOT_EXECUTED"))
        write(output/"LAYOUT.json",layout)
        result.update(status=layout["status"],actual_instance_count=layout["actual_instance_count"],unit_count=len(layout["units"]),layout_seed_index=attempt,
            replayed_layout_seed_indices=list(range(attempt+1)),failed_rules=[r for r in layout["audit"]["rules"] if r["status"]!="PASS"],
            net_mezzanine_fraction=domain["actual_net_mezzanine_fraction"])
        if layout["status"]=="LAYOUT_PASS":break
    result.update(elapsed_s=time.monotonic()-started,peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
        checks={f"C{i}":"NOT_EXECUTED" for i in range(1,6)},READY=False)
    if result["status"]=="LAYOUT_FAIL":result["disposition"]="EXCLUDED_LAYOUT" if last_seed_index==3 else "REPAIR_TRIAL_REMAINING_SAME_FIXED_INDICES_NOT_YET_RUN"
    write(output/"PRECOMPUTE_RESULT.json",result)
    return result


def _routing_repair_worker(payload):
    return repair_routing_building(*payload)


def bind_physics_inputs(project_root,building,source_directory,metadata_paths,approval_path,output,effective_plan_path=None,presentation_stages=None):
    """Bind approved, actually measured colliders to unchanged authored poses."""
    import numpy as np
    from pxr import Usd,UsdGeom,UsdPhysics
    import resource
    started=time.monotonic();project_root=Path(project_root);source_directory=Path(source_directory);output=Path(output)
    approval_path=Path(approval_path);approval=read(approval_path)
    if approval.get("approved") is not True or approval.get("policy")!="native_exact_triangles_for_nonbox":raise ValueError("Explicit native-triangle representation approval missing")
    approval_ref=ref(approval_path)
    originals={name:read(source_directory/name) for name in ("LAYOUT.json","SPATIAL_DOMAINS.json","stairs_spec.json")}
    layout=copy.deepcopy(originals["LAYOUT.json"]);domain=copy.deepcopy(originals["SPATIAL_DOMAINS.json"]);spec=copy.deepcopy(originals["stairs_spec.json"])
    config_path=config_file(project_root);plan_path=project_root/"pipeline/v4/V4_BUILDING_PLAN.json";pools_path=project_root/"pipeline/v4/ROLE_POOLS_V4.json"
    plan=read(effective_plan_path)["building_plan"] if effective_plan_path else next(r for r in read(plan_path)["buildings"] if r["building"]==building)
    if effective_plan_path:inputs_plan=[Path(effective_plan_path)]
    else:inputs_plan=[]
    if layout["building"]!=building or len(metadata_paths)!=plan["stair_count"] or len(spec["stairs"])!=len(metadata_paths):raise ValueError("Fixed layout/stair source count or identity differs")
    if presentation_stages and len(presentation_stages)!=len(metadata_paths):raise ValueError("Explicit alternative presentation count must match actual stairs")
    inputs=[config_path,plan_path,pools_path,approval_path,Path(__file__),*inputs_plan,*[source_directory/name for name in originals]];measured=[]
    for index,(row,metadata_path) in enumerate(zip(spec["stairs"],metadata_paths),1):
        metadata_path=Path(metadata_path);meta=read(metadata_path);checks=meta["collision_checks"]
        if meta.get("version") not in ("stairs_collision_v8","stairs_collision_v9") or meta.get("exit_code")!=0 or checks.get("PASS") is not True:raise ValueError("Actual approved collider checks have not passed: "+str(metadata_path))
        recorded_approval=meta.get("approval_record",{})
        if meta.get("contract_status")!="USER_APPROVED_NATIVE_TRIANGLES_NONBOX" or meta.get("representation_approved") is not True or any(recorded_approval.get(k)!=v for k,v in approval_ref.items()) or recorded_approval.get("policy")!=approval["policy"]:raise ValueError("Collider representation is not bound to current explicit approval")
        if meta["seed"]!=row["seed"] or meta["kind"]!=row["kind"] or meta["kind"]!=plan["stair_kinds"][index-1]:raise ValueError("Actual collider would replace the fixed canonical stair seed/kind")
        if checks["volume_relative_error"]>.01 or checks["max_tread_top_error_m"]>.005 or checks["missing_tread_levels"]:raise ValueError("Measured original volume/tread-height limits fail")
        raw_metadata=read(row["layout_metadata"])
        if checks["expected_tread_levels"]!=len(raw_metadata["actual_measured_tread_levels"]):raise ValueError("Actual collision is missing a fixed source tread level")
        oldbounds=np.asarray(row["native_actual_bounds_m"],dtype=float);newbounds=np.asarray([meta["visual_aabb_min_m"],meta["visual_aabb_max_m"]])
        tolerance=float(raw_metadata["actual_face_measurement_tolerance_m"]);error=float(np.max(np.abs(oldbounds-newbounds)))
        if error>tolerance:raise ValueError("Actual collider bounds exceed fixed source precision; explicit geometry/layout investigation required: "+str(error))
        proxy=meta["collision_proxy"];readback=meta["collision_readback"]
        for bound in (proxy,readback):
            if digest(bound["path"])!=bound["sha256"]:raise ValueError("Actual collider/readback bytes changed")
            inputs.append(Path(bound["path"]))
        readback_data=read(readback["path"])
        if not readback_data.get("tread_details") or readback_data["volume_relative_error"]!=checks["volume_relative_error"] or readback_data["max_tread_top_error_m"]!=checks["max_tread_top_error_m"]:raise ValueError("Actual independent readback disagrees with collider checks")
        if readback_data["volume_relative_error"]>.01 or readback_data["max_tread_top_error_m"]>.005 or any(t["error_m"]>.005 for t in readback_data["tread_details"]):raise ValueError("Actual independent original-volume/tread-height readback failed")
        for path,expected in meta.get("input_sha",{}).items():
            if digest(path)!=expected:raise ValueError("Collider actual input bytes changed: "+path)
        source_visual=Path(presentation_stages[index-1]) if presentation_stages else Path(row["asset_stage"]["path"])
        if presentation_stages:
            pr=source_visual.parent/"PRESENTATION_RECEIPT.json";presentation=read(pr)
            if presentation.get("style")!=plan["material"] or presentation.get("source_only") is not True or presentation.get("exit_code")!=0 or digest(source_visual)!=presentation["output_sha"]:raise ValueError("Actual alternative presentation style/source-only binding differs")
            original_metrics={p:h for p,h in raw_metadata["input_sha"].items() if Path(p).name=="generation_metrics.json"}
            if len(original_metrics)!=1 or any(presentation.get("input_sha",{}).get(p)!=h for p,h in original_metrics.items()):raise ValueError("Alternative presentation is not bound to this actual native seed/metrics")
            for path,expected in presentation.get("input_sha",{}).items():
                if digest(path)!=expected:raise ValueError("Actual alternative presentation input bytes changed: "+path)
            row["asset_stage"]=ref(source_visual);inputs.append(pr)
        elif row["asset_stage"]!=ref(source_visual):raise ValueError("Fixed source-only visual binding changed")
        visual=Usd.Stage.Open(str(source_visual));physical=Usd.Stage.Open(proxy["path"])
        visual_root=visual.GetDefaultPrim();physical_root=physical.GetDefaultPrim()
        if not visual_root or not physical_root or str(physical_root.GetPath())!=proxy["default_prim"]:raise ValueError("Actual visual/collider default root differs from metadata")
        for stage,root in ((visual,visual_root),(physical,physical_root)):
            if UsdGeom.GetStageUpAxis(stage)!="Z" or abs(UsdGeom.GetStageMetersPerUnit(stage)-1.)>1e-9:raise ValueError("Actual stair source units/up-axis differ")
            if not np.allclose(np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(root)),np.eye(4),rtol=0,atol=1e-10):raise ValueError("Actual stair root frame changed; fixed offset cannot be silently reused")
        count=lambda root:sum(p.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False for p in Usd.PrimRange(root,Usd.TraverseInstanceProxies()))
        if count(visual_root)!=0 or count(physical_root)!=proxy["enabled_colliders"]:raise ValueError("Actual source-only visual/proxy collider count differs")
        if presentation_stages:
            low=np.full(3,np.inf);high=np.full(3,-np.inf);xc=UsdGeom.XformCache()
            for prim in Usd.PrimRange(visual_root,Usd.TraverseInstanceProxies()):
                if not prim.IsA(UsdGeom.Mesh):continue
                points=np.asarray(UsdGeom.Mesh(prim).GetPointsAttr().Get(),dtype=float);matrix=np.asarray(xc.GetLocalToWorldTransform(prim));points=points@matrix[:3,:3]+matrix[3,:3]
                low=np.minimum(low,points.min(0));high=np.maximum(high,points.max(0))
            if not np.isfinite([low,high]).all() or np.max(np.abs(np.asarray([low,high])-oldbounds))>tolerance:raise ValueError("Actual alternative visual geometry differs from the placed native source")
        row.update(collision_proxy=dict(proxy),collision_metadata=str(metadata_path),metadata_path=str(metadata_path),collision_readback=dict(readback),
            source_collision_checks=checks,layout_only=False,physics_candidate_created=True,collision_qualification="C1_C5_NOT_EXECUTED",approval_record=approval_ref)
        measured.append(dict(stair_id=row["id"],seed=row["seed"],kind=row["kind"],bounds_max_difference_m=error,source_precision_m=tolerance,
            volume_relative_error=checks["volume_relative_error"],max_tread_top_error_m=checks["max_tread_top_error_m"],
            metadata=ref(metadata_path),collision_proxy=dict(proxy),collision_readback=dict(readback),native_readback_keys=sorted(readback_data)))
        inputs.extend([metadata_path,source_visual,Path(row["layout_metadata"])])
    for value in (layout,domain,spec):
        value.update(layout_only=False,physics_candidate_created=True,collision_qualification="C1_C5_NOT_EXECUTED",approval_record=approval_ref,
            checks={f"C{i}":"NOT_EXECUTED" for i in range(1,6)},READY=False)
        value.pop("pending_user_decision",None)
    spec["draft_stairs_for_layout"]=False
    audit=audit_instances(read(config_path),domain,read(pools_path)["pools"],plan,layout["instances"],layout["units"])
    if audit["status"]!="PASS":raise ValueError("Fixed actual layout no longer passes ordinary unchanged CPU audit")
    if layout["instances"]!=originals["LAYOUT.json"]["instances"] or layout["units"]!=originals["LAYOUT.json"]["units"]:raise AssertionError("Binding modified fixed equipment instances/units")
    layout.update(audit=audit,original_layout_tool_version=layout["tool_version"],tool_version=VERSION,
        layout_authoring_input_sha=layout["input_sha"],input_sha={str(p):digest(p) for p in inputs},source_fixed_layout=ref(source_directory/"LAYOUT.json"))
    output.mkdir(parents=True,exist_ok=False)
    for name,value in (("LAYOUT.json",layout),("SPATIAL_DOMAINS.json",domain),("stairs_spec.json",spec)):write_new(output/name,value)
    receipt=dict(schema="Factory80FixedLayoutPhysicsBindingV1",building=building,source_directory=str(source_directory),output_directory=str(output),
        input_sha=layout["input_sha"],output_sha={str(output/name):digest(output/name) for name in originals},approval_record=approval_ref,
        instances_unchanged=True,units_unchanged=True,layout_seed_unchanged=True,stair_offsets_rotations_unchanged=True,
        ordinary_layout_audit_status=audit["status"],ordinary_layout_audit_identical_to_source=audit==originals["LAYOUT.json"]["audit"],
        actual_instance_count=len(layout["instances"]),unit_count=len(layout["units"]),stairs=measured,
        layout_only=False,physics_candidate_created=True,collision_qualification="C1_C5_NOT_EXECUTED",C1_C5="NOT_EXECUTED",READY=False,
        elapsed_s=time.monotonic()-started,peak_memory_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024)
    write_new(output/"PHYSICS_BINDING_RECEIPT.json",receipt)
    return receipt


def author_c4_alternative_one(project_root,building,source_directory,alternative_root,output,native_receipts):
    """One actual type alternative, ordinary placement and the same seed1."""
    from shapely.geometry import shape,box
    project_root=Path(project_root);source_directory=Path(source_directory);alternative_root=Path(alternative_root);output=Path(output);started=time.monotonic()
    plan_path=project_root/"pipeline/v4/V4_BUILDING_PLAN.json";master=read(plan_path);original_plan=next(r for r in master["buildings"] if r["building"]==building)
    plan=copy.deepcopy(original_plan);plan["stair_kinds"]=["straight"]*plan["stair_count"]
    oldspec=read(source_directory/"stairs_spec.json");prior=read(source_directory/"SPATIAL_DOMAINS.json")
    if len(native_receipts)!=len(oldspec["stairs"]):raise ValueError("Three explicit actual successful native process receipts are required")
    effective=dict(schema="Factory80SingleC4TypeAlternativeV1",building_plan=plan,master_plan=ref(plan_path),original_stair_kinds=original_plan["stair_kinds"],
        alternative_index=1,alternatives_used=1,alternatives_remaining=0,kind_change_only=True,native_width_override=False,layout_seed_index=1,
        fixed_layout_seed_decimal=str(rank(plan["building_seed"],"layout",1)),original_failed_checks=ref(project_root/"runs"/building/"v4/pilot_v1/checks_v8_first/CHECKS.json"))
    output.mkdir(parents=True,exist_ok=False);write_new(output/"EFFECTIVE_BUILDING_PLAN.json",effective);records=[];raw_bindings=[]
    try:
        for index,old in enumerate(oldspec["stairs"],1):
            root=alternative_root/f"S{index:02d}";seed=old["seed"];receipt=Path(native_receipts[index-1]);native=receipt.parent;asset=native/"asset";metrics=read(asset/"generation_metrics.json")
            begin=read(native/"BEGIN.json");argv=begin.get("argv",begin.get("identity",{}).get("argv",[]))
            if any(str(a) in ("--width","--staircase-width","--stair-width") for a in argv):raise ValueError("Alternative native width override is outside this authoring request")
            style=metrics["style"]
            if metrics["kind"]!="straight" or metrics["seed"]!=seed or metrics["adapted_dimensions"]["target_height_m"]!=4. or style.get("support_type")!="solid" or style.get("handrail_type")!=plan["handrail_type"] or read(receipt)["exit_code"]!=0:raise ValueError("Actual alternative native identity/style/height differs")
            row=dict(building=building,stair_index=index,initial_native_seed=seed,actual_native_seed=seed,kind="straight",planned_material=plan["material"],asset_dir=str(asset),
                receipt=str(receipt),native_exit_code=0,metrics_path=str(asset/"generation_metrics.json"),traversal_path=str(asset/"traversal_path.json"))
            data=raw_stair_layout_metadata(row,receipt);data["asset_stage"]=ref(asset/"staircase.usd")
            path=output/f"S{index:02d}_ACTUAL_LAYOUT_METADATA.json";write_new(path,data);records.append(dict(metadata_path=path,metadata=data,asset_stage=asset/"staircase.usd"))
            raw_bindings.append(dict(stair_id=f"S{index:02d}",seed=seed,kind="straight",actual_dimensions=metrics["adapted_dimensions"],native_receipt=ref(receipt),native_begin=ref(native/"BEGIN.json")))
        spec=place_stairs(plan,records,prior,layout_only=True);domain=make_domain(plan,prior,spec["stairs"]);domain=measured_exterior_doors(project_root,plan,domain)
        spec["C4_type_alternative"]=effective;domain["C4_type_alternative_index"]=1
        config_path=config_file(project_root);pools_path=project_root/"pipeline/v4/ROLE_POOLS_V4.json";library_path=project_root/"asset_library/LIBRARY.json"
        config=read(config_path);layout=place_instances(config,domain,read(pools_path)["pools"],plan,1)
        pool_lookup={p["key"]:p for p in read(pools_path)["pools"]};lib_lookup={p["id"]:p for p in read(library_path)["assets"]};upper=shape(domain["floors"]["Floor2"]["support_geometry"]);condition_rows=[]
        for instance in layout["instances"]:
            source=pool_lookup[instance["asset_id"]]
            if source.get("technical_tag")!="UNDER_MEZZ_ONLY":continue
            evidence=lib_lookup[source["key"]];lo,hi=instance["world_union_bounds_m"]
            lower=next(iter({float(v["conservative_asset_support_lower_z_m"]) for v in evidence["conditional_placement"].values()}))
            gap=domain["upper_slab_bottom_z_m"]-(lo[2]+lower-source["actual_union_bounds_m"][0][2]);contained=upper.covers(box(lo[0],lo[1],hi[0],hi[1]))
            valid=instance["floor"]=="Floor1" and contained and gap<config["standability_pinned"]["body_height_m"]
            condition_rows.append(dict(prim_path=instance["prim_path"],asset_id=source["key"],actual_net_slab_covers_full_bounds=contained,actual_standing_gap_upper_bound_m=gap,condition_satisfied=valid,isolated_V1_original=evidence["V1_static"]["status"]))
        if any(not r["condition_satisfied"] for r in condition_rows):raise ValueError("Selected conditional asset does not satisfy actual new net slab placement")
        layout.update(effective_plan=ref(output/"EFFECTIVE_BUILDING_PLAN.json"),C4_type_alternative_index=1,
            input_sha={str(p):digest(p) for p in [config_path,plan_path,pools_path,library_path,output/"EFFECTIVE_BUILDING_PLAN.json",Path(__file__),source_directory/"SPATIAL_DOMAINS.json"]})
        for name,value in (("LAYOUT.json",layout),("SPATIAL_DOMAINS.json",domain),("stairs_spec.json",spec)):write_new(output/name,value)
        result=dict(status=layout["status"],building=building,alternative_index=1,alternatives_remaining=0,layout_seed_index=1,
            actual_instance_count=layout["actual_instance_count"],unit_count=len(layout["units"]),actual_net_mezzanine_fraction=domain["actual_net_mezzanine_fraction"],
            failed_rules=[r for r in layout["audit"]["rules"] if r["status"]!="PASS"],native_sources=raw_bindings,
            effective_plan=ref(output/"EFFECTIVE_BUILDING_PLAN.json"),master_plan=ref(plan_path),layout_only=True,physics_candidate_created=False,
            collision_qualification=LAYOUT_ONLY_STATE,output_sha={str(output/name):digest(output/name) for name in ("LAYOUT.json","SPATIAL_DOMAINS.json","stairs_spec.json")},
            ordinary_stair_candidate_counts=spec["actual_candidate_counts"],searched_candidates=spec["searched_candidates"],elapsed_s=time.monotonic()-started)
        result["existing_conditional_asset_use"]=dict(library=ref(library_path),placement_count=len(condition_rows),condition_satisfied=sum(r["condition_satisfied"] for r in condition_rows),rows=condition_rows,new_V1_executed=False)
        write_new(output/"C4_ALTERNATIVE_AUTHORING_RECEIPT.json",result);return result
    except Exception as error:
        write_new(output/"AUTHORING_FAILURE.json",dict(status="ACTUAL_C4_ALTERNATIVE_AUTHORING_FAIL",error_type=type(error).__name__,error=str(error),
            alternative_index=1,alternatives_remaining=0,layout_seed_index=1,raw_bindings=raw_bindings,elapsed_s=time.monotonic()-started))
        raise


def main():
    global CONFIG_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["prepare","place","stairs","metadata","precompute","repair-routing","bind-physics","c4-alternative"])
    parser.add_argument("--project-root",type=Path,default=DEFAULT_ROOT)
    parser.add_argument("--config",type=Path,help="Immutable batch config; defaults to project-root/pipeline/v4/config_batch_v1.yaml")
    parser.add_argument("--output",type=Path)
    parser.add_argument("--building")
    parser.add_argument("--buildings",nargs="+",help="Explicit affected building IDs for the routing implementation replay only")
    parser.add_argument("--last-seed-index",type=int,default=3,choices=range(4),help="Replay only already-existing fixed layout indices up to this index")
    parser.add_argument("--workers",type=int,default=4,choices=range(1,5),help="At most four distinct layout-only building processes")
    parser.add_argument("--domain",type=Path)
    parser.add_argument("--source-directory",type=Path)
    parser.add_argument("--approval-record",type=Path)
    parser.add_argument("--effective-plan",type=Path)
    parser.add_argument("--alternative-root",type=Path)
    parser.add_argument("--native-receipts",type=Path,nargs="+")
    parser.add_argument("--attempt",type=int,default=0,choices=range(20))
    parser.add_argument("--stair-metadata",type=Path,nargs="+")
    parser.add_argument("--presentation-stages",type=Path,nargs="+",help="Explicit actual presentation USDs in the same order; collision/tread metadata remains independently bound")
    parser.add_argument("--draft-stairs-for-layout",action="store_true",help="Explicit placement-only draft using actual native geometry; never qualifies collision or enables C1-C5")
    parser.add_argument("--layout-only",action="store_true",help="Explicit source-only geometry metadata and CPU placement; no physics candidate or checks")
    args=parser.parse_args()
    CONFIG_PATH=args.config or args.project_root/"pipeline/v4/config_batch_v1.yaml"
    if os.name == "posix":
        Path("/proc/self/oom_score_adj").write_text("1000")
    started=time.monotonic()
    if args.command == "prepare":
        result=prepare(args.project_root,args.output)
    elif args.command=="c4-alternative":
        if not args.building or not args.source_directory or not args.alternative_root or not args.output or not args.native_receipts:parser.error("c4-alternative requires --building --source-directory --alternative-root --output --native-receipts")
        result=author_c4_alternative_one(args.project_root,args.building,args.source_directory,args.alternative_root,args.output,args.native_receipts)
    elif args.command=="bind-physics":
        if not args.building or not args.source_directory or not args.output or not args.stair_metadata or not args.approval_record:parser.error("bind-physics requires fixed --building --source-directory --output --stair-metadata and --approval-record")
        result=bind_physics_inputs(args.project_root,args.building,args.source_directory,args.stair_metadata,args.approval_record,args.output,args.effective_plan,args.presentation_stages)
    elif args.command=="repair-routing":
        if not args.layout_only:parser.error("repair-routing requires explicit --layout-only")
        buildings=args.buildings or ([args.building] if args.building else [])
        if not buildings:parser.error("repair-routing requires explicit --building or --buildings")
        if len(buildings)==1:result=repair_routing_building(args.project_root,buildings[0],args.last_seed_index)
        else:
            from concurrent.futures import ProcessPoolExecutor,as_completed
            rows=[]
            with ProcessPoolExecutor(max_workers=args.workers,initializer=_layout_worker_init) as pool:
                futures=[pool.submit(_routing_repair_worker,(args.project_root,bid,args.last_seed_index)) for bid in buildings]
                for future in as_completed(futures):
                    row=future.result();rows.append(row);print(json.dumps(row),flush=True)
            result=dict(status="ROUTING_REPLAY_TERMINAL",items=rows,counts=dict(collections.Counter(r["status"] for r in rows)),layout_only=True,physics_candidate_created=False,READY=False)
            write(args.project_root/"pipeline/v4/LAYOUT_ROUTING_REPAIR_V1_RESULT.json",result)
    elif args.command in ("metadata","precompute"):
        if not args.building or not args.layout_only:parser.error("metadata/precompute require --building and explicit --layout-only")
        if args.building=="all":
            if args.command!="precompute" or args.domain or args.output:parser.error("all builds require precompute with default per-building outputs/domains")
            from concurrent.futures import ProcessPoolExecutor,as_completed
            buildings=[r["building"] for r in sorted(read(args.project_root/"pipeline/v4/V4_BUILDING_PLAN.json")["buildings"],key=lambda r:(r["grid"],int(r["building"])))]
            rows=[]
            with ProcessPoolExecutor(max_workers=args.workers,initializer=_layout_worker_init) as pool:
                futures={pool.submit(_layout_worker,(args.project_root,bid,config_file(args.project_root))):bid for bid in buildings}
                for future in as_completed(futures):
                    row=future.result();rows.append(row);print(json.dumps(row),flush=True)
            result=dict(status="LAYOUT_ONLY_BATCH_TERMINAL",buildings=len(rows),counts=dict(collections.Counter(r["status"] for r in rows)),items=rows,layout_only=True,physics_candidate_created=False,READY=False,
                input_sha={str(p):digest(p) for p in (Path(__file__),args.project_root/"pipeline/v4/V4_BUILDING_PLAN.json",args.project_root/"pipeline/v4/NATIVE_STAIR_PREGEN_V1/RESULT.json")})
            write(args.project_root/"pipeline/v4/LAYOUT_PRECOMPUTE_BATCH_V1_RESULT.json",result)
        else:
            result=precompute_building(args.project_root,args.building,args.output,args.domain,args.command=="metadata")
    elif args.command == "place":
        if not args.building or not args.domain:
            parser.error("place requires --building and --domain containing actual finalized geometry")
        config_path=config_file(args.project_root);plan_path=args.project_root/"pipeline/v4/V4_BUILDING_PLAN.json";pools_path=args.project_root/"pipeline/v4/ROLE_POOLS_V4.json"
        config=read(config_path)
        plan=next(r for r in read(plan_path)["buildings"] if r["building"]==args.building)
        result=place_instances(config,read(args.domain),read(pools_path)["pools"],plan,args.attempt)
        input_sha={str(p):digest(p) for p in (config_path,plan_path,pools_path,args.domain,Path(__file__))}
        result["input_sha"]=input_sha
        result["collision_qualification"]=read(args.domain).get("collision_qualification","C1_C5_NOT_EXECUTED")
        output=args.output or args.project_root/"runs"/args.building/"v4"
        input_id=hashlib.sha256(json.dumps(input_sha,sort_keys=True).encode()).hexdigest()[:12]
        attempt_path=output/f"LAYOUT_ATTEMPT_{args.attempt}_{VERSION.replace('@','_').replace('.','_')}_{input_id}.json"
        write_new(attempt_path,result)
        write(output/"LAYOUT.json",result)
        write(output/"SPATIAL_DOMAINS.json",read(args.domain))
        import resource
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
        receipt=dict(input_sha=input_sha,output_sha={str(attempt_path):digest(attempt_path)},exit_code=0 if result["status"]=="LAYOUT_PASS" else 1,
            peak_memory_bytes=peak,elapsed_s=time.monotonic()-started,status=result["status"],
            measured=dict(actual_instance_count=result["actual_instance_count"],unit_count=len(result["units"]),layout_seed_index=args.attempt))
        write_new(attempt_path.with_name(attempt_path.stem+"_RECEIPT.json"),receipt)
        result=dict(status=result["status"],actual_instance_count=result["actual_instance_count"],unit_count=len(result["units"]),
            failed_rules=[dict(rule=r["rule"],failed_bays=r.get("failed_bays"),failures=r.get("failures",[])[:2]) for r in result["audit"]["rules"] if r["status"]!="PASS"])
    else:
        if not args.building or not args.domain or not args.stair_metadata:
            parser.error("stairs requires --building, --domain (measured shell data) and --stair-metadata (qualified real outputs)")
        plan=next(r for r in read(args.project_root/"pipeline/v4/V4_BUILDING_PLAN.json")["buildings"] if r["building"]==args.building)
        records=[dict(metadata_path=p,metadata=read(p)) for p in args.stair_metadata]
        if args.presentation_stages:
            if len(args.presentation_stages)!=len(records):parser.error("--presentation-stages must match --stair-metadata count/order")
            for record,stage in zip(records,args.presentation_stages):record["asset_stage"]=stage
        spec=place_stairs(plan,records,read(args.domain),args.draft_stairs_for_layout,args.layout_only)
        domain=make_domain(plan,read(args.domain),spec["stairs"])
        output=args.output or args.project_root/"runs"/args.building/"v4"
        write(output/"stairs_spec.json",spec)
        write(output/"SPATIAL_DOMAINS.json",domain)
        result=dict(status="ACTUAL_STAIR_LAYOUT_AUTHORED_C1_C5_NOT_EXECUTED",stairs=len(spec["stairs"]),
            net_mezzanine_fraction=domain["actual_net_mezzanine_fraction"],searched_candidates=spec["searched_candidates"])
    print(json.dumps({**result,"elapsed_s":time.monotonic()-started},ensure_ascii=True),flush=True)
    if result.get("status")=="LAYOUT_FAIL":sys.exit(1)


if __name__ == "__main__":
    main()
