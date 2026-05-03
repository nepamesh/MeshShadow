import os

# MQTT Configuration
MQTT_HOST = os.getenv("MQTT_HOST", "")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")
MQTT_TOPICS = os.getenv("MQTT_TOPICS", "msh/US/2/e/#").split(",")

# Meshtastic encryption (default key)
MESH_DEFAULT_KEY = os.getenv("MESH_KEY", "1PG7OiApB1nwvP+rz05pAQ==")

# Database
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "meshprop.db"))

# Mesh geographic center (NE PA) — used for weather fetching
MESH_CENTER_LAT = float(os.getenv("MESH_CENTER_LAT", "41.0"))
MESH_CENTER_LON = float(os.getenv("MESH_CENTER_LON", "-75.9"))

# Weather
WEATHER_INTERVAL_SEC = int(os.getenv("WEATHER_INTERVAL_SEC", "900"))  # 15 minutes

# Shadow analysis grid
GRID_CELL_SIZE_M = int(os.getenv("GRID_CELL_SIZE_M", "100"))
GRID_PADDING_KM = float(os.getenv("GRID_PADDING_KM", "2.0"))
MAX_NODE_RANGE_KM = float(os.getenv("MAX_NODE_RANGE_KM", "15.0"))
COVERAGE_RECALC_INTERVAL_SEC = int(os.getenv("COVERAGE_RECALC_SEC", "600"))  # 10 minutes
SHADOW_THRESHOLD = float(os.getenv("SHADOW_THRESHOLD", "0.6"))
MIN_DEAD_ZONE_CELLS = int(os.getenv("MIN_DEAD_ZONE_CELLS", "5"))

# Elevation API
ELEVATION_API_URL = os.getenv("ELEVATION_API_URL", "https://api.opentopodata.org/v1/srtm90m")
ELEVATION_BATCH_SIZE = int(os.getenv("ELEVATION_BATCH_SIZE", "100"))
ELEVATION_RATE_LIMIT_SEC = float(os.getenv("ELEVATION_RATE_LIMIT_SEC", "1.0"))

# Coverage snapshots
SNAPSHOT_INTERVAL_SEC = int(os.getenv("SNAPSHOT_INTERVAL_SEC", "3600"))  # 1 hour

# Anomaly detection
ANOMALY_CHECK_INTERVAL_SEC = int(os.getenv("ANOMALY_CHECK_INTERVAL_SEC", "300"))  # 5 minutes
ANOMALY_SNR_STDDEV_THRESHOLD = float(os.getenv("ANOMALY_SNR_STDDEV", "2.0"))
ANOMALY_MIN_OBSERVATIONS = int(os.getenv("ANOMALY_MIN_OBS", "20"))
ANOMALY_LOST_LINK_HOURS = int(os.getenv("ANOMALY_LOST_LINK_HOURS", "6"))

# Discord
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DISCORD_ALERT_CHANNEL_ID = int(os.getenv("DISCORD_ALERT_CHANNEL_ID", "0"))
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")  # optional, for faster slash command sync

# Web dashboard
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "5000"))
WEB_BASE_URL = os.getenv("WEB_BASE_URL", "http://localhost:5000")

# Rendering cache
CACHE_DIR = os.getenv("CACHE_DIR", os.path.join(os.path.dirname(__file__), "data", "map_cache"))
CACHE_TTL_MAP = int(os.getenv("CACHE_TTL_MAP", "300"))  # 5 minutes
CACHE_TTL_CHART = int(os.getenv("CACHE_TTL_CHART", "120"))  # 2 minutes

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Black hole detection
BLACKHOLE_CHECK_INTERVAL_SEC = int(os.getenv("BLACKHOLE_CHECK_SEC", "600"))  # 10 minutes

# Channel utilization alerts
CHANNEL_UTIL_THRESHOLD = float(os.getenv("CHANNEL_UTIL_THRESHOLD", "40.0"))  # percent
CHANNEL_UTIL_ALERT_COOLDOWN_HOURS = int(os.getenv("CHANNEL_UTIL_COOLDOWN_HOURS", "6"))

# Shadow alert quiet hours and rate limit
SHADOW_ALERT_START_HOUR = int(os.getenv("SHADOW_ALERT_START_HOUR", "9"))    # 9am
SHADOW_ALERT_END_HOUR   = int(os.getenv("SHADOW_ALERT_END_HOUR",   "17"))   # 5pm
SHADOW_ALERT_COOLDOWN_MIN = int(os.getenv("SHADOW_ALERT_COOLDOWN_MIN", "30"))
