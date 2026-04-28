import io
import logging
import time
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from database.store import DataStore

log = logging.getLogger(__name__)


def _ts_to_dt(ts):
    return datetime.fromtimestamp(ts)


def chart_coverage_timeline(store: DataStore, days: int = 30):
    snapshots = store.get_coverage_snapshots(days)
    if len(snapshots) < 2:
        return None

    times = [_ts_to_dt(s["timestamp"]) for s in snapshots]
    coverage = [s["coverage_pct"] for s in snapshots]
    nodes = [s["active_nodes"] for s in snapshots]
    shadow = [s["shadow_area_km2"] * 0.386102 for s in snapshots]

    fig, ax1 = plt.subplots(figsize=(10, 5))

    ax1.plot(times, coverage, "-o", color="#00cc00", markersize=3, linewidth=2, label="Coverage %")
    ax1.set_ylabel("Coverage (%)", color="#00cc00")
    ax1.set_ylim(0, 105)
    ax1.set_xlabel("Time")

    ax2 = ax1.twinx()
    ax2.fill_between(times, shadow, alpha=0.2, color="#cc0000", label="Shadow Area")
    ax2.plot(times, shadow, "--", color="#cc0000", linewidth=1, alpha=0.7)
    ax2.set_ylabel("Shadow Area (mi²)", color="#cc0000")

    ax1.set_title(f"Coverage Evolution (last {days} days)")
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    fig.autofmt_xdate()

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_coverage_breakdown(store: DataStore):
    summary = store.get_coverage_summary()
    if summary["total_cells"] == 0:
        return None

    dead_zones = store.get_dead_zones(active_only=True)

    # Count causes
    causes = {"terrain": 0, "distance": 0, "mixed": 0, "unknown": 0}
    for dz in dead_zones:
        cause = dz.get("cause") or "unknown"
        if cause in causes:
            causes[cause] += dz["area_km2"]
        else:
            causes["unknown"] += dz["area_km2"]

    covered = summary["covered_area_km2"]
    shadow_total = summary["shadow_area_km2"]
    categorized = sum(causes.values())
    uncategorized_shadow = max(0, shadow_total - categorized)

    labels = ["Covered"]
    sizes = [covered]
    colors = ["#00cc00"]

    if causes["terrain"] > 0:
        labels.append("Shadow (Terrain)")
        sizes.append(causes["terrain"])
        colors.append("#cc0000")
    if causes["distance"] > 0:
        labels.append("Shadow (Distance)")
        sizes.append(causes["distance"])
        colors.append("#ff8800")
    if causes["mixed"] > 0:
        labels.append("Shadow (Mixed)")
        sizes.append(causes["mixed"])
        colors.append("#cc6600")
    if causes["unknown"] > 0 or uncategorized_shadow > 0:
        labels.append("Shadow (Unknown)")
        sizes.append(causes["unknown"] + uncategorized_shadow)
        colors.append("#888888")

    fig, ax = plt.subplots(figsize=(8, 6))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, colors=colors, autopct="%1.1f%%",
        startangle=90, pctdistance=0.85,
    )
    for text in autotexts:
        text.set_fontsize(10)

    ax.set_title(f"Coverage Breakdown ({summary['total_area_km2'] * 0.386102:.1f} mi² total)")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_dead_zone_evolution(store: DataStore, days: int = 30):
    snapshots = store.get_coverage_snapshots(days)
    if len(snapshots) < 2:
        return None

    times = [_ts_to_dt(s["timestamp"]) for s in snapshots]
    dz_counts = [s["dead_zone_count"] for s in snapshots]
    shadow_areas = [s["shadow_area_km2"] * 0.386102 for s in snapshots]

    fig, ax1 = plt.subplots(figsize=(10, 5))

    ax1.fill_between(times, shadow_areas, alpha=0.3, color="#cc0000")
    ax1.plot(times, shadow_areas, "-", color="#cc0000", linewidth=2, label="Shadow Area (mi²)")
    ax1.set_ylabel("Shadow Area (mi²)", color="#cc0000")
    ax1.set_xlabel("Time")

    ax2 = ax1.twinx()
    ax2.plot(times, dz_counts, "-s", color="#6633cc", markersize=4, linewidth=1.5, label="Dead Zone Count")
    ax2.set_ylabel("Dead Zone Count", color="#6633cc")
    ax2.yaxis.set_major_locator(plt.MaxNLocator(integer=True))

    ax1.set_title(f"Dead Zone Evolution (last {days} days)")
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    fig.autofmt_xdate()

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_shadow_overview(store: DataStore):
    nodes = store.get_nodes_with_positions()
    shadow_cells = store.get_shadow_cells(threshold=0.6)
    suggestions = store.get_placement_suggestions(limit=3)

    if not nodes:
        return None

    fig, ax = plt.subplots(figsize=(10, 8))

    # Shadow cells as red dots
    if shadow_cells:
        step = max(1, len(shadow_cells) // 3000)
        s_lats = [c["center_lat"] for i, c in enumerate(shadow_cells) if i % step == 0]
        s_lons = [c["center_lon"] for i, c in enumerate(shadow_cells) if i % step == 0]
        s_scores = [c["shadow_score"] for i, c in enumerate(shadow_cells) if i % step == 0]
        scatter = ax.scatter(s_lons, s_lats, c=s_scores, cmap="Reds", s=2, alpha=0.4,
                             vmin=0.5, vmax=1.0, zorder=1)
        plt.colorbar(scatter, ax=ax, label="Shadow Score", shrink=0.7)

    # Node positions
    for node in nodes:
        age = int(time.time()) - node["last_seen"]
        color = "#00cc00" if age < 3600 else "#cccc00" if age < 86400 else "#cc0000"
        label = node.get("short_name") or node["node_id"][-4:]
        ax.plot(node["longitude"], node["latitude"], "o", color=color, markersize=10, zorder=3)
        ax.annotate(label, (node["longitude"], node["latitude"]),
                    textcoords="offset points", xytext=(5, 5), fontsize=8, zorder=4)

    # Suggestions
    for sug in suggestions:
        ax.plot(sug["longitude"], sug["latitude"], "*", color="#00cc00", markersize=15, zorder=3,
                markeredgecolor="#006600", markeredgewidth=1)
        ax.annotate(f"#{sug['rank']}", (sug["longitude"], sug["latitude"]),
                    textcoords="offset points", xytext=(8, 0), fontsize=9, color="#006600",
                    fontweight="bold", zorder=4)

    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    summary = store.get_coverage_summary()
    ax.set_title(f"RF Shadow Map - {summary['coverage_pct']:.1f}% Coverage ({summary['dead_zone_count']} dead zones)")
    ax.grid(True, alpha=0.3)
    ax.set_aspect("equal")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf
