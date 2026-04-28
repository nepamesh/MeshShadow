# MeshShadow

RF propagation, coverage, and shadow-zone analytics for [Meshtastic](https://meshtastic.org/) networks. MeshShadow ingests live MQTT traffic from a Meshtastic mesh, correlates link quality with weather, models terrain-driven shadow zones, detects packet black holes, and surfaces it all through a web dashboard and a Discord bot.

## Features

- **Live MQTT ingest** — subscribes to Meshtastic MQTT topics, decrypts packets with the configured PSK, and stores nodes, positions, neighbor links, and packet metadata in SQLite.
- **Coverage & shadow mapping** — builds a configurable grid around the mesh center, fetches SRTM elevation data, and computes per-cell coverage and terrain-shadowed dead zones.
- **Placement suggestions** — recommends candidate locations to fill detected dead zones based on terrain and current coverage.
- **Anomaly detection** — flags links whose SNR deviates from baseline and links that have gone silent.
- **Black-hole detection** — identifies nodes that receive but do not relay traffic.
- **Weather correlation** — fetches periodic weather for the mesh center and correlates with link quality.
- **Web dashboard** — Flask + Folium maps, Matplotlib charts, served by Waitress.
- **Discord bot** — slash commands for stats and dead-zone reports, plus alert push to a channel.

## Architecture

```
┌──────────────┐     ┌────────────────┐
│ Meshtastic   │────▶│ MQTT subscriber│──┐
│  MQTT broker │     └────────────────┘  │
└──────────────┘                         │
                                         ▼
┌────────────┐   ┌──────────────────┐   ┌──────────────┐
│ Open-Meteo │──▶│ Weather fetcher  │──▶│   SQLite DB  │
└────────────┘   └──────────────────┘   │ (data/*.db)  │
                                         └───┬──────┬───┘
┌──────────────┐                             │      │
│ OpenTopoData │──▶ Elevation fetcher  ──────┘      │
└──────────────┘                                    │
                                                    ▼
                  ┌─────────────────────────────────────┐
                  │ Background pipelines                │
                  │  • Coverage / shadow recompute      │
                  │  • Anomaly detection                │
                  │  • Black-hole detection             │
                  │  • Coverage snapshots               │
                  └────────────┬────────────────────────┘
                               │
                  ┌────────────┴───────────┐
                  ▼                        ▼
           ┌────────────┐           ┌─────────────┐
           │ Flask web  │           │ Discord bot │
           │ dashboard  │           │  (alerts)   │
           └────────────┘           └─────────────┘
```

## Requirements

- Docker + Docker Compose (recommended), or Python 3.12 with the packages in `requirements.txt`.
- Access to a Meshtastic MQTT broker (community or self-hosted).
- A Discord bot token (optional — the dashboard runs without it).
- Outbound network access to `api.opentopodata.org` and `api.open-meteo.com`.

## Quick start (Docker)

```bash
git clone https://github.com/nepamesh/MeshShadow.git
cd MeshShadow
cp .env.example .env
# edit .env: MQTT credentials, DISCORD_TOKEN, MESH_CENTER_LAT/LON, PROXY_SECRET, etc.
docker compose up -d --build
```

The dashboard binds to `0.0.0.0:5000` by default. To bind to a specific host IP, set `WEB_BIND_ADDR` in `.env` (e.g. `WEB_BIND_ADDR=192.168.1.10`).

## Quick start (Python)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then edit
set -a; source .env; set +a
python main.py
```

## Configuration

All configuration is via environment variables (typically through `.env`). See `.env.example` for the complete list. Highlights:

| Variable | Purpose |
| --- | --- |
| `MQTT_HOST` / `MQTT_PORT` / `MQTT_USER` / `MQTT_PASS` | Meshtastic MQTT broker |
| `MQTT_TOPICS` | Comma-separated topic filters |
| `MESH_KEY` | Base64 PSK (defaults to the standard Meshtastic public key) |
| `MESH_CENTER_LAT` / `MESH_CENTER_LON` | Center point for grid + weather |
| `DISCORD_TOKEN` / `DISCORD_ALERT_CHANNEL_ID` | Discord bot (omit token to skip) |
| `WEB_PORT` / `WEB_BASE_URL` | Web dashboard |
| `PROXY_SECRET` | Shared secret for the reverse-proxy gate (see [Reverse proxy](#reverse-proxy-caddy)) — generate with `openssl rand -hex 32` |
| `GRID_CELL_SIZE_M` / `GRID_PADDING_KM` / `MAX_NODE_RANGE_KM` | Shadow grid sizing |
| `SHADOW_THRESHOLD` / `MIN_DEAD_ZONE_CELLS` | Dead-zone detection sensitivity |

## Project layout

```
analysis/      Coverage, shadow, propagation, placement, black-hole analytics
database/      SQLite schema + DataStore facade
discord_bot/   discord.py bot, slash commands, alerts
mqtt/          paho-mqtt subscriber + Meshtastic decoder
rendering/     Folium maps and Matplotlib charts
weather/       Open-Meteo fetcher
web/           Flask app, routes, templates, static assets
config.py      Env-var configuration
main.py        Entry point — wires up all subsystems
generate_docs.py  Builds the setup-guide PDF
```

## Data persistence

SQLite (WAL mode) at `DB_PATH` (default `data/meshprop.db`). The Docker setup mounts a named volume `meshprop-data` at `/app/data`. The `data/` directory is gitignored.

## Reverse proxy (Caddy)

When `PROXY_SECRET` is set, the Flask app's `before_request` hook rejects any request that does not present a matching `X-Proxy-Secret` header (HMAC-compared, returns 403 otherwise). This is intentional: MeshShadow is designed to sit behind a reverse proxy — typically [Caddy](https://caddyserver.com/) — that terminates TLS, adds the shared-secret header, and forwards to the container. Direct hits to the app port from outside the proxy are blocked.

Leave `PROXY_SECRET` unset in development to disable the check.

Example Caddyfile fragment for a NEPAMesh-style deployment:

```caddyfile
propagation.example.com {
    reverse_proxy 127.0.0.1:5000 {
        header_up X-Proxy-Secret {env.PROXY_SECRET}
    }
}
```

Export `PROXY_SECRET` in Caddy's environment (e.g. via `systemctl edit caddy` → `Environment="PROXY_SECRET=..."`) and set the same value in the app's `.env`. Pair this with binding the app to localhost only — set `WEB_BIND_ADDR=127.0.0.1` in `.env` so the container port is unreachable except via Caddy.

## Security notes

- `.env` is gitignored — never commit real tokens or broker credentials.
- `PROXY_SECRET` gates every HTTP request; see [Reverse proxy](#reverse-proxy-caddy). Leave empty in development, set a strong value (and bind to localhost) in production.
- The container runs as a non-root `appuser`.
- The default `MESH_KEY` is the Meshtastic public-channel key; change it if your mesh uses a custom PSK.

## License

Add a license of your choice (e.g. MIT) before publishing.
