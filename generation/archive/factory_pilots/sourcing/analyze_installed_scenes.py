#!/usr/bin/env python3
"""Rank the installed MolmoSpaces scenes using explicit industrial evidence.

This is a screening tool, not an acceptance shortcut.  It joins the scene asset IDs
with the official Objathor annotations and records every matched term so the final
selection can be audited against geometry and renders.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import re


# Operational anchors deliberately omit bare ``industrial``, ``machine`` and
# furniture styles.  In Objathor descriptions those tokens also match coffee
# machines, machine-learning books and domestic "industrial-style" furniture.
OPERATIONAL_PHRASES = (
    "assembly line", "loading dock", "loading bay", "machine shop", "workshop",
    "forklift", "conveyor", "pallet", "workbench", "tool bench", "tool chest",
    "tool trolley", "tool cart", "tool cabinet", "toolbox", "pegboard",
    "electric drill", "power drill", "cordless drill", "industrial drill",
    "caliper", "micrometer", "multimeter", "locking plier", "pliers",
    "screwdriver", "hand tool", "handheld saw", "control panel",
    "electrical panel", "compressor", "generator", "impeller", "pipe fitting",
    "mechanical component", "aluminum extrusion", "caster wheel",
)
WAREHOUSE_PHRASES = (
    "warehouse", "storage rack", "warehouse rack", "shipping crate",
    "storage crate", "wooden crate", "platform cart", "hand truck",
    "oil drum", "steel drum", "material bin", "parts bin", "storage bin",
    "metal shelving", "packing station", "packing table",
)
INDUSTRIAL_PHRASES = OPERATIONAL_PHRASES + WAREHOUSE_PHRASES
FALSE_INDUSTRIAL_OBJECT_TYPES = {
    "book", "coffeemachine", "objacoffeemachine", "coffeetable", "tvstand",
    "armchair", "stool", "pendantlight", "lightfixture", "objalightfixture",
    "objaledlightfixture", "objalantern", "objaartwork", "objasculpture",
}
FALSE_INDUSTRIAL_TYPE_FRAGMENTS = (
    "chair", "sofa", "couch", "table", "desk", "bed", "dresser", "cabinet",
    "shelf", "television", "toaster", "microwave", "oven", "refrigerator",
    "fridge", "airpurifier", "radio", "keypad", "security", "projector",
)
RESIDENTIAL_PHRASES = (
    "bedroom", "bathroom", "kitchen", "nursery", "living room", "bed ",
    "bathtub", "toilet", "sofa", "couch", "crib", "nightstand", "wardrobe",
    "dining table", "television stand", "coffee table",
)
SCHOOL_PHRASES = (
    "classroom", "school desk", "blackboard", "whiteboard", "student desk",
    "lectern", "podium",
)
OFFICE_PHRASES = (
    "office", "conference room", "office chair", "filing cabinet", "cubicle",
    "printer", "copier",
)


def normalize(value: object) -> str:
    text = str(value or "").replace("_", " ").replace("-", " ").lower()
    return re.sub(r"\s+", " ", text).strip()


def annotation_text(asset_id: str, record: dict | None) -> str:
    record = record or {}
    values: list[object] = [
        asset_id,
        record.get("objectType"),
        record.get("category"),
        record.get("synset"),
        record.get("description_auto"),
        record.get("description"),
        record.get("description_long"),
    ]
    short = record.get("description_short")
    if isinstance(short, dict):
        values.extend(short.values())
    return normalize(" ".join(str(value or "") for value in values))


def matches(text: str, phrases: tuple[str, ...]) -> list[str]:
    padded = f" {text} "
    return sorted({phrase for phrase in phrases if f" {phrase} " in padded or phrase in text})


def asset_groups(asset_id: str, record: dict | None) -> dict[str, list[str]]:
    text = annotation_text(asset_id, record)
    object_type = normalize((record or {}).get("objectType")).replace(" ", "")
    excluded = (object_type in FALSE_INDUSTRIAL_OBJECT_TYPES
                or any(fragment in object_type for fragment in FALSE_INDUSTRIAL_TYPE_FRAGMENTS))
    operational = [] if excluded else matches(text, OPERATIONAL_PHRASES)
    warehouse = [] if excluded else matches(text, WAREHOUSE_PHRASES)
    return {
        "operational": operational,
        "warehouse": warehouse,
        "industrial": sorted(set(operational + warehouse)),
        "residential": matches(text, RESIDENTIAL_PHRASES),
        "school": matches(text, SCHOOL_PHRASES),
        "office": matches(text, OFFICE_PHRASES),
    }


def scene_assets(scene: dict) -> Counter[str]:
    counts: Counter[str] = Counter()
    for room in scene.get("room_distribution", {}).values():
        if not isinstance(room, dict):
            continue
        counts.update({str(k): int(v) for k, v in room.get("asset_id_counts", {}).items()})
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene-types", type=Path, required=True)
    parser.add_argument("--object-metadata", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    types_doc = json.loads(args.scene_types.read_text(encoding="utf-8"))
    with gzip.open(args.object_metadata, "rt", encoding="utf-8") as stream:
        metadata = json.load(stream)

    rows = []
    missing_assets: set[str] = set()
    for scene_name, scene in sorted(types_doc["scenes"].items()):
        counts = scene_assets(scene)
        evidence: dict[str, list[dict]] = defaultdict(list)
        occurrence_counts = Counter()
        unique_counts = Counter()
        for asset_id, count in counts.items():
            record = metadata.get(asset_id)
            if record is None:
                missing_assets.add(asset_id)
            groups = asset_groups(asset_id, record)
            for group, terms in groups.items():
                if not terms:
                    continue
                occurrence_counts[group] += count
                unique_counts[group] += 1
                evidence[group].append({
                    "asset_id": asset_id,
                    "count": count,
                    "matched_terms": terms,
                    "object_type": (record or {}).get("objectType"),
                    "category": (record or {}).get("category"),
                    "description": (record or {}).get("description"),
                })

        room_rows = []
        for room_name, room in sorted(scene.get("room_distribution", {}).items()):
            if room_name == "unassigned" or not isinstance(room, dict):
                continue
            room_occurrences = Counter()
            room_unique = Counter()
            for asset_id, count in room.get("asset_id_counts", {}).items():
                for group, terms in asset_groups(str(asset_id), metadata.get(str(asset_id))).items():
                    if terms:
                        room_occurrences[group] += int(count)
                        room_unique[group] += 1
            room_rows.append({
                "room": room_name,
                "asset_count": room.get("asset_count"),
                "occurrences": dict(room_occurrences),
                "unique_assets": dict(room_unique),
                "industrial_core": (
                    room_occurrences["operational"] >= 8
                    and room_unique["operational"] >= 4
                    and room_occurrences["industrial"] >= room_occurrences["residential"]
                    and room_occurrences["school"] <= 1
                ),
            })

        # Ranking only. Final eligibility also requires geometry and visual review.
        score = (
            12 * occurrence_counts["operational"]
            + 5 * occurrence_counts["warehouse"]
            + 2 * unique_counts["industrial"]
            - 4 * occurrence_counts["residential"]
            - 5 * occurrence_counts["school"]
        )
        industrial_core_rooms = [room for room in room_rows if room["industrial_core"]]
        screen_pass = (
            occurrence_counts["operational"] >= 12
            and unique_counts["operational"] >= 5
            and occurrence_counts["industrial"] >= occurrence_counts["residential"]
            and occurrence_counts["school"] <= 2
            and occurrence_counts["industrial"] >= 3 * occurrence_counts["school"]
            and len(industrial_core_rooms) >= 1
        )
        rows.append({
            "scene": scene_name,
            "automatic_residential_classifier_type": scene.get("type"),
            "asset_count": scene.get("asset_count"),
            "room_count": len([r for r in scene.get("room_distribution", {}) if r != "unassigned"]),
            "occurrences": dict(occurrence_counts),
            "unique_assets": dict(unique_counts),
            "score": score,
            "text_screen_pass": screen_pass,
            "industrial_core_room_count": len(industrial_core_rooms),
            "room_evidence": room_rows,
            "evidence": {key: sorted(value, key=lambda x: (-x["count"], x["asset_id"]))
                         for key, value in evidence.items()},
        })

    rows.sort(key=lambda row: (-row["score"], row["scene"]))
    result = {
        "schema_version": 1,
        "purpose": "text-evidence screening only; geometry and visual gates remain mandatory",
        "rules": {
            "operational_phrases": OPERATIONAL_PHRASES,
            "warehouse_phrases": WAREHOUSE_PHRASES,
            "industrial_phrases": INDUSTRIAL_PHRASES,
            "false_industrial_object_types": sorted(FALSE_INDUSTRIAL_OBJECT_TYPES),
            "false_industrial_type_fragments": list(FALSE_INDUSTRIAL_TYPE_FRAGMENTS),
            "residential_phrases": RESIDENTIAL_PHRASES,
            "school_phrases": SCHOOL_PHRASES,
            "office_phrases_separate_from_school": OFFICE_PHRASES,
            "screen_pass": (
                "operational occurrences >=12; unique operational assets >=5; industrial "
                "occurrences >= residential occurrences; school occurrences <=2 and industrial "
                "occurrences >=3*school occurrences; at least one room has >=8 operational "
                "occurrences, >=4 unique operational assets, industrial >= residential, school <=1"
            ),
        },
        "scene_count": len(rows),
        "text_screen_pass_count": sum(row["text_screen_pass"] for row in rows),
        "metadata_asset_count": len(metadata),
        "missing_annotation_asset_count": len(missing_assets),
        "missing_annotation_assets": sorted(missing_assets),
        "ranked_scenes": rows,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    for row in rows[: args.limit]:
        industrial = row["occurrences"].get("industrial", 0)
        residential = row["occurrences"].get("residential", 0)
        operational = row["occurrences"].get("operational", 0)
        descriptions = [
            e.get("description") or e["asset_id"]
            for e in row["evidence"].get("industrial", [])[:5]
        ]
        print(
            f"{row['scene']}\tscore={row['score']}\tpass={row['text_screen_pass']}\t"
            f"operational={operational}\tindustrial={industrial}\tresidential={residential}\t"
            f"core_rooms={row['industrial_core_room_count']}\trooms={row['room_count']}\t{descriptions}"
        )


if __name__ == "__main__":
    main()
