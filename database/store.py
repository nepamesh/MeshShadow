import json
import sqlite3
import threading
import logging
import time

from .schema import SCHEMA_SQL

log = logging.getLogger(__name__)


def _parse_node_list(raw):
    """Parse affected_nodes from either JSON or Python repr format."""
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Handle Python repr format: "['!abc', '!def']"
        import ast
        try:
            return ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return []


class DataStore:
    _NODES_COLUMNS = frozenset({
        "short_name", "long_name", "hw_model", "latitude", "longitude",
        "altitude", "last_seen", "battery_level", "voltage", "channel_util",
        "air_util_tx", "uptime_seconds", "role",
    })
    _DEAD_ZONES_COLUMNS = frozenset({
        "name", "center_lat", "center_lon", "area_km2", "cell_count",
        "avg_shadow_score", "max_shadow_score", "cause", "first_detected",
        "last_updated", "active",
    })
    _BLACK_HOLES_COLUMNS = frozenset({
        "name", "center_lat", "center_lon", "radius_km", "severity",
        "evidence_type", "affected_nodes", "description", "first_detected",
        "last_updated", "active", "notified",
    })
    _NODE_ROUTING_STATS_COLUMNS = frozenset({
        "packets_seen", "packets_as_relay", "avg_hops_taken", "expected_hops",
        "forwarding_ratio", "via_mqtt_pct", "asymmetric_links", "last_updated",
    })
    _WEATHER_COLUMNS = frozenset({
        "temperature_c", "humidity_pct", "pressure_hpa", "precipitation_mm",
        "cloud_cover_pct", "wind_speed_kmh", "wind_direction_deg",
    })

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = None

    def initialize(self):
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()
        log.info("Database initialized at %s", self.db_path)

    def _execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _fetchall(self, sql, params=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _fetchone(self, sql, params=()):
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    # --- Nodes ---

    def upsert_node(self, node_id: str, **kwargs):
        unknown = set(kwargs) - self._NODES_COLUMNS
        if unknown:
            log.warning("upsert_node: ignoring unknown columns: %s", unknown)
            kwargs = {k: v for k, v in kwargs.items() if k in self._NODES_COLUMNS}
        kwargs["last_seen"] = kwargs.get("last_seen", int(time.time()))
        existing = self._fetchone("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
        if existing:
            sets = []
            vals = []
            for k, v in kwargs.items():
                if v is not None:
                    sets.append(f"{k} = ?")
                    vals.append(v)
            if sets:
                vals.append(node_id)
                self._execute(f"UPDATE nodes SET {', '.join(sets)} WHERE node_id = ?", vals)
        else:
            cols = ["node_id"] + [k for k, v in kwargs.items() if v is not None]
            placeholders = ", ".join(["?"] * len(cols))
            vals = [node_id] + [v for v in kwargs.values() if v is not None]
            self._execute(f"INSERT INTO nodes ({', '.join(cols)}) VALUES ({placeholders})", vals)

    def get_node(self, node_id: str):
        return self._fetchone("SELECT * FROM nodes WHERE node_id = ?", (node_id,))

    def get_all_nodes(self):
        return self._fetchall("SELECT * FROM nodes ORDER BY last_seen DESC")

    def get_active_nodes(self, hours: int = 24):
        cutoff = int(time.time()) - (hours * 3600)
        return self._fetchall("SELECT * FROM nodes WHERE last_seen > ? ORDER BY last_seen DESC", (cutoff,))

    def get_node_position(self, node_id: str):
        node = self._fetchone("SELECT latitude, longitude FROM nodes WHERE node_id = ? AND latitude IS NOT NULL", (node_id,))
        if node:
            return (node["latitude"], node["longitude"])
        return None

    # --- Positions ---

    def insert_position(self, node_id: str, ts: int, lat: float, lon: float, alt: float = None, sats: int = None):
        self._execute(
            "INSERT INTO positions (node_id, timestamp, latitude, longitude, altitude, sats_in_view) VALUES (?, ?, ?, ?, ?, ?)",
            (node_id, ts, lat, lon, alt, sats),
        )
        self.upsert_node(node_id, latitude=lat, longitude=lon, altitude=alt, last_seen=ts)

    def get_node_positions(self, node_id: str, hours: int = 24):
        cutoff = int(time.time()) - (hours * 3600)
        return self._fetchall(
            "SELECT * FROM positions WHERE node_id = ? AND timestamp > ? ORDER BY timestamp", (node_id, cutoff)
        )

    # --- Link Observations ---

    def insert_link_observation(self, ts: int, node_a: str, node_b: str,
                                 a_lat: float = None, a_lon: float = None,
                                 b_lat: float = None, b_lon: float = None,
                                 snr: float = None, rssi: float = None,
                                 distance: float = None, weather_id: int = None):
        self._execute(
            """INSERT INTO link_observations
               (timestamp, node_a_id, node_b_id, node_a_lat, node_a_lon, node_b_lat, node_b_lon,
                snr, rssi, distance_km, weather_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, node_a, node_b, a_lat, a_lon, b_lat, b_lon, snr, rssi, distance, weather_id),
        )

    def get_link_observations(self, start_ts: int = None, end_ts: int = None,
                               node_a: str = None, node_b: str = None, limit: int = 10000):
        sql = "SELECT * FROM link_observations WHERE 1=1"
        params = []
        if start_ts:
            sql += " AND timestamp >= ?"
            params.append(start_ts)
        if end_ts:
            sql += " AND timestamp <= ?"
            params.append(end_ts)
        if node_a:
            sql += " AND (node_a_id = ? OR node_b_id = ?)"
            params.extend([node_a, node_a])
        if node_b:
            sql += " AND (node_a_id = ? OR node_b_id = ?)"
            params.extend([node_b, node_b])
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return self._fetchall(sql, params)

    def get_latest_links(self, hours: int = 24):
        cutoff = int(time.time()) - (hours * 3600)
        return self._fetchall(
            """SELECT node_a_id, node_b_id,
                      AVG(snr) as avg_snr, MIN(snr) as min_snr, MAX(snr) as max_snr,
                      AVG(rssi) as avg_rssi,
                      AVG(distance_km) as avg_distance,
                      COUNT(*) as obs_count,
                      MAX(timestamp) as last_seen,
                      node_a_lat, node_a_lon, node_b_lat, node_b_lon
               FROM link_observations
               WHERE timestamp > ?
               GROUP BY node_a_id, node_b_id
               ORDER BY obs_count DESC""",
            (cutoff,),
        )

    def get_link_stats(self, node_a: str, node_b: str):
        return self._fetchone(
            """SELECT AVG(snr) as avg_snr, MIN(snr) as min_snr, MAX(snr) as max_snr,
                      AVG(rssi) as avg_rssi, COUNT(*) as obs_count,
                      AVG(distance_km) as avg_distance,
                      MIN(timestamp) as first_seen, MAX(timestamp) as last_seen
               FROM link_observations
               WHERE (node_a_id = ? AND node_b_id = ?) OR (node_a_id = ? AND node_b_id = ?)""",
            (node_a, node_b, node_b, node_a),
        )

    # --- Weather ---

    def insert_weather(self, ts: int, lat: float, lon: float, **conditions) -> int:
        unknown = set(conditions) - self._WEATHER_COLUMNS
        if unknown:
            log.warning("insert_weather: ignoring unknown columns: %s", unknown)
            conditions = {k: v for k, v in conditions.items() if k in self._WEATHER_COLUMNS}
        cols = ["timestamp", "latitude", "longitude"] + list(conditions.keys())
        placeholders = ", ".join(["?"] * len(cols))
        vals = [ts, lat, lon] + list(conditions.values())
        cur = self._execute(
            f"INSERT INTO weather_observations ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
        return cur.lastrowid

    def get_latest_weather(self):
        return self._fetchone("SELECT * FROM weather_observations ORDER BY timestamp DESC LIMIT 1")

    def get_weather_near_time(self, ts: int):
        row = self._fetchone(
            "SELECT id FROM weather_observations ORDER BY ABS(timestamp - ?) LIMIT 1", (ts,)
        )
        return row["id"] if row else None

    # --- Device Metrics ---

    def insert_device_metrics(self, node_id: str, ts: int, battery: int = None,
                               voltage: float = None, ch_util: float = None,
                               air_util: float = None, uptime: int = None):
        self._execute(
            "INSERT INTO device_metrics (node_id, timestamp, battery_level, voltage, channel_util, air_util_tx, uptime_seconds) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (node_id, ts, battery, voltage, ch_util, air_util, uptime),
        )
        self.upsert_node(node_id, battery_level=battery, voltage=voltage,
                         channel_util=ch_util, air_util_tx=air_util, uptime_seconds=uptime, last_seen=ts)

    def get_node_metrics_history(self, node_id: str, hours: int = 24):
        cutoff = int(time.time()) - (hours * 3600)
        return self._fetchall(
            "SELECT * FROM device_metrics WHERE node_id = ? AND timestamp > ? ORDER BY timestamp",
            (node_id, cutoff),
        )

    # --- Anomalies ---

    def insert_anomaly(self, ts: int, event_type: str, desc: str, node_a: str = None, node_b: str = None):
        self._execute(
            "INSERT INTO anomaly_events (timestamp, event_type, description, node_a_id, node_b_id) VALUES (?, ?, ?, ?, ?)",
            (ts, event_type, desc, node_a, node_b),
        )

    def get_pending_anomalies(self):
        return self._fetchall("SELECT * FROM anomaly_events WHERE notified = 0 ORDER BY timestamp")

    def mark_anomaly_notified(self, anomaly_id: int):
        self._execute("UPDATE anomaly_events SET notified = 1 WHERE id = ?", (anomaly_id,))

    # --- Summary ---

    def get_mesh_summary(self):
        now = int(time.time())
        h1 = now - 3600
        h24 = now - 86400
        return {
            "total_nodes": self._fetchone("SELECT COUNT(*) as c FROM nodes")["c"],
            "active_nodes_1h": self._fetchone("SELECT COUNT(*) as c FROM nodes WHERE last_seen > ?", (h1,))["c"],
            "active_nodes_24h": self._fetchone("SELECT COUNT(*) as c FROM nodes WHERE last_seen > ?", (h24,))["c"],
            "total_links_24h": self._fetchone("SELECT COUNT(*) as c FROM link_observations WHERE timestamp > ?", (h24,))["c"],
            "unique_pairs_24h": self._fetchone(
                "SELECT COUNT(DISTINCT node_a_id || '-' || node_b_id) as c FROM link_observations WHERE timestamp > ?", (h24,)
            )["c"],
            "total_positions": self._fetchone("SELECT COUNT(*) as c FROM positions")["c"],
            "latest_weather": self.get_latest_weather(),
        }

    def get_links_with_weather(self, hours: int = 168):
        cutoff = int(time.time()) - (hours * 3600)
        return self._fetchall(
            """SELECT l.*, w.temperature_c, w.humidity_pct, w.pressure_hpa,
                      w.cloud_cover_pct, w.wind_speed_kmh, w.precipitation_mm
               FROM link_observations l
               LEFT JOIN weather_observations w ON l.weather_id = w.id
               WHERE l.timestamp > ? AND l.weather_id IS NOT NULL
               ORDER BY l.timestamp""",
            (cutoff,),
        )

    # --- Shadow Mapper: Nodes with Positions ---

    def get_nodes_with_positions(self):
        return self._fetchall(
            "SELECT * FROM nodes WHERE latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY last_seen DESC"
        )

    def get_link_count_for_node(self, node_id: str, hours: int = 24):
        cutoff = int(time.time()) - (hours * 3600)
        row = self._fetchone(
            "SELECT COUNT(*) as c FROM link_observations WHERE (node_a_id = ? OR node_b_id = ?) AND timestamp > ?",
            (node_id, node_id, cutoff),
        )
        return row["c"] if row else 0

    # --- Shadow Mapper: Elevation Cache ---

    def _executemany(self, sql, params_list):
        with self._lock:
            cur = self._conn.executemany(sql, params_list)
            self._conn.commit()
            return cur

    def cache_elevation(self, lat: float, lon: float, elevation_m: float):
        self._execute(
            "INSERT OR REPLACE INTO elevation_cache (latitude, longitude, elevation_m, fetched_at) VALUES (?, ?, ?, ?)",
            (round(lat, 5), round(lon, 5), elevation_m, int(time.time())),
        )

    def cache_elevations_bulk(self, data: list):
        self._executemany(
            "INSERT OR REPLACE INTO elevation_cache (latitude, longitude, elevation_m, fetched_at) VALUES (?, ?, ?, ?)",
            [(round(lat, 5), round(lon, 5), elev, int(time.time())) for lat, lon, elev in data],
        )

    def get_cached_elevation(self, lat: float, lon: float):
        row = self._fetchone(
            "SELECT elevation_m FROM elevation_cache WHERE ROUND(latitude, 5) = ? AND ROUND(longitude, 5) = ?",
            (round(lat, 5), round(lon, 5)),
        )
        return row["elevation_m"] if row else None

    def get_cached_elevations_bulk(self, coords: list):
        results = {}
        for lat, lon in coords:
            elev = self.get_cached_elevation(lat, lon)
            if elev is not None:
                results[(round(lat, 5), round(lon, 5))] = elev
        return results

    # --- Shadow Mapper: Coverage Grid ---

    def upsert_grid_cells_bulk(self, cells: list):
        self._executemany(
            """INSERT OR REPLACE INTO coverage_grid
               (grid_row, grid_col, center_lat, center_lon, elevation_m,
                coverage_score, shadow_score, obs_density, reachable_nodes, obstructed_nodes, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            cells,
        )

    def get_grid_cells(self):
        return self._fetchall("SELECT * FROM coverage_grid ORDER BY grid_row, grid_col")

    def get_grid_shadow_scores(self):
        """Memory-efficient: return only (row, col, shadow_score, lat, lon) tuples."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT grid_row, grid_col, shadow_score, center_lat, center_lon FROM coverage_grid"
            ).fetchall()
            return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    def get_shadow_cells(self, threshold: float = 0.6):
        return self._fetchall(
            "SELECT * FROM coverage_grid WHERE shadow_score >= ? ORDER BY shadow_score DESC",
            (threshold,),
        )

    # --- Shadow Mapper: Dead Zones ---

    def insert_dead_zone(self, name: str, center_lat: float, center_lon: float,
                          area_km2: float, cell_count: int, avg_shadow: float,
                          max_shadow: float, cause: str = None):
        now = int(time.time())
        cur = self._execute(
            """INSERT INTO dead_zones (name, center_lat, center_lon, area_km2, cell_count,
               avg_shadow_score, max_shadow_score, cause, first_detected, last_updated, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (name, center_lat, center_lon, area_km2, cell_count, avg_shadow, max_shadow, cause, now, now),
        )
        return cur.lastrowid

    def update_dead_zone(self, zone_id: int, **kwargs):
        unknown = set(kwargs) - self._DEAD_ZONES_COLUMNS
        if unknown:
            log.warning("update_dead_zone: ignoring unknown columns: %s", unknown)
            kwargs = {k: v for k, v in kwargs.items() if k in self._DEAD_ZONES_COLUMNS}
        kwargs["last_updated"] = int(time.time())
        sets = [f"{k} = ?" for k in kwargs]
        vals = list(kwargs.values()) + [zone_id]
        self._execute(f"UPDATE dead_zones SET {', '.join(sets)} WHERE id = ?", vals)

    def deactivate_dead_zone(self, zone_id: int):
        self._execute("UPDATE dead_zones SET active = 0, last_updated = ? WHERE id = ?",
                       (int(time.time()), zone_id))

    def get_dead_zones(self, active_only: bool = True):
        if active_only:
            return self._fetchall("SELECT * FROM dead_zones WHERE active = 1 ORDER BY area_km2 DESC")
        return self._fetchall("SELECT * FROM dead_zones ORDER BY active DESC, area_km2 DESC")

    def get_dead_zone(self, zone_id: int):
        return self._fetchone("SELECT * FROM dead_zones WHERE id = ?", (zone_id,))

    def get_dead_zone_by_name(self, name: str):
        return self._fetchone("SELECT * FROM dead_zones WHERE LOWER(name) LIKE ?",
                               (f"%{name.lower()}%",))

    def set_dead_zone_cells(self, zone_id: int, cells: list):
        with self._lock:
            self._conn.execute("DELETE FROM dead_zone_cells WHERE dead_zone_id = ?", (zone_id,))
            self._conn.executemany(
                "INSERT INTO dead_zone_cells (dead_zone_id, grid_row, grid_col) VALUES (?, ?, ?)",
                [(zone_id, r, c) for r, c in cells],
            )
            self._conn.commit()

    def get_dead_zone_cells(self, zone_id: int):
        return self._fetchall(
            """SELECT dzc.grid_row, dzc.grid_col, cg.center_lat, cg.center_lon, cg.shadow_score
               FROM dead_zone_cells dzc
               JOIN coverage_grid cg ON dzc.grid_row = cg.grid_row AND dzc.grid_col = cg.grid_col
               WHERE dzc.dead_zone_id = ?""",
            (zone_id,),
        )

    # --- Shadow Mapper: Placement Suggestions ---

    def clear_placement_suggestions(self):
        self._execute("DELETE FROM placement_suggestions")

    def insert_placement_suggestions(self, suggestions: list):
        self._executemany(
            """INSERT INTO placement_suggestions
               (latitude, longitude, elevation_m, shadow_reduction_km2,
                shadow_reduction_pct, cells_improved, rank, reasoning, computed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            suggestions,
        )

    def get_placement_suggestions(self, limit: int = 5):
        return self._fetchall(
            "SELECT * FROM placement_suggestions ORDER BY rank LIMIT ?", (limit,)
        )

    # --- Shadow Mapper: Coverage Snapshots ---

    def insert_coverage_snapshot(self, total_cells: int, covered: int, shadow: int,
                                  coverage_pct: float, total_area: float, covered_area: float,
                                  shadow_area: float, active_nodes: int, dead_zone_count: int):
        self._execute(
            """INSERT INTO coverage_snapshots
               (timestamp, total_cells, covered_cells, shadow_cells, coverage_pct,
                total_area_km2, covered_area_km2, shadow_area_km2, active_nodes, dead_zone_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (int(time.time()), total_cells, covered, shadow, coverage_pct,
             total_area, covered_area, shadow_area, active_nodes, dead_zone_count),
        )

    def get_coverage_snapshots(self, days: int = 30):
        cutoff = int(time.time()) - (days * 86400)
        return self._fetchall(
            "SELECT * FROM coverage_snapshots WHERE timestamp > ? ORDER BY timestamp", (cutoff,)
        )

    def get_latest_snapshot(self):
        return self._fetchone("SELECT * FROM coverage_snapshots ORDER BY timestamp DESC LIMIT 1")

    # --- Shadow Mapper: Grid Metadata ---

    def save_grid_metadata(self, min_lat, max_lat, min_lon, max_lon, cell_size, rows, cols):
        now = int(time.time())
        existing = self._fetchone("SELECT * FROM grid_metadata WHERE id = 1")
        if existing:
            self._execute(
                """UPDATE grid_metadata SET min_lat=?, max_lat=?, min_lon=?, max_lon=?,
                   cell_size_m=?, rows=?, cols=?, updated_at=? WHERE id=1""",
                (min_lat, max_lat, min_lon, max_lon, cell_size, rows, cols, now),
            )
        else:
            self._execute(
                """INSERT INTO grid_metadata (id, min_lat, max_lat, min_lon, max_lon,
                   cell_size_m, rows, cols, created_at, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (min_lat, max_lat, min_lon, max_lon, cell_size, rows, cols, now, now),
            )

    def get_grid_metadata(self):
        return self._fetchone("SELECT * FROM grid_metadata WHERE id = 1")

    # --- Shadow Mapper: Coverage Summary ---

    def get_coverage_summary(self):
        now = int(time.time())
        h24 = now - 86400
        total = self._fetchone("SELECT COUNT(*) as c FROM coverage_grid")
        shadow = self._fetchone("SELECT COUNT(*) as c FROM coverage_grid WHERE shadow_score >= 0.6")
        zones = self._fetchone("SELECT COUNT(*) as c FROM dead_zones WHERE active = 1")
        nodes = self._fetchone("SELECT COUNT(*) as c FROM nodes WHERE last_seen > ?", (h24,))
        meta = self.get_grid_metadata()
        cell_area_km2 = (meta["cell_size_m"] / 1000.0) ** 2 if meta else 0.01
        total_c = total["c"] if total else 0
        shadow_c = shadow["c"] if shadow else 0
        covered_c = total_c - shadow_c
        return {
            "total_cells": total_c,
            "covered_cells": covered_c,
            "shadow_cells": shadow_c,
            "coverage_pct": round((covered_c / total_c * 100) if total_c > 0 else 0, 1),
            "total_area_km2": round(total_c * cell_area_km2, 2),
            "covered_area_km2": round(covered_c * cell_area_km2, 2),
            "shadow_area_km2": round(shadow_c * cell_area_km2, 2),
            "dead_zone_count": zones["c"] if zones else 0,
            "active_nodes_24h": nodes["c"] if nodes else 0,
        }

    # --- Black Hole Detection: Packet Observations ---

    def insert_packet_observation(self, timestamp, packet_id, from_id, to_id,
                                   portnum, hop_start, hop_limit, rx_snr, rx_rssi,
                                   via_mqtt, relay_node, channel):
        hops_taken = None
        if hop_start is not None and hop_limit is not None and hop_start > 0:
            hops_taken = hop_start - hop_limit
        self._execute(
            """INSERT INTO packet_observations
               (timestamp, packet_id, from_id, to_id, portnum, hop_start, hop_limit,
                hops_taken, rx_snr, rx_rssi, via_mqtt, relay_node, channel)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, packet_id, from_id, to_id, portnum, hop_start, hop_limit,
             hops_taken, rx_snr, rx_rssi, 1 if via_mqtt else 0, relay_node, channel),
        )

    def get_packet_stats_by_node(self, hours=24):
        cutoff = int(time.time()) - (hours * 3600)
        return self._fetchall(
            """SELECT from_id,
                      COUNT(*) as packet_count,
                      AVG(hops_taken) as avg_hops,
                      MAX(hops_taken) as max_hops,
                      SUM(CASE WHEN via_mqtt = 1 THEN 1 ELSE 0 END) as mqtt_count,
                      AVG(rx_snr) as avg_snr,
                      AVG(rx_rssi) as avg_rssi
               FROM packet_observations
               WHERE timestamp > ? AND hops_taken IS NOT NULL
               GROUP BY from_id
               ORDER BY packet_count DESC""",
            (cutoff,),
        )

    def get_relay_stats(self, hours=24):
        """Get nodes that appear as relay_node and how often."""
        cutoff = int(time.time()) - (hours * 3600)
        return self._fetchall(
            """SELECT relay_node as node_id,
                      COUNT(*) as relay_count
               FROM packet_observations
               WHERE timestamp > ? AND relay_node IS NOT NULL
               GROUP BY relay_node
               ORDER BY relay_count DESC""",
            (cutoff,),
        )

    # --- Black Hole Detection: Traceroutes ---

    def insert_traceroute(self, timestamp, origin_id, destination_id,
                           route_forward, snr_forward, route_back, snr_back,
                           hop_count, completed):
        self._execute(
            """INSERT INTO traceroutes
               (timestamp, origin_id, destination_id, route_forward, snr_forward,
                route_back, snr_back, hop_count, completed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, origin_id, destination_id,
             json.dumps(route_forward) if route_forward else None,
             json.dumps(snr_forward) if snr_forward else None,
             json.dumps(route_back) if route_back else None,
             json.dumps(snr_back) if snr_back else None,
             hop_count, 1 if completed else 0),
        )

    def get_traceroutes(self, hours=24, limit=50):
        cutoff = int(time.time()) - (hours * 3600)
        rows = self._fetchall(
            """SELECT * FROM traceroutes
               WHERE timestamp > ?
               ORDER BY timestamp DESC LIMIT ?""",
            (cutoff, limit),
        )
        for r in rows:
            if r.get("route_forward"):
                r["route_forward"] = json.loads(r["route_forward"])
            if r.get("snr_forward"):
                r["snr_forward"] = json.loads(r["snr_forward"])
            if r.get("route_back"):
                r["route_back"] = json.loads(r["route_back"])
            if r.get("snr_back"):
                r["snr_back"] = json.loads(r["snr_back"])
        return rows

    # --- Black Hole Detection: Asymmetric Links ---

    def get_asymmetric_links(self, hours=24, min_obs=3):
        """Find links where A sees B but B never sees A (or vice versa)."""
        cutoff = int(time.time()) - (hours * 3600)
        return self._fetchall(
            """SELECT a.node_a_id, a.node_b_id,
                      a.cnt as forward_obs,
                      COALESCE(b.cnt, 0) as reverse_obs,
                      a.avg_snr as forward_snr,
                      a.avg_dist as distance_km
               FROM (
                   SELECT node_a_id, node_b_id, COUNT(*) as cnt,
                          AVG(snr) as avg_snr, AVG(distance_km) as avg_dist
                   FROM link_observations
                   WHERE timestamp > ?
                   GROUP BY node_a_id, node_b_id
                   HAVING cnt >= ?
               ) a
               LEFT JOIN (
                   SELECT node_a_id, node_b_id, COUNT(*) as cnt
                   FROM link_observations
                   WHERE timestamp > ?
                   GROUP BY node_a_id, node_b_id
               ) b ON a.node_a_id = b.node_b_id AND a.node_b_id = b.node_a_id
               WHERE COALESCE(b.cnt, 0) = 0
               ORDER BY a.cnt DESC""",
            (cutoff, min_obs, cutoff),
        )

    # --- Black Hole Detection: Black Holes ---

    def insert_black_hole(self, name, center_lat, center_lon, radius_km,
                           severity, evidence_type, affected_nodes, description):
        now = int(time.time())
        cur = self._execute(
            """INSERT INTO black_holes
               (name, center_lat, center_lon, radius_km, severity, evidence_type,
                affected_nodes, description, first_detected, last_updated, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (name, center_lat, center_lon, radius_km, severity, evidence_type,
             json.dumps(affected_nodes) if affected_nodes else None,
             description, now, now),
        )
        return cur.lastrowid

    def update_black_hole(self, bh_id, **kwargs):
        unknown = set(kwargs) - self._BLACK_HOLES_COLUMNS
        if unknown:
            log.warning("update_black_hole: ignoring unknown columns: %s", unknown)
            kwargs = {k: v for k, v in kwargs.items() if k in self._BLACK_HOLES_COLUMNS}
        kwargs["last_updated"] = int(time.time())
        sets = [f"{k} = ?" for k in kwargs]
        vals = list(kwargs.values()) + [bh_id]
        self._execute(f"UPDATE black_holes SET {', '.join(sets)} WHERE id = ?", vals)

    def get_black_holes(self, active_only=True):
        if active_only:
            rows = self._fetchall(
                "SELECT * FROM black_holes WHERE active = 1 ORDER BY severity DESC")
        else:
            rows = self._fetchall(
                "SELECT * FROM black_holes ORDER BY active DESC, severity DESC")
        for r in rows:
            if r.get("affected_nodes"):
                r["affected_nodes"] = _parse_node_list(r["affected_nodes"])
        return rows

    def get_black_hole_by_name(self, name):
        row = self._fetchone(
            "SELECT * FROM black_holes WHERE LOWER(name) LIKE ?",
            (f"%{name.lower()}%",))
        if row and row.get("affected_nodes"):
            row["affected_nodes"] = _parse_node_list(row["affected_nodes"])
        return row

    def deactivate_black_hole(self, bh_id):
        self._execute(
            "UPDATE black_holes SET active = 0, last_updated = ? WHERE id = ?",
            (int(time.time()), bh_id))

    def get_unnotified_black_holes(self):
        rows = self._fetchall(
            "SELECT * FROM black_holes WHERE notified = 0 AND active = 1 ORDER BY severity DESC")
        for r in rows:
            if r.get("affected_nodes"):
                r["affected_nodes"] = _parse_node_list(r["affected_nodes"])
        return rows

    def mark_black_hole_notified(self, bh_id):
        self._execute("UPDATE black_holes SET notified = 1 WHERE id = ?", (bh_id,))

    # --- Black Hole Detection: Node Routing Stats ---

    def upsert_node_routing_stats(self, node_id, **kwargs):
        unknown = set(kwargs) - self._NODE_ROUTING_STATS_COLUMNS
        if unknown:
            log.warning("upsert_node_routing_stats: ignoring unknown columns: %s", unknown)
            kwargs = {k: v for k, v in kwargs.items() if k in self._NODE_ROUTING_STATS_COLUMNS}
        kwargs["last_updated"] = int(time.time())
        existing = self._fetchone(
            "SELECT * FROM node_routing_stats WHERE node_id = ?", (node_id,))
        if existing:
            sets = [f"{k} = ?" for k in kwargs]
            vals = list(kwargs.values()) + [node_id]
            self._execute(
                f"UPDATE node_routing_stats SET {', '.join(sets)} WHERE node_id = ?", vals)
        else:
            cols = ["node_id"] + list(kwargs.keys())
            placeholders = ", ".join(["?"] * len(cols))
            vals = [node_id] + list(kwargs.values())
            self._execute(
                f"INSERT INTO node_routing_stats ({', '.join(cols)}) VALUES ({placeholders})",
                vals)

    def get_node_routing_stats(self, node_id=None):
        if node_id:
            return self._fetchone(
                """SELECT nrs.*, n.short_name, n.long_name, n.role
                   FROM node_routing_stats nrs
                   LEFT JOIN nodes n ON nrs.node_id = n.node_id
                   WHERE nrs.node_id = ?""", (node_id,))
        return self._fetchall(
            """SELECT nrs.*, n.short_name, n.long_name, n.role
               FROM node_routing_stats nrs
               LEFT JOIN nodes n ON nrs.node_id = n.node_id
               ORDER BY nrs.forwarding_ratio ASC NULLS LAST""")

    def get_suspect_nodes(self, max_forwarding_ratio=0.1, min_packets=10):
        """Nodes with low forwarding ratio relative to their position in the mesh."""
        return self._fetchall(
            """SELECT nrs.*, n.short_name, n.long_name, n.latitude, n.longitude
               FROM node_routing_stats nrs
               JOIN nodes n ON nrs.node_id = n.node_id
               WHERE nrs.forwarding_ratio IS NOT NULL
               AND nrs.forwarding_ratio <= ?
               AND nrs.packets_seen >= ?
               AND n.latitude IS NOT NULL
               ORDER BY nrs.forwarding_ratio ASC""",
            (max_forwarding_ratio, min_packets),
        )

    def cleanup_old_packets(self, max_age_hours=72):
        """Prune old packet observations to prevent DB bloat."""
        cutoff = int(time.time()) - (max_age_hours * 3600)
        self._execute("DELETE FROM packet_observations WHERE timestamp < ?", (cutoff,))

