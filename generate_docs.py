#!/usr/bin/env python3
"""Generate the MeshPropagation setup guide PDF."""

import os
import sys

from fpdf import FPDF


class SetupGuide(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, "MeshPropagation Setup Guide - NEPAMesh RF Monitor", align="R")
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def chapter_title(self, title):
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(26, 26, 46)
        self.cell(0, 12, title)
        self.ln(8)
        self.set_draw_color(78, 205, 196)
        self.set_line_width(0.8)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(6)

    def section_title(self, title):
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(26, 26, 46)
        self.cell(0, 10, title)
        self.ln(8)

    def body_text(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(51, 51, 51)
        self.multi_cell(0, 5.5, text)
        self.ln(3)

    def code_block(self, text):
        self.set_font("Courier", "", 9)
        self.set_fill_color(240, 240, 240)
        self.set_text_color(51, 51, 51)
        # Pad each line
        lines = text.strip().split("\n")
        x = self.get_x()
        for line in lines:
            self.cell(0, 5, "  " + line, fill=True)
            self.ln(5)
        self.ln(3)
        self.set_font("Helvetica", "", 10)

    def bullet(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(51, 51, 51)
        x = self.get_x()
        self.cell(5, 5.5, "-")
        self.multi_cell(0, 5.5, text)
        self.ln(1)

    def numbered_step(self, num, text):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(78, 205, 196)
        self.cell(8, 6, f"{num}.")
        self.set_font("Helvetica", "", 10)
        self.set_text_color(51, 51, 51)
        self.multi_cell(0, 6, text)
        self.ln(2)

    def warning_box(self, text):
        self.set_fill_color(255, 243, 205)
        self.set_draw_color(255, 193, 7)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(133, 100, 4)
        y = self.get_y()
        self.rect(10, y, 190, 14, "DF")
        self.set_xy(14, y + 2)
        self.multi_cell(182, 5, "WARNING: " + text)
        self.ln(6)

    def info_box(self, text):
        self.set_fill_color(209, 236, 241)
        self.set_draw_color(0, 150, 170)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(0, 80, 100)
        y = self.get_y()
        self.rect(10, y, 190, 14, "DF")
        self.set_xy(14, y + 2)
        self.multi_cell(182, 5, text)
        self.ln(6)


def generate_pdf(output_path):
    pdf = SetupGuide()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)

    # ---- TITLE PAGE ----
    pdf.add_page()
    pdf.ln(40)
    pdf.set_font("Helvetica", "B", 28)
    pdf.set_text_color(26, 26, 46)
    pdf.cell(0, 15, "MeshPropagation", align="C")
    pdf.ln(12)
    pdf.set_font("Helvetica", "", 16)
    pdf.set_text_color(78, 205, 196)
    pdf.cell(0, 10, "RF Propagation & Shadow Mapping for NEPAMesh", align="C")
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, "Complete Setup & Deployment Guide", align="C")
    pdf.ln(30)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 6, "Passively monitors Meshtastic MQTT data to build RF propagation maps.", align="C")
    pdf.ln(6)
    pdf.cell(0, 6, "Maps dead zones, analyzes terrain shadows, and suggests optimal node placements.", align="C")
    pdf.ln(6)
    pdf.cell(0, 6, "Served via Discord bot and live web dashboard. Zero additional mesh network overhead.", align="C")

    # ---- TABLE OF CONTENTS ----
    pdf.add_page()
    pdf.chapter_title("Table of Contents")
    toc = [
        ("1. Prerequisites", 3),
        ("2. Creating a Discord Application & Bot", 3),
        ("3. Configuring Bot Permissions & Intents", 5),
        ("4. Inviting the Bot to Your Server", 6),
        ("5. Configuration (.env File)", 7),
        ("6. Building & Running with Docker", 9),
        ("7. Accessing the Web Dashboard", 10),
        ("8. Discord Slash Commands", 11),
        ("9. RF Shadow Mapper & Coverage Analysis", 13),
        ("10. Architecture Overview", 15),
        ("11. Troubleshooting", 16),
    ]
    for title, page in toc:
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(160, 8, title)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 8, str(page), align="R")
        pdf.ln(8)

    # ---- 1. PREREQUISITES ----
    pdf.add_page()
    pdf.chapter_title("1. Prerequisites")

    pdf.section_title("Required Software")
    pdf.bullet("Docker (version 20.10 or later)")
    pdf.bullet("Docker Compose (version 2.0 or later, included with Docker Desktop)")
    pdf.bullet("A web browser for accessing the dashboard")
    pdf.bullet("A Discord account")
    pdf.ln(4)

    pdf.section_title("Required Access")
    pdf.bullet("Access to a Meshtastic MQTT broker (community or self-hosted)")
    pdf.bullet("A Discord server where you have 'Manage Server' permission")
    pdf.bullet("Network access from the Docker host to the MQTT broker (port 1883)")
    pdf.bullet("A port open on the Docker host for the web dashboard (default: 5000)")
    pdf.ln(4)

    pdf.section_title("Install Docker")
    pdf.body_text("If Docker is not already installed, follow the official guide for your OS:")
    pdf.ln(2)
    pdf.body_text("Linux (Debian/Ubuntu):")
    pdf.code_block(
        "curl -fsSL https://get.docker.com -o get-docker.sh\n"
        "sudo sh get-docker.sh\n"
        "sudo usermod -aG docker $USER\n"
        "# Log out and back in for group changes to take effect"
    )
    pdf.body_text("Then verify:")
    pdf.code_block("docker --version\ndocker compose version")

    # ---- 2. CREATING DISCORD APP ----
    pdf.add_page()
    pdf.chapter_title("2. Creating a Discord Application & Bot")

    pdf.body_text(
        "You need to create a Discord Application to get a bot token. "
        "This token allows MeshPropagation to connect to Discord and respond to slash commands."
    )
    pdf.ln(3)

    pdf.numbered_step(1, "Open the Discord Developer Portal in your browser:")
    pdf.code_block("https://discord.com/developers/applications")
    pdf.ln(2)

    pdf.numbered_step(2,
        'Click the "New Application" button in the top right corner.'
    )
    pdf.ln(2)

    pdf.numbered_step(3,
        'Enter a name for your application (e.g., "MeshPropagation" or "NEPAMesh RF Monitor"). '
        'Accept the Terms of Service and click "Create".'
    )
    pdf.ln(2)

    pdf.numbered_step(4,
        'You will be taken to the application settings page. Note the "Application ID" - '
        "you may need this later."
    )
    pdf.ln(2)

    pdf.numbered_step(5,
        'Click "Bot" in the left sidebar navigation.'
    )
    pdf.ln(2)

    pdf.numbered_step(6,
        'Under the "Token" section, click "Reset Token". You may be asked to confirm '
        "and enter your 2FA code."
    )
    pdf.ln(2)

    pdf.numbered_step(7,
        "Copy the token immediately and save it somewhere secure. "
        "You will NOT be able to see this token again."
    )
    pdf.ln(2)

    pdf.warning_box(
        "Never share your bot token or commit it to version control. "
        "Anyone with your token can control your bot."
    )

    # ---- 3. BOT PERMISSIONS ----
    pdf.add_page()
    pdf.chapter_title("3. Configuring Bot Permissions & Intents")

    pdf.section_title("Bot Permissions")
    pdf.body_text(
        "Still on the Bot settings page in the Developer Portal, configure these settings:"
    )
    pdf.ln(2)

    pdf.body_text('Under "Privileged Gateway Intents":')
    pdf.bullet("Message Content Intent: ENABLED (toggle ON)")
    pdf.ln(4)

    pdf.body_text(
        "The bot needs these permissions in your Discord server "
        "(configured when generating the invite link):"
    )
    pdf.bullet("Send Messages - Post propagation maps and alerts")
    pdf.bullet("Embed Links - Rich embeds with maps and charts")
    pdf.bullet("Attach Files - PNG charts and map images")
    pdf.bullet("Use Slash Commands - Register and respond to /propagation, /mesh, etc.")
    pdf.ln(4)

    pdf.section_title("OAuth2 Scopes")
    pdf.body_text(
        'Navigate to "OAuth2" in the left sidebar. Under "OAuth2 URL Generator", '
        "select these scopes:"
    )
    pdf.bullet("bot")
    pdf.bullet("applications.commands")

    # ---- 4. INVITING THE BOT ----
    pdf.add_page()
    pdf.chapter_title("4. Inviting the Bot to Your Server")

    pdf.numbered_step(1,
        'In the Developer Portal, go to "OAuth2" > "URL Generator".'
    )
    pdf.ln(2)

    pdf.numbered_step(2,
        'Under "Scopes", check: bot, applications.commands'
    )
    pdf.ln(2)

    pdf.numbered_step(3,
        'Under "Bot Permissions", check: Send Messages, Embed Links, Attach Files, '
        "Use Slash Commands."
    )
    pdf.ln(2)

    pdf.numbered_step(4,
        'Copy the generated URL at the bottom of the page and paste it into your browser.'
    )
    pdf.ln(2)

    pdf.numbered_step(5,
        "Select the Discord server you want to add the bot to from the dropdown. "
        'You must have "Manage Server" permission on that server.'
    )
    pdf.ln(2)

    pdf.numbered_step(6,
        'Click "Authorize" and complete any CAPTCHA if prompted.'
    )
    pdf.ln(2)

    pdf.body_text(
        "The bot should now appear in your server's member list (it will show as offline "
        "until you start the MeshPropagation container)."
    )
    pdf.ln(4)

    pdf.section_title("Getting Channel & Server IDs")
    pdf.body_text(
        "You'll need the channel ID for anomaly alerts and optionally the server (guild) ID:"
    )
    pdf.ln(2)
    pdf.numbered_step(1,
        "In Discord, go to User Settings > Advanced > Enable Developer Mode."
    )
    pdf.numbered_step(2,
        "Right-click the channel for alerts > Copy Channel ID. Save this for the .env file."
    )
    pdf.numbered_step(3,
        "Right-click the server name > Copy Server ID (optional, for faster command sync)."
    )

    # ---- 5. CONFIGURATION ----
    pdf.add_page()
    pdf.chapter_title("5. Configuration (.env File)")

    pdf.body_text(
        "MeshPropagation uses a .env file for all configuration. "
        "Copy the example file and edit it with your values:"
    )
    pdf.code_block("cp .env.example .env\nnano .env   # or your preferred editor")
    pdf.ln(2)

    pdf.section_title("Required Settings")
    pdf.code_block(
        "# Your Discord bot token (from Step 2)\n"
        "DISCORD_TOKEN=your_actual_token_here\n"
        "\n"
        "# Channel ID for propagation alerts\n"
        "DISCORD_ALERT_CHANNEL_ID=123456789012345678"
    )
    pdf.ln(2)

    pdf.section_title("MQTT Settings")
    pdf.code_block(
        "MQTT_HOST=mqtt.example.com\n"
        "MQTT_PORT=1883\n"
        "MQTT_USER=your_mqtt_user\n"
        "MQTT_PASS=your_mqtt_password\n"
        "MQTT_TOPICS=msh/US/2/e/#"
    )
    pdf.ln(2)

    pdf.section_title("Web Dashboard")
    pdf.code_block(
        "WEB_PORT=5000\n"
        "# Set to your server's public URL for Discord embed links\n"
        "WEB_BASE_URL=http://your-server-ip:5000"
    )
    pdf.ln(2)

    pdf.section_title("Shadow Analysis Settings")
    pdf.code_block(
        "# Grid cell size in meters (default 100 = 100x100m cells)\n"
        "GRID_CELL_SIZE_M=100\n"
        "# Padding around node positions in km\n"
        "GRID_PADDING_KM=2.0\n"
        "# Maximum assumed node range in km\n"
        "MAX_NODE_RANGE_KM=15.0\n"
        "# How often to recalculate coverage (seconds)\n"
        "COVERAGE_RECALC_SEC=600\n"
        "# Shadow score threshold for dead zone detection\n"
        "SHADOW_THRESHOLD=0.6\n"
        "# Minimum cells to form a dead zone\n"
        "MIN_DEAD_ZONE_CELLS=5"
    )
    pdf.ln(2)

    pdf.section_title("Elevation API")
    pdf.code_block(
        "# Open-Topo-Data endpoint (free, no API key needed)\n"
        "ELEVATION_API_URL=https://api.opentopodata.org/v1/srtm90m\n"
        "ELEVATION_BATCH_SIZE=100\n"
        "ELEVATION_RATE_LIMIT_SEC=1.0"
    )
    pdf.ln(2)

    pdf.section_title("Optional: Guild ID for Development")
    pdf.body_text(
        "Setting DISCORD_GUILD_ID makes slash commands sync instantly to that server "
        "(instead of waiting up to 1 hour for global sync). Recommended during setup."
    )
    pdf.code_block("DISCORD_GUILD_ID=123456789012345678")

    # ---- 6. DOCKER DEPLOYMENT ----
    pdf.add_page()
    pdf.chapter_title("6. Building & Running with Docker")

    pdf.section_title("Quick Start")
    pdf.code_block(
        "# Clone or copy the meshpropagation directory to your server\n"
        "cd meshpropagation\n"
        "\n"
        "# Copy and edit the environment file\n"
        "cp .env.example .env\n"
        "nano .env\n"
        "\n"
        "# Build and start the container\n"
        "docker compose up -d --build\n"
        "\n"
        "# Check logs to verify startup\n"
        "docker compose logs -f"
    )
    pdf.ln(4)

    pdf.section_title("What You Should See in Logs")
    pdf.code_block(
        "meshpropagation  | 2026-04-08 12:00:00 [INFO] meshpropagation: Database ready\n"
        "meshpropagation  | 2026-04-08 12:00:00 [INFO] meshpropagation: MQTT subscriber started\n"
        "meshpropagation  | 2026-04-08 12:00:00 [INFO] mqtt.subscriber: MQTT connected with rc=Success\n"
        "meshpropagation  | 2026-04-08 12:00:00 [INFO] weather.fetcher: Weather fetched: 15.2C...\n"
        "meshpropagation  | 2026-04-08 12:00:00 [INFO] meshpropagation: Starting Discord bot...\n"
        "meshpropagation  | 2026-04-08 12:00:01 [INFO] discord_bot.bot: Discord bot logged in as..."
    )
    pdf.ln(4)

    pdf.section_title("Managing the Container")
    pdf.code_block(
        "# Stop the container\n"
        "docker compose down\n"
        "\n"
        "# Restart after config change\n"
        "docker compose down && docker compose up -d --build\n"
        "\n"
        "# View live logs\n"
        "docker compose logs -f\n"
        "\n"
        "# Check container status\n"
        "docker compose ps"
    )
    pdf.ln(4)

    pdf.info_box(
        "Data is persisted in a Docker volume (meshprop-data). "
        "Your database survives container rebuilds."
    )

    # ---- 7. WEB DASHBOARD ----
    pdf.add_page()
    pdf.chapter_title("7. Accessing the Web Dashboard")

    pdf.body_text("Once the container is running, open your browser to:")
    pdf.code_block("http://your-server-ip:5000")
    pdf.ln(4)

    pdf.section_title("Available Pages")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Dashboard  /")
    pdf.ln(6)
    pdf.body_text(
        "Overview of the mesh network: active node count, link observations, "
        "weather conditions, node table, and activity charts. Stats auto-refresh every 30 seconds."
    )

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Live Map  /map")
    pdf.ln(6)
    pdf.body_text(
        "Interactive Folium map showing all nodes as colored markers and RF links as "
        "colored lines. Green = good SNR, red = poor. Click nodes/links for details. "
        "Supports OpenStreetMap and OpenTopoMap layers. Auto-refreshes every 60 seconds."
    )

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Node Detail  /node/<node_id>")
    pdf.ln(6)
    pdf.body_text(
        "Detailed view of a single node: identity, hardware, battery, position, "
        "active links with SNR stats, and battery history chart."
    )

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Shadow Map  /shadows")
    pdf.ln(6)
    pdf.body_text(
        "Interactive heatmap showing RF shadow coverage. Green = covered, red = shadow. "
        "Dead zone outlines, node markers, and placement suggestions overlay the map."
    )

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Shadow Dashboard  /shadows/dashboard")
    pdf.ln(6)
    pdf.body_text(
        "Coverage stats grid (coverage %, shadow area, dead zone count), coverage breakdown "
        "and timeline charts, dead zone table, and top placement suggestions."
    )

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Placement Suggestions  /suggestions")
    pdf.ln(6)
    pdf.body_text(
        "Ranked table of optimal node placement locations with elevation, shadow reduction, "
        "and reasoning. Includes an interactive evaluate form to test arbitrary coordinates."
    )
    pdf.ln(2)

    pdf.section_title("API Endpoints")
    pdf.body_text("JSON data is available for integration with other tools:")
    pdf.code_block(
        "GET /api/summary             - Mesh network summary stats\n"
        "GET /api/nodes               - All known nodes\n"
        "GET /api/links?hours=24      - Link observations (aggregated)\n"
        "GET /api/chart/activity      - Network activity chart (PNG)\n"
        "GET /api/chart/weather       - Weather correlation chart (PNG)\n"
        "GET /api/shadow/summary      - Coverage stats (%, areas, dead zones)\n"
        "GET /api/shadow/deadzones    - Active dead zone list\n"
        "GET /api/shadow/suggestions  - Placement suggestions\n"
        "GET /api/shadow/snapshots    - Coverage timeline data\n"
        "GET /api/shadow/evaluate?lat=&lon= - Evaluate a placement"
    )

    # ---- 8. DISCORD COMMANDS ----
    pdf.add_page()
    pdf.chapter_title("8. Discord Slash Commands")

    pdf.body_text(
        "After the bot starts and syncs commands, these slash commands are available "
        "in your Discord server:"
    )
    pdf.ln(4)

    commands = [
        ("/propagation", "Shows the current RF propagation map as a PNG image with network stats. "
         "Includes a link to the interactive web map."),
        ("/history [hours]", "Shows network activity over time. Default is 24 hours. "
         "Displays a bar chart of link observations per hour."),
        ("/weather [days]", "Shows weather vs propagation correlation scatter plots. "
         "Displays Pearson correlation coefficients for temperature, humidity, and pressure vs SNR. "
         "Also shows current weather conditions."),
        ("/node <name>", "Shows detailed information for a specific node. "
         "Accepts short name, long name, or node ID (e.g., /node Leo or /node !abcd1234). "
         "Includes battery history chart."),
        ("/mesh", "Shows a complete mesh network overview: node counts, top links by "
         "observation count, weather conditions, and a network map."),
        ("/shadows", "Shows the RF shadow map overview with coverage percentage, grade, "
         "shadow area, dead zone count, and active dead zone list."),
        ("/coverage", "Shows coverage percentage with letter grade (A-F), area breakdown "
         "(total, covered, shadow in mi2), and a coverage breakdown pie chart."),
        ("/suggest", "Shows top 5 optimal node placement suggestions ranked by shadow "
         "reduction. Includes elevation (ft) and expected improvement percentage."),
        ("/deadzone <name>", "Shows details for a specific dead zone: area, cell count, "
         "cause (terrain/distance/mixed), shadow scores, and center coordinates."),
        ("/coverage-history [days]", "Shows coverage evolution over time with a timeline chart. "
         "Reports whether coverage improved, declined, or held stable."),
        ("/evaluate <lat> <lon>", "Evaluates a proposed node placement location. Reports "
         "expected shadow reduction and rates the location from excellent to no improvement."),
    ]

    for cmd, desc in commands:
        pdf.set_font("Courier", "B", 11)
        pdf.set_text_color(78, 205, 196)
        pdf.cell(0, 7, cmd)
        pdf.ln(7)
        pdf.body_text(desc)
        pdf.ln(2)

    pdf.section_title("Anomaly Alerts")
    pdf.body_text(
        "The bot automatically posts alerts to the configured alert channel when it detects "
        "unusual propagation events:"
    )
    pdf.bullet("Signal BOOST (ducting) - SNR significantly above historical average")
    pdf.bullet("Signal FADE - SNR significantly below historical average")
    pdf.bullet("New Link - New long-range link (>10km) detected for the first time")
    pdf.bullet("Link Lost - Known active link has gone silent while both nodes are still active")
    pdf.ln(2)
    pdf.body_text(
        "Shadow alerts are also posted automatically:"
    )
    pdf.bullet("New Dead Zone - A new contiguous shadow region has appeared")
    pdf.bullet("Dead Zone Eliminated - A previously active dead zone is no longer detected")
    pdf.bullet("Coverage Drop - Overall coverage has declined by more than 5%")

    # ---- 9. RF SHADOW MAPPER ----
    pdf.add_page()
    pdf.chapter_title("9. RF Shadow Mapper & Coverage Analysis")

    pdf.body_text(
        "The RF Shadow Mapper identifies where mesh RF signals DON'T reach. It builds a "
        "grid-based coverage model, detects dead zones using connected component analysis, "
        "checks terrain line-of-sight via elevation data, and suggests optimal node placements."
    )
    pdf.ln(4)

    pdf.section_title("How Coverage is Calculated")
    pdf.body_text(
        "The analysis area is defined by a bounding box around all node positions plus padding "
        "(default 2 mi). This area is divided into grid cells (default ~330 x 330 ft). For each "
        "cell, a coverage score is calculated based on the reliability and distance of reachable "
        "nodes. A shadow score is the inverse, weighted by observation confidence to prevent "
        "labeling unmonitored areas as dead zones."
    )
    pdf.ln(2)

    pdf.section_title("Dead Zone Detection")
    pdf.body_text(
        "Cells with shadow scores above the threshold (default 0.6) are flagged as shadow cells. "
        "Connected components of shadow cells are grouped into dead zones using scipy. Zones "
        "smaller than the minimum size (default 5 cells) are filtered out. Each zone is "
        "auto-named by cardinal direction and distance from the mesh center."
    )
    pdf.ln(2)

    pdf.section_title("Terrain Analysis")
    pdf.body_text(
        "Elevation data is fetched from the Open-Topo-Data API (SRTM 90m resolution, free, "
        "no API key required). The system progressively caches elevation data for grid cells. "
        "Line-of-sight checks sample elevation along the path between a node and a cell, "
        "comparing against a straight line with Fresnel zone margin. Dead zones are classified "
        "as terrain-caused, distance-caused, mixed, or unknown."
    )
    pdf.ln(2)

    pdf.section_title("Node Placement Optimization")
    pdf.body_text(
        "A greedy algorithm evaluates candidate positions within shadow cells. Each candidate "
        "is scored by how many other shadow cells it would cover, weighted by elevation "
        "(hilltops preferred). Nearby candidates are clustered (>1640 ft apart). The top 5 "
        "suggestions are saved with reasoning text and expected shadow reduction."
    )
    pdf.ln(2)

    pdf.section_title("Coverage Timeline")
    pdf.body_text(
        "Hourly snapshots record coverage percentage, areas, active node count, and dead zone "
        "count. This enables tracking coverage evolution over days and weeks via the "
        "/coverage-history command and web dashboard charts."
    )
    pdf.ln(2)

    pdf.info_box(
        "The coverage pipeline runs automatically every 10 minutes. Elevation data fetches "
        "progressively and may take ~40 minutes for a full grid on first run."
    )

    # ---- 10. ARCHITECTURE ----
    pdf.add_page()
    pdf.chapter_title("10. Architecture Overview")

    pdf.body_text(
        "MeshPropagation runs as a single Docker container with multiple internal threads:"
    )
    pdf.ln(4)

    components = [
        ("MQTT Subscriber", "Connects to the Meshtastic MQTT broker and decodes protobuf packets "
         "(Position, NodeInfo, Telemetry, NeighborInfo). Writes all data to SQLite. "
         "Runs in its own thread via paho-mqtt loop_start()."),
        ("Weather Fetcher", "Polls the Open-Meteo API every 15 minutes for current weather "
         "conditions at the mesh network center. Associates weather data with link observations "
         "for correlation analysis. Daemon thread."),
        ("Anomaly Detector", "Checks every 5 minutes for unusual propagation events by comparing "
         "recent SNR measurements against historical baselines. Writes events to the database "
         "for the alert dispatcher to pick up. Daemon thread."),
        ("Coverage Pipeline", "Recalculates the coverage grid every 10 minutes: builds grid, "
         "computes coverage/shadow scores, detects dead zones via connected components, and "
         "runs the placement optimization algorithm. Daemon thread."),
        ("Elevation Fetcher", "Progressively fetches SRTM elevation data from Open-Topo-Data "
         "for grid cells. Rate-limited to 1 request/sec, 100 points/batch. Caches results "
         "in SQLite (terrain doesn't change). Daemon thread."),
        ("Snapshot Taker", "Takes hourly coverage snapshots for timeline tracking. Records "
         "coverage %, areas, active node count, and dead zone count. Daemon thread."),
        ("Flask Web Server", "Serves the web dashboard, interactive maps, shadow analysis pages, "
         "and API endpoints. Generates Folium maps and Matplotlib charts on-demand. Daemon thread."),
        ("Discord Bot", "Runs discord.py's async event loop on the main thread. Handles slash "
         "commands, dispatches anomaly alerts and shadow alerts. Uses asyncio.to_thread() for "
         "chart rendering to avoid blocking."),
    ]

    for name, desc in components:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, name)
        pdf.ln(7)
        pdf.body_text(desc)

    pdf.ln(4)
    pdf.section_title("Data Flow")
    pdf.code_block(
        "Meshtastic Nodes --> MQTT Broker --> MQTT Subscriber --> SQLite DB\n"
        "                                                            |\n"
        "                    +----------+----------+-----------+-----+------+\n"
        "                    |          |          |           |            |\n"
        "               Flask Web  Discord Bot  Anomaly    Coverage    Elevation\n"
        "               Dashboard  Slash Cmds  Detector    Pipeline    Fetcher\n"
        "                    |          |         |            |            |\n"
        "              Folium Maps  Charts    Alerts   Shadows/Zones  Open-Topo\n"
        "              Shadow Maps              |     Placements       Data API\n"
        "                                  Shadow Alerts"
    )

    # ---- 11. TROUBLESHOOTING ----
    pdf.add_page()
    pdf.chapter_title("11. Troubleshooting")

    problems = [
        ("Bot is online but slash commands don't appear",
         "Slash commands can take up to 1 hour to sync globally. Set DISCORD_GUILD_ID in .env "
         "for instant sync to your server. Restart the container after changing this."),
        ("MQTT connection fails",
         "Check that your Docker host can reach the MQTT_HOST on the configured port. "
         "Verify MQTT_USER and MQTT_PASS in .env. Check logs for the specific error code."),
        ("No data appearing on the map",
         "The map requires nodes to report both Position AND NeighborInfo packets. Position data "
         "is frequent, but NeighborInfo may take 30+ minutes to appear. Check the dashboard "
         "node table - if nodes appear but the map is empty, wait for more data."),
        ("Weather correlation charts show 'No data'",
         "Correlation requires link observations that have associated weather data. This takes "
         "time to accumulate - the weather fetcher runs every 15 minutes and link observations "
         "must arrive while weather data exists. Allow at least 24 hours of operation."),
        ("Container exits immediately",
         "Check logs with 'docker compose logs'. Common causes: invalid DISCORD_TOKEN, "
         "missing .env file, or port conflict on WEB_PORT."),
        ("Web dashboard loads but shows no nodes",
         "MQTT is likely connected but no packets have arrived yet. Check MQTT_TOPICS in .env "
         "matches your mesh network. The default topics (msh/US/2/e/# and msh/nepa/2/e/#) "
         "are correct for NEPAMesh."),
        ("'Permission denied' errors in Docker",
         "Ensure the data volume has correct permissions. Try: docker compose down -v "
         "(warning: deletes data) then docker compose up -d --build. Or fix permissions on "
         "the volume mount point."),
        ("Charts look wrong or have missing data",
         "Matplotlib renders are generated on-demand. If data is sparse, charts may look empty. "
         "The system needs time to accumulate data - most visualizations improve significantly "
         "after 24-48 hours of continuous operation."),
        ("Shadow map shows no data",
         "The coverage pipeline runs every 10 minutes. On first start, it needs at least 2 nodes "
         "with GPS positions before it can build a grid. Check that nodes are reporting positions "
         "on the dashboard."),
        ("Elevation data not loading",
         "The Open-Topo-Data API is rate-limited to 1 request per second with 100 points per "
         "request. A full grid may take ~40 minutes. Check logs for 'Elevation fetcher error'. "
         "The API may be temporarily unavailable - the fetcher will retry automatically."),
        ("Dead zones not being detected",
         "Dead zone detection requires enough shadow cells to form connected components above "
         "the minimum size (default 5 cells). If your mesh has good coverage, there may genuinely "
         "be no dead zones. Try lowering SHADOW_THRESHOLD in .env (default 0.6)."),
    ]

    for problem, solution in problems:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(204, 0, 0)
        pdf.multi_cell(0, 5.5, "Problem: " + problem)
        pdf.ln(2)
        pdf.body_text("Solution: " + solution)
        pdf.ln(3)

    pdf.ln(6)
    pdf.section_title("Viewing Logs")
    pdf.code_block(
        "# All logs\n"
        "docker compose logs -f\n"
        "\n"
        "# Filter for MQTT activity\n"
        "docker compose logs -f | grep mqtt\n"
        "\n"
        "# Filter for errors only\n"
        "docker compose logs -f | grep ERROR\n"
        "\n"
        "# Enable debug logging (in .env)\n"
        "LOG_LEVEL=DEBUG"
    )

    # ---- OUTPUT ----
    pdf.output(output_path)
    print(f"PDF generated: {output_path}")


if __name__ == "__main__":
    output = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MeshPropagation_Setup_Guide.pdf")
    generate_pdf(output)
