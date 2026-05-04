import hashlib
import logging
import math
import time

import gc
import numpy as np
import config

from database.store import DataStore

log = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0
DEG_PER_KM_LAT = 1.0 / 111.32


def deg_per_km_lon(lat_deg: float) -> float:
    return 1.0 / (111.32 * math.cos(math.radians(lat_deg)))


def haversine(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def compute_bounding_box(nodes: list, padding_km: float):
    lats = [n["latitude"] for n in nodes]
    lons = [n["longitude"] for n in nodes]
    min_lat = min(lats)
    max_lat = max(lats)
    min_lon = min(lons)
    max_lon = max(lons)
    center_lat = (min_lat + max_lat) / 2.0
    pad_lat = padding_km * DEG_PER_KM_LAT
    pad_lon = padding_km * deg_per_km_lon(center_lat)
    return (min_lat - pad_lat, max_lat + pad_lat, min_lon - pad_lon, max_lon + pad_lon)


def build_grid_params(bbox, cell_size_m: int):
    min_lat, max_lat, min_lon, max_lon = bbox
    center_lat = (min_lat + max_lat) / 2.0
    dlat = cell_size_m / 111320.0
    dlon = cell_size_m / (111320.0 * math.cos(math.radians(center_lat)))
    rows = max(1, int((max_lat - min_lat) / dlat))
    cols = max(1, int((max_lon - min_lon) / dlon))
    return {
        "min_lat": min_lat, "max_lat": max_lat,
        "min_lon": min_lon, "max_lon": max_lon,
        "dlat": dlat, "dlon": dlon,
        "rows": rows, "cols": cols,
        "cell_size_m": cell_size_m,
    }


def grid_cell_centers(grid):
    lats = grid["min_lat"] + (np.arange(grid["rows"]) + 0.5) * grid["dlat"]
    lons = grid["min_lon"] + (np.arange(grid["cols"]) + 0.5) * grid["dlon"]
    lat_grid, lon_grid = np.meshgrid(lats, lons, indexing="ij")
    return lat_grid, lon_grid


def calculate_coverage_scores(grid, nodes: list, max_range_km: float, store: DataStore):
    rows, cols = grid["rows"], grid["cols"]
    lat_centers, lon_centers = grid_cell_centers(grid)

    coverage = np.zeros((rows, cols), dtype=np.float64)
    reachable = np.zeros((rows, cols), dtype=np.int32)

    for node in nodes:
        n_lat = node["latitude"]
        n_lon = node["longitude"]

        obs_count = store.get_link_count_for_node(node["node_id"], hours=config.NODE_ACTIVE_HOURS)
        # Reliability boosts score but floor is 0.5 — a node with GPS position
        # exists at this location and provides coverage regardless of observations
        reliability = min(1.0, 0.5 + obs_count / 100.0)

        dlat = np.radians(lat_centers - n_lat)
        dlon = np.radians(lon_centers - n_lon)
        a = np.sin(dlat / 2) ** 2 + math.cos(math.radians(n_lat)) * np.cos(np.radians(lat_centers)) * np.sin(dlon / 2) ** 2
        dist_km = EARTH_RADIUS_KM * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

        in_range = dist_km <= max_range_km
        # Linear falloff: 1.0 at the node, 0.0 at max_range
        contribution = np.where(in_range, reliability * (1.0 - dist_km / max_range_km), 0.0)
        coverage += contribution
        reachable += in_range.astype(np.int32)

    # Cap at 1.0 — any cell near a node with decent signal should be fully covered
    coverage = np.minimum(1.0, coverage)

    return coverage, reachable


def calculate_observation_density(grid, store: DataStore, hours: int = 24):
    rows, cols = grid["rows"], grid["cols"]
    density = np.zeros((rows, cols), dtype=np.int32)

    links = store.get_latest_links(hours=hours)

    for link in links:
        a_lat, a_lon = link.get("node_a_lat"), link.get("node_a_lon")
        b_lat, b_lon = link.get("node_b_lat"), link.get("node_b_lon")
        if not all([a_lat, a_lon, b_lat, b_lon]):
            continue

        obs_count = link.get("obs_count", 1)

        # Sample points along the link line
        n_samples = max(2, int(link.get("avg_distance", 1.0) / (grid["cell_size_m"] / 1000.0)))
        n_samples = min(n_samples, 200)

        for i in range(n_samples):
            t = i / (n_samples - 1) if n_samples > 1 else 0.5
            lat = a_lat + t * (b_lat - a_lat)
            lon = a_lon + t * (b_lon - a_lon)

            row = int((lat - grid["min_lat"]) / grid["dlat"])
            col = int((lon - grid["min_lon"]) / grid["dlon"])

            if 0 <= row < rows and 0 <= col < cols:
                density[row, col] += obs_count

    return density


def calculate_shadow_scores(coverage: np.ndarray, obs_density: np.ndarray, reachable: np.ndarray):
    """Calculate shadow scores.

    Cells with low coverage that are within range of nodes but have no observations
    are likely dead zones — they should score HIGH, not zero.

    Only cells that are completely outside any node's range AND have no observations
    get reduced confidence (we genuinely have no information about them).
    """
    raw_shadow = 1.0 - coverage

    # Confidence: high if cell is within range of at least one node OR has observations
    has_reachable = reachable > 0
    has_obs = obs_density > 0

    # Cells within node range: full confidence (lack of observations IS the signal)
    # Cells outside all node range with no observations: low confidence (no data)
    # Cells outside range but with some observations: moderate confidence
    confidence = np.where(
        has_reachable, 1.0,
        np.where(has_obs, 0.5, 0.1)
    )

    shadow = raw_shadow * confidence
    return shadow


# Module-level cache for skip-if-unchanged
_last_fingerprint = None


def _compute_fingerprint(nodes, store):
    """Hash node positions + link count to detect meaningful changes."""
    parts = []
    for n in sorted(nodes, key=lambda x: x["node_id"]):
        # Round to ~100m precision — small GPS jitter shouldn't trigger recalc
        parts.append(f"{n['node_id']}:{round(n['latitude'], 3)}:{round(n['longitude'], 3)}")
    # Include link observation count (affects observation density)
    link_count = store._fetchone(
        "SELECT COUNT(*) as c FROM link_observations WHERE timestamp > ?",
        (int(time.time()) - (config.NODE_ACTIVE_HOURS * 3600),))
    parts.append(f"links:{link_count['c'] if link_count else 0}")
    raw = "|".join(parts)
    return hashlib.md5(raw.encode()).hexdigest()


def recalculate(store: DataStore, cell_size_m: int, padding_km: float, max_range_km: float):
    global _last_fingerprint

    nodes = store.get_nodes_with_positions()
    if len(nodes) < 2:
        log.warning("Need at least 2 nodes with positions for coverage analysis")
        return None

    fingerprint = _compute_fingerprint(nodes, store)
    if fingerprint == _last_fingerprint:
        log.info("Coverage skip: %d nodes unchanged since last recalc", len(nodes))
        return None

    log.info("Recalculating coverage grid with %d nodes (fingerprint changed)", len(nodes))
    start = time.time()

    bbox = compute_bounding_box(nodes, padding_km)
    grid = build_grid_params(bbox, cell_size_m)

    log.info("Grid: %d rows x %d cols = %d cells (bbox: %.4f-%.4f, %.4f-%.4f)",
             grid["rows"], grid["cols"], grid["rows"] * grid["cols"],
             bbox[0], bbox[1], bbox[2], bbox[3])

    # Cap grid size to prevent memory issues
    if grid["rows"] * grid["cols"] > 1_000_000:
        log.warning("Grid too large (%d cells), increasing cell size", grid["rows"] * grid["cols"])
        scale = math.ceil(math.sqrt(grid["rows"] * grid["cols"] / 500_000))
        grid = build_grid_params(bbox, cell_size_m * scale)
        log.info("Adjusted grid: %d rows x %d cols", grid["rows"], grid["cols"])

    store.save_grid_metadata(
        bbox[0], bbox[1], bbox[2], bbox[3],
        grid["cell_size_m"], grid["rows"], grid["cols"],
    )

    coverage, reachable = calculate_coverage_scores(grid, nodes, max_range_km, store)
    obs_density = calculate_observation_density(grid, store)
    shadow = calculate_shadow_scores(coverage, obs_density, reachable)

    lat_centers, lon_centers = grid_cell_centers(grid)

    # Persist to database in batches
    now = int(time.time())
    batch = []
    for r in range(grid["rows"]):
        for c in range(grid["cols"]):
            batch.append((
                r, c,
                float(lat_centers[r, c]), float(lon_centers[r, c]),
                None,  # elevation filled later
                float(coverage[r, c]),
                float(shadow[r, c]),
                int(obs_density[r, c]),
                int(reachable[r, c]),
                0,  # obstructed_nodes filled by terrain analysis
                now,
            ))
            if len(batch) >= 5000:
                store.upsert_grid_cells_bulk(batch)
                batch = []
    if batch:
        store.upsert_grid_cells_bulk(batch)

    elapsed = time.time() - start
    log.info("Coverage recalculation complete in %.1fs: %d cells, avg coverage=%.2f, avg shadow=%.2f",
             elapsed, grid["rows"] * grid["cols"],
             float(coverage.mean()), float(shadow.mean()))

    # Free large arrays to prevent OOM
    del coverage, shadow, obs_density, reachable, lat_centers, lon_centers
    gc.collect()

    _last_fingerprint = fingerprint
    return {"grid": grid}
