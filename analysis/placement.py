"""Greedy node-placement suggestions to fill detected shadow areas.

The algorithm picks N suggestions iteratively. At each step it scores every
remaining candidate cell by the sum of shadow scores within `max_range_km`
(a vectorized haversine), multiplied by a small elevation bonus (+10% per
1000m of elevation — higher ground tends to cover more terrain). After each
pick, the covered cells' shadow scores are zeroed so subsequent suggestions
target *uncovered* shadow rather than re-covering the same area. Suggestions
are also forced ≥500m apart from each other.

Candidate pool: cells with `shadow_score >= 0.8` (strongest shadow first),
falling back to all shadow cells if fewer than 50 strong candidates exist.
Downsampled to ~1000 cells if the pool is larger to keep the inner loop fast.
"""

import logging
import math
import time

import numpy as np

from database.store import DataStore

log = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0


def _haversine_grid(lat1, lon1, lat2_arr, lon2_arr):
    """Vectorized haversine distance from one point to arrays of points (km)."""
    dlat = np.radians(lat2_arr - lat1)
    dlon = np.radians(lon2_arr - lon1)
    a = np.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * np.cos(np.radians(lat2_arr)) * np.sin(dlon / 2) ** 2
    return EARTH_RADIUS_KM * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def suggest_placements(store: DataStore, max_range_km: float, num_suggestions: int = 5,
                       elevation_fetcher=None):
    """Compute and persist the top `num_suggestions` placement candidates.

    Replaces the entire `placement_suggestions` table on each call. Each
    suggestion is a tuple of (lat, lon, elev_m, reduction_km2, reduction_pct,
    cells_improved, rank, reasoning_text, timestamp). If `elevation_fetcher`
    is provided, missing elevations for chosen suggestions are filled in
    after selection so the reasoning string includes elevation.
    """
    grid_meta = store.get_grid_metadata()
    if not grid_meta:
        log.warning("No grid metadata, cannot compute placement suggestions")
        return []

    shadow_cells = store.get_shadow_cells(threshold=0.6)
    if not shadow_cells:
        log.info("No shadow cells found, no placement suggestions needed")
        return []

    cell_area_km2 = (grid_meta["cell_size_m"] / 1000.0) ** 2

    shadow_lats = np.array([c["center_lat"] for c in shadow_cells])
    shadow_lons = np.array([c["center_lon"] for c in shadow_cells])
    shadow_scores = np.array([c["shadow_score"] for c in shadow_cells])

    total_shadow_area = len(shadow_cells) * cell_area_km2

    # Prefer candidates that are deeply in shadow (high score, low reachable nodes)
    # Filter candidates to cells with shadow_score >= 0.8 first, fall back if too few
    strong_shadow_indices = [i for i, c in enumerate(shadow_cells) if c["shadow_score"] >= 0.8]
    if len(strong_shadow_indices) < 50:
        strong_shadow_indices = list(range(len(shadow_cells)))

    # Downsample candidates if too many
    if len(strong_shadow_indices) > 2000:
        step = max(1, len(strong_shadow_indices) // 1000)
        candidate_indices = [strong_shadow_indices[i] for i in range(0, len(strong_shadow_indices), step)]
    else:
        candidate_indices = strong_shadow_indices

    log.info("Evaluating %d candidate placements from %d shadow cells",
             len(candidate_indices), len(shadow_cells))

    suggestions = []
    used_positions = set()

    for rank in range(num_suggestions):
        best_score = 0
        best_idx = -1
        best_covered = None

        for ci in candidate_indices:
            c_lat = shadow_lats[ci]
            c_lon = shadow_lons[ci]

            # Skip if too close to an already-selected suggestion
            too_close = False
            for pos in used_positions:
                d = math.sqrt((c_lat - pos[0]) ** 2 + (c_lon - pos[1]) ** 2)
                if d < 0.005:  # ~500m
                    too_close = True
                    break
            if too_close:
                continue

            distances = _haversine_grid(c_lat, c_lon, shadow_lats, shadow_lons)
            covered_mask = distances <= max_range_km
            reduction = float(np.sum(shadow_scores[covered_mask]))

            # Prefer higher elevation cells
            elev = shadow_cells[ci].get("elevation_m")
            elev_bonus = 1.0 + (elev / 1000.0 * 0.1) if elev and elev > 0 else 1.0

            score = reduction * elev_bonus

            if score > best_score:
                best_score = score
                best_idx = ci
                best_covered = covered_mask

        if best_idx < 0:
            break

        cell = shadow_cells[best_idx]
        cells_improved = int(np.sum(best_covered))
        reduction_km2 = cells_improved * cell_area_km2
        reduction_pct = (reduction_km2 / total_shadow_area * 100) if total_shadow_area > 0 else 0

        # Try grid cell elevation first, then check elevation cache directly
        elev = cell.get("elevation_m")
        if elev is None:
            elev = store.get_cached_elevation(cell["center_lat"], cell["center_lon"])
        elev_str = f" at {elev * 3.28084:.0f} ft elevation" if elev else ""
        reduction_mi2 = reduction_km2 * 0.386102

        reasoning = (
            f"Placing a node here{elev_str} would reduce shadow area by "
            f"{reduction_mi2:.2f} mi² ({reduction_pct:.1f}% of total shadow), "
            f"improving coverage for {cells_improved} grid cells"
        )

        suggestions.append((
            cell["center_lat"], cell["center_lon"], elev,
            reduction_km2, reduction_pct, cells_improved,
            rank + 1, reasoning, int(time.time()),
        ))

        used_positions.add((cell["center_lat"], cell["center_lon"]))

        # Zero out covered cells so next iteration finds different area
        shadow_scores[best_covered] = 0.0

        log.info("Suggestion #%d: (%.4f, %.4f) reduces shadow by %.2f km2 (%d cells)",
                 rank + 1, cell["center_lat"], cell["center_lon"], reduction_km2, cells_improved)

    # Fetch elevation for suggestions that don't have it
    if elevation_fetcher and suggestions:
        need_elev = [(s[0], s[1]) for s in suggestions if s[2] is None]
        if need_elev:
            try:
                elevations = elevation_fetcher.fetch_elevations(need_elev)
                updated = []
                for s in suggestions:
                    if s[2] is None:
                        key = (round(s[0], 5), round(s[1], 5))
                        elev = elevations.get(key)
                        if elev is not None:
                            elev_str = f" at {elev * 3.28084:.0f} ft elevation"
                            reduction_mi2 = s[3] * 0.386102
                            reasoning = (
                                f"Placing a node here{elev_str} would reduce shadow area by "
                                f"{reduction_mi2:.2f} mi² ({s[4]:.1f}% of total shadow), "
                                f"improving coverage for {s[5]} grid cells"
                            )
                            updated.append((s[0], s[1], elev, s[3], s[4], s[5], s[6], reasoning, s[8]))
                        else:
                            updated.append(s)
                    else:
                        updated.append(s)
                suggestions = updated
            except Exception as e:
                log.warning("Failed to fetch elevations for suggestions: %s", e)

    store.clear_placement_suggestions()
    if suggestions:
        store.insert_placement_suggestions(suggestions)

    return suggestions


def evaluate_placement(store: DataStore, lat: float, lon: float, max_range_km: float):
    """Score a hypothetical node at (lat, lon) without persisting it.

    Used by the Discord `/evaluate` command and the web "what-if" tool.
    Returns the same shape that `suggest_placements` would produce for a
    single candidate, plus the totals it was measured against.
    """
    shadow_cells = store.get_shadow_cells(threshold=0.6)
    if not shadow_cells:
        return {"reduction_km2": 0, "reduction_pct": 0, "cells_improved": 0}

    grid_meta = store.get_grid_metadata()
    cell_area_km2 = (grid_meta["cell_size_m"] / 1000.0) ** 2 if grid_meta else 0.01

    total_shadow_area = len(shadow_cells) * cell_area_km2

    shadow_lats = np.array([c["center_lat"] for c in shadow_cells])
    shadow_lons = np.array([c["center_lon"] for c in shadow_cells])

    distances = _haversine_grid(lat, lon, shadow_lats, shadow_lons)
    covered = distances <= max_range_km
    cells_improved = int(np.sum(covered))
    reduction_km2 = cells_improved * cell_area_km2
    reduction_pct = (reduction_km2 / total_shadow_area * 100) if total_shadow_area > 0 else 0

    elev = store.get_cached_elevation(lat, lon)

    return {
        "latitude": lat,
        "longitude": lon,
        "elevation_m": elev,
        "reduction_km2": round(reduction_km2, 3),
        "reduction_pct": round(reduction_pct, 1),
        "cells_improved": cells_improved,
        "total_shadow_cells": len(shadow_cells),
        "total_shadow_area_km2": round(total_shadow_area, 2),
    }
