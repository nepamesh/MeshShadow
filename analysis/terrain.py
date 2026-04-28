"""Terrain elevation lookup and line-of-sight checks.

Wraps an OpenTopoData-compatible elevation API with a SQLite-backed cache. All
cache keys are `(round(lat, 5), round(lon, 5))` (≈1m precision) — callers must
use the same rounding when reading back, or cache hits will silently miss.

`check_line_of_sight` samples ground elevation along the great-circle path
between a node and a target cell and reports the worst obstruction relative to
the straight-line beam (with a 10m margin for SRTM resolution + Fresnel zone).
"""

import logging
import math
import time

import requests

from database.store import DataStore

log = logging.getLogger(__name__)


class ElevationFetcher:
    """Batched, rate-limited elevation lookup against an OpenTopoData-style API.

    Results are cached in `DataStore` keyed by lat/lon rounded to 5 decimal
    places. The fetcher coalesces requests into batches of `batch_size` and
    waits at least `rate_limit_sec` between HTTP calls to respect public-API
    quotas.
    """

    def __init__(self, store: DataStore, api_url: str, batch_size: int = 100, rate_limit_sec: float = 1.0):
        self.store = store
        self.api_url = api_url
        self.batch_size = batch_size
        self.rate_limit_sec = rate_limit_sec
        self._last_request = 0.0

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit_sec:
            time.sleep(self.rate_limit_sec - elapsed)
        self._last_request = time.time()

    def fetch_elevations(self, coords: list) -> dict:
        """Return `{(round(lat,5), round(lon,5)): elevation_m}` for all coords.

        Cached points are returned immediately; missing points are fetched in
        batches and written back to the cache. On API failure, partial results
        accumulated so far are returned (logged as a warning/error).
        """
        cached = self.store.get_cached_elevations_bulk(coords)
        missing = [(lat, lon) for lat, lon in coords if (round(lat, 5), round(lon, 5)) not in cached]

        if not missing:
            return cached

        log.info("Fetching elevations for %d points (%d cached)", len(missing), len(cached))
        results = dict(cached)

        for i in range(0, len(missing), self.batch_size):
            batch = missing[i:i + self.batch_size]
            locations = "|".join(f"{lat},{lon}" for lat, lon in batch)

            self._rate_limit()
            try:
                resp = requests.get(self.api_url, params={"locations": locations}, timeout=30)
                resp.raise_for_status()
                data = resp.json()

                if data.get("status") == "OK":
                    cache_batch = []
                    for result in data.get("results", []):
                        lat = result["location"]["lat"]
                        lon = result["location"]["lng"]
                        elev = result["elevation"]
                        if elev is not None:
                            results[(round(lat, 5), round(lon, 5))] = elev
                            cache_batch.append((lat, lon, elev))
                    if cache_batch:
                        self.store.cache_elevations_bulk(cache_batch)
                    log.debug("Fetched %d elevations (batch %d-%d)",
                              len(data.get("results", [])), i, i + len(batch))
                else:
                    log.warning("Elevation API returned status: %s", data.get("status"))

            except requests.RequestException as e:
                log.error("Elevation API error: %s", e)
                break

        return results

    def fetch_grid_elevations(self, store: DataStore, grid_meta: dict, batch_limit: int = 0):
        """Progressively populate `coverage_grid.elevation_m` for cells missing it.

        Called by the elevation-fetcher background thread. `batch_limit` caps
        how many cells are fetched per invocation so the API rate limit is
        respected across runs.
        """
        if not grid_meta:
            return

        cells = store.get_grid_cells()
        need_elev = [(c["center_lat"], c["center_lon"]) for c in cells if c["elevation_m"] is None]

        if not need_elev:
            log.info("All grid cell elevations already cached")
            return

        if batch_limit > 0:
            need_elev = need_elev[:batch_limit]

        log.info("Fetching elevations for %d grid cells", len(need_elev))
        elevations = self.fetch_elevations(need_elev)

        updated = 0
        for cell in cells:
            if cell["elevation_m"] is not None:
                continue
            key = (round(cell["center_lat"], 5), round(cell["center_lon"], 5))
            if key in elevations:
                store._execute(
                    "UPDATE coverage_grid SET elevation_m = ? WHERE grid_row = ? AND grid_col = ?",
                    (elevations[key], cell["grid_row"], cell["grid_col"]),
                )
                updated += 1
        log.info("Updated %d grid cells with elevation data", updated)


def check_line_of_sight(node_lat, node_lon, node_elev, cell_lat, cell_lon, cell_elev,
                         elevation_fetcher, antenna_height_m: float = 5.0):
    """Test whether terrain blocks the straight-line path from a node to a cell.

    Samples ground elevation at evenly-spaced points between the endpoints
    (one sample per ~90m of distance, capped at 50) and compares each ground
    height to the linearly interpolated beam height (node antenna -> cell
    ground). Returns `(is_clear, max_obstruction_m, obstruction_lat, obstruction_lon)`.

    A 10m tolerance is applied to absorb SRTM vertical error and the first
    Fresnel-zone clearance budget at typical Meshtastic distances.
    """
    dist_km = _simple_distance(node_lat, node_lon, cell_lat, cell_lon)
    n_samples = max(3, int(dist_km / 0.09))
    n_samples = min(n_samples, 50)

    sample_coords = []
    for i in range(1, n_samples - 1):
        t = i / (n_samples - 1)
        lat = node_lat + t * (cell_lat - node_lat)
        lon = node_lon + t * (cell_lon - node_lon)
        sample_coords.append((lat, lon))

    if not sample_coords:
        return True, 0.0, None, None

    elevations = elevation_fetcher.fetch_elevations(sample_coords)

    node_h = (node_elev or 0) + antenna_height_m
    cell_h = cell_elev or 0

    max_obstruction = 0.0
    obstruction_lat = None
    obstruction_lon = None

    for i, (lat, lon) in enumerate(sample_coords):
        key = (round(lat, 5), round(lon, 5))
        ground_elev = elevations.get(key)
        if ground_elev is None:
            continue

        t = (i + 1) / (n_samples - 1)
        expected_h = node_h + t * (cell_h - node_h)

        obstruction = ground_elev - expected_h
        if obstruction > max_obstruction:
            max_obstruction = obstruction
            obstruction_lat = lat
            obstruction_lon = lon

    is_clear = max_obstruction <= 10.0  # 10m margin for Fresnel zone + SRTM resolution
    return is_clear, max_obstruction, obstruction_lat, obstruction_lon


def _simple_distance(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def classify_shadow_cause(store, zone_cells, nodes, elevation_fetcher, max_range_km):
    """Classify a dead zone as 'terrain' / 'distance' / 'mixed' / 'unknown'.

    For up to 50 sample cells in the zone, tally how many reachable-distance
    nodes are blocked by terrain vs. how many cells are simply out of range.
    Thresholds: >50% terrain-blocked → 'terrain'; >70% distance-limited →
    'distance'; both >20%/>30% → 'mixed'.
    """
    if not zone_cells or not nodes:
        return "unknown"

    terrain_blocked = 0
    distance_limited = 0
    total_checks = 0

    for cell in zone_cells[:50]:  # Sample up to 50 cells
        c_lat = cell["center_lat"]
        c_lon = cell["center_lon"]
        c_elev = None
        key = (round(c_lat, 5), round(c_lon, 5))
        cached = store.get_cached_elevation(c_lat, c_lon)
        if cached is not None:
            c_elev = cached

        for node in nodes:
            n_lat = node["latitude"]
            n_lon = node["longitude"]
            n_elev = node.get("altitude")
            dist = _simple_distance(n_lat, n_lon, c_lat, c_lon)

            if dist > max_range_km:
                distance_limited += 1
                total_checks += 1
                continue

            if c_elev is not None and n_elev is not None:
                is_clear, _, _, _ = check_line_of_sight(
                    n_lat, n_lon, n_elev, c_lat, c_lon, c_elev, elevation_fetcher
                )
                if not is_clear:
                    terrain_blocked += 1
                total_checks += 1
            else:
                total_checks += 1

    if total_checks == 0:
        return "unknown"

    terrain_ratio = terrain_blocked / total_checks
    distance_ratio = distance_limited / total_checks

    if terrain_ratio > 0.5:
        return "terrain"
    elif distance_ratio > 0.7:
        return "distance"
    elif terrain_ratio > 0.2 and distance_ratio > 0.3:
        return "mixed"
    return "unknown"
