"""Discord alert dispatchers.

Three long-running async tasks poll the database and push embeds to a
configured channel:

* `AnomalyAlertDispatcher` — propagation anomalies from `analysis/propagation.py`
  (ducting, fade, lost_link, new_link). Marks each anomaly notified after send.
* `ShadowAlertDispatcher`  — newly activated dead zones and large coverage drops.
* `BlackHoleAlertDispatcher` — newly flagged black-hole nodes from
  `analysis/blackholes.py`.

Each dispatcher runs forever in its own asyncio task, sleeps `interval`
seconds between checks, and swallows exceptions so a transient DB or Discord
error doesn't kill the loop.
"""

import asyncio
import logging
from datetime import datetime

import discord

from database.store import DataStore

log = logging.getLogger(__name__)

EVENT_COLORS = {
    "ducting": 0x00CC00,
    "fade": 0xCC0000,
    "new_link": 0x0066CC,
    "lost_link": 0xCC8800,
    "chan_util_high": 0xE67E22,
}

EVENT_ICONS = {
    "ducting": "Signal BOOST",
    "fade": "Signal FADE",
    "new_link": "New Link",
    "lost_link": "Link Lost",
    "chan_util_high": "Channel Congestion",
}


class AnomalyAlertDispatcher:
    """Pushes propagation-anomaly embeds; marks each row notified after send."""

    def __init__(self, bot: discord.Client, store: DataStore, channel_id: int, interval: int = 60):
        self.bot = bot
        self.store = store
        self.channel_id = channel_id
        self.interval = interval

    async def start(self):
        log.info("Anomaly alert dispatcher started")
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self._check_and_send()
            except Exception as e:
                log.error("Alert dispatcher error: %s", e, exc_info=True)

    async def _check_and_send(self):
        anomalies = self.store.get_pending_anomalies()
        if not anomalies:
            return

        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            log.warning("Alert channel %d not found", self.channel_id)
            return

        for anomaly in anomalies:
            embed = discord.Embed(
                title=f"RF Anomaly: {EVENT_ICONS.get(anomaly['event_type'], anomaly['event_type'])}",
                description=anomaly["description"],
                color=EVENT_COLORS.get(anomaly["event_type"], 0x888888),
                timestamp=datetime.fromtimestamp(anomaly["timestamp"]),
            )
            if anomaly.get("node_a_id"):
                embed.add_field(name="Node A", value=f"`{anomaly['node_a_id']}`", inline=True)
            if anomaly.get("node_b_id"):
                embed.add_field(name="Node B", value=f"`{anomaly['node_b_id']}`", inline=True)
            embed.set_footer(text="MeshPropagation Anomaly Detection")

            await channel.send(embed=embed)
            self.store.mark_anomaly_notified(anomaly["id"])
            log.info("Sent anomaly alert: %s (%s)", anomaly["event_type"], anomaly["id"])


class ShadowAlertDispatcher:
    """Posts when a new dead zone activates or coverage % drops noticeably.

    Tracks active dead-zone ids and the last reported coverage % in memory
    (not persisted) — on bot restart the first tick re-baselines silently.
    """

    def __init__(self, bot: discord.Client, store: DataStore, channel_id: int, interval: int = 300):
        self.bot = bot
        self.store = store
        self.channel_id = channel_id
        self.interval = interval
        self._last_dead_zone_ids = set()
        self._last_coverage_pct = None

    async def start(self):
        log.info("Shadow alert dispatcher started")
        zones = self.store.get_dead_zones(active_only=True)
        self._last_dead_zone_ids = {z["id"] for z in zones}
        snap = self.store.get_latest_snapshot()
        if snap:
            self._last_coverage_pct = snap["coverage_pct"]

        while True:
            await asyncio.sleep(self.interval)
            try:
                await self._check_and_alert()
            except Exception as e:
                log.error("Shadow alert error: %s", e, exc_info=True)

    async def _check_and_alert(self):
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            return

        zones = self.store.get_dead_zones(active_only=True)
        current_ids = {z["id"] for z in zones}

        # New dead zones
        new_ids = current_ids - self._last_dead_zone_ids
        for zone in zones:
            if zone["id"] in new_ids:
                embed = discord.Embed(
                    title="New Dead Zone Detected",
                    description=f"**{zone['name']}** has appeared",
                    color=0xE74C3C,
                )
                embed.add_field(name="Area", value=f"{zone['area_km2'] * 0.386102:.2f} mi²", inline=True)
                embed.add_field(name="Cause", value=(zone.get("cause") or "unknown").title(), inline=True)
                embed.set_footer(text="MeshPropagation Shadow Alert")
                await channel.send(embed=embed)

        # Eliminated dead zones
        gone_ids = self._last_dead_zone_ids - current_ids
        if gone_ids:
            embed = discord.Embed(
                title="Dead Zone Eliminated!",
                description=f"{len(gone_ids)} dead zone(s) no longer detected",
                color=0x27AE60,
            )
            embed.set_footer(text="MeshPropagation Shadow Alert")
            await channel.send(embed=embed)

        # Coverage drop
        snap = self.store.get_latest_snapshot()
        if snap and self._last_coverage_pct is not None:
            drop = self._last_coverage_pct - snap["coverage_pct"]
            if drop > 5:
                embed = discord.Embed(
                    title="Coverage Drop Alert",
                    description=f"Coverage dropped {drop:.1f}% ({self._last_coverage_pct:.1f}% -> {snap['coverage_pct']:.1f}%)",
                    color=0xE67E22,
                )
                embed.set_footer(text="MeshPropagation Shadow Alert")
                await channel.send(embed=embed)

        self._last_dead_zone_ids = current_ids
        if snap:
            self._last_coverage_pct = snap["coverage_pct"]



SEVERITY_COLORS = {
    "critical": 0xCC0000,
    "high": 0xE67E22,
    "medium": 0xF1C40F,
    "low": 0x888888,
}

EVIDENCE_ICONS = {
    "asymmetric_links": "One-Way Links",
    "hop_anomaly": "Routing Detour",
    "mqtt_leak": "MQTT Leak",
    "traceroute_detour": "Path Avoidance",
}


class BlackHoleAlertDispatcher:
    """Posts when a node is newly flagged as a suspected black hole.

    Suppresses re-alerts on already-reported detection ids by relying on the
    `notified` flag in `blackhole_detections`, just like the anomaly path.
    """

    def __init__(self, bot: discord.Client, store: DataStore, channel_id: int, interval: int = 120):
        self.bot = bot
        self.store = store
        self.channel_id = channel_id
        self.interval = interval

    async def start(self):
        log.info("Black hole alert dispatcher started")
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self._check_and_send()
            except Exception as e:
                log.error("Black hole alert error: %s", e, exc_info=True)

    async def _check_and_send(self):
        unnotified = self.store.get_unnotified_black_holes()
        if not unnotified:
            return

        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            log.warning("Alert channel %d not found", self.channel_id)
            return

        for bh in unnotified:
            severity = bh["severity"]
            if severity >= 0.7:
                sev_label = "critical"
            elif severity >= 0.4:
                sev_label = "high"
            elif severity >= 0.2:
                sev_label = "medium"
            else:
                sev_label = "low"

            evidence = EVIDENCE_ICONS.get(bh["evidence_type"], bh["evidence_type"])

            embed = discord.Embed(
                title=f"Black Hole Detected: {bh['name']}",
                description=bh.get("description", ""),
                color=SEVERITY_COLORS.get(sev_label, 0x888888),
                timestamp=datetime.fromtimestamp(bh["first_detected"]),
            )
            embed.add_field(name="Evidence", value=evidence, inline=True)
            embed.add_field(name="Severity", value=f"{severity:.0%} ({sev_label})", inline=True)
            embed.add_field(name="Radius", value=f"{bh['radius_km'] * 0.621371:.1f} mi", inline=True)

            affected = bh.get("affected_nodes", [])
            if affected:
                node_labels = []
                for nid in affected[:8]:
                    node = self.store.get_node(nid)
                    label = (node.get("short_name") or nid[-4:]) if node else nid[-4:]
                    node_labels.append(f"`{label}`")
                nodes_str = ", ".join(node_labels)
                if len(affected) > 8:
                    nodes_str += f" +{len(affected) - 8} more"
                embed.add_field(name="Affected Nodes", value=nodes_str, inline=False)

            embed.add_field(
                name="Location",
                value=f"`{bh['center_lat']:.4f}, {bh['center_lon']:.4f}`",
                inline=False,
            )
            embed.set_footer(text="MeshPropagation Black Hole Detection")

            await channel.send(embed=embed)
            self.store.mark_black_hole_notified(bh["id"])
            log.info("Sent black hole alert: %s (severity %.2f)", bh["name"], severity)
