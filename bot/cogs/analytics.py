"""Event tracking.

Every listener that exists purely to record what happened lives here, so the
feature cogs stay about features and tracking can be disabled by unloading one
extension.

Writes are deliberately fire and forget: a slow or failing analytics write must
never delay a command response or break the thing it is observing.

One thing this deliberately does not record is message content. The bot has the
intent for it, but logging what people say is invasive and would dwarf every
other event. Commands are recorded as "this command ran", never as the text.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.db import Database

if TYPE_CHECKING:
    from bot.core import LastCall

log = logging.getLogger(__name__)

# How often to sample gateway latency and guild counts.
HEALTH_INTERVAL_MINUTES = 15


def _voice_flags(state: discord.VoiceState) -> dict[str, bool]:
    """The parts of a voice state worth diffing between updates."""
    return {
        "self_mute": state.self_mute,
        "self_deaf": state.self_deaf,
        "mute": state.mute,
        "deaf": state.deaf,
        "self_stream": state.self_stream,
        "self_video": state.self_video,
        "suppress": state.suppress,
    }


class Analytics(commands.Cog):
    def __init__(self, bot: "LastCall"):
        self.bot = bot
        # Strong refs so fire and forget tasks are not garbage collected
        # mid-flight, which asyncio otherwise permits.
        self._pending: set[asyncio.Task] = set()

    def cog_unload(self):
        self.health_sample.cancel()
        for task in self._pending:
            task.cancel()

    def record(self, event_type: str, **kwargs: Any):
        """Schedule an event write without making the caller wait for it."""
        task = asyncio.create_task(Database.log_event(event_type, **kwargs))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    # ============ Bot health ============

    @commands.Cog.listener()
    async def on_ready(self):
        self.record(
            "bot.ready",
            guild_count=len(self.bot.guilds),
            member_total=sum(g.member_count or 0 for g in self.bot.guilds),
            latency_ms=round(self.bot.latency * 1000) if self.bot.latency else None,
        )
        if not self.health_sample.is_running():
            self.health_sample.start()

    @commands.Cog.listener()
    async def on_disconnect(self):
        self.record("bot.disconnect")

    @commands.Cog.listener()
    async def on_resumed(self):
        self.record("bot.resumed")

    @tasks.loop(minutes=HEALTH_INTERVAL_MINUTES)
    async def health_sample(self):
        """Periodic snapshot, so uptime and reach can be charted after the fact."""
        latency = self.bot.latency
        self.record(
            "bot.health",
            guild_count=len(self.bot.guilds),
            member_total=sum(g.member_count or 0 for g in self.bot.guilds),
            voice_active=sum(
                len(c.members)
                for g in self.bot.guilds
                for c in [*g.voice_channels, *g.stage_channels]
            ),
            latency_ms=round(latency * 1000) if latency and latency == latency else None,
        )

    @health_sample.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # ============ Guild lifecycle ============

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        log.info(f"Joined guild {guild.name} ({guild.id})")
        self.record(
            "guild.join",
            guild_id=guild.id,
            name=guild.name,
            member_count=guild.member_count,
            owner_id=guild.owner_id,
            guild_total=len(self.bot.guilds),
        )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        log.info(f"Removed from guild {guild.name} ({guild.id})")
        self.record(
            "guild.remove",
            guild_id=guild.id,
            name=guild.name,
            member_count=guild.member_count,
            guild_total=len(self.bot.guilds),
        )

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        if before.name != after.name:
            self.record(
                "guild.rename",
                guild_id=after.id,
                before=before.name,
                after=after.name,
            )

    # ============ Membership ============

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        self.record(
            "member.join",
            guild_id=member.guild.id,
            user_id=member.id,
            bot=member.bot,
            member_count=member.guild.member_count,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        self.record(
            "member.remove",
            guild_id=member.guild.id,
            user_id=member.id,
            bot=member.bot,
            member_count=member.guild.member_count,
        )

    # ============ Voice detail ============

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ):
        """Record voice movement and state changes.

        The tracker cog owns session durations; this records the finer grained
        events it does not keep, such as mute, deafen, streaming and video.
        """
        if member.bot:
            return

        if not before.channel and after.channel:
            action = "voice.join"
        elif before.channel and not after.channel:
            action = "voice.leave"
        elif before.channel and after.channel and before.channel != after.channel:
            action = "voice.move"
        else:
            action = None

        if action:
            self.record(
                action,
                guild_id=member.guild.id,
                user_id=member.id,
                channel_id=after.channel.id if after.channel else None,
                channel_name=after.channel.name if after.channel else None,
                from_channel_id=before.channel.id if before.channel else None,
                from_channel_name=before.channel.name if before.channel else None,
                by_bot=member.id in self.bot.disconnecting_users,
                occupancy=len(after.channel.members) if after.channel else None,
            )
            return

        # Same channel: only worth recording when a flag actually flipped.
        was, now = _voice_flags(before), _voice_flags(after)
        changed = {k: now[k] for k in now if now[k] != was[k]}
        if changed:
            self.record(
                "voice.state",
                guild_id=member.guild.id,
                user_id=member.id,
                channel_id=after.channel.id if after.channel else None,
                channel_name=after.channel.name if after.channel else None,
                changed=changed,
            )

    # ============ Command usage ============

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context):
        # Stashed on the context so completion can measure how long it took.
        # on_command and on_command_completion receive the same Context.
        ctx._lastcall_started = datetime.now(timezone.utc)  # type: ignore[attr-defined]

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        started = getattr(ctx, "_lastcall_started", None)
        latency = (
            round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            if started else None
        )
        self.record(
            "command.ok",
            guild_id=ctx.guild.id if ctx.guild else None,
            user_id=ctx.author.id,
            command=ctx.command.qualified_name if ctx.command else None,
            kind="slash" if ctx.interaction else "prefix",
            channel_id=ctx.channel.id if ctx.channel else None,
            latency_ms=latency,
        )

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CommandNotFound):
            return
        self.record(
            "command.error",
            guild_id=ctx.guild.id if ctx.guild else None,
            user_id=ctx.author.id,
            command=ctx.command.qualified_name if ctx.command else None,
            kind="slash" if ctx.interaction else "prefix",
            error_type=type(error).__name__,
            message=str(error)[:500],
        )

    @commands.Cog.listener()
    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: app_commands.Command
    ):
        # Hybrid commands already report through on_command_completion; this
        # catches anything registered only as a slash command.
        if isinstance(command, app_commands.ContextMenu) or interaction.command is None:
            return
        latency = round(
            (datetime.now(timezone.utc) - interaction.created_at).total_seconds() * 1000
        )
        self.record(
            "command.app_ok",
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            command=command.qualified_name,
            kind="slash",
            channel_id=interaction.channel_id,
            latency_ms=latency,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Analytics(bot))  # type: ignore[arg-type]
