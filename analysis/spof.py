"""Single Point of Failure (articulation point) detection for the mesh link graph.

Builds an undirected graph from recent link observations, then uses an
iterative depth-first search to find articulation points — nodes whose removal
would split the network into disconnected components.

For each articulation point the impact score is computed: the total number of
nodes that would become unreachable from the largest remaining component if
that node were removed. Higher impact = more critical.
"""

import logging

log = logging.getLogger(__name__)


def find_spof_nodes(nodes, links):
    """Return a list of articulation point dicts, sorted by impact descending.

    Args:
        nodes: list of node dicts with at least 'node_id'
        links: list of link dicts with 'node_a_id' and 'node_b_id'

    Returns:
        list of dicts:
            node_id       – the articulation point
            impact        – number of nodes cut off if this node is removed
            components    – list of sorted lists, each a group of isolated node IDs
    """
    node_ids = {n["node_id"] for n in nodes}

    # Build undirected adjacency list (include only nodes we know about)
    adj = {nid: [] for nid in node_ids}
    for link in links:
        a, b = link["node_a_id"], link["node_b_id"]
        if a in adj and b in adj and a != b:
            adj[a].append(b)
            adj[b].append(a)

    # Remove isolated nodes — they can't be articulation points
    connected = {nid for nid, neighbors in adj.items() if neighbors}
    if len(connected) < 2:
        return []

    ap = _find_articulation_points(adj, connected)

    if not ap:
        return []

    # For each articulation point, compute isolated components
    results = []
    for node in ap:
        remaining = connected - {node}
        components = _connected_components(adj, remaining)
        if len(components) < 2:
            continue
        largest = max(components, key=len)
        cut_off = [c for c in components if c is not largest]
        impact = sum(len(c) for c in cut_off)
        results.append({
            "node_id": node,
            "impact": impact,
            "components": [sorted(c) for c in cut_off],
        })

    results.sort(key=lambda x: x["impact"], reverse=True)
    log.debug("SPOF analysis: %d articulation points found among %d nodes", len(results), len(connected))
    return results


def _find_articulation_points(adj, connected):
    """Iterative Tarjan articulation-point algorithm. Returns a set of node IDs."""
    disc = {}
    low = {}
    parent = {}
    ap = set()
    timer = [0]

    for start in connected:
        if start in disc:
            continue
        parent[start] = None
        disc[start] = low[start] = timer[0]
        timer[0] += 1

        # Stack frames: [node, neighbor_list, neighbor_index, tree_child_count]
        stack = [[start, list(adj[start]), 0, 0]]

        while stack:
            frame = stack[-1]
            u, neighbors, idx, _ = frame

            if idx < len(neighbors):
                v = neighbors[idx]
                frame[2] += 1  # advance neighbor index

                if v not in disc:
                    parent[v] = u
                    disc[v] = low[v] = timer[0]
                    timer[0] += 1
                    frame[3] += 1  # u gains a DFS tree child
                    stack.append([v, list(adj[v]), 0, 0])
                elif v != parent.get(u):
                    # Back edge — update low via discovery time
                    low[u] = min(low[u], disc[v])
            else:
                # All neighbours of u processed — pop and propagate
                stack.pop()
                if stack:
                    pu = stack[-1][0]
                    low[pu] = min(low[pu], low[u])
                    if parent.get(pu) is None:
                        # Root is AP if it has 2+ DFS tree children
                        if stack[-1][3] >= 2:
                            ap.add(pu)
                    elif low[u] >= disc[pu]:
                        ap.add(pu)

    return ap


def _connected_components(adj, node_set):
    """Return list of sets, each a connected component within node_set."""
    visited = set()
    components = []
    for start in node_set:
        if start in visited:
            continue
        component = set()
        stack = [start]
        while stack:
            n = stack.pop()
            if n in visited:
                continue
            visited.add(n)
            component.add(n)
            for nb in adj[n]:
                if nb in node_set and nb not in visited:
                    stack.append(nb)
        components.append(component)
    return components
