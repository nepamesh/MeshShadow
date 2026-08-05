"""Discord bot factory.

Wires slash-command registration (`commands.py`) and the alert dispatchers
(`alerts.py`) onto a single `discord.Client`. If `guild_id` is set, slash
commands are copied to that guild for instant sync (recommended during
development); otherwise they sync globally and may take up to an hour to
appear. If `alert_channel_id` is 0, the channel-based dispatchers don't
start — the claimed-node DM dispatcher starts regardless, since it targets
users directly rather than the alert channel.
"""

import asyncio
import logging

import discord
from discord import app_commands

import config
from database.store import DataStore
from .commands import setup_commands
from .alerts import (
    AnomalyAlertDispatcher, ShadowAlertDispatcher, BlackHoleAlertDispatcher,
    RouterOfflineDispatcher, ClaimedNodeOfflineDispatcher, DailyDigestDispatcher,
)

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

            if config.DISCORD_BLACKHOLE_ALERTS:
                blackhole_dispatcher = BlackHoleAlertDispatcher(bot, store, alert_channel_id)
                asyncio.create_task(blackhole_dispatcher.start())
                log.info("Black hole alerts enabled for channel %d", alert_channel_id)
            else:
                log.info("Black hole alerts disabled (DISCORD_BLACKHOLE_ALERTS=false)")

            if config.DISCORD_ROUTER_OFFLINE_ALERTS:
                router_offline_dispatcher = RouterOfflineDispatcher(bot, store, alert_channel_id)
                asyncio.create_task(router_offline_dispatcher.start())
                log.info("Router offline alerts enabled for channel %d", alert_channel_id)
            else:
                log.info("Router offline alerts disabled (DISCORD_ROUTER_OFFLINE_ALERTS=false)")

            digest_dispatcher = DailyDigestDispatcher(bot, store, alert_channel_id)
            asyncio.create_task(digest_dispatcher.start())
            log.info("Daily digest enabled for channel %d (fires at %02d:00)", alert_channel_id, config.DISCORD_DIGEST_HOUR)

        # Claimed-node DMs don't depend on the alert channel — they go
        # straight to the claiming user, so start regardless of alert_channel_id.
        claimed_dispatcher = ClaimedNodeOfflineDispatcher(bot, store)
        asyncio.create_task(claimed_dispatcher.start())
        log.info("Claimed-node offline DMs enabled")

    return bot
