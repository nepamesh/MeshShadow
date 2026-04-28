function timeAgo(ts) {
    const diff = Math.floor(Date.now() / 1000) - ts;
    if (diff < 60) return diff + "s ago";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return Math.floor(diff / 86400) + "d ago";
}

function updateTimeAgos() {
    document.querySelectorAll(".time-ago").forEach(el => {
        const ts = parseInt(el.dataset.ts);
        if (ts) el.textContent = timeAgo(ts);
    });
}

async function refreshStats() {
    try {
        const resp = await fetch("/api/summary");
        const data = await resp.json();
        const fields = {
            "total-nodes": data.total_nodes,
            "active-1h": data.active_nodes_1h,
            "active-24h": data.active_nodes_24h,
            "links-24h": data.total_links_24h,
            "pairs-24h": data.unique_pairs_24h,
            "positions": data.total_positions,
        };
        for (const [id, val] of Object.entries(fields)) {
            const el = document.getElementById(id);
            if (el) el.textContent = val;
        }
    } catch (e) {
        console.error("Failed to refresh stats:", e);
    }
}

updateTimeAgos();
setInterval(updateTimeAgos, 10000);
setInterval(refreshStats, 30000);

// Table sorting
(function() {
    const table = document.getElementById("node-table");
    if (!table) return;

    const headers = table.querySelectorAll("th.sortable");
    let currentSort = null;
    let currentDir = "asc";

    headers.forEach(th => {
        th.style.cursor = "pointer";
        th.addEventListener("click", function() {
            const col = this.dataset.sort;
            if (currentSort === col) {
                currentDir = currentDir === "asc" ? "desc" : "asc";
            } else {
                currentSort = col;
                currentDir = col === "lastseen" ? "desc" : "asc";
            }

            // Update arrows
            headers.forEach(h => {
                h.querySelector(".sort-arrow").textContent = "";
            });
            this.querySelector(".sort-arrow").textContent = currentDir === "asc" ? " \u25B2" : " \u25BC";

            // Find column index
            const colIndex = Array.from(this.parentNode.children).indexOf(this);
            const tbody = table.querySelector("tbody");
            const rows = Array.from(tbody.querySelectorAll("tr"));

            rows.sort((a, b) => {
                const aCell = a.children[colIndex];
                const bCell = b.children[colIndex];
                let aVal = aCell.dataset.sortValue;
                let bVal = bCell.dataset.sortValue;

                if (col === "chutil" || col === "lastseen") {
                    aVal = parseFloat(aVal);
                    bVal = parseFloat(bVal);
                    if (isNaN(aVal)) aVal = -Infinity;
                    if (isNaN(bVal)) bVal = -Infinity;
                    return currentDir === "asc" ? aVal - bVal : bVal - aVal;
                }

                // String sort
                aVal = aVal || "";
                bVal = bVal || "";
                const cmp = aVal.localeCompare(bVal);
                return currentDir === "asc" ? cmp : -cmp;
            });

            rows.forEach(row => tbody.appendChild(row));
        });
    });
})();
