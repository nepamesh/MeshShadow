import logging
import time

import folium
from folium.plugins import MarkerCluster

from database.store import DataStore

log = logging.getLogger(__name__)


def _snr_to_color(snr):
    """Map SNR value to a color: green (good) -> yellow -> red (poor)."""
    if snr is None:
        return "#888888"
    if snr >= 10:
        return "#00cc00"
    elif snr >= 5:
        return "#88cc00"
    elif snr >= 0:
        return "#cccc00"
    elif snr >= -5:
        return "#cc8800"
    elif snr >= -10:
        return "#cc4400"
    else:
        return "#cc0000"


def _snr_to_weight(obs_count):
    """Map observation count to line weight."""
    if obs_count >= 50:
        return 6
    elif obs_count >= 20:
        return 5
    elif obs_count >= 5:
        return 4
    return 3


def _time_ago(ts):
    """Human-readable time ago string."""
    diff = int(time.time()) - ts
    if diff < 60:
        return f"{diff}s ago"
    elif diff < 3600:
        return f"{diff // 60}m ago"
    elif diff < 86400:
        return f"{diff // 3600}h ago"
    else:
        return f"{diff // 86400}d ago"


def generate_propagation_map(store: DataStore, hours: int = 24):
    """Generate a Folium HTML map showing nodes and link quality."""
    nodes = store.get_all_nodes()
    links = store.get_latest_links(hours)

    # Find center of all nodes with positions
    positioned = [n for n in nodes if n["latitude"] and n["longitude"]]
    if positioned:
        center_lat = sum(n["latitude"] for n in positioned) / len(positioned)
        center_lon = sum(n["longitude"] for n in positioned) / len(positioned)
    else:
        center_lat, center_lon = 41.0, -75.9  # Default NE PA

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=10,
        tiles=None,
        prefer_canvas=True,
        zoom_control=True,
    )

    # Add tile layers
    folium.TileLayer("OpenStreetMap", name="Street Map").add_to(m)
    folium.TileLayer(
        tiles="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attr='Map data: &copy; OpenStreetMap contributors, SRTM | Map style: &copy; OpenTopoMap',
        name="Topo Map",
    ).add_to(m)

    # Link lines layer
    link_group = folium.FeatureGroup(name="RF Links", show=True)
    for link in links:
        if not all([link["node_a_lat"], link["node_a_lon"], link["node_b_lat"], link["node_b_lon"]]):
            continue
        color = _snr_to_color(link["avg_snr"])
        weight = _snr_to_weight(link["obs_count"])

        node_a_name = _get_node_label(store, link["node_a_id"])
        node_b_name = _get_node_label(store, link["node_b_id"])

        popup_html = f"""
        <b>{node_a_name} ↔ {node_b_name}</b><br>
        SNR: {link['avg_snr']:.1f} dB (min: {link['min_snr']:.1f}, max: {link['max_snr']:.1f})<br>
        Distance: {link['avg_distance'] * 0.621371:.1f} mi<br>
        Observations: {link['obs_count']}<br>
        Last seen: {_time_ago(link['last_seen'])}
        """

        folium.PolyLine(
            locations=[
                [link["node_a_lat"], link["node_a_lon"]],
                [link["node_b_lat"], link["node_b_lon"]],
            ],
            color=color,
            weight=weight,
            opacity=0.8,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"{node_a_name} ↔ {node_b_name}: {link['avg_snr']:.1f} dB",
        ).add_to(link_group)
    link_group.add_to(m)

    # Node markers layer
    node_group = folium.FeatureGroup(name="Nodes", show=True)
    for node in nodes:
        if not node["latitude"] or not node["longitude"]:
            continue

        # Color by recency
        age = int(time.time()) - node["last_seen"]
        if age < 3600:
            marker_color = "#00cc00"
        elif age < 86400:
            marker_color = "#cccc00"
        else:
            marker_color = "#cc0000"

        label = node["short_name"] or node["long_name"] or node["node_id"]
        popup_html = f"""
        <b>{node.get('long_name') or node['node_id']}</b><br>
        ID: {node['node_id']}<br>
        Hardware: {node.get('hw_model', 'Unknown')}<br>
        Battery: {node.get('battery_level', '?')}%
        ({node.get('voltage', '?')}V)<br>
        Ch Util: {node.get('channel_util', '?')}%<br>
        Alt: {node.get('altitude', '?')}m<br>
        Last seen: {_time_ago(node['last_seen'])}
        """

        folium.CircleMarker(
            location=[node["latitude"], node["longitude"]],
            radius=10,
            color=marker_color,
            weight=2,
            fill=True,
            fillColor=marker_color,
            fillOpacity=0.7,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=label,
        ).add_to(node_group)

        # Node label
        folium.Marker(
            location=[node["latitude"], node["longitude"]],
            icon=folium.DivIcon(
                html=f'<div style="font-size:10px;font-weight:bold;color:#333;white-space:nowrap;">{label}</div>',
                icon_size=(80, 20),
                icon_anchor=(0, -10),
            ),
        ).add_to(node_group)

    node_group.add_to(m)

    # Add legend
    legend_html = """
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;background:white;
                padding:10px;border-radius:5px;border:2px solid #ccc;font-size:12px;">
        <b>SNR Legend</b><br>
        <span style="color:#00cc00;">■</span> ≥10 dB (Excellent)<br>
        <span style="color:#88cc00;">■</span> 5-10 dB (Good)<br>
        <span style="color:#cccc00;">■</span> 0-5 dB (Fair)<br>
        <span style="color:#cc8800;">■</span> -5-0 dB (Poor)<br>
        <span style="color:#cc4400;">■</span> -10 to -5 dB (Bad)<br>
        <span style="color:#cc0000;">■</span> < -10 dB (Critical)<br>
        <br><b>Node Status</b><br>
        <span style="color:#00cc00;">●</span> Active (< 1h)<br>
        <span style="color:#cccc00;">●</span> Stale (< 24h)<br>
        <span style="color:#cc0000;">●</span> Offline (> 24h)
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    folium.LayerControl().add_to(m)
    return m._repr_html_()


def generate_node_map(store: DataStore, node_id: str, hours: int = 24):
    """Generate a map centered on a specific node showing its links."""
    node = store.get_node(node_id)
    if not node or not node["latitude"]:
        return None

    m = folium.Map(location=[node["latitude"], node["longitude"]], zoom_start=12, prefer_canvas=True)

    # The node itself
    folium.CircleMarker(
        location=[node["latitude"], node["longitude"]],
        radius=12,
        color="#0066cc",
        fill=True,
        fillColor="#0066cc",
        fillOpacity=0.8,
        tooltip=node.get("long_name") or node["node_id"],
    ).add_to(m)

    # Its links
    links = store.get_link_observations(
        start_ts=int(time.time()) - hours * 3600,
        node_a=node_id,
    )
    # Aggregate by peer
    peers = {}
    for obs in links:
        peer = obs["node_b_id"] if obs["node_a_id"] == node_id else obs["node_a_id"]
        if peer not in peers:
            peers[peer] = {"snrs": [], "lat": None, "lon": None}
        if obs["snr"] is not None:
            peers[peer]["snrs"].append(obs["snr"])
        peer_pos = store.get_node_position(peer)
        if peer_pos:
            peers[peer]["lat"], peers[peer]["lon"] = peer_pos

    for peer_id, info in peers.items():
        if not info["lat"]:
            continue
        avg_snr = sum(info["snrs"]) / len(info["snrs"]) if info["snrs"] else 0
        color = _snr_to_color(avg_snr)
        peer_label = _get_node_label(store, peer_id)

        folium.CircleMarker(
            location=[info["lat"], info["lon"]],
            radius=10,
            color=color,
            weight=2,
            fill=True,
            fillColor=color,
            fillOpacity=0.7,
            tooltip=f"{peer_label}: {avg_snr:.1f} dB",
        ).add_to(m)

        folium.PolyLine(
            locations=[[node["latitude"], node["longitude"]], [info["lat"], info["lon"]]],
            color=color,
            weight=3,
            opacity=0.7,
        ).add_to(m)

    return m._repr_html_()


def _get_node_label(store, node_id):
    node = store.get_node(node_id)
    if node:
        return node.get("short_name") or node.get("long_name") or node_id
    return node_id
