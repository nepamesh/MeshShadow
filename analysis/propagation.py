"""SNR-based RF propagation anomaly detection.

For each node-pair with at least `min_observations` historical SNR samples,
compute the historical mean and stddev of SNR and compare the recent (last
hour) mean against it. Deviations beyond `snr_stddev_threshold` σ are
classified as 'ducting' (unusually high — possible atmospheric enhancement)
or 'fade' (unusually low — possible interference / weather attenuation).

Also detects 'lost_link' (a previously active pair has had no observations
in the last hour while both nodes are otherwise active) and 'new_link' (a
freshly observed long-range link, >10 km, with no prior history). Anomalies
are written to the `anomalies` table and picked up by the Discord alert
dispatcher.
"""

import logging
import math
import time

import config
from database.store import DataStore

log = logging.getLogger(__name__)


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance between two lat/lon points (km)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def detect_anomalies(store: DataStore, snr_stddev_threshold: float = 2.0,
                     min_observations: int = 20, lost_link_hours: int = 6):
    """Run a single anomaly-detection pass and insert findings into the DB.

    Called periodically by the anomaly-detector thread in `main.py`. Idempotent
    over time: callers handle deduplication via `anomalies.notified` flag.
    """
    now = int(time.time())
    h1 = now - 3600
    h24 = now - 86400

    # Get all unique pairs with enough history
    pairs = store._fetchall(
        """SELECT node_a_id, node_b_id,
                  AVG(snr) as mean_snr,
                  COUNT(*) as total_obs,
                  SUM(CASE WHEN timestamp > ? THEN 1 ELSE 0 END) as recent_obs,
                  AVG(CASE WHEN timestamp > ? THEN snr END) as recent_mean_snr
           FROM link_observations
           WHERE snr IS NOT NULL
           GROUP BY node_a_id, node_b_id
           HAVING total_obs >= ?""",
        (h1, h1, min_observations),
    )

    for pair in pairs:
        node_a = pair["node_a_id"]
        node_b = pair["node_b_id"]

        # Compute stddev
        obs = store._fetchall(
            "SELECT snr FROM link_observations WHERE node_a_id = ? AND node_b_id = ? AND snr IS NOT NULL",
            (node_a, node_b),
        )
        if len(obs) < min_observations:
            continue

        snrs = [o["snr"] for o in obs]
        mean = sum(snrs) / len(snrs)
        variance = sum((x - mean) ** 2 for x in snrs) / len(snrs)
        stddev = math.sqrt(variance) if variance > 0 else 0

        if stddev == 0:
            continue

        recent_mean = pair["recent_mean_snr"]
        if recent_mean is None:
            # No recent observations — check for lost link
            if pair["recent_obs"] == 0:
                # Check both nodes are still active
                node_a_data = store.get_node(node_a)
                node_b_data = store.get_node(node_b)
                if (node_a_data and node_a_data["last_seen"] > h1 and
                        node_b_data and node_b_data["last_seen"] > h1):
                    store.insert_anomaly(
                        now, "lost_link",
                        f"Link {node_a} <-> {node_b} has had no observations in the last hour, "
                        f"but both nodes are active. Historical avg SNR: {mean:.1f} dB",
                        node_a, node_b,
                    )
            continue

        deviation = (recent_mean - mean) / stddev

        if deviation > snr_stddev_threshold:
            store.insert_anomaly(
                now, "ducting",
                f"Link {node_a} <-> {node_b} SNR is unusually HIGH: "
                f"{recent_mean:.1f} dB (historical mean: {mean:.1f} dB, +{deviation:.1f}σ). "
                f"Possible atmospheric ducting or enhanced propagation.",
                node_a, node_b,
            )
            log.info("Anomaly: ducting on %s <-> %s (%.1fσ)", node_a, node_b, deviation)

        elif deviation < -snr_stddev_threshold:
            store.insert_anomaly(
                now, "fade",
                f"Link {node_a} <-> {node_b} SNR is unusually LOW: "
                f"{recent_mean:.1f} dB (historical mean: {mean:.1f} dB, {deviation:.1f}σ). "
                f"Possible interference or atmospheric fade.",
                node_a, node_b,
            )
            log.info("Anomaly: fade on %s <-> %s (%.1fσ)", node_a, node_b, deviation)

    # Check for new long-range links
    new_links = store._fetchall(
        """SELECT node_a_id, node_b_id, snr, distance_km, timestamp
           FROM link_observations
           WHERE timestamp > ? AND distance_km > 10
           AND NOT EXISTS (
               SELECT 1 FROM link_observations lo2
               WHERE lo2.node_a_id = link_observations.node_a_id
               AND lo2.node_b_id = link_observations.node_b_id
               AND lo2.timestamp <= ?
           )""",
        (h1, h1),
    )
    for link in new_links:
        store.insert_anomaly(
            now, "new_link",
            f"New long-range link detected: {link['node_a_id']} <-> {link['node_b_id']} "
            f"at {link['distance_km']:.1f} km, SNR: {link['snr']:.1f} dB",
            link["node_a_id"], link["node_b_id"],
        )

    log.debug("Anomaly detection completed, checked %d pairs", len(pairs))

    # Channel utilization alerts
    _check_channel_util(store, now)


def _check_channel_util(store: DataStore, now: int):
    """Alert when a node's average channel utilization over the last hour exceeds threshold.

    Uses a per-node cooldown so the same node isn't re-alerted until it either
    clears and re-crosses the threshold or the cooldown window expires.
    """
    threshold = config.CHANNEL_UTIL_THRESHOLD
    cooldown_sec = config.CHANNEL_UTIL_ALERT_COOLDOWN_HOURS * 3600
    since = now - 3600

    rows = store._fetchall(
        """SELECT node_id, AVG(channel_util) AS avg_util, MAX(channel_util) AS max_util
           FROM device_metrics
           WHERE timestamp > ? AND channel_util IS NOT NULL
           GROUP BY node_id
           HAVING avg_util >= ?""",
        (since, threshold),
    )

    for row in rows:
        node_id = row["node_id"]
        avg_util = row["avg_util"]
        max_util = row["max_util"]

        # Skip if already alerted within the cooldown window
        recent = store._fetchone(
            """SELECT id FROM anomaly_events
               WHERE event_type = 'chan_util_high' AND node_a_id = ?
               AND timestamp > ?""",
            (node_id, now - cooldown_sec),
        )
        if recent:
            continue

        node = store.get_node(node_id)
        label = (node.get("short_name") or node.get("long_name") or node_id) if node else node_id
        store.insert_anomaly(
            now, "chan_util_high",
            f"Node {label} ({node_id}) channel utilization is high: "
            f"avg {avg_util:.1f}% (peak {max_util:.1f}%) over the last hour "
            f"(threshold: {threshold:.0f}%). "
            f"Heavy traffic or congestion may be degrading mesh performance.",
            node_id,
        )
        log.info("Anomaly: chan_util_high on %s (avg %.1f%%)", node_id, avg_util)
