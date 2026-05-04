"""Discord slash-command registration.

Each `@tree.command(...)` function is one user-facing slash command. The
general pattern is: defer the response (charts can take seconds), render a
chart on a worker thread via `asyncio.to_thread` so the bot's event loop
stays responsive, build a Discord embed with summary fields, attach the
chart as a PNG, and post via `interaction.followup.send`.

`web_base_url` is woven into embeds so users can click through to the
interactive web dashboard for the same view.
"""

import asyncio
import io
import logging
import json
import time

import discord
from discord import app_commands

from database.store import DataStore
from rendering.charts import (
    chart_mesh_overview, chart_snr_history, chart_network_activity,
    chart_node_battery, chart_weather_correlation,
)
from rendering.shadow_charts import (
    chart_shadow_overview, chart_coverage_timeline, chart_coverage_breakdown,
)
from analysis.placement import evaluate_placement
import config

log = logging.getLogger(__name__)


def setup_commands(tree: app_commands.CommandTree, store: DataStore, web_base_url: str):
    """Attach all slash commands to `tree`. Called once during bot construction."""

    @tree.command(name="propagation", description="Show current RF propagation map")
    async def propagation(interaction: discord.Interaction):
        await interaction.response.defer()
        buf = await asyncio.to_thread(chart_mesh_overview, store)
        if not buf:
            await interaction.followup.send("No propagation data available yet. Waiting for node data to accumulate.")
            return
        embed = discord.Embed(
            title="RF Propagation Map",
            description=f"[View interactive map]({web_base_url}/map)",
            color=0x4ECDC4,
        )
        file = discord.File(buf, filename="propagation.png")
        embed.set_image(url="attachment://propagation.png")

        summary = store.get_mesh_summary()
        embed.add_field(name="Active Nodes (1h)", value=str(summary["active_nodes_1h"]), inline=True)
        embed.add_field(name=f"Active Nodes ({config.NODE_ACTIVE_HOURS}h)", value=str(summary["active_nodes"]), inline=True)
        embed.add_field(name=f"Link Obs ({config.NODE_ACTIVE_HOURS}h)", value=str(summary["total_links"]), inline=True)
        embed.set_footer(text="MeshPropagation - NEPAMesh RF Monitor")

        await interaction.followup.send(embed=embed, file=file)

    @tree.command(name="history", description="Show propagation history over time")
    @app_commands.describe(hours="Hours of history to show (default 24)")
    async def history(interaction: discord.Interaction, hours: int = 24):
        await interaction.response.defer()
        buf = await asyncio.to_thread(chart_network_activity, store, hours)
        if not buf:
            await interaction.followup.send("No history data available yet.")
            return
        embed = discord.Embed(
            title=f"Network Activity (last {hours}h)",
            color=0x0066CC,
        )
        file = discord.File(buf, filename="activity.png")
        embed.set_image(url="attachment://activity.png")
        embed.set_footer(text="MeshPropagation - NEPAMesh RF Monitor")
        await interaction.followup.send(embed=embed, file=file)

    @tree.command(name="weather", description="Show weather vs RF propagation correlation")
    @app_commands.describe(days="Days of data to analyze (default 7)")
    async def weather(interaction: discord.Interaction, days: int = 7):
        await interaction.response.defer()
        hours = days * 24
        buf = await asyncio.to_thread(chart_weather_correlation, store, hours)

        embed = discord.Embed(
            title=f"Weather vs Propagation ({days}d)",
            color=0x009900,
        )

        # Add current weather
        w = store.get_latest_weather()
        if w:
            temp_f = w['temperature_c'] * 9 / 5 + 32
            wind_mph = w['wind_speed_kmh'] * 0.621371
            weather_text = (
                f"Temp: {temp_f:.1f}°F | "
                f"Humidity: {w['humidity_pct']:.0f}% | "
                f"Pressure: {w['pressure_hpa']:.1f} hPa | "
                f"Wind: {wind_mph:.1f} mph"
            )
            embed.add_field(name="Current Conditions", value=weather_text, inline=False)

        if buf:
            file = discord.File(buf, filename="weather.png")
            embed.set_image(url="attachment://weather.png")
            await interaction.followup.send(embed=embed, file=file)
        else:
            embed.description = "Not enough data for correlation analysis yet. Need more link observations with weather data."
            await interaction.followup.send(embed=embed)

    @tree.command(name="node", description="Show details for a specific node")
    @app_commands.describe(name="Node name or ID (e.g. Leo or !abcd1234)")
    async def node(interaction: discord.Interaction, name: str):
        await interaction.response.defer()

        # Find node by name or ID
        node_data = store.get_node(name)
        if not node_data:
            all_nodes = store.get_all_nodes()
            for n in all_nodes:
                if (n.get("short_name", "").lower() == name.lower() or
                        n.get("long_name", "").lower() == name.lower()):
                    node_data = n
                    break

        if not node_data:
            await interaction.followup.send(f"Node '{name}' not found.")
            return

        node_id = node_data["node_id"]
        label = node_data.get("long_name") or node_data.get("short_name") or node_id

        embed = discord.Embed(
            title=f"Node: {label}",
            url=f"{web_base_url}/node/{node_id}",
            color=0x4ECDC4,
        )
        embed.add_field(name="ID", value=f"`{node_id}`", inline=True)
        embed.add_field(name="Hardware", value=node_data.get("hw_model", "?"), inline=True)

        if node_data.get("battery_level") is not None:
            bat_text = f"{node_data['battery_level']}%"
            if node_data.get("voltage"):
                bat_text += f" ({node_data['voltage']}V)"
            embed.add_field(name="Battery", value=bat_text, inline=True)

        if node_data.get("channel_util") is not None:
            embed.add_field(name="Ch Util", value=f"{node_data['channel_util']:.1f}%", inline=True)

        if node_data.get("latitude") is not None and node_data.get("longitude") is not None:
            alt_str = ""
            if node_data.get("altitude") is not None:
                alt_ft = node_data["altitude"] * 3.28084
                alt_str = f" ({alt_ft:.0f} ft)"
            embed.add_field(
                name="Position",
                value=f"{node_data['latitude']:.6f}, {node_data['longitude']:.6f}{alt_str}",
                inline=False,
            )

        if node_data.get("uptime_seconds") is not None:
            days = node_data["uptime_seconds"] // 86400
            hours_up = (node_data["uptime_seconds"] % 86400) // 3600
            embed.add_field(name="Uptime", value=f"{days}d {hours_up}h", inline=True)

        last_seen = node_data.get("last_seen")
        if last_seen is None:
            embed.add_field(name="Last Seen", value="Unknown", inline=True)
            await interaction.followup.send(embed=embed, files=[])
            return
        age = int(time.time()) - last_seen
        if age < 60:
            seen = f"{age}s ago"
        elif age < 3600:
            seen = f"{age // 60}m ago"
        else:
            seen = f"{age // 3600}h ago"
        embed.add_field(name="Last Seen", value=seen, inline=True)

        # Battery chart
        buf = await asyncio.to_thread(chart_node_battery, store, node_id, 48)
        files = []
        if buf:
            file = discord.File(buf, filename="battery.png")
            embed.set_image(url="attachment://battery.png")
            files.append(file)

        embed.set_footer(text="MeshPropagation - NEPAMesh RF Monitor")
        await interaction.followup.send(embed=embed, files=files)

    @tree.command(name="mesh", description="Show mesh network overview")
    async def mesh(interaction: discord.Interaction):
        await interaction.response.defer()

        summary = store.get_mesh_summary()
        links = store.get_latest_links(24)

        embed = discord.Embed(
            title="Mesh Network Overview",
            url=f"{web_base_url}/",
            color=0x1A1A2E,
        )
        embed.add_field(name="Total Nodes", value=str(summary["total_nodes"]), inline=True)
        embed.add_field(name="Active (1h)", value=str(summary["active_nodes_1h"]), inline=True)
        embed.add_field(name=f"Active ({config.NODE_ACTIVE_HOURS}h)", value=str(summary["active_nodes"]), inline=True)
        embed.add_field(name=f"Link Observations ({config.NODE_ACTIVE_HOURS}h)", value=str(summary["total_links"]), inline=True)
        embed.add_field(name=f"Unique Pairs ({config.NODE_ACTIVE_HOURS}h)", value=str(summary["unique_pairs"]), inline=True)
        embed.add_field(name="Total Positions", value=str(summary["total_positions"]), inline=True)

        # Top links by observation count
        if links:
            top = links[:5]
            top_text = ""
            for l in top:
                a = _node_label(store, l["node_a_id"])
                b = _node_label(store, l["node_b_id"])
                snr = f"{l['avg_snr']:.1f}" if l["avg_snr"] is not None else "?"
                dist = f"{l['avg_distance'] * 0.621371:.1f}mi" if l["avg_distance"] is not None else "?"
                top_text += f"**{a}** ↔ **{b}**: {snr} dB, {dist}, {l['obs_count']} obs\n"
            embed.add_field(name="Top Links (24h)", value=top_text, inline=False)

        # Weather
        w = summary.get("latest_weather")
        if w:
            embed.add_field(
                name="Weather",
                value=f"{w['temperature_c'] * 9 / 5 + 32:.1f}°F | {w['humidity_pct']:.0f}% | {w['pressure_hpa']:.1f} hPa",
                inline=False,
            )

        buf = await asyncio.to_thread(chart_mesh_overview, store)
        files = []
        if buf:
            file = discord.File(buf, filename="mesh.png")
            embed.set_image(url="attachment://mesh.png")
            files.append(file)

        embed.set_footer(text="MeshPropagation - NEPAMesh RF Monitor")
        await interaction.followup.send(embed=embed, files=files)


    # --- Shadow Mapper Commands ---

    def _coverage_grade(pct):
        if pct >= 90: return "A"
        elif pct >= 80: return "B"
        elif pct >= 70: return "C"
        elif pct >= 60: return "D"
        return "F"

    def _grade_color(grade):
        return {"A": 0x27AE60, "B": 0x2ECC71, "C": 0xF1C40F, "D": 0xE67E22, "F": 0xE74C3C}.get(grade, 0x888888)

    @tree.command(name="shadows", description="Show current RF shadow map overview")
    async def shadows(interaction: discord.Interaction):
        await interaction.response.defer()
        buf = await asyncio.to_thread(chart_shadow_overview, store)

        summary = store.get_coverage_summary()
        grade = _coverage_grade(summary["coverage_pct"])

        embed = discord.Embed(
            title="RF Shadow Map",
            description=f"[View interactive map]({web_base_url}/shadows)",
            color=_grade_color(grade),
        )
        embed.add_field(name="Coverage", value=f"{summary['coverage_pct']}% (Grade: {grade})", inline=True)
        embed.add_field(name="Shadow Area", value=f"{summary['shadow_area_km2'] * 0.386102:.2f} mi²", inline=True)
        embed.add_field(name="Dead Zones", value=str(summary["dead_zone_count"]), inline=True)

        zones = store.get_dead_zones(active_only=True)
        if zones:
            zone_text = "\n".join(
                f"- **{z['name']}**: {z['area_km2'] * 0.386102:.2f} mi² ({z.get('cause') or 'unknown'})"
                for z in zones[:5]
            )
            embed.add_field(name="Active Dead Zones", value=zone_text, inline=False)

        files = []
        if buf:
            file = discord.File(buf, filename="shadows.png")
            embed.set_image(url="attachment://shadows.png")
            files.append(file)

        embed.set_footer(text="MeshPropagation - NEPAMesh RF Monitor")
        await interaction.followup.send(embed=embed, files=files)

    @tree.command(name="coverage", description="Show coverage percentage and breakdown")
    async def coverage(interaction: discord.Interaction):
        await interaction.response.defer()

        summary = store.get_coverage_summary()
        grade = _coverage_grade(summary["coverage_pct"])

        embed = discord.Embed(
            title=f"Coverage Report: {summary['coverage_pct']}% (Grade {grade})",
            url=f"{web_base_url}/shadows/dashboard",
            color=_grade_color(grade),
        )
        embed.add_field(name="Total Area", value=f"{summary['total_area_km2'] * 0.386102:.2f} mi²", inline=True)
        embed.add_field(name="Covered", value=f"{summary['covered_area_km2'] * 0.386102:.2f} mi²", inline=True)
        embed.add_field(name="Shadow", value=f"{summary['shadow_area_km2'] * 0.386102:.2f} mi²", inline=True)
        embed.add_field(name="Dead Zones", value=str(summary["dead_zone_count"]), inline=True)

        buf = await asyncio.to_thread(chart_coverage_breakdown, store)
        files = []
        if buf:
            file = discord.File(buf, filename="breakdown.png")
            embed.set_image(url="attachment://breakdown.png")
            files.append(file)

        embed.set_footer(text="MeshPropagation - NEPAMesh RF Monitor")
        await interaction.followup.send(embed=embed, files=files)

    @tree.command(name="suggest", description="Show optimal node placement suggestions")
    async def suggest(interaction: discord.Interaction):
        await interaction.response.defer()

        suggestions = store.get_placement_suggestions(limit=5)

        embed = discord.Embed(
            title="Node Placement Suggestions",
            url=f"{web_base_url}/suggestions",
            color=0x27AE60,
        )

        if not suggestions:
            embed.description = "No placement suggestions available yet. Coverage analysis needs to run first."
            await interaction.followup.send(embed=embed)
            return

        for s in suggestions:
            elev = f"{s['elevation_m'] * 3.28084:.0f} ft" if s.get("elevation_m") else "?"
            reduction_mi2 = s['shadow_reduction_km2'] * 0.386102
            embed.add_field(
                name=f"#{s['rank']}: ({s['latitude']:.4f}, {s['longitude']:.4f})",
                value=f"Elev: {elev} | Reduces shadow by {reduction_mi2:.2f} mi² ({s['shadow_reduction_pct']:.1f}%)",
                inline=False,
            )

        embed.set_footer(text="MeshPropagation - NEPAMesh RF Monitor")
        await interaction.followup.send(embed=embed)

    @tree.command(name="deadzone", description="Show details for a specific dead zone")
    @app_commands.describe(name="Dead zone name (partial match)")
    async def deadzone(interaction: discord.Interaction, name: str):
        await interaction.response.defer()

        zone = store.get_dead_zone_by_name(name)
        if not zone:
            await interaction.followup.send(f"No dead zone matching '{name}' found.")
            return

        embed = discord.Embed(title=f"Dead Zone: {zone['name']}", color=0xE74C3C)
        embed.add_field(name="Area", value=f"{zone['area_km2'] * 0.386102:.2f} mi²", inline=True)
        embed.add_field(name="Cells", value=str(zone["cell_count"]), inline=True)
        embed.add_field(name="Cause", value=(zone.get("cause") or "unknown").title(), inline=True)
        embed.add_field(name="Avg Shadow", value=f"{zone['avg_shadow_score']:.2f}", inline=True)
        embed.add_field(name="Max Shadow", value=f"{zone['max_shadow_score']:.2f}", inline=True)
        embed.add_field(name="Center", value=f"`{zone['center_lat']:.4f}, {zone['center_lon']:.4f}`", inline=False)

        embed.set_footer(text="MeshPropagation - NEPAMesh RF Monitor")
        await interaction.followup.send(embed=embed)

    @tree.command(name="coverage-history", description="Show coverage evolution over time")
    @app_commands.describe(days="Number of days to show (default 7)")
    async def coverage_history(interaction: discord.Interaction, days: int = 7):
        await interaction.response.defer()

        buf = await asyncio.to_thread(chart_coverage_timeline, store, days)

        embed = discord.Embed(title=f"Coverage History ({days} days)", color=0x1A1A2E)

        snapshots = store.get_coverage_snapshots(days)
        if len(snapshots) >= 2:
            first = snapshots[0]["coverage_pct"]
            last = snapshots[-1]["coverage_pct"]
            diff = last - first
            trend = "improved" if diff > 0 else "declined" if diff < 0 else "stable"
            embed.description = f"Coverage {trend} by {abs(diff):.1f}% over {days} days"

        files = []
        if buf:
            file = discord.File(buf, filename="timeline.png")
            embed.set_image(url="attachment://timeline.png")
            files.append(file)
        else:
            embed.description = "Not enough snapshot data yet. Snapshots are taken hourly."

        embed.set_footer(text="MeshPropagation - NEPAMesh RF Monitor")
        await interaction.followup.send(embed=embed, files=files)

    @tree.command(name="evaluate", description="Evaluate a proposed node placement location")
    @app_commands.describe(lat="Latitude", lon="Longitude")
    async def evaluate_cmd(interaction: discord.Interaction, lat: float, lon: float):
        await interaction.response.defer()

        result = await asyncio.to_thread(evaluate_placement, store, lat, lon, config.MAX_NODE_RANGE_KM)

        color = 0x27AE60 if result["reduction_pct"] > 5 else 0xF1C40F if result["reduction_pct"] > 0 else 0xE74C3C
        embed = discord.Embed(title="Placement Evaluation", color=color)
        embed.add_field(name="Location", value=f"`{lat:.4f}, {lon:.4f}`", inline=True)
        embed.add_field(name="Shadow Reduction", value=f"{result['reduction_km2'] * 0.386102:.2f} mi² ({result['reduction_pct']:.1f}%)", inline=True)
        embed.add_field(name="Cells Improved", value=str(result["cells_improved"]), inline=True)

        if result["reduction_pct"] > 10:
            embed.description = "Excellent placement location!"
        elif result["reduction_pct"] > 5:
            embed.description = "Good placement location."
        elif result["reduction_pct"] > 0:
            embed.description = "Marginal improvement."
        else:
            embed.description = "Would not reduce shadow coverage."

        embed.set_footer(text="MeshPropagation - NEPAMesh RF Monitor")
        await interaction.followup.send(embed=embed)


    # --- Black Hole Detection Commands ---

    @tree.command(name="blackholes", description="Show detected routing black holes")
    async def blackholes(interaction: discord.Interaction):
        await interaction.response.defer()

        holes = store.get_black_holes(active_only=True)

        if not holes:
            embed = discord.Embed(
                title="Routing Black Holes",
                description="No active black holes detected. Packet data is still being collected — "
                            "black hole analysis runs every 10 minutes.",
                color=0x27AE60,
            )
            embed.set_footer(text="MeshPropagation Black Hole Detection")
            await interaction.followup.send(embed=embed)
            return

        embed = discord.Embed(
            title=f"Routing Black Holes ({len(holes)} active)",
            description="Areas where packets enter but don't come out",
            color=0xCC0000 if any(h["severity"] >= 0.5 for h in holes) else 0xF1C40F,
        )

        evidence_labels = {
            "asymmetric_links": "One-Way Links",
            "hop_anomaly": "Routing Detour",
            "mqtt_leak": "MQTT Leak",
        }

        for bh in holes[:8]:
            severity = bh["severity"]
            sev_bar = "+" * min(10, max(1, int(severity * 10)))
            evidence = evidence_labels.get(bh["evidence_type"], bh["evidence_type"])

            affected = bh.get("affected_nodes", [])
            affected_str = ""
            if affected:
                labels = []
                for nid in affected[:5]:
                    node = store.get_node(nid)
                    labels.append((node.get("short_name") or nid[-4:]) if node else nid[-4:])
                affected_str = f"\nNodes: {', '.join(labels)}"
                if len(affected) > 5:
                    affected_str += f" +{len(affected) - 5}"

            embed.add_field(
                name=f"{bh['name']}",
                value=(
                    f"Severity: `[{sev_bar:<10}]` {severity:.0%}\n"
                    f"Type: {evidence} | Radius: {bh['radius_km'] * 0.621371:.1f} mi"
                    f"{affected_str}"
                ),
                inline=False,
            )

        embed.set_footer(text="MeshPropagation Black Hole Detection")
        await interaction.followup.send(embed=embed)

    @tree.command(name="blackhole", description="Show details for a specific black hole")
    @app_commands.describe(name="Black hole name (partial match)")
    async def blackhole_detail(interaction: discord.Interaction, name: str):
        await interaction.response.defer()

        bh = store.get_black_hole_by_name(name)
        if not bh:
            await interaction.followup.send(f"No black hole matching '{name}' found.")
            return

        severity = bh["severity"]
        if severity >= 0.7:
            color = 0xCC0000
        elif severity >= 0.4:
            color = 0xE67E22
        else:
            color = 0xF1C40F

        embed = discord.Embed(
            title=f"Black Hole: {bh['name']}",
            description=bh.get("description", ""),
            color=color,
        )
        embed.add_field(name="Severity", value=f"{severity:.0%}", inline=True)
        embed.add_field(name="Evidence", value=bh["evidence_type"].replace("_", " ").title(), inline=True)
        embed.add_field(name="Radius", value=f"{bh['radius_km'] * 0.621371:.1f} mi", inline=True)
        embed.add_field(name="Center", value=f"`{bh['center_lat']:.4f}, {bh['center_lon']:.4f}`", inline=True)
        embed.add_field(name="Active", value="Yes" if bh["active"] else "No", inline=True)

        affected = bh.get("affected_nodes", [])
        if affected:
            node_details = []
            for nid in affected[:10]:
                node = store.get_node(nid)
                if node:
                    label = node.get("long_name") or node.get("short_name") or nid
                    stats = store.get_node_routing_stats(nid)
                    stat_str = ""
                    if stats:
                        stat_str = f" (fwd: {stats['forwarding_ratio']:.1%}, mqtt: {stats['via_mqtt_pct']:.0f}%)"
                    node_details.append(f"- **{label}** `{nid}`{stat_str}")
                else:
                    node_details.append(f"- `{nid}`")
            embed.add_field(
                name=f"Affected Nodes ({len(affected)})",
                value="\n".join(node_details),
                inline=False,
            )

        embed.set_footer(text="MeshPropagation Black Hole Detection")
        await interaction.followup.send(embed=embed)

    @tree.command(name="routing", description="Show routing health for a node")
    @app_commands.describe(name="Node name or ID")
    async def routing(interaction: discord.Interaction, name: str):
        await interaction.response.defer()

        node_data = store.get_node(name)
        if not node_data:
            all_nodes = store.get_all_nodes()
            for n in all_nodes:
                if (n.get("short_name", "").lower() == name.lower() or
                        n.get("long_name", "").lower() == name.lower()):
                    node_data = n
                    break

        if not node_data:
            await interaction.followup.send(f"Node '{name}' not found.")
            return

        node_id = node_data["node_id"]
        label = node_data.get("long_name") or node_data.get("short_name") or node_id

        stats = store.get_node_routing_stats(node_id)

        embed = discord.Embed(
            title=f"Routing Health: {label}",
            color=0x4ECDC4,
        )
        embed.add_field(name="ID", value=f"`{node_id}`", inline=True)

        if not stats:
            embed.description = "No routing data available yet. Packet observations are being collected."
            embed.set_footer(text="MeshPropagation Black Hole Detection")
            await interaction.followup.send(embed=embed)
            return

        embed.add_field(name="Packets Seen (24h)", value=str(stats["packets_seen"]), inline=True)
        embed.add_field(name="Times As Relay", value=str(stats["packets_as_relay"]), inline=True)

        if stats["avg_hops_taken"] is not None:
            embed.add_field(name="Avg Hops", value=f"{stats['avg_hops_taken']:.1f}", inline=True)

        fwd = stats.get("forwarding_ratio")
        if fwd is not None:
            if fwd >= 0.5:
                fwd_status = "Healthy"
            elif fwd >= 0.1:
                fwd_status = "Low"
            else:
                fwd_status = "Suspect"
            embed.add_field(name="Forwarding Ratio", value=f"{fwd:.1%} ({fwd_status})", inline=True)

        mqtt_pct = stats.get("via_mqtt_pct")
        if mqtt_pct is not None:
            embed.add_field(name="Via MQTT", value=f"{mqtt_pct:.0f}%", inline=True)

        asym = stats.get("asymmetric_links", 0)
        if asym > 0:
            embed.add_field(name="Asymmetric Links", value=str(asym), inline=True)

        # Check if this node is in any black holes
        holes = store.get_black_holes(active_only=True)
        in_holes = [h for h in holes if node_id in (h.get("affected_nodes") or [])]
        if in_holes:
            hole_names = ", ".join(h["name"] for h in in_holes)
            embed.add_field(name="In Black Holes", value=hole_names, inline=False)

        embed.set_footer(text="MeshPropagation Black Hole Detection")
        await interaction.followup.send(embed=embed)

    @tree.command(name="traceroutes", description="Show recent traceroute observations")
    async def traceroutes_cmd(interaction: discord.Interaction):
        await interaction.response.defer()

        traces = store.get_traceroutes(hours=72, limit=10)

        if not traces:
            embed = discord.Embed(
                title="Traceroutes",
                description="No traceroute data captured yet. Traceroutes are recorded "
                            "when nodes in the mesh run traceroute commands.",
                color=0x888888,
            )
            embed.set_footer(text="MeshPropagation Black Hole Detection")
            await interaction.followup.send(embed=embed)
            return

        embed = discord.Embed(
            title=f"Recent Traceroutes ({len(traces)})",
            color=0x0066CC,
        )

        for tr in traces[:8]:
            origin_node = store.get_node(tr["origin_id"])
            dest_node = store.get_node(tr["destination_id"])
            origin_label = (origin_node.get("short_name") or tr["origin_id"][-4:]) if origin_node else tr["origin_id"][-4:]
            dest_label = (dest_node.get("short_name") or tr["destination_id"][-4:]) if dest_node else tr["destination_id"][-4:]

            route = tr.get("route_forward", [])
            if route:
                hops = []
                for nid in route:
                    n = store.get_node(nid)
                    hops.append((n.get("short_name") or nid[-4:]) if n else nid[-4:])
                route_str = f"{origin_label} -> {' -> '.join(hops)} -> {dest_label}"
            else:
                route_str = f"{origin_label} -> {dest_label} (direct)"

            snr_str = ""
            snr_fwd = tr.get("snr_forward", [])
            if snr_fwd:
                snr_str = f"\nSNR: {', '.join(f'{s:.0f}dB' for s in snr_fwd)}"

            status = "Completed" if tr["completed"] else "Failed"

            from datetime import datetime
            ts = datetime.fromtimestamp(tr["timestamp"]).strftime("%m/%d %H:%M")

            embed.add_field(
                name=f"{ts} ({tr['hop_count']} hops, {status})",
                value=f"`{route_str}`{snr_str}",
                inline=False,
            )

        embed.set_footer(text="MeshPropagation Black Hole Detection")
        await interaction.followup.send(embed=embed)


def _node_label(store, node_id):
    node = store.get_node(node_id)
    if node:
        return node.get("short_name") or node.get("long_name") or node_id
    return node_id
