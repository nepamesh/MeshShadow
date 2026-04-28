"""Black hole detection engine for mesh routing failures.

A "black hole" is an area where packets enter but don't come out — routing
failures that are distinct from RF dead zones (no signal). This module detects
them by analyzing:

1. Asymmetric links: A sees B but B never sees A (one-way packet sinks)
2. Hop anomalies: packets from a region consistently take more hops than
   geometry predicts, suggesting detours around a problem area
3. Forwarding failures: nodes that receive traffic but rarely relay it
4. Via-MQTT leakage: nodes whose packets only arrive via internet gateway,
   implying their RF-relayed packets get lost
5. Traceroute path analysis: actual routes that detour around an area
"""

import json
import logging
import math
import time

from database.store import DataStore

log = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0

# Roles that are NOT expected to forward traffic — don't flag these as black holes
NON_FORWARDING_ROLES = {
    "CLIENT", "CLIENT_MUTE", "CLIENT_HIDDEN", "CLIENT_BASE",
    "TRACKER", "SENSOR", "TAK", "TAK_TRACKER", "LOST_AND_FOUND",
}


def _haversine(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _direction_label(lat, lon, center_lat, center_lon):
    dlat = lat - center_lat
    dlon = lon - center_lon
    angle = math.degrees(math.atan2(dlon, dlat))
    if angle < 0:
        angle += 360
    directions = [
        (0, "N"), (45, "NE"), (90, "E"), (135, "SE"),
        (180, "S"), (225, "SW"), (270, "W"), (315, "NW"), (360, "N"),
    ]
    for i in range(len(directions) - 1):
        mid = (directions[i][0] + directions[i + 1][0]) / 2
        if angle < mid:
            return directions[i][1]
    return "N"


def _cluster_nodes(nodes_with_pos, radius_km=5.0):
    """Group nearby nodes into spatial clusters."""
    clusters = []
    used = set()

    for i, node in enumerate(nodes_with_pos):
        if i in used:
            continue
        cluster = [node]
        used.add(i)

        for j, other in enumerate(nodes_with_pos):
            if j in used:
                continue
            dist = _haversine(node["latitude"], node["longitude"],
                              other["latitude"], other["longitude"])
            if dist <= radius_km:
                cluster.append(other)
                used.add(j)

        if cluster:
            clusters.append(cluster)

    return clusters


def compute_node_routing_stats(store: DataStore, hours=24):
    """Compute per-node routing statistics from packet observations."""
    packet_stats = store.get_packet_stats_by_node(hours)
    relay_stats = store.get_relay_stats(hours)
    asymmetric = store.get_asymmetric_links(hours)

    relay_map = {r["node_id"]: r["relay_count"] for r in relay_stats}

    # Count asymmetric links per node
    asym_count = {}
    for link in asymmetric:
        a = link["node_a_id"]
        b = link["node_b_id"]
        asym_count[a] = asym_count.get(a, 0) + 1
        asym_count[b] = asym_count.get(b, 0) + 1

    for ps in packet_stats:
        node_id = ps["from_id"]
        packets_seen = ps["packet_count"]
        relay_count = relay_map.get(node_id, 0)

        # Check node role — non-forwarding nodes get a pass
        node = store.get_node(node_id)
        node_role = node.get("role") if node else None

        # Forwarding ratio: how often this node relays others' packets
        # relative to how much traffic it generates itself.
        # Non-forwarding roles (CLIENT, TRACKER, etc.) are expected to have 0.
        if node_role in NON_FORWARDING_ROLES:
            forwarding_ratio = None  # not applicable
        else:
            forwarding_ratio = relay_count / max(packets_seen, 1)

        via_mqtt_pct = (ps["mqtt_count"] / packets_seen * 100) if packets_seen > 0 else 0

        store.upsert_node_routing_stats(
            node_id,
            packets_seen=packets_seen,
            packets_as_relay=relay_count,
            avg_hops_taken=ps["avg_hops"],
            forwarding_ratio=round(forwarding_ratio, 3),
            via_mqtt_pct=round(via_mqtt_pct, 1),
            asymmetric_links=asym_count.get(node_id, 0),
        )

    log.info("Updated routing stats for %d nodes", len(packet_stats))
    return len(packet_stats)


def detect_asymmetric_clusters(store: DataStore, hours=24, mesh_center_lat=41.0,
                                mesh_center_lon=-75.9):
    """Detect clusters of asymmetric links that indicate a routing black hole.

    If multiple one-way links point INTO an area but nothing comes back out,
    that area is acting as a packet sink.
    """
    asymmetric = store.get_asymmetric_links(hours)
    if not asymmetric:
        return []

    # Get positions of involved nodes
    sink_nodes = []  # nodes that are heard but don't hear back
    for link in asymmetric:
        b_node = store.get_node(link["node_b_id"])
        if not b_node or not b_node.get("latitude") or not b_node.get("longitude"):
            continue
        # Skip non-forwarding nodes — they're SUPPOSED to not respond
        if b_node.get("role") in NON_FORWARDING_ROLES:
            continue
        if True:
            sink_nodes.append({
                "node_id": link["node_b_id"],
                "latitude": b_node["latitude"],
                "longitude": b_node["longitude"],
                "reporter": link["node_a_id"],
                "forward_obs": link["forward_obs"],
                "snr": link["forward_snr"],
            })

    if not sink_nodes:
        return []

    # Cluster nearby sink nodes
    clusters = _cluster_nodes(sink_nodes, radius_km=8.0)

    results = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue

        center_lat = sum(n["latitude"] for n in cluster) / len(cluster)
        center_lon = sum(n["longitude"] for n in cluster) / len(cluster)

        # Calculate radius
        max_dist = 0
        for n in cluster:
            d = _haversine(center_lat, center_lon, n["latitude"], n["longitude"])
            max_dist = max(max_dist, d)
        radius = max(max_dist * 1.2, 1.0)  # at least 1km

        affected = [n["node_id"] for n in cluster]
        total_obs = sum(n["forward_obs"] for n in cluster)

        # Severity: more nodes + more observations = higher confidence
        severity = min(1.0, (len(cluster) / 5.0) * (total_obs / 20.0))

        dist_mi = _haversine(center_lat, center_lon,
                             mesh_center_lat, mesh_center_lon) * 0.621371
        direction = _direction_label(center_lat, center_lon,
                                     mesh_center_lat, mesh_center_lon)

        results.append({
            "name": f"{direction} Black Hole ({dist_mi:.1f}mi)",
            "center_lat": center_lat,
            "center_lon": center_lon,
            "radius_km": round(radius, 2),
            "severity": round(severity, 2),
            "evidence_type": "asymmetric_links",
            "affected_nodes": affected,
            "description": (
                f"{len(cluster)} nodes in this area have asymmetric links — "
                f"neighboring nodes hear them but they don't hear back. "
                f"Packets entering this zone may be getting lost. "
                f"({total_obs} one-way observations)"
            ),
        })

    results.sort(key=lambda x: x["severity"], reverse=True)
    return results


def detect_hop_anomalies(store: DataStore, hours=24, mesh_center_lat=41.0,
                          mesh_center_lon=-75.9):
    """Detect nodes whose packets consistently take more hops than expected.

    If a node is geometrically close to the gateway but its packets always
    arrive with high hop counts, routing is detouring around something.
    """
    packet_stats = store.get_packet_stats_by_node(hours)
    if not packet_stats:
        return []

    # Get positions and compute expected hops based on distance
    anomalies = []
    for ps in packet_stats:
        if ps["avg_hops"] is None or ps["packet_count"] < 5:
            continue

        node = store.get_node(ps["from_id"])
        if not node or not node.get("latitude"):
            continue

        # Rough expected hops: 1 hop per ~5km of distance to mesh center
        dist = _haversine(node["latitude"], node["longitude"],
                          mesh_center_lat, mesh_center_lon)
        expected_hops = max(1, dist / 5.0)
        actual_hops = ps["avg_hops"]

        # Flag if actual hops are significantly more than expected
        hop_ratio = actual_hops / expected_hops if expected_hops > 0 else 0
        if hop_ratio > 2.0 and actual_hops > 2:
            anomalies.append({
                "node_id": ps["from_id"],
                "latitude": node["latitude"],
                "longitude": node["longitude"],
                "expected_hops": round(expected_hops, 1),
                "actual_hops": round(actual_hops, 1),
                "hop_ratio": round(hop_ratio, 1),
                "packet_count": ps["packet_count"],
                "short_name": node.get("short_name"),
            })

    if not anomalies:
        return []

    # Cluster nodes with hop anomalies
    clusters = _cluster_nodes(anomalies, radius_km=8.0)

    results = []
    for cluster in clusters:
        if len(cluster) < 1:
            continue

        center_lat = sum(n["latitude"] for n in cluster) / len(cluster)
        center_lon = sum(n["longitude"] for n in cluster) / len(cluster)

        max_dist = max(
            (_haversine(center_lat, center_lon, n["latitude"], n["longitude"])
             for n in cluster),
            default=1.0
        )
        radius = max(max_dist * 1.2, 1.0)

        avg_ratio = sum(n["hop_ratio"] for n in cluster) / len(cluster)
        severity = min(1.0, (avg_ratio - 1.0) / 3.0 * len(cluster) / 3.0)

        affected = [n["node_id"] for n in cluster]
        dist_mi = _haversine(center_lat, center_lon,
                             mesh_center_lat, mesh_center_lon) * 0.621371
        direction = _direction_label(center_lat, center_lon,
                                     mesh_center_lat, mesh_center_lon)

        hop_details = ", ".join(
            f"{n.get('short_name') or n['node_id'][-4:]}: "
            f"{n['actual_hops']:.1f} actual vs {n['expected_hops']:.1f} expected"
            for n in cluster[:5]
        )

        results.append({
            "name": f"{direction} Routing Detour ({dist_mi:.1f}mi)",
            "center_lat": center_lat,
            "center_lon": center_lon,
            "radius_km": round(radius, 2),
            "severity": round(severity, 2),
            "evidence_type": "hop_anomaly",
            "affected_nodes": affected,
            "description": (
                f"{len(cluster)} node(s) in this area have packets taking "
                f"{avg_ratio:.1f}x more hops than distance predicts. "
                f"Routing may be detouring around a failure zone. ({hop_details})"
            ),
        })

    return results


def detect_mqtt_leakers(store: DataStore, hours=24, mqtt_threshold_pct=80.0,
                         mesh_center_lat=41.0, mesh_center_lon=-75.9):
    """Detect nodes whose packets predominantly arrive via MQTT (internet) rather
    than RF relay. This suggests their RF-forwarded packets are getting lost.

    Nodes that are RF-connected to the mesh but whose traffic only reaches the
    server via MQTT gateway are likely surrounded by routing failures.
    """
    packet_stats = store.get_packet_stats_by_node(hours)
    if not packet_stats:
        return []

    leakers = []
    for ps in packet_stats:
        if ps["packet_count"] < 5:
            continue

        mqtt_pct = (ps["mqtt_count"] / ps["packet_count"] * 100)
        if mqtt_pct < mqtt_threshold_pct:
            continue

        node = store.get_node(ps["from_id"])
        if not node or not node.get("latitude"):
            continue

        # Non-forwarding roles are expected to use MQTT — skip them
        if node.get("role") in NON_FORWARDING_ROLES:
            continue

        # Check if this node has neighbors (it's connected to the mesh)
        # If it has neighbors but traffic only comes via MQTT, that's suspicious
        leakers.append({
            "node_id": ps["from_id"],
            "latitude": node["latitude"],
            "longitude": node["longitude"],
            "mqtt_pct": round(mqtt_pct, 1),
            "packet_count": ps["packet_count"],
            "short_name": node.get("short_name"),
        })

    if not leakers:
        return []

    clusters = _cluster_nodes(leakers, radius_km=8.0)

    results = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue

        center_lat = sum(n["latitude"] for n in cluster) / len(cluster)
        center_lon = sum(n["longitude"] for n in cluster) / len(cluster)

        max_dist = max(
            (_haversine(center_lat, center_lon, n["latitude"], n["longitude"])
             for n in cluster),
            default=1.0
        )
        radius = max(max_dist * 1.2, 1.0)

        avg_mqtt = sum(n["mqtt_pct"] for n in cluster) / len(cluster)
        severity = min(1.0, (avg_mqtt / 100.0) * (len(cluster) / 3.0))

        affected = [n["node_id"] for n in cluster]
        dist_mi = _haversine(center_lat, center_lon,
                             mesh_center_lat, mesh_center_lon) * 0.621371
        direction = _direction_label(center_lat, center_lon,
                                     mesh_center_lat, mesh_center_lon)

        results.append({
            "name": f"{direction} MQTT Leak ({dist_mi:.1f}mi)",
            "center_lat": center_lat,
            "center_lon": center_lon,
            "radius_km": round(radius, 2),
            "severity": round(severity, 2),
            "evidence_type": "mqtt_leak",
            "affected_nodes": affected,
            "description": (
                f"{len(cluster)} nodes in this area send {avg_mqtt:.0f}% of "
                f"packets via MQTT instead of RF relay. Their RF-forwarded "
                f"packets may be getting lost in the mesh."
            ),
        })

    return results


def analyze_traceroute_detours(store: DataStore, hours=72, mesh_center_lat=41.0,
                                mesh_center_lon=-75.9):
    """Analyze traceroute data to find areas that routes consistently avoid.

    If traceroutes between nearby nodes take long detours, the area being
    avoided likely has routing problems.
    """
    traceroutes = store.get_traceroutes(hours, limit=100)
    if len(traceroutes) < 3:
        return []

    detours = []
    for tr in traceroutes:
        if not tr.get("route_forward") or not tr["completed"]:
            continue

        origin = store.get_node(tr["origin_id"])
        dest = store.get_node(tr["destination_id"])
        if not origin or not dest:
            continue
        if not origin.get("latitude") or not dest.get("latitude"):
            continue

        direct_dist = _haversine(
            origin["latitude"], origin["longitude"],
            dest["latitude"], dest["longitude"])

        # Calculate actual route distance
        route_nodes = [tr["origin_id"]] + tr["route_forward"] + [tr["destination_id"]]
        route_dist = 0
        prev_pos = (origin["latitude"], origin["longitude"])

        for nid in route_nodes[1:]:
            n = store.get_node(nid)
            if n and n.get("latitude"):
                d = _haversine(prev_pos[0], prev_pos[1],
                               n["latitude"], n["longitude"])
                route_dist += d
                prev_pos = (n["latitude"], n["longitude"])

        if direct_dist < 1.0:
            continue

        detour_ratio = route_dist / direct_dist if direct_dist > 0 else 0
        if detour_ratio > 2.0:
            detours.append({
                "origin": tr["origin_id"],
                "destination": tr["destination_id"],
                "direct_km": round(direct_dist, 1),
                "route_km": round(route_dist, 1),
                "detour_ratio": round(detour_ratio, 1),
                "route": route_nodes,
                "hop_count": tr["hop_count"],
            })

    # For now, just log detours — full spatial clustering of avoided areas
    # requires more traceroute data to be statistically meaningful
    if detours:
        log.info("Found %d traceroute detours (ratio > 2.0x)", len(detours))
        for d in detours[:5]:
            log.info("  %s -> %s: %.1f km direct, %.1f km actual (%.1fx), %d hops",
                     d["origin"][-4:], d["destination"][-4:],
                     d["direct_km"], d["route_km"], d["detour_ratio"], d["hop_count"])

    return detours


def _merge_black_holes(new_holes, existing_holes, overlap_km=5.0):
    """Match new detections against existing black holes by proximity."""
    matched = {}

    for nh in new_holes:
        best_match = None
        best_dist = float("inf")

        for eh in existing_holes:
            dist = _haversine(nh["center_lat"], nh["center_lon"],
                              eh["center_lat"], eh["center_lon"])
            if dist < overlap_km and dist < best_dist:
                best_dist = dist
                best_match = eh

        if best_match:
            matched[id(nh)] = best_match["id"]

    return matched


def run_black_hole_detection(store: DataStore, hours=24, mesh_center_lat=41.0,
                              mesh_center_lon=-75.9):
    """Main entry point: run all detection methods and persist results."""
    log.info("Running black hole detection...")

    # Step 1: Update per-node routing stats
    stats_count = compute_node_routing_stats(store, hours)

    # Step 2: Run each detection method
    all_detections = []

    asymmetric = detect_asymmetric_clusters(store, hours, mesh_center_lat, mesh_center_lon)
    all_detections.extend(asymmetric)
    log.info("Asymmetric link clusters: %d", len(asymmetric))

    hop_anomalies = detect_hop_anomalies(store, hours, mesh_center_lat, mesh_center_lon)
    all_detections.extend(hop_anomalies)
    log.info("Hop anomaly clusters: %d", len(hop_anomalies))

    mqtt_leaks = detect_mqtt_leakers(store, hours, 80.0, mesh_center_lat, mesh_center_lon)
    all_detections.extend(mqtt_leaks)
    log.info("MQTT leak clusters: %d", len(mqtt_leaks))

    traceroute_detours = analyze_traceroute_detours(store, hours * 3,
                                                     mesh_center_lat, mesh_center_lon)
    log.info("Traceroute detours: %d", len(traceroute_detours))

    # Step 3: Merge with existing black holes
    existing = store.get_black_holes(active_only=False)
    matched = _merge_black_holes(all_detections, existing)

    matched_existing_ids = set()
    for i, detection in enumerate(all_detections):
        existing_id = matched.get(id(detection))

        if existing_id:
            matched_existing_ids.add(existing_id)
            store.update_black_hole(
                existing_id,
                center_lat=detection["center_lat"],
                center_lon=detection["center_lon"],
                radius_km=detection["radius_km"],
                severity=detection["severity"],
                affected_nodes=json.dumps(detection["affected_nodes"]),
                description=detection["description"],
                active=1,
            )
            log.debug("Updated black hole #%d: %s", existing_id, detection["name"])
        else:
            bh_id = store.insert_black_hole(
                name=detection["name"],
                center_lat=detection["center_lat"],
                center_lon=detection["center_lon"],
                radius_km=detection["radius_km"],
                severity=detection["severity"],
                evidence_type=detection["evidence_type"],
                affected_nodes=detection["affected_nodes"],
                description=detection["description"],
            )
            log.info("New black hole: %s (severity %.2f)", detection["name"],
                     detection["severity"])

    # Deactivate black holes no longer detected
    for eh in existing:
        if eh["id"] not in matched_existing_ids and eh["active"]:
            store.deactivate_black_hole(eh["id"])
            log.info("Deactivated black hole: %s", eh["name"])

    # Step 4: Prune old packet observations
    store.cleanup_old_packets(max_age_hours=72)

    log.info("Black hole detection complete: %d active detections, %d node stats",
             len(all_detections), stats_count)

    return {
        "asymmetric_clusters": len(asymmetric),
        "hop_anomalies": len(hop_anomalies),
        "mqtt_leaks": len(mqtt_leaks),
        "traceroute_detours": len(traceroute_detours),
        "total_active": len(all_detections),
        "nodes_analyzed": stats_count,
    }
