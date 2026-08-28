import hashlib
import logging
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.db import Database
from bot.utils.embed import error as embed_error

log = logging.getLogger(__name__)

COGS = [
    "bot.cogs.timer",
    "bot.cogs.tracker",
    "bot.cogs.config",
    "bot.cogs.help",
    "bot.cogs.owner",
]


class LastCall(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.guilds = True
        intents.members = True

        # Parse owner IDs from env
        owner_ids_str = os.getenv("OWNER_IDS", "")
        owner_ids = set()
        if owner_ids_str:
            for id_str in owner_ids_str.split(","):
                id_str = id_str.strip()
                if id_str.isdigit():
                    owner_ids.add(int(id_str))

        super().__init__(
            command_prefix=self._get_prefix,
            intents=intents,
            help_command=None,
            owner_ids=owner_ids if owner_ids else None
        )

        # Track user IDs currently being disconnected by the bot to prevent event race conditions
        self.disconnecting_users: set[int] = set()

    async def _get_prefix(self, bot: commands.Bot, message: discord.Message) -> list[str]:
        """Get guild prefix or default."""
        if not message.guild:
            return commands.when_mentioned_or(os.getenv("DEFAULT_PREFIX", "!"))(bot, message)

        prefix = await Database.get_prefix(message.guild.id)
        return commands.when_mentioned_or(prefix)(bot, message)

    async def setup_hook(self):
        """Load cogs and sync commands."""
        for cog in COGS:
            await self.load_extension(cog)
            log.info(f"Loaded: {cog}")

        self.tree.on_error = self._on_app_command_error

        await self._sync_if_changed()

    def _command_fingerprint(self) -> str:
        """Fingerprint of the current slash command surface."""
        parts = []
        for cmd in sorted(self.tree.get_commands(), key=lambda c: c.qualified_name):
            params = ""
            if isinstance(cmd, app_commands.Command):
                params = ",".join(
                    f"{p.name}:{p.type.name}:{p.required}" for p in cmd.parameters
                )
            description = getattr(cmd, "description", "")
            parts.append(f"{cmd.qualified_name}|{description}|{params}")
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()

    async def _sync_if_changed(self):
        """Sync slash commands only when the command set actually changed.

        Global syncs are heavily rate limited and take up to an hour to
        propagate, so syncing on every restart is wasteful. Never syncing means
        new commands would never appear, so compare against what was last synced.
        Set SYNC_ON_START=1 to force one.
        """
        forced = os.getenv("SYNC_ON_START", "").lower() in ("1", "true", "yes")
        current = self._command_fingerprint()

        try:
            stored = await Database.get_state("command_hash")
        except Exception:
            log.exception("Could not read stored command hash, syncing anyway")
            stored = None

        if not forced and stored == current:
            log.info("Slash commands unchanged, skipping sync")
            return

        try:
            synced = await self.tree.sync()
            await Database.set_state("command_hash", current)
            log.info(f"Synced {len(synced)} slash commands")
        except discord.HTTPException:
            log.exception("Slash command sync failed")

    async def on_ready(self):
        if not self.user:
            return
        log.info(f"Logged in as {self.user} (ID: {self.user.id})")
        log.info(f"Connected to {len(self.guilds)} guilds")

        # Rich presence status
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.guilds)} servers | @help"
            )
        )

    def _error_message(
        self,
        error: Exception,
        ctx: Optional[commands.Context] = None
    ) -> Optional[str]:
        """Map an exception to a user facing message.

        Returns None when the error is unexpected, so the caller logs it and
        falls back to a generic reply. Shared by the prefix and slash handlers so
        both explain themselves the same way.
        """
        if isinstance(error, (commands.MissingPermissions, app_commands.MissingPermissions)):
            return "You don't have permission to do that."

        if isinstance(error, (commands.BotMissingPermissions, app_commands.BotMissingPermissions)):
            return "I don't have permission to do that."

        if isinstance(error, commands.MemberNotFound):
            return "Member not found."

        if isinstance(error, commands.MissingRequiredArgument):
            if ctx and ctx.command:
                usage = f"{ctx.clean_prefix}{ctx.command.qualified_name} {ctx.command.signature}"
                return f"Missing `{error.param.name}`.\nUsage: `{usage.strip()}`"
            return f"Missing `{error.param.name}`."

        if isinstance(error, (commands.CommandOnCooldown, app_commands.CommandOnCooldown)):
            return f"Slow down. Try again in {error.retry_after:.0f}s."

        if isinstance(error, commands.NoPrivateMessage):
            return "That command only works in a server."

        if isinstance(error, commands.BadArgument):
            return f"Invalid argument: {error}"

        if isinstance(error, (commands.CheckFailure, app_commands.CheckFailure)):
            return "You can't use that command here."

        return None

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Global error handler for prefix commands."""
        if isinstance(error, commands.CommandNotFound):
            return

        message = self._error_message(error, ctx)
        if message is None:
            log.error(f"Unhandled error in command {ctx.command}", exc_info=error)
            message = "Something went wrong."

        try:
            await ctx.send(embed=embed_error(message))
        except discord.HTTPException:
            log.warning("Could not deliver error message", exc_info=True)

    async def _on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError
    ):
        """Global error handler for slash commands.

        Without this, a failing slash command shows the user nothing but
        "The application did not respond".
        """
        message = self._error_message(error)
        if message is None:
            log.error(
                f"Unhandled error in app command {interaction.command}",
                exc_info=error
            )
            message = "Something went wrong."

        embed = embed_error(message)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            log.warning("Could not deliver app command error message", exc_info=True)
