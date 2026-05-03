import gc
import logging
import os
import sys
import threading
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
import config
from database.store import DataStore
from mqtt.subscriber import MQTTSubscriber
from weather.fetcher import WeatherFetcher
from analysis.propagation import detect_anomalies
from analysis.coverage import recalculate
from analysis.shadows import update_dead_zones
from analysis.placement import suggest_placements
from analysis.terrain import ElevationFetcher
from analysis.blackholes import run_black_hole_detection
from web.app import create_flask_app
from discord_bot.bot import create_bot

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("meshpropagation")


def run_anomaly_detector(store: DataStore):
    """Periodically check for anomalous propagation events."""
    while True:
        time.sleep(config.ANOMALY_CHECK_INTERVAL_SEC)
        try:
            detect_anomalies(
                store,
                snr_stddev_threshold=config.ANOMALY_SNR_STDDEV_THRESHOLD,
                min_observations=config.ANOMALY_MIN_OBSERVATIONS,
                lost_link_hours=config.ANOMALY_LOST_LINK_HOURS,
            )
        except Exception as e:
            log.error("Anomaly detection error: %s", e, exc_info=True)


def run_coverage_pipeline(store: DataStore, elevation_fetcher: ElevationFetcher):
    """Periodically recalculate coverage grid, detect dead zones, suggest placements."""
    while True:
        time.sleep(config.COVERAGE_RECALC_INTERVAL_SEC)
        try:
            log.info("Running coverage pipeline...")
            result = recalculate(
                store,
                cell_size_m=config.GRID_CELL_SIZE_M,
                padding_km=config.GRID_PADDING_KM,
                max_range_km=config.MAX_NODE_RANGE_KM,
            )
            if result:
                del result
                gc.collect()

                update_dead_zones(
                    store,
                    shadow_threshold=config.SHADOW_THRESHOLD,
                    min_cells=config.MIN_DEAD_ZONE_CELLS,
                    mesh_center_lat=config.MESH_CENTER_LAT,
                    mesh_center_lon=config.MESH_CENTER_LON,
                )

                suggest_placements(store, max_range_km=config.MAX_NODE_RANGE_KM,
                                  elevation_fetcher=elevation_fetcher)
                log.info("Coverage pipeline complete")
        except Exception as e:
            log.error("Coverage pipeline error: %s", e, exc_info=True)


def run_snapshot_taker(store: DataStore):
    """Periodically take coverage snapshots for timeline tracking."""
    while True:
        time.sleep(config.SNAPSHOT_INTERVAL_SEC)
        try:
            summary = store.get_coverage_summary()
            zones = store.get_dead_zones(active_only=True)
            store.insert_coverage_snapshot(
                total_cells=summary["total_cells"],
                covered=summary["covered_cells"],
                shadow=summary["shadow_cells"],
                coverage_pct=summary["coverage_pct"],
                total_area=summary["total_area_km2"],
                covered_area=summary["covered_area_km2"],
                shadow_area=summary["shadow_area_km2"],
                active_nodes=summary["active_nodes_24h"],
                dead_zone_count=len(zones),
            )
            log.info("Coverage snapshot: %.1f%% coverage, %d dead zones",
                     summary["coverage_pct"], len(zones))
        except Exception as e:
            log.error("Snapshot error: %s", e, exc_info=True)


def run_elevation_fetcher(store: DataStore, elevation_fetcher: ElevationFetcher):
    """Progressively fetch elevation data for grid cells."""
    time.sleep(config.COVERAGE_RECALC_INTERVAL_SEC + 30)
    while True:
        try:
            grid_meta = store.get_grid_metadata()
            if grid_meta:
                elevation_fetcher.fetch_grid_elevations(store, grid_meta, batch_limit=500)
            time.sleep(300)
        except Exception as e:
            log.error("Elevation fetcher error: %s", e, exc_info=True)
            time.sleep(60)



def run_maintenance(store: DataStore):
    """Hourly housekeeping: purge stale nodes and old packet observations."""
    while True:
        time.sleep(3600)
        try:
            store.cleanup_old_nodes(max_age_hours=24)
            store.cleanup_old_packets(max_age_hours=72)
            log.info("Maintenance: pruned nodes >24h and packets >72h")
        except Exception as e:
            log.error("Maintenance error: %s", e, exc_info=True)


def run_black_hole_detector(store: DataStore):
    """Periodically run black hole detection analysis."""
    # Wait for some packet data to accumulate before first run
    time.sleep(config.BLACKHOLE_CHECK_INTERVAL_SEC)
    while True:
        try:
            result = run_black_hole_detection(
                store,
                hours=24,
                mesh_center_lat=config.MESH_CENTER_LAT,
                mesh_center_lon=config.MESH_CENTER_LON,
            )
            log.info("Black hole detection: %d active, %d nodes analyzed",
                     result["total_active"], result["nodes_analyzed"])
        except Exception as e:
            log.error("Black hole detection error: %s", e, exc_info=True)
        time.sleep(config.BLACKHOLE_CHECK_INTERVAL_SEC)


def main():
    # Ensure data directories exist
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    os.makedirs(config.CACHE_DIR, exist_ok=True)

    # 1. Initialize database
    store = DataStore(config.DB_PATH)
    store.initialize()
    log.info("Database ready")

    # 2. Start MQTT subscriber
    mqtt_sub = MQTTSubscriber(
        store=store,
        host=config.MQTT_HOST,
        port=config.MQTT_PORT,
        user=config.MQTT_USER,
        password=config.MQTT_PASS,
        topics=config.MQTT_TOPICS,
        default_key_b64=config.MESH_DEFAULT_KEY,
    )
    mqtt_sub.start()
    log.info("MQTT subscriber started")

    # 3. Start weather fetcher
    weather = WeatherFetcher(
        store=store,
        lat=config.MESH_CENTER_LAT,
        lon=config.MESH_CENTER_LON,
        interval_sec=config.WEATHER_INTERVAL_SEC,
    )
    weather.start()
    log.info("Weather fetcher started")

    # 4. Start anomaly detector
    threading.Thread(target=run_anomaly_detector, args=(store,), daemon=True).start()
    log.info("Anomaly detector started")

    # 4b. Start coverage pipeline (shadow mapper)
    elevation_fetcher = ElevationFetcher(
        store=store,
        api_url=config.ELEVATION_API_URL,
        batch_size=config.ELEVATION_BATCH_SIZE,
        rate_limit_sec=config.ELEVATION_RATE_LIMIT_SEC,
    )
    threading.Thread(target=run_coverage_pipeline, args=(store, elevation_fetcher), daemon=True).start()
    log.info("Coverage pipeline started (interval: %ds)", config.COVERAGE_RECALC_INTERVAL_SEC)

    # 4c. Start elevation fetcher
    threading.Thread(target=run_elevation_fetcher, args=(store, elevation_fetcher), daemon=True).start()
    log.info("Elevation fetcher started")

    # 4d. Start snapshot taker
    threading.Thread(target=run_snapshot_taker, args=(store,), daemon=True).start()
    log.info("Snapshot taker started (interval: %ds)", config.SNAPSHOT_INTERVAL_SEC)

    # 4e. Start black hole detector
    threading.Thread(target=run_black_hole_detector, args=(store,), daemon=True).start()
    log.info("Black hole detector started (interval: %ds)", config.BLACKHOLE_CHECK_INTERVAL_SEC)

    # 4f. Start maintenance thread (node/packet pruning)
    threading.Thread(target=run_maintenance, args=(store,), daemon=True).start()
    log.info("Maintenance thread started (hourly)")

    # 5. Start Flask web dashboard (waitress)
    flask_app = create_flask_app(store)

    def run_waitress():
        from waitress import serve
        serve(flask_app, host=config.WEB_HOST, port=config.WEB_PORT,
              threads=4, channel_timeout=30, ident=None)

    flask_thread = threading.Thread(target=run_waitress, daemon=True)
    flask_thread.start()
    log.info("Web dashboard started on %s:%d (waitress)", config.WEB_HOST, config.WEB_PORT)

    # 6. Start Discord bot (runs on main thread)
    if config.DISCORD_TOKEN:
        log.info("Starting Discord bot...")
        bot = create_bot(
            store=store,
            alert_channel_id=config.DISCORD_ALERT_CHANNEL_ID,
            guild_id=config.DISCORD_GUILD_ID,
            web_base_url=config.WEB_BASE_URL,
        )
        bot.run(config.DISCORD_TOKEN, log_handler=None)
    else:
        log.warning("No DISCORD_TOKEN set — running without Discord bot")
        log.info("Web dashboard available at %s:%d", config.WEB_HOST, config.WEB_PORT)
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            log.info("Shutting down...")

    mqtt_sub.stop()
    weather.stop()


if __name__ == "__main__":
    main()
