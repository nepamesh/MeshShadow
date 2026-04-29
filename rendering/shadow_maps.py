import logging
import time
from html import escape

import folium
from folium.plugins import HeatMap

from database.store import DataStore

log = logging.getLogger(__name__)


def _time_ago(ts):
    diff = int(time.time()) - ts
    if diff < 60:
        return f"{diff}s ago"
    elif diff < 3600:
        return f"{diff // 60}m ago"
    elif diff < 86400:
        return f"{diff // 3600}h ago"
    else:
        return f"{diff // 86400}d ago"


def _get_node_label(store, node_id):
    node = store.get_node(node_id)
    if node:
        return node.get("short_name") or node.get("long_name") or node_id
    return node_id


def generate_shadow_map(store: DataStore):
    grid_meta = store.get_grid_metadata()
    nodes = store.get_nodes_with_positions()
    dead_zones = store.get_dead_zones(active_only=True)
    suggestions = store.get_placement_suggestions(limit=5)

    if nodes:
        center_lat = sum(n["latitude"] for n in nodes) / len(nodes)
        center_lon = sum(n["longitude"] for n in nodes) / len(nodes)
    else:
        center_lat, center_lon = 41.0, -75.9

    m = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles=None, prefer_canvas=True)

    folium.TileLayer("OpenStreetMap", name="Street Map").add_to(m)
    folium.TileLayer(
        tiles="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attr='Map data: &copy; OpenStreetMap contributors, SRTM | Map style: &copy; OpenTopoMap',
        name="Topo Map",
    ).add_to(m)

    # Shadow heatmap layer
    shadow_cells = store.get_shadow_cells(threshold=0.3)
    if shadow_cells:
        # Downsample for performance
        step = max(1, len(shadow_cells) // 10000)
        heat_data = [
            [c["center_lat"], c["center_lon"], c["shadow_score"]]
            for i, c in enumerate(shadow_cells) if i % step == 0
        ]
        heat_group = folium.FeatureGroup(name="Shadow Heatmap", show=True)
        HeatMap(
            heat_data,
            min_opacity=0.3,
            max_zoom=15,
            radius=15,
            blur=10,
            gradient={
                "0.0": "#00ff00",
                "0.3": "#ffff00",
                "0.6": "#ff8800",
                "0.8": "#ff0000",
                "1.0": "#cc0000",
            },
        ).add_to(heat_group)
        heat_group.add_to(m)

    # Dead zone outlines
    if dead_zones:
        dz_group = folium.FeatureGroup(name="Dead Zones", show=True)
        for dz in dead_zones:
            dz_cells = store.get_dead_zone_cells(dz["id"])
            if not dz_cells:
                continue

            # Create boundary polygon from cell positions
            lats = [c["center_lat"] for c in dz_cells]
            lons = [c["center_lon"] for c in dz_cells]

            # Draw a circle marker at center with radius proportional to area
            import math
            radius_m = math.sqrt(dz["area_km2"] * 1e6 / math.pi)

            cause_str = escape(dz.get("cause") or "unknown")
            dz_name = escape(dz["name"])
            popup_html = f"""
            <b>{dz_name}</b><br>
            Area: {dz['area_km2'] * 0.386102:.2f} mi²<br>
            Cells: {dz['cell_count']}<br>
            Avg Shadow Score: {dz['avg_shadow_score']:.2f}<br>
            Max Shadow Score: {dz['max_shadow_score']:.2f}<br>
            Cause: {cause_str}<br>
            First Detected: {_time_ago(dz['first_detected'])}
            """

            folium.Circle(
                location=[dz["center_lat"], dz["center_lon"]],
                radius=radius_m,
                color="#cc0000",
                fill=True,
                fillColor="#cc0000",
                fillOpacity=0.15,
                weight=2,
                dash_array="5,5",
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=f"{dz_name} ({dz['area_km2'] * 0.386102:.1f} mi²)",
            ).add_to(dz_group)

            # Label
            folium.Marker(
                location=[dz["center_lat"], dz["center_lon"]],
                icon=folium.DivIcon(
                    html=f'<div style="font-size:11px;font-weight:bold;color:#cc0000;white-space:nowrap;text-shadow:1px 1px #fff;">{dz_name}</div>',
                    icon_size=(150, 20),
                    icon_anchor=(75, 10),
                ),
            ).add_to(dz_group)

        dz_group.add_to(m)

    # Node markers
    node_group = folium.FeatureGroup(name="Nodes", show=True)
    for node in nodes:
        age = int(time.time()) - node["last_seen"]
        if age < 3600:
            color = "#00cc00"
        elif age < 86400:
            color = "#cccc00"
        else:
            color = "#cc0000"

        label = escape(node.get("short_name") or node.get("long_name") or node["node_id"])
        popup_html = f"""
        <b>{escape(node.get('long_name') or node['node_id'])}</b><br>
        ID: {escape(node['node_id'])}<br>
        Hardware: {escape(node.get('hw_model') or 'Unknown')}<br>
        Alt: {node.get('altitude', '?')}m<br>
        Last seen: {_time_ago(node['last_seen'])}
        """

        folium.CircleMarker(
            location=[node["latitude"], node["longitude"]],
            radius=10,
            color=color,
            weight=2,
            fill=True,
            fillColor=color,
            fillOpacity=0.8,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=label,
        ).add_to(node_group)

        folium.Marker(
            location=[node["latitude"], node["longitude"]],
            icon=folium.DivIcon(
                html=f'<div style="font-size:10px;font-weight:bold;color:#333;white-space:nowrap;">{label}</div>',
                icon_size=(80, 20),
                icon_anchor=(0, -10),
            ),
        ).add_to(node_group)
    node_group.add_to(m)

    # Placement suggestions
    if suggestions:
        sug_group = folium.FeatureGroup(name="Suggested Placements", show=True)
        for sug in suggestions:
            elev_str = f"{sug['elevation_m'] * 3.28084:.0f} ft" if sug.get("elevation_m") else "?"
            reduction_mi2 = sug['shadow_reduction_km2'] * 0.386102
            popup_html = f"""
            <b>Suggestion #{sug['rank']}</b><br>
            Location: {sug['latitude']:.4f}, {sug['longitude']:.4f}<br>
            Elevation: {elev_str}<br>
            Shadow Reduction: {reduction_mi2:.2f} mi² ({sug['shadow_reduction_pct']:.1f}%)<br>
            Cells Improved: {sug['cells_improved']}<br>
            <i>{escape(sug.get('reasoning', ''))}</i>
            """

            folium.Marker(
                location=[sug["latitude"], sug["longitude"]],
                icon=folium.Icon(color="green", icon="plus", prefix="fa"),
                popup=folium.Popup(popup_html, max_width=350),
                tooltip=f"Suggestion #{sug['rank']}: -{reduction_mi2:.1f} mi²",
            ).add_to(sug_group)
        sug_group.add_to(m)

    # Legend
    legend_html = """
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;background:white;
                padding:10px;border-radius:5px;border:2px solid #ccc;font-size:12px;">
        <b>Shadow Intensity</b><br>
        <span style="color:#00ff00;">&#9632;</span> Good Coverage<br>
        <span style="color:#ffff00;">&#9632;</span> Weak Coverage<br>
        <span style="color:#ff8800;">&#9632;</span> Poor Coverage<br>
        <span style="color:#cc0000;">&#9632;</span> Dead Zone<br>
        <br><b>Nodes</b><br>
        <span style="color:#00cc00;">&#9679;</span> Active (< 1h)<br>
        <span style="color:#cccc00;">&#9679;</span> Stale (< 24h)<br>
        <span style="color:#cc0000;">&#9679;</span> Offline (> 24h)<br>
        <br><b>Markers</b><br>
        <span style="color:#00cc00;">&#10010;</span> Suggested Placement
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    folium.LayerControl().add_to(m)
    return m._repr_html_()


def generate_suggestion_map(store: DataStore):
    suggestions = store.get_placement_suggestions(limit=5)
    nodes = store.get_nodes_with_positions()

    if not suggestions:
        return None

    center_lat = suggestions[0]["latitude"]
    center_lon = suggestions[0]["longitude"]

    m = folium.Map(location=[center_lat, center_lon], zoom_start=11, prefer_canvas=True)

    # Existing nodes
    for node in nodes:
        label = node.get("short_name") or node["node_id"]
        folium.CircleMarker(
            location=[node["latitude"], node["longitude"]],
            radius=9,
            color="#0066cc",
            weight=2,
            fill=True,
            fillColor="#0066cc",
            fillOpacity=0.7,
            tooltip=label,
        ).add_to(m)

    # Suggestions with coverage radius circles
    for sug in suggestions:
        folium.Marker(
            location=[sug["latitude"], sug["longitude"]],
            icon=folium.Icon(color="green", icon="plus", prefix="fa"),
            tooltip=f"#{sug['rank']}: -{sug['shadow_reduction_km2'] * 0.386102:.1f} mi²",
        ).add_to(m)

        # Show approximate coverage area
        folium.Circle(
            location=[sug["latitude"], sug["longitude"]],
            radius=5000,  # 5km approximate display radius
            color="#00cc00",
            fill=True,
            fillColor="#00cc00",
            fillOpacity=0.1,
            weight=1,
        ).add_to(m)

    return m._repr_html_()
