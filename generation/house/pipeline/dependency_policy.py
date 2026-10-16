#!/usr/bin/env python3
"""Fail-closed policy for classifying unresolved scene dependencies."""

from __future__ import annotations

from pathlib import Path
import re


DEPENDENCY_CONTRACT_VERSION = 2
STRUCTURE_TOKENS = {
    "building", "ceiling", "column", "door", "floor", "foundation",
    "opening", "partition", "roof", "room", "slab", "stair", "wall",
    "window", "beam",
}
COLLISION_TOKENS = {
    "collider", "collision", "convex", "physics", "physx", "rigidbody",
}
NONCRITICAL_METADATA_EXTENSIONS = {".csv", ".md", ".txt"}


def dependency_tokens(value):
    return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if token}


def classify_missing_dependency(path, evidence=None):
    """Classify one unresolved dependency; uncertainty is always critical."""
    normalized = str(path).replace("\\", "/")
    contexts = []
    for record in evidence or []:
        contexts.append(str(record.get("reference_text", "")))
    path_tokens = dependency_tokens(normalized)
    reference_tokens = dependency_tokens(" ".join(contexts).lower())
    suffix = Path(normalized).suffix.lower()
    if path_tokens & COLLISION_TOKENS:
        category = "collision"
        basis = "path or referring-layer evidence contains collision/physics semantics"
    elif path_tokens & STRUCTURE_TOKENS:
        category = "building_structure"
        basis = "path or referring-layer evidence contains structural semantics"
    elif "/objects/" in normalized.lower() or "/assets/objects/" in normalized.lower():
        category = "furniture_visibility"
        basis = "unresolved asset is below the scene objects tree"
    elif suffix in {".usd", ".usda", ".usdc", ".obj", ".fbx", ".glb", ".gltf"}:
        category = "unclassified_unknown"
        basis = "unresolved geometry/composition asset has no provably safe classification"
    elif suffix in {".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff", ".hdr"}:
        category = "furniture_visibility"
        basis = "unresolved visual asset can change scene-object visibility/appearance"
    elif suffix in NONCRITICAL_METADATA_EXTENSIONS and path_tokens & {
            "readme", "license", "metadata", "documentation"}:
        category = "noncritical_metadata"
        basis = "text dependency is explicitly identified as metadata/documentation"
    else:
        category = "unclassified_unknown"
        basis = "impact cannot be proven noncritical"
    critical = category != "noncritical_metadata"
    return {
        "path": normalized,
        "category": category,
        "critical": critical,
        "classification_basis": basis,
        "candidate_effect": ("FAIL" if critical else "WARNING_ONLY"),
        "reference_evidence": list(evidence or []),
        "reference_semantic_tokens": sorted(reference_tokens),
    }
