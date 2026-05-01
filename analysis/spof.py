"""Single Point of Failure (articulation point) detection for the mesh link graph.

Builds an undirected graph from recent link observations, then uses a
depth-first search to find articulation points — nodes whose removal would
split the network into disconnected components.

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
            components    – list of sets, each a group of isolated node IDs
    """
    node_ids = {n["node_id"] for n in nodes}

    # Build undirected adjacency list (include only nodes we know about)
    adj = {nid: set() for nid in node_ids}
    for link in links:
        a, b = link["node_a_id"], link["node_b_id"]
        if a in adj and b in adj and a != b:
            adj[a].add(b)
            adj[b].add(a)

    # Remove isolated nodes — they can't be articulation points
    connected = {nid for nid, neighbors in adj.items() if neighbors}
    if len(connected) < 2:
        return []

    # Tarjan's articulation point algorithm
    disc = {}   # discovery timestamps
    low = {}    # lowest disc reachable via back edges
    parent = {}
    ap = set()
    timer = [0]

    def dfs(u):
        disc[u] = low[u] = timer[0]
        timer[0] += 1
        child_count = 0

        for v in adj[u]:
            if v not in disc:
                child_count += 1
                parent[v] = u
                dfs(v)
                low[u] = min(low[u], low[v])

                # Root with 2+ children
                if parent.get(u) is None and child_count > 1:
                    ap.add(u)
                # Non-root: child can't reach above u via back edges
                if parent.get(u) is not None and low[v] >= disc[u]:
                    ap.add(u)
            elif v != parent.get(u):
                low[u] = min(low[u], disc[v])

    for nid in connected:
        if nid not in disc:
            parent[nid] = None
            dfs(nid)

    if not ap:
        return []

    # For each articulation point, compute isolated components
    results = []
    for node in ap:
        remaining = connected - {node}
        components = _connected_components(adj, remaining)
        if len(components) < 2:
            continue  # shouldn't happen but guard anyway
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
