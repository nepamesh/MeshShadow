import io
import logging
import time
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

import config
from database.store import DataStore
from analysis.correlation import correlate_snr_weather

log = logging.getLogger(__name__)


def _ts_to_dt(ts):
    return datetime.fromtimestamp(ts)


def chart_snr_history(store: DataStore, node_a: str, node_b: str, hours: int = 24):
    """Line chart of SNR over time for a specific link pair. Returns PNG BytesIO."""
    start = int(time.time()) - hours * 3600
    obs = store.get_link_observations(start_ts=start, node_a=node_a, node_b=node_b)
    obs = [o for o in obs if o["snr"] is not None]

    if not obs:
        return None

    times = [_ts_to_dt(o["timestamp"]) for o in obs]
    snrs = [o["snr"] for o in obs]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(times, snrs, "o-", markersize=3, linewidth=1, color="#0066cc")
    ax.set_xlabel("Time")
    ax.set_ylabel("SNR (dB)")

    a_label = _get_label(store, node_a)
    b_label = _get_label(store, node_b)
    ax.set_title(f"SNR History: {a_label} ↔ {b_label} (last {hours}h)")

    ax.axhline(y=0, color="#cc0000", linestyle="--", alpha=0.5, label="0 dB threshold")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    fig.autofmt_xdate()
    ax.legend()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_network_activity(store: DataStore, hours: int = 24):
    """Bar chart of link observations per hour. Returns PNG BytesIO."""
    start = int(time.time()) - hours * 3600
    obs = store.get_link_observations(start_ts=start, limit=50000)

    if not obs:
        return None

    # Bucket by hour
    buckets = {}
    for o in obs:
        hour_ts = (o["timestamp"] // 3600) * 3600
        buckets[hour_ts] = buckets.get(hour_ts, 0) + 1

    if not buckets:
        return None

    times = [_ts_to_dt(ts) for ts in sorted(buckets.keys())]
    counts = [buckets[ts] for ts in sorted(buckets.keys())]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(times, counts, width=1/24, color="#0066cc", alpha=0.7)
    ax.set_xlabel("Time")
    ax.set_ylabel("Link Observations")
    ax.set_title(f"Network Activity (last {hours}h)")
    ax.grid(True, alpha=0.3, axis="y")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    fig.autofmt_xdate()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_node_battery(store: DataStore, node_id: str, hours: int = 24):
    """Line chart of battery level and voltage over time. Returns PNG BytesIO."""
    metrics = store.get_node_metrics_history(node_id, hours)
    metrics = [m for m in metrics if m["battery_level"] is not None or m["voltage"] is not None]

    if not metrics:
        return None

    times = [_ts_to_dt(m["timestamp"]) for m in metrics]
    label = _get_label(store, node_id)

    fig, ax1 = plt.subplots(figsize=(10, 4))

    # Battery level on left axis
    bat = [m["battery_level"] for m in metrics]
    if any(b is not None for b in bat):
        ax1.plot(times, bat, "o-", markersize=2, linewidth=1, color="#00cc00", label="Battery %")
        ax1.set_ylabel("Battery Level (%)", color="#00cc00")
        ax1.set_ylim(0, 105)

    # Voltage on right axis
    volts = [m["voltage"] for m in metrics]
    if any(v is not None for v in volts):
        ax2 = ax1.twinx()
        ax2.plot(times, volts, "o-", markersize=2, linewidth=1, color="#cc6600", label="Voltage")
        ax2.set_ylabel("Voltage (V)", color="#cc6600")

    ax1.set_xlabel("Time")
    ax1.set_title(f"Battery History: {label} (last {hours}h)")
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    fig.autofmt_xdate()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_weather_correlation(store: DataStore, hours: int = 168):
    """Scatter plots: SNR vs weather parameters. Returns PNG BytesIO."""
    data = correlate_snr_weather(store, hours)
    if not data or data["count"] < 5:
        return None

    fields = [
        ("temperature", "Temperature (°F)", "#cc3300"),
        ("humidity", "Humidity (%)", "#0066cc"),
        ("pressure", "Pressure (hPa)", "#009900"),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    for ax, (field, xlabel, color) in zip(axes, fields):
        x = data[field]
        y = data["snr"]
        # Filter out None pairs
        if field == "temperature":
            x = [xi * 9 / 5 + 32 if xi is not None else None for xi in x]
        pairs = [(xi, yi) for xi, yi in zip(x, y) if xi is not None and yi is not None]
        if not pairs:
            ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
            continue

        px, py = zip(*pairs)
        ax.scatter(px, py, alpha=0.3, s=10, color=color)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("SNR (dB)")

        r = data["correlations"].get(field)
        if r is not None:
            ax.set_title(f"r = {r:.3f}")
        ax.grid(True, alpha=0.3)

    fig.suptitle(f"SNR vs Weather Conditions (last {hours // 24}d)", fontsize=14)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_mesh_overview(store: DataStore):
    """Simple node positions plotted on a matplotlib chart (for Discord embeds). Returns PNG BytesIO."""
    nodes = store.get_active_nodes(24)
    links = store.get_latest_links(24)

    positioned = [n for n in nodes if n["latitude"] and n["longitude"]]
    if not positioned:
        return None

    fig, ax = plt.subplots(figsize=(10, 8))

    # Draw links
    for link in links:
        if not all([link["node_a_lat"], link["node_a_lon"], link["node_b_lat"], link["node_b_lon"]]):
            continue
        color = _snr_color_mpl(link["avg_snr"])
        ax.plot(
            [link["node_a_lon"], link["node_b_lon"]],
            [link["node_a_lat"], link["node_b_lat"]],
            "-", color=color, alpha=0.6, linewidth=1.5,
        )

    # Draw nodes
    for node in positioned:
        age = int(time.time()) - node["last_seen"]
        color = "#00cc00" if age < 3600 else "#cccc00" if age < 86400 else "#cc0000"
        label = node.get("short_name") or node["node_id"][-4:]
        ax.plot(node["longitude"], node["latitude"], "o", color=color, markersize=10)
        ax.annotate(label, (node["longitude"], node["latitude"]),
                    textcoords="offset points", xytext=(5, 5), fontsize=8)

    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(f"{config.SITE_ORG_NAME} Network Overview ({len(positioned)} nodes)")
    ax.grid(True, alpha=0.3)
    ax.set_aspect("equal")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _snr_color_mpl(snr):
    if snr is None:
        return "#888888"
    if snr >= 10:
        return "#00cc00"
    elif snr >= 0:
        return "#cccc00"
    else:
        return "#cc0000"


def _get_label(store, node_id):
    node = store.get_node(node_id)
    if node:
        return node.get("short_name") or node.get("long_name") or node_id
    return node_id
