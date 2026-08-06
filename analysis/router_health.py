"""Proactive health checks for router-role nodes.

Distinct from `analysis/blackholes.py` (routing behavior on a live node) and
the offline dispatcher (a router gone dark). These catch a router on its way
to trouble while it's still up: a battery draining fast or reading erratic,
a voltage sag, or link SNR quietly drifting below its own historical
baseline the way Chani's did before it went dark.
"""

import logging
import statistics

import config
from database.store import DataStore

log = logging.getLogger(__name__)


def check_battery_health(store: DataStore, hours=None, drain_pct=None,
                          jitter_stddev=None, voltage_sag=None):
    """Return a list of {node_id, issue, detail} for routers with bad battery/voltage trends."""
    hours = hours if hours is not None else config.ROUTER_BATTERY_WINDOW_HOURS
    drain_pct = drain_pct if drain_pct is not None else config.ROUTER_BATTERY_DRAIN_PCT
    jitter_stddev = jitter_stddev if jitter_stddev is not None else config.ROUTER_BATTERY_JITTER_STDDEV
    voltage_sag = voltage_sag if voltage_sag is not None else config.ROUTER_BATTERY_VOLTAGE_SAG

    findings = []
    for node in store.get_router_nodes():
        history = store.get_node_metrics_history(node["node_id"], hours=hours)
        if len(history) < 2:
            continue

        label = node.get("short_name") or node.get("long_name") or node["node_id"]

        levels = [h["battery_level"] for h in history if h["battery_level"] is not None]
        if len(levels) >= 2:
            drain = levels[0] - levels[-1]
            if drain >= drain_pct:
                findings.append({
                    "node_id": node["node_id"], "label": label, "issue": "fast_drain",
                    "detail": f"Battery dropped {drain:.0f} points in {hours}h "
                              f"({levels[0]:.0f}% -> {levels[-1]:.0f}%).",
                })

            if len(levels) >= 4:
                jitter = statistics.pstdev(levels)
                if jitter >= jitter_stddev:
                    findings.append({
                        "node_id": node["node_id"], "label": label, "issue": "erratic_battery",
                        "detail": f"Battery readings swinging (stddev {jitter:.1f} points over "
                                  f"{len(levels)} readings in {hours}h) rather than draining smoothly.",
                    })

        voltages = [h["voltage"] for h in history if h["voltage"] is not None]
        if voltages and voltages[-1] < voltage_sag:
            findings.append({
                "node_id": node["node_id"], "label": label, "issue": "low_voltage",
                "detail": f"Latest voltage {voltages[-1]:.2f}V, below the {voltage_sag:.2f}V sag threshold.",
            })

    return findings


def check_signal_health(store: DataStore, baseline_days=None, recent_hours=None,
                         drop_db=None, min_baseline_obs=None, min_recent_obs=None):
    """Return a list of {node_id, issue, detail} for routers whose aggregate link
    SNR has dropped well below their own historical baseline."""
    baseline_days = baseline_days if baseline_days is not None else config.ROUTER_SNR_BASELINE_DAYS
    recent_hours = recent_hours if recent_hours is not None else config.ROUTER_SNR_RECENT_HOURS
    drop_db = drop_db if drop_db is not None else config.ROUTER_SNR_DROP_DB
    min_baseline_obs = min_baseline_obs if min_baseline_obs is not None else config.ROUTER_SNR_MIN_BASELINE_OBS
    min_recent_obs = min_recent_obs if min_recent_obs is not None else config.ROUTER_SNR_MIN_RECENT_OBS

    findings = []
    for node in store.get_router_nodes():
        node_id = node["node_id"]
        label = node.get("short_name") or node.get("long_name") or node_id

        baseline = store.get_node_snr_stats(node_id, hours=baseline_days * 24)
        if not baseline or baseline["obs_count"] < min_baseline_obs or baseline["avg_snr"] is None:
            continue

        recent = store.get_node_snr_stats(node_id, hours=recent_hours)
        if not recent or recent["obs_count"] < min_recent_obs or recent["avg_snr"] is None:
            continue

        drop = baseline["avg_snr"] - recent["avg_snr"]
        if drop >= drop_db:
            findings.append({
                "node_id": node_id, "label": label, "issue": "signal_drop",
                "detail": f"Link SNR averaging {recent['avg_snr']:.1f} dB over the last {recent_hours}h, "
                          f"down {drop:.1f} dB from its {baseline_days}-day baseline of {baseline['avg_snr']:.1f} dB.",
            })

    return findings


def check_router_health(store: DataStore):
    """Run both proactive checks and return a combined findings list."""
    findings = check_battery_health(store) + check_signal_health(store)
    log.debug("Router health check: %d finding(s)", len(findings))
    return findings
