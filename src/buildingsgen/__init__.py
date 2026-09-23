"""Generate, connect and accept multi-storey buildings of any type.

Public surface:

    load_profile / available_profiles   building-type configuration
    Workspace                           where one run writes
    scan_building / connect_storeys     inspect a source, add its stairs
    bake_connected / accept_building    rasterise, then run the gates
    StandabilityV1                      the pinned body proxy (not configurable)
"""

from .config import BuildingProfile, ProfileError, available_profiles, load_profile
from .pipeline import (
    Workspace,
    accept_building,
    bake_connected,
    connect_storeys,
    run_all,
    scan_building,
)
from .voxel.standability import PinnedSemanticsError, StandabilityV1

__version__ = "0.1.0"

__all__ = [
    "BuildingProfile",
    "PinnedSemanticsError",
    "ProfileError",
    "StandabilityV1",
    "Workspace",
    "__version__",
    "accept_building",
    "available_profiles",
    "bake_connected",
    "connect_storeys",
    "load_profile",
    "run_all",
    "scan_building",
]
