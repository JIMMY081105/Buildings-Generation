"""Command line entry point.

    buildingsgen types                          list building types
    buildingsgen scan    SOURCE --type factory  what storeys does it have?
    buildingsgen stairs  --rise 3.0 --type school   design a flight, no building
    buildingsgen connect SOURCE --type house    add stairs into an overlay
    buildingsgen bake    STAGE  CACHE           rasterise a stage
    buildingsgen accept  --workspace DIR        run the gates
    buildingsgen run     SOURCE --type factory  connect, bake, accept

Every subcommand prints a human summary and writes a machine-readable record
under the workspace, so a result can be cited later without rerunning it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .acceptance.runner import summarise
from .config import available_profiles, load_profile
from .pipeline import (
    Workspace,
    accept_building,
    connect_storeys,
    run_all,
    scan_building,
)
from .stairs.spec import design_stair, traversal_path


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--type", dest="building_type", default="generic",
        help="building-type profile (default: generic)",
    )
    parser.add_argument(
        "--config-root", type=Path, default=None,
        help="directory of building-type YAML files",
    )
    parser.add_argument(
        "--ground-z", type=float, default=0.0,
        help=(
            "flat terrain height in metres; pass 'nan' if the building authors "
            "its own ground slab"
        ),
    )


def _workspace(args, source: Path | None) -> Workspace:
    if args.workspace:
        root = Path(args.workspace)
        name = root.name
    else:
        name = (source.stem if source else "building")
        root = Path("out") / name
    return Workspace(root=root, name=name)


def _ground(args) -> float | None:
    value = args.ground_z
    return None if value != value else float(value)  # NaN means "no terrain"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="buildingsgen", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("types", help="list available building-type profiles")

    scan = sub.add_parser("scan", help="bake a source building and report its storeys")
    scan.add_argument("source", type=Path)
    scan.add_argument("--workspace", type=Path, default=None)
    _add_common(scan)

    stairs = sub.add_parser("stairs", help="design a flight for a storey height")
    stairs.add_argument("--rise", type=float, required=True, help="storey-to-storey rise, metres")
    stairs.add_argument("--family", default=None, help="straight | l_shaped | u_shaped")
    stairs.add_argument("--out", type=Path, default=None, help="write the asset to this .usda")
    _add_common(stairs)

    connect = sub.add_parser("connect", help="design, place and author stairs into an overlay")
    connect.add_argument("source", type=Path)
    connect.add_argument("--workspace", type=Path, default=None)
    _add_common(connect)

    bake = sub.add_parser("bake", help="rasterise a stage into a cache")
    bake.add_argument("stage", type=Path)
    bake.add_argument("cache", type=Path)

    accept = sub.add_parser("accept", help="run the acceptance gates")
    accept.add_argument("--workspace", type=Path, required=True)
    accept.add_argument(
        "--stair-prim", default=None, help="prim path of the stair, e.g. /World/Stair_0"
    )
    accept.add_argument(
        "--keep-going", action="store_true", help="run every gate even after a FAIL"
    )
    _add_common(accept)

    run = sub.add_parser("run", help="connect, bake and accept in one go")
    run.add_argument("source", type=Path)
    run.add_argument("--workspace", type=Path, default=None)
    run.add_argument("--keep-going", action="store_true")
    _add_common(run)

    args = parser.parse_args(argv)

    if args.command == "types":
        for name in available_profiles(getattr(args, "config_root", None)):
            profile = load_profile(name, getattr(args, "config_root", None))
            summary = (
                profile.description.strip().splitlines()[0] if profile.description else ""
            )
            print(f"{name:<12} {summary}")
        return 0

    profile = load_profile(args.building_type, args.config_root)

    if args.command == "scan":
        workspace = _workspace(args, args.source) if args.workspace else None
        record = scan_building(
            args.source, profile, workspace=workspace, ground_plane_z_m=_ground(args)
        )
        storeys = record["storeys"]
        print(f"{args.source.name}: {storeys['storey_count']} storey(s), "
              f"{record['standable_area_m2']} m2 standable")
        for storey in storeys["storeys"]:
            print(f"  {storey['label']:<12} z={storey['floor_z_m']:>7.3f} m  "
                  f"{storey['area_m2']:>8.2f} m2")
        if storeys["implausible_separations"]:
            print("  implausible rises:", json.dumps(storeys["implausible_separations"]))
        return 0

    if args.command == "stairs":
        spec = design_stair(args.rise, profile.stair, family=args.family)
        print(json.dumps(spec.as_dict(), indent=2))
        if args.out:
            from .stairs.asset import build_stair_usd

            record = build_stair_usd(spec, args.out)
            print(f"wrote {record['stage']} "
                  f"({record['authored_tread_count']} treads; the upper floor is tread "
                  f"{spec.riser_count})")
        else:
            print(f"traversal path points: {len(traversal_path(spec))}")
        return 0

    if args.command == "bake":
        from .voxel.bake import bake_stage

        report = bake_stage(args.stage, args.cache, verbose=True)
        print(json.dumps({k: v for k, v in report.items() if k != "manifest_arrays"}, indent=2))
        return 0 if report["status"] == "PASS" else 1

    if args.command == "connect":
        workspace = _workspace(args, args.source)
        record = connect_storeys(args.source, workspace, profile, ground_plane_z_m=_ground(args))
        print(f"{len(record['connections'])} stair(s) authored into {record['overlay_stage']}")
        for entry in record["connections"]:
            spec = entry["spec"]
            print(f"  {entry['lower_storey']['label']} -> {entry['upper_storey']['label']}: "
                  f"{spec['family']} {spec['riser_count']}x{spec['riser_m']:.3f} m riser, "
                  f"going {spec['going_m']:.3f} m, width {spec['width_m']:.2f} m")
        print(f"source unmodified: {record['source_unmodified']}")
        return 0

    if args.command == "accept":
        workspace = _workspace(args, None)
        record = accept_building(
            workspace, profile, stair_prim_path=args.stair_prim,
            ground_plane_z_m=_ground(args), keep_going=args.keep_going,
        )
        print(summarise(record))
        return 0 if record["status"] == "PASS" else 1

    if args.command == "run":
        workspace = _workspace(args, args.source)
        record = run_all(
            args.source, workspace, profile,
            ground_plane_z_m=_ground(args), keep_going=args.keep_going,
        )
        print(summarise(record["acceptance"]))
        return 0 if record["acceptance"]["status"] == "PASS" else 1

    parser.error(f"unhandled command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
