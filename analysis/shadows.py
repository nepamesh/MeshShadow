"""Dead-zone (shadow region) detection and lifecycle management.

A "dead zone" is a contiguous region of grid cells whose shadow score exceeds
`SHADOW_THRESHOLD`. Detection uses `scipy.ndimage.label` to find 4-connected
components on the boolean mask, filters out blobs smaller than `min_cells`,
and emits one zone record per surviving component.

`update_dead_zones` is the upsert path: it matches each newly detected zone
against the most-overlapping existing zone (>30% cell overlap → considered
the same zone, updated in place). Existing zones with no match are deactivated
rather than deleted, so historical references stay valid.

Zone names follow the pattern "<compass-dir> Shadow (<distance>mi)" relative
to the mesh center, with " #2", " #3" suffixes for collisions.
"""

import logging
import math
import time

import gc
import numpy as np
from scipy import ndimage

from database.store import DataStore

log = logging.getLogger(__name__)


def _direction_from_center(lat, lon, center_lat, center_lon):
    """Return one of N/NE/E/SE/S/SW/W/NW for the bearing from center to (lat, lon)."""
    dlat = lat - center_lat
    dlon = lon - center_lon
    angle = math.degrees(math.atan2(dlon, dlat))
    if angle < 0:
        angle += 360

    directions = [
        (0, "N"), (45, "NE"), (90, "E"), (135, "SE"),
        (180, "S"), (225, "SW"), (270, "W"), (315, "NW"), (360, "N"),
    ]
    for i in range(len(directions) - 1):
        mid = (directions[i][0] + directions[i + 1][0]) / 2
        if angle < mid:
            return directions[i][1]
    return "N"


def _distance_km(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def name_dead_zone(center_lat, center_lon, mesh_center_lat, mesh_center_lon, existing_names):
    """Generate a human-friendly zone name like 'NE Shadow (3.2mi)'.

    Adds ' #2', ' #3', ... if the base name already exists in `existing_names`
    (case-insensitive) so persisted names stay unique within the active set.
    """
    direction = _direction_from_center(center_lat, center_lon, mesh_center_lat, mesh_center_lon)
    distance = _distance_km(center_lat, center_lon, mesh_center_lat, mesh_center_lon)

    distance_mi = distance * 0.621371
    base_name = f"{direction} Shadow ({distance_mi:.1f}mi)"

    name = base_name
    counter = 2
    while name.lower() in [n.lower() for n in existing_names]:
        name = f"{base_name} #{counter}"
        counter += 1

    return name


def detect_dead_zones(store: DataStore, shadow_threshold: float, min_cells: int):
    """Find connected dead-zone blobs on the current shadow grid.

    Returns a list of dicts (`cells`, `cell_count`, `area_km2`, `center_lat/lon`,
    `avg_shadow`, `max_shadow`) sorted by area descending. Pure read — does
    not write to the database; `update_dead_zones` handles persistence.
    """
    grid_meta = store.get_grid_metadata()
    if not grid_meta:
        return []

    rows = grid_meta["rows"]
    cols = grid_meta["cols"]

    # Memory-efficient: only fetch the 3 columns we need, not full rows
    shadow_data = store.get_grid_shadow_scores()
    if not shadow_data:
        return []

    shadow_grid = np.zeros((rows, cols), dtype=np.float32)
    lat_grid = np.zeros((rows, cols), dtype=np.float32)
    lon_grid = np.zeros((rows, cols), dtype=np.float32)
    for r, c, score, lat, lon in shadow_data:
        if 0 <= r < rows and 0 <= c < cols:
            shadow_grid[r, c] = score
            lat_grid[r, c] = lat
            lon_grid[r, c] = lon
    del shadow_data
    gc.collect()

    is_shadow = shadow_grid >= shadow_threshold
    labeled, num_features = ndimage.label(is_shadow)

    cell_area_km2 = (grid_meta["cell_size_m"] / 1000.0) ** 2
    zones = []

    for zone_label in range(1, num_features + 1):
        zone_mask = labeled == zone_label
        zone_cells = list(zip(*np.where(zone_mask)))

        if len(zone_cells) < min_cells:
            continue

        shadow_values = [float(shadow_grid[r, c]) for r, c in zone_cells]
        lats = [float(lat_grid[r, c]) for r, c in zone_cells if lat_grid[r, c] != 0]
        lons = [float(lon_grid[r, c]) for r, c in zone_cells if lon_grid[r, c] != 0]

        if not lats:
            continue

        zones.append({
            "cells": [(r, c) for r, c in zone_cells],
            "cell_count": len(zone_cells),
            "area_km2": len(zone_cells) * cell_area_km2,
            "center_lat": sum(lats) / len(lats),
            "center_lon": sum(lons) / len(lons),
            "avg_shadow": sum(shadow_values) / len(shadow_values),
            "max_shadow": max(shadow_values),
        })

    del shadow_grid, lat_grid, lon_grid, labeled
    gc.collect()

    zones.sort(key=lambda z: z["area_km2"], reverse=True)
    log.info("Detected %d dead zones (from %d connected components, threshold=%.2f)",
             len(zones), num_features, shadow_threshold)
    return zones


def _zones_overlap(cells_a, cells_b):
    """Fraction of cells_a that also appear in cells_b (asymmetric Jaccard-ish)."""
    set_a = set(cells_a)
    set_b = set(cells_b)
    intersection = len(set_a & set_b)
    if not set_a:
        return 0.0
    return intersection / len(set_a)


def update_dead_zones(store: DataStore, shadow_threshold: float, min_cells: int,
                       mesh_center_lat: float, mesh_center_lon: float):
    """Reconcile freshly detected zones with stored ones.

    For each newly detected zone, find the existing active zone with the
    highest cell-overlap. If overlap >30% → update that zone in place
    (preserves id and name). Otherwise insert a new zone with a generated
    name. Existing active zones with no match are deactivated.
    """
    new_zones = detect_dead_zones(store, shadow_threshold, min_cells)
    existing = store.get_dead_zones(active_only=False)

    existing_cell_map = {}
    for ez in existing:
        ez_cells = store.get_dead_zone_cells(ez["id"])
        existing_cell_map[ez["id"]] = [(c["grid_row"], c["grid_col"]) for c in ez_cells]

    matched_existing = set()
    existing_names = [e["name"] for e in existing]

    for nz in new_zones:
        best_match = None
        best_overlap = 0.0

        for ez in existing:
            if ez["id"] in matched_existing:
                continue
            overlap = _zones_overlap(nz["cells"], existing_cell_map.get(ez["id"], []))
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = ez

        if best_match and best_overlap > 0.3:
            matched_existing.add(best_match["id"])
            store.update_dead_zone(
                best_match["id"],
                center_lat=nz["center_lat"],
                center_lon=nz["center_lon"],
                area_km2=nz["area_km2"],
                cell_count=nz["cell_count"],
                avg_shadow_score=nz["avg_shadow"],
                max_shadow_score=nz["max_shadow"],
                active=1,
            )
            store.set_dead_zone_cells(best_match["id"], nz["cells"])
            log.debug("Updated dead zone '%s' (%d cells)", best_match["name"], nz["cell_count"])
        else:
            zone_name = name_dead_zone(nz["center_lat"], nz["center_lon"],
                                        mesh_center_lat, mesh_center_lon, existing_names)
            zone_id = store.insert_dead_zone(
                zone_name, nz["center_lat"], nz["center_lon"],
                nz["area_km2"], nz["cell_count"], nz["avg_shadow"], nz["max_shadow"],
            )
            store.set_dead_zone_cells(zone_id, nz["cells"])
            existing_names.append(zone_name)
            log.info("New dead zone: '%s' (%.2f km2, %d cells)", zone_name, nz["area_km2"], nz["cell_count"])

    for ez in existing:
        if ez["id"] not in matched_existing and ez["active"]:
            store.deactivate_dead_zone(ez["id"])
            log.info("Deactivated dead zone: '%s'", ez["name"])
