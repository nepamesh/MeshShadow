import time
from flask import Blueprint, render_template, current_app, jsonify, Response, request

from rendering.maps import generate_propagation_map, generate_node_map
from rendering.charts import (
    chart_snr_history, chart_network_activity, chart_node_battery, chart_weather_correlation,
)
from rendering.shadow_maps import generate_shadow_map
from rendering.shadow_charts import (
    chart_coverage_timeline, chart_coverage_breakdown,
    chart_dead_zone_evolution, chart_shadow_overview,
)
from analysis.placement import evaluate_placement
from analysis.blackholes import run_black_hole_detection
from analysis.spof import find_spof_nodes
import config

bp = Blueprint("main", __name__)


def safe_int(value, default, min_val=1, max_val=8760):
    """Safely parse an integer query parameter with bounds."""
    try:
        v = int(value)
        return max(min_val, min(v, max_val))
    except (TypeError, ValueError):
        return default


def _store():
    return current_app.config["store"]


@bp.route("/")
def dashboard():
    store = _store()
    summary = store.get_mesh_summary()
    nodes = store.get_all_nodes()
    return render_template("dashboard.html", summary=summary, nodes=nodes)


@bp.route("/map")
def map_page():
    return render_template("map.html")


@bp.route("/map/data")
def map_data():
    store = _store()
    hours = safe_int(request_arg("hours"), 24)
    html = generate_propagation_map(store, hours)
    return Response(html, mimetype="text/html")


@bp.route("/node/<node_id>")
def node_detail(node_id):
    store = _store()
    node = store.get_node(node_id)
    if not node:
        # Try matching by short_name
        all_nodes = store.get_all_nodes()
        for n in all_nodes:
            if (n.get("short_name") or "").lower() == node_id.lower() or \
               (n.get("long_name") or "").lower() == node_id.lower():
                node = n
                node_id = n["node_id"]
                break
    if not node:
        return "Node not found", 404

    metrics = store.get_node_metrics_history(node_id, 48)
    links = store.get_latest_links(24)
    node_links = [l for l in links if l["node_a_id"] == node_id or l["node_b_id"] == node_id]
    return render_template("node.html", node=node, metrics=metrics, links=node_links)


# --- API Endpoints ---

@bp.route("/api/summary")
def api_summary():
    return jsonify(_store().get_mesh_summary())


@bp.route("/api/nodes")
def api_nodes():
    return jsonify(_store().get_all_nodes())


@bp.route("/api/links")
def api_links():
    hours = safe_int(request_arg("hours"), 24)
    return jsonify(_store().get_latest_links(hours))


@bp.route("/api/chart/snr/<node_a>/<node_b>")
def api_chart_snr(node_a, node_b):
    hours = safe_int(request_arg("hours"), 24)
    buf = chart_snr_history(_store(), node_a, node_b, hours)
    if not buf:
        return "No data", 404
    return Response(buf.getvalue(), mimetype="image/png")


@bp.route("/api/chart/activity")
def api_chart_activity():
    hours = safe_int(request_arg("hours"), 24)
    buf = chart_network_activity(_store(), hours)
    if not buf:
        return "No data", 404
    return Response(buf.getvalue(), mimetype="image/png")


@bp.route("/api/chart/battery/<node_id>")
def api_chart_battery(node_id):
    hours = safe_int(request_arg("hours"), 48)
    buf = chart_node_battery(_store(), node_id, hours)
    if not buf:
        return "No data", 404
    return Response(buf.getvalue(), mimetype="image/png")


@bp.route("/api/chart/weather")
def api_chart_weather():
    hours = safe_int(request_arg("hours"), 168)
    buf = chart_weather_correlation(_store(), hours)
    if not buf:
        return "No data", 404
    return Response(buf.getvalue(), mimetype="image/png")


def request_arg(name, default=None):
    return request.args.get(name, default)


# --- Shadow Mapper Pages ---

@bp.route("/shadows")
def shadow_map_page():
    return render_template("shadow_map.html")


@bp.route("/shadows/data")
def shadow_map_data():
    html = generate_shadow_map(_store())
    return Response(html, mimetype="text/html")


@bp.route("/shadows/dashboard")
def shadow_dashboard():
    store = _store()
    coverage = store.get_coverage_summary()
    dead_zones = store.get_dead_zones(active_only=True)
    suggestions = store.get_placement_suggestions(limit=5)
    return render_template("shadow_dashboard.html",
                           coverage=coverage, dead_zones=dead_zones, suggestions=suggestions)


@bp.route("/suggestions")
def suggestions_page():
    store = _store()
    suggestions = store.get_placement_suggestions(limit=10)
    summary = store.get_coverage_summary()
    return render_template("suggestions.html", suggestions=suggestions, summary=summary)


# --- Shadow Mapper API ---

@bp.route("/api/shadow/summary")
def api_shadow_summary():
    return jsonify(_store().get_coverage_summary())


@bp.route("/api/shadow/deadzones")
def api_shadow_deadzones():
    return jsonify(_store().get_dead_zones(active_only=True))


@bp.route("/api/shadow/suggestions")
def api_shadow_suggestions():
    return jsonify(_store().get_placement_suggestions(limit=10))


@bp.route("/api/shadow/snapshots")
def api_shadow_snapshots():
    days = safe_int(request.args.get("days"), 30, max_val=365)
    return jsonify(_store().get_coverage_snapshots(days))


@bp.route("/api/shadow/evaluate")
def api_shadow_evaluate():
    lat = request.args.get("lat")
    lon = request.args.get("lon")
    if not lat or not lon:
        return jsonify({"error": "lat and lon required"}), 400
    try:
        lat = float(lat)
        lon = float(lon)
    except ValueError:
        return jsonify({"error": "Invalid coordinates"}), 400
    result = evaluate_placement(_store(), lat, lon, config.MAX_NODE_RANGE_KM)
    return jsonify(result)


@bp.route("/api/shadow/chart/coverage")
def api_shadow_chart_coverage():
    days = safe_int(request.args.get("days"), 30, max_val=365)
    buf = chart_coverage_timeline(_store(), days)
    if not buf:
        return "No data", 404
    return Response(buf.getvalue(), mimetype="image/png")


@bp.route("/api/shadow/chart/breakdown")
def api_shadow_chart_breakdown():
    buf = chart_coverage_breakdown(_store())
    if not buf:
        return "No data", 404
    return Response(buf.getvalue(), mimetype="image/png")


@bp.route("/api/shadow/chart/evolution")
def api_shadow_chart_evolution():
    days = safe_int(request.args.get("days"), 30, max_val=365)
    buf = chart_dead_zone_evolution(_store(), days)
    if not buf:
        return "No data", 404
    return Response(buf.getvalue(), mimetype="image/png")


@bp.route("/api/shadow/chart/overview")
def api_shadow_chart_overview():
    buf = chart_shadow_overview(_store())
    if not buf:
        return "No data", 404
    return Response(buf.getvalue(), mimetype="image/png")


# --- Black Hole Detection Pages ---

@bp.route("/blackholes")
def blackholes_page():
    store = _store()
    holes = store.get_black_holes(active_only=True)
    # Enrich with node names
    for h in holes:
        affected = h.get("affected_nodes", [])
        enriched = []
        for nid in affected:
            node = store.get_node(nid)
            enriched.append({
                "node_id": nid,
                "short_name": node.get("short_name") if node else None,
                "long_name": node.get("long_name") if node else None,
            })
        h["affected_node_details"] = enriched
    routing_stats = store.get_node_routing_stats()
    return render_template("blackholes.html", holes=holes, routing_stats=routing_stats)


# --- Black Hole Detection API ---

@bp.route("/api/blackholes")
def api_blackholes():
    return jsonify(_store().get_black_holes(active_only=True))


@bp.route("/api/blackholes/all")
def api_blackholes_all():
    return jsonify(_store().get_black_holes(active_only=False))


@bp.route("/api/routing/stats")
def api_routing_stats():
    return jsonify(_store().get_node_routing_stats())


@bp.route("/api/routing/node/<node_id>")
def api_routing_node(node_id):
    stats = _store().get_node_routing_stats(node_id)
    if not stats:
        return jsonify({"error": "No routing data for this node"}), 404
    return jsonify(stats)


@bp.route("/api/routing/suspects")
def api_routing_suspects():
    return jsonify(_store().get_suspect_nodes())


@bp.route("/api/routing/asymmetric")
def api_routing_asymmetric():
    hours = safe_int(request.args.get("hours"), 24)
    return jsonify(_store().get_asymmetric_links(hours))


@bp.route("/api/traceroutes")
def api_traceroutes():
    hours = safe_int(request.args.get("hours"), 72, max_val=720)
    limit = safe_int(request.args.get("limit"), 50, max_val=200)
    return jsonify(_store().get_traceroutes(hours, limit))


@bp.route("/api/packets/stats")
def api_packet_stats():
    hours = safe_int(request.args.get("hours"), 24)
    return jsonify(_store().get_packet_stats_by_node(hours))


@bp.route("/api/spof")
def api_spof():
    store = _store()
    hours = safe_int(request.args.get("hours"), 24)
    nodes = store.get_all_nodes()
    links = store.get_latest_links(hours)
    spof = find_spof_nodes(nodes, links)
    # Enrich with node names
    node_map = {n["node_id"]: n for n in nodes}
    for entry in spof:
        n = node_map.get(entry["node_id"], {})
        entry["short_name"] = n.get("short_name")
        entry["long_name"] = n.get("long_name")
        entry["latitude"] = n.get("latitude")
        entry["longitude"] = n.get("longitude")
    return jsonify(spof)
