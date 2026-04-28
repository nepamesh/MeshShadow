import logging

from database.store import DataStore

log = logging.getLogger(__name__)


def correlate_snr_weather(store: DataStore, hours: int = 168):
    """Join link observations with weather data and return correlation data for plotting."""
    data = store.get_links_with_weather(hours)
    if not data:
        return None

    result = {
        "snr": [],
        "temperature": [],
        "humidity": [],
        "pressure": [],
        "cloud_cover": [],
        "wind_speed": [],
        "precipitation": [],
        "count": len(data),
    }

    for row in data:
        if row["snr"] is None:
            continue
        result["snr"].append(row["snr"])
        result["temperature"].append(row.get("temperature_c"))
        result["humidity"].append(row.get("humidity_pct"))
        result["pressure"].append(row.get("pressure_hpa"))
        result["cloud_cover"].append(row.get("cloud_cover_pct"))
        result["wind_speed"].append(row.get("wind_speed_kmh"))
        result["precipitation"].append(row.get("precipitation_mm"))

    # Compute simple Pearson correlation coefficients
    result["correlations"] = {}
    for field in ["temperature", "humidity", "pressure", "cloud_cover", "wind_speed"]:
        r = _pearson(result["snr"], result[field])
        if r is not None:
            result["correlations"][field] = round(r, 3)

    return result


def _pearson(x_list, y_list):
    """Compute Pearson correlation coefficient between two lists, ignoring None values."""
    pairs = [(x, y) for x, y in zip(x_list, y_list) if x is not None and y is not None]
    n = len(pairs)
    if n < 5:
        return None

    x = [p[0] for p in pairs]
    y = [p[1] for p in pairs]

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    std_x = sum((xi - mean_x) ** 2 for xi in x) ** 0.5
    std_y = sum((yi - mean_y) ** 2 for yi in y) ** 0.5

    if std_x == 0 or std_y == 0:
        return None

    return cov / (std_x * std_y)
