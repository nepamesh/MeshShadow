# MeshShadow Architecture

This document describes how MeshShadow's processes, threads, and data stores fit together. It's aimed at contributors and operators who need to reason about behaviour beyond the per-module docstrings.

## Process model

MeshShadow runs as **one Python process** that hosts:

- A foreground asyncio event loop (the Discord bot, on the main thread)
- A waitress-served Flask app (in its own daemon thread)
- A paho-mqtt network thread (started by `client.loop_start()`)
- Five Python `threading.Thread` workers for periodic analytics
- A weather-fetcher thread

If `DISCORD_TOKEN` is unset, the bot is skipped and the main thread parks in a `time.sleep(60)` loop instead. All non-Discord work continues on its own threads.

```
┌────────── main process (python main.py) ──────────────────────┐
│                                                                │
│  Main thread ──── Discord bot (asyncio loop)                   │
│                   ├─ slash command handlers                    │
│                   ├─ AnomalyAlertDispatcher    (async task)    │
│                   ├─ ShadowAlertDispatcher     (async task)    │
│                   └─ BlackHoleAlertDispatcher  (async task)    │
│                                                                │
│  Daemon threads:                                               │
│   • paho-mqtt loop          (MQTT ingest → DataStore)          │
│   • WeatherFetcher          (Open-Meteo → DataStore)           │
│   • run_anomaly_detector    (analysis/propagation.py)          │
│   • run_coverage_pipeline   (analysis/coverage + shadows +     │
│                              placement)                        │
│   • run_elevation_fetcher   (analysis/terrain.py)              │
│   • run_snapshot_taker      (coverage_snapshots row insert)    │
│   • run_black_hole_detector (analysis/blackholes.py)           │
│   • waitress (Flask)        (web/app.py + web/routes.py)       │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

All threads communicate exclusively through the **DataStore** (SQLite). There are no in-memory queues or shared caches between threads — each loop reads its inputs from the database, computes, and writes results back.

## Threading & concurrency model

`DataStore` (`database/store.py`) holds a single `sqlite3.Connection` opened with `check_same_thread=False` and serialises every read and write through one `threading.RLock`. SQLite is configured with `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`. WAL means readers don't block writers, but this code keeps things simple by serialising everything through the lock — concurrency is gated by the workers' cadences, not by raw throughput.

This is fine because:

- MQTT writes are short and frequent, but each one is small.
- Coverage pipeline writes are larger but infrequent (every 10 minutes).
- The web layer is read-mostly.

If write throughput ever becomes a bottleneck, the path of least resistance is to give the coverage pipeline its own connection and let WAL do its job, rather than splitting into multiple processes.

## Background loop cadences

| Loop | File | Default interval | What it does |
|---|---|---|---|
| MQTT ingest | `mqtt/subscriber.py` | continuous | ServiceEnvelope → MeshPacket → decrypt → portnum dispatch → DataStore writes |
| Weather fetch | `weather/fetcher.py` | 15 min | Open-Meteo current conditions for the mesh center |
| Anomaly detection | `analysis/propagation.py` | 5 min | σ-based ducting/fade, lost-link, new-link |
| Coverage pipeline | `analysis/coverage.py` + `shadows.py` + `placement.py` | 10 min | Recompute grid → detect dead zones → suggest placements |
| Elevation fetch | `analysis/terrain.py` | 5 min after first coverage run, then every 5 min | Backfill SRTM elevation for grid cells |
| Coverage snapshot | `main.py::run_snapshot_taker` | 1 hr | Append a row to `coverage_snapshots` for timeline charting |
| Black-hole detection | `analysis/blackholes.py` | 10 min | Asymmetric-link / hop-anomaly / mqtt-leak / traceroute-detour heuristics |
| Anomaly alerts | `discord_bot/alerts.py` | 60 s | Drain `anomalies` table, post unsent rows to Discord |
| Shadow alerts | `discord_bot/alerts.py` | 5 min | New dead zones + coverage-drop notifications |
| Black-hole alerts | `discord_bot/alerts.py` | 2 min | Drain unsent black-hole detections |

All intervals are tunable via env vars; see `.env.example` and `config.py`.

## Data flow

### Ingest path

```
Meshtastic broker ── MQTT ──▶ MQTTSubscriber._on_message
                                  │
                                  ├─ upsert nodes(last_seen)
                                  ├─ insert packet_observations         (always — feeds black-hole detector)
                                  └─ portnum dispatch:
                                       POSITION_APP    → positions
                                       TELEMETRY_APP   → device_metrics on nodes
                                       NODEINFO_APP    → nodes (name/hw/role)
                                       NEIGHBORINFO_APP→ link_observations  ◀── primary SNR source
                                       TRACEROUTE_APP  → traceroutes
```

`link_observations` rows are tagged with the nearest `weather_observations.id` at insert time (`store.get_weather_near_time`) so weather correlation is a cheap FK join, not a window query.

### Analytics path

```
link_observations + nodes  ──▶  detect_anomalies          ──▶ anomalies
nodes + grid + elevation   ──▶  recalculate (coverage)    ──▶ coverage_grid (shadow_score)
coverage_grid              ──▶  detect/update_dead_zones  ──▶ dead_zones + dead_zone_cells
coverage_grid + nodes      ──▶  suggest_placements        ──▶ placement_suggestions
packet_observations + traceroutes ─▶ run_black_hole_detection ─▶ blackhole_detections
coverage_grid + dead_zones ──▶  run_snapshot_taker        ──▶ coverage_snapshots
```

### Egress paths

```
DataStore ──▶  Flask routes (web/routes.py)  ──▶ HTML/JSON  ──▶ user browser
DataStore ──▶  Discord slash commands         ──▶ embeds    ──▶ Discord channel
DataStore ──▶  alert dispatchers              ──▶ embeds    ──▶ Discord channel (push)
```

## Database schema (highlights)

Full DDL lives in `database/schema.py`. Key tables:

- **`nodes`** — current node state (name, hw, role, last_seen, latest device metrics).
- **`positions`** — historical GPS fixes per node.
- **`link_observations`** — every reported neighbor SNR. Carries a `weather_id` FK for correlation. Indexed on `(timestamp)` and `(node_a_id, node_b_id)`.
- **`packet_observations`** — one row per received MeshPacket envelope, regardless of portnum. Source of truth for the black-hole detector (hop_start/hop_limit, relay_node, via_mqtt, channel).
- **`weather_observations`** — periodic Open-Meteo samples.
- **`coverage_grid`** — per-cell shadow score and elevation. Sized by `GRID_CELL_SIZE_M`, `GRID_PADDING_KM`, mesh extent.
- **`dead_zones`** + **`dead_zone_cells`** — persisted shadow regions with stable ids; reconciled on each coverage pass via cell overlap (>30% → same zone, update in place; otherwise → new zone or deactivate).
- **`placement_suggestions`** — replaced wholesale on each coverage pass.
- **`anomalies`** / **`blackhole_detections`** — append-only event tables with a `notified` flag for Discord dedup.
- **`coverage_snapshots`** — hourly rollup for the coverage-over-time chart.
- **`elevation_cache`** — keyed on `(round(lat, 5), round(lon, 5))`. Callers must use the same rounding when reading.

## Web layer

`web/app.py` builds the Flask app and registers the `/proxy/...`-agnostic security gate: when `PROXY_SECRET` is set, **every request** must present a matching `X-Proxy-Secret` header (HMAC-compared, 403 otherwise). MeshShadow is intended to sit behind a reverse proxy (Caddy) that injects this header — see the README's "Reverse proxy (Caddy)" section. With `PROXY_SECRET` unset, the gate is disabled (development mode).

Routes are defined in `web/routes.py`. Rendering helpers live in `rendering/` — Folium for interactive maps, Matplotlib for static PNG charts. Charts have short TTL caches in `data/map_cache/` keyed by query.

## Discord layer

`discord_bot/bot.py` is the factory: it builds a `discord.Client`, registers slash commands from `discord_bot/commands.py`, and on `on_ready` schedules the three alert dispatchers as long-running asyncio tasks.

Slash command convention: defer the response immediately, render the chart on a worker thread via `asyncio.to_thread` (matplotlib is not asyncio-friendly), then post via `interaction.followup.send` with the PNG attached and an embed that links back to the corresponding view in the web dashboard.

## Adding a new analyzer

If you wanted to add, say, a "node uptime" analytic that runs every 15 minutes:

1. Create `analysis/uptime.py` with a top-level `run_uptime_analysis(store)` function. Read from existing tables; either write to a new table you add in `database/schema.py` or extend an existing table.
2. Add a thread launcher in `main.py` modelled after `run_anomaly_detector`.
3. If you need to surface results, add a route in `web/routes.py` and a template, and/or a slash command in `discord_bot/commands.py`.
4. If you need alerts, add a dispatcher class in `discord_bot/alerts.py` and start it from `discord_bot/bot.py::on_ready`.

There's no plugin loader — wiring is explicit in `main.py` so the threading model stays obvious.

## What is intentionally not here

- **No message queue / broker** between threads. SQLite is the queue.
- **No multi-process scaling.** The whole app is one process; the read-mostly web layer is the only thing that would benefit from horizontal scaling, and it doesn't yet need it.
- **No ORM.** `DataStore` is a thin facade over `sqlite3` with named methods. Schema changes are hand-written DDL in `database/schema.py`.
- **No background job framework** (Celery, RQ, etc.). All schedules are `time.sleep` loops in dedicated threads.
- **No structured/JSON logging.** Plain `logging` to stdout, captured by Docker's json-file driver.
