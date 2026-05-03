"""Discord bot factory.

Wires slash-command registration (`commands.py`) and the three alert
dispatchers (`alerts.py`) onto a single `discord.Client`. If `guild_id` is
set, slash commands are copied to that guild for instant sync (recommended
during development); otherwise they sync globally and may take up to an hour
to appear. If `alert_channel_id` is 0, the dispatchers don't start.
"""

import asyncio
import logging

import discord
from discord import app_commands

import config
from database.store import DataStore
from .commands import setup_commands
from .alerts import AnomalyAlertDispatcher, ShadowAlertDispatcher, BlackHoleAlertDispatcher, DailyDigestDispatcher

log = logging.getLogger(__name__)


def create_bot(store: DataStore, alert_channel_id: int = 0, guild_id: str = "",
               web_base_url: str = "http://localhost:5000"):
    """Build (but do not start) a configured `discord.Client`.

    The caller is responsible for `bot.run(token)`. Returns the client; alert
    dispatchers are scheduled inside `on_ready` so they can use a connected
    bot instance.
    """
    intents = discord.Intents.default()
    intents.message_content = True

    bot = discord.Client(intents=intents)
    tree = app_commands.CommandTree(bot)

    setup_commands(tree, store, web_base_url)

    alert_dispatcher = None

    @bot.event
    async def on_ready():
        nonlocal alert_dispatcher
        log.info("Discord bot logged in as %s", bot.user)

        # Sync commands
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
            log.info("Synced commands to guild %s", guild_id)
        else:
            await tree.sync()
            log.info("Synced global commands")

        # Start anomaly alerts
        if alert_channel_id:
            alert_dispatcher = AnomalyAlertDispatcher(bot, store, alert_channel_id)
            asyncio.create_task(alert_dispatcher.start())
            log.info("Anomaly alerts enabled for channel %d", alert_channel_id)

            shadow_dispatcher = ShadowAlertDispatcher(bot, store, alert_channel_id)
            asyncio.create_task(shadow_dispatcher.start())
            log.info("Shadow alerts enabled for channel %d", alert_channel_id)

            blackhole_dispatcher = BlackHoleAlertDispatcher(bot, store, alert_channel_id)
            asyncio.create_task(blackhole_dispatcher.start())
            log.info("Black hole alerts enabled for channel %d", alert_channel_id)

            digest_dispatcher = DailyDigestDispatcher(bot, store, alert_channel_id)
            asyncio.create_task(digest_dispatcher.start())
            log.info("Daily digest enabled for channel %d (fires at %02d:00)", alert_channel_id, config.DISCORD_DIGEST_HOUR)

    return bot
