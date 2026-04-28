SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id         TEXT PRIMARY KEY,
    short_name      TEXT,
    long_name       TEXT,
    hw_model        TEXT,
    latitude        REAL,
    longitude       REAL,
    altitude        REAL,
    last_seen       INTEGER NOT NULL,
    battery_level   INTEGER,
    voltage         REAL,
    channel_util    REAL,
    air_util_tx     REAL,
    uptime_seconds  INTEGER,
    role            TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         TEXT NOT NULL,
    timestamp       INTEGER NOT NULL,
    latitude        REAL NOT NULL,
    longitude       REAL NOT NULL,
    altitude        REAL,
    sats_in_view    INTEGER,
    FOREIGN KEY (node_id) REFERENCES nodes(node_id)
);
CREATE INDEX IF NOT EXISTS idx_positions_node_time ON positions(node_id, timestamp);

CREATE TABLE IF NOT EXISTS link_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       INTEGER NOT NULL,
    node_a_id       TEXT NOT NULL,
    node_b_id       TEXT NOT NULL,
    node_a_lat      REAL,
    node_a_lon      REAL,
    node_b_lat      REAL,
    node_b_lon      REAL,
    snr             REAL,
    rssi            REAL,
    distance_km     REAL,
    weather_id      INTEGER,
    FOREIGN KEY (node_a_id) REFERENCES nodes(node_id),
    FOREIGN KEY (node_b_id) REFERENCES nodes(node_id),
    FOREIGN KEY (weather_id) REFERENCES weather_observations(id)
);
CREATE INDEX IF NOT EXISTS idx_links_time ON link_observations(timestamp);
CREATE INDEX IF NOT EXISTS idx_links_pair ON link_observations(node_a_id, node_b_id);

CREATE TABLE IF NOT EXISTS weather_observations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           INTEGER NOT NULL,
    latitude            REAL NOT NULL,
    longitude           REAL NOT NULL,
    temperature_c       REAL,
    humidity_pct        REAL,
    pressure_hpa        REAL,
    precipitation_mm    REAL,
    cloud_cover_pct     REAL,
    wind_speed_kmh      REAL,
    wind_direction_deg  REAL
);
CREATE INDEX IF NOT EXISTS idx_weather_time ON weather_observations(timestamp);

CREATE TABLE IF NOT EXISTS device_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         TEXT NOT NULL,
    timestamp       INTEGER NOT NULL,
    battery_level   INTEGER,
    voltage         REAL,
    channel_util    REAL,
    air_util_tx     REAL,
    uptime_seconds  INTEGER,
    FOREIGN KEY (node_id) REFERENCES nodes(node_id)
);
CREATE INDEX IF NOT EXISTS idx_metrics_node_time ON device_metrics(node_id, timestamp);

CREATE TABLE IF NOT EXISTS anomaly_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       INTEGER NOT NULL,
    event_type      TEXT NOT NULL,
    description     TEXT,
    node_a_id       TEXT,
    node_b_id       TEXT,
    notified        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_anomaly_pending ON anomaly_events(notified, timestamp);

-- Shadow Mapper tables

CREATE TABLE IF NOT EXISTS elevation_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    latitude        REAL NOT NULL,
    longitude       REAL NOT NULL,
    elevation_m     REAL NOT NULL,
    fetched_at      INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_elevation_latlon
    ON elevation_cache(ROUND(latitude, 5), ROUND(longitude, 5));

CREATE TABLE IF NOT EXISTS coverage_grid (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    grid_row        INTEGER NOT NULL,
    grid_col        INTEGER NOT NULL,
    center_lat      REAL NOT NULL,
    center_lon      REAL NOT NULL,
    elevation_m     REAL,
    coverage_score  REAL NOT NULL DEFAULT 0.0,
    shadow_score    REAL NOT NULL DEFAULT 0.0,
    obs_density     INTEGER NOT NULL DEFAULT 0,
    reachable_nodes INTEGER NOT NULL DEFAULT 0,
    obstructed_nodes INTEGER NOT NULL DEFAULT 0,
    last_updated    INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_grid_cell ON coverage_grid(grid_row, grid_col);

CREATE TABLE IF NOT EXISTS dead_zones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    center_lat      REAL NOT NULL,
    center_lon      REAL NOT NULL,
    area_km2        REAL NOT NULL,
    cell_count      INTEGER NOT NULL,
    avg_shadow_score REAL NOT NULL,
    max_shadow_score REAL NOT NULL,
    cause           TEXT,
    first_detected  INTEGER NOT NULL,
    last_updated    INTEGER NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS dead_zone_cells (
    dead_zone_id    INTEGER NOT NULL,
    grid_row        INTEGER NOT NULL,
    grid_col        INTEGER NOT NULL,
    FOREIGN KEY (dead_zone_id) REFERENCES dead_zones(id),
    PRIMARY KEY (dead_zone_id, grid_row, grid_col)
);

CREATE TABLE IF NOT EXISTS placement_suggestions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    latitude                REAL NOT NULL,
    longitude               REAL NOT NULL,
    elevation_m             REAL,
    shadow_reduction_km2    REAL NOT NULL,
    shadow_reduction_pct    REAL NOT NULL,
    cells_improved          INTEGER NOT NULL,
    rank                    INTEGER NOT NULL,
    reasoning               TEXT,
    computed_at             INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS coverage_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       INTEGER NOT NULL,
    total_cells     INTEGER NOT NULL,
    covered_cells   INTEGER NOT NULL,
    shadow_cells    INTEGER NOT NULL,
    coverage_pct    REAL NOT NULL,
    total_area_km2  REAL NOT NULL,
    covered_area_km2 REAL NOT NULL,
    shadow_area_km2 REAL NOT NULL,
    active_nodes    INTEGER NOT NULL,
    dead_zone_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_time ON coverage_snapshots(timestamp);

CREATE TABLE IF NOT EXISTS grid_metadata (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    min_lat     REAL NOT NULL,
    max_lat     REAL NOT NULL,
    min_lon     REAL NOT NULL,
    max_lon     REAL NOT NULL,
    cell_size_m INTEGER NOT NULL,
    rows        INTEGER NOT NULL,
    cols        INTEGER NOT NULL,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

-- Black Hole Detection tables

CREATE TABLE IF NOT EXISTS packet_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       INTEGER NOT NULL,
    packet_id       INTEGER,
    from_id         TEXT NOT NULL,
    to_id           TEXT,
    portnum         INTEGER,
    hop_start       INTEGER,
    hop_limit       INTEGER,
    hops_taken      INTEGER,
    rx_snr          REAL,
    rx_rssi         REAL,
    via_mqtt        INTEGER DEFAULT 0,
    relay_node      TEXT,
    channel         INTEGER,
    FOREIGN KEY (from_id) REFERENCES nodes(node_id)
);
CREATE INDEX IF NOT EXISTS idx_packets_time ON packet_observations(timestamp);
CREATE INDEX IF NOT EXISTS idx_packets_from ON packet_observations(from_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_packets_hops ON packet_observations(hops_taken);

CREATE TABLE IF NOT EXISTS traceroutes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       INTEGER NOT NULL,
    origin_id       TEXT NOT NULL,
    destination_id  TEXT NOT NULL,
    route_forward   TEXT,
    snr_forward     TEXT,
    route_back      TEXT,
    snr_back        TEXT,
    hop_count       INTEGER,
    completed       INTEGER DEFAULT 0,
    FOREIGN KEY (origin_id) REFERENCES nodes(node_id),
    FOREIGN KEY (destination_id) REFERENCES nodes(node_id)
);
CREATE INDEX IF NOT EXISTS idx_traceroutes_time ON traceroutes(timestamp);
CREATE INDEX IF NOT EXISTS idx_traceroutes_origin ON traceroutes(origin_id);

CREATE TABLE IF NOT EXISTS black_holes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    center_lat      REAL NOT NULL,
    center_lon      REAL NOT NULL,
    radius_km       REAL NOT NULL,
    severity        REAL NOT NULL DEFAULT 0.0,
    evidence_type   TEXT NOT NULL,
    affected_nodes  TEXT,
    description     TEXT,
    first_detected  INTEGER NOT NULL,
    last_updated    INTEGER NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    notified        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_blackholes_active ON black_holes(active);

CREATE TABLE IF NOT EXISTS node_routing_stats (
    node_id             TEXT PRIMARY KEY,
    packets_seen        INTEGER DEFAULT 0,
    packets_as_relay    INTEGER DEFAULT 0,
    avg_hops_taken      REAL,
    expected_hops       REAL,
    forwarding_ratio    REAL,
    via_mqtt_pct        REAL,
    asymmetric_links    INTEGER DEFAULT 0,
    last_updated        INTEGER NOT NULL,
    FOREIGN KEY (node_id) REFERENCES nodes(node_id)
);

"""
