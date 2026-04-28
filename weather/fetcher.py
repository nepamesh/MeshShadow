"""Periodic weather sampling for RF/weather correlation.

Polls the free Open-Meteo API for current conditions at the mesh center and
inserts a row into `weather_observations` each time. Link observations
captured by the MQTT subscriber are tagged with the nearest weather row so
the correlation analytics can do a simple FK join rather than a window query.
"""

import logging
import threading
import time

import requests

from database.store import DataStore

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherFetcher:
    """Background poller that writes one weather row per `interval_sec`.

    Open-Meteo requires no API key but is rate-limited; defaults to one
    sample every 15 minutes which is plenty for correlation purposes.
    """

    def __init__(self, store: DataStore, lat: float, lon: float, interval_sec: int = 900):
        self.store = store
        self.lat = lat
        self.lon = lon
        self.interval = interval_sec
        self._stop = threading.Event()

    def start(self):
        t = threading.Thread(target=self._fetch_loop, daemon=True)
        t.start()
        log.info("Weather fetcher started (every %ds for %.4f, %.4f)", self.interval, self.lat, self.lon)

    def stop(self):
        self._stop.set()

    def _fetch_loop(self):
        # Fetch immediately on start, then on interval
        while not self._stop.is_set():
            try:
                self.fetch_current()
            except Exception as e:
                log.error("Weather fetch error: %s", e, exc_info=True)
            self._stop.wait(self.interval)

    def fetch_current(self):
        """Hit Open-Meteo once and persist the result. Returns the new row id."""
        params = {
            "latitude": self.lat,
            "longitude": self.lon,
            "current": ",".join([
                "temperature_2m",
                "relative_humidity_2m",
                "surface_pressure",
                "precipitation",
                "cloud_cover",
                "wind_speed_10m",
                "wind_direction_10m",
            ]),
            "timezone": "auto",
            "forecast_days": 1,
        }
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        current = data.get("current", {})

        weather_id = self.store.insert_weather(
            ts=int(time.time()),
            lat=self.lat,
            lon=self.lon,
            temperature_c=current.get("temperature_2m"),
            humidity_pct=current.get("relative_humidity_2m"),
            pressure_hpa=current.get("surface_pressure"),
            precipitation_mm=current.get("precipitation"),
            cloud_cover_pct=current.get("cloud_cover"),
            wind_speed_kmh=current.get("wind_speed_10m"),
            wind_direction_deg=current.get("wind_direction_10m"),
        )
        log.info("Weather fetched: %.1f°C, %s%% humidity, %.1f hPa (id=%d)",
                 current.get("temperature_2m", 0),
                 current.get("relative_humidity_2m", 0),
                 current.get("surface_pressure", 0),
                 weather_id)
        return weather_id
