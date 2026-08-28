import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.db import Database
from bot.utils.embed import make as embed
from bot.utils.embed import error as embed_error
from bot.utils.time import format_duration, settle_time

if TYPE_CHECKING:
    from bot.core import LastCall

log = logging.getLogger(__name__)

# How long the bot must have been away before a session that survived the gap is
# split, rather than crediting the downtime to the user. Two heartbeat intervals.
OFFLINE_GAP_SECONDS = 120


class Tracker(commands.Cog):
    def __init__(self, bot: "LastCall"):
        self.bot = bot
        self._ready = False

    def cog_unload(self):
        self.heartbeat.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        """Reconcile active sessions on bot startup."""
        if self._ready:
            return
        self._ready = True
        # Reconcile first: it reads the heartbeat left by the previous run, so
        # the new one must not be written until afterwards.
        await self.reconcile_sessions()
        if not self.heartbeat.is_running():
            self.heartbeat.start()

    @tasks.loop(minutes=1)
    async def heartbeat(self):
        """Record liveness so the next startup knows when we went away."""
        try:
            await Database.set_heartbeat()
        except Exception:
            log.exception("Failed to write heartbeat")

    async def reconcile_sessions(self):
        """Reconcile active sessions in database with actual voice states on startup."""
        log.info("Reconciling voice sessions...")

        # Everything that ended while the bot was down is settled against the
        # last heartbeat instead of now, so downtime is never credited as voice
        # time. On a first-ever boot there is no heartbeat and now is all we have.
        cutoff = await Database.get_last_heartbeat()
        gap = (
            (datetime.now(timezone.utc) - cutoff).total_seconds()
            if cutoff else 0
        )
        long_outage = cutoff is not None and gap > OFFLINE_GAP_SECONDS

        # 1. Get all tracked active sessions from DB
        db_sessions = await Database.get_all_active_sessions()
        db_active_map = {(s["guild_id"], s["user_id"]): s for s in db_sessions}

        # 2. Get all actual voice states from discord guilds.
        #    Guild.voice_states is not public API (it is _voice_states), so walk
        #    the voice and stage channels and read their members instead.
        actual_active = set()
        for guild in self.bot.guilds:
            for channel in [*guild.voice_channels, *guild.stage_channels]:
                for member in channel.members:
                    if not member.bot:
                        actual_active.add(
                            (guild.id, member.id, channel.id, channel.name)
                        )

        # 3. For any session in DB that is NOT actually active, end it
        actual_keys = {(g_id, u_id) for g_id, u_id, _, _ in actual_active}
        ended_count = 0
        for (guild_id, user_id), session in db_active_map.items():
            if (guild_id, user_id) not in actual_keys:
                await Database.end_session(
                    guild_id,
                    user_id,
                    "offline_disconnect",
                    end_time=settle_time(session["joined_at"], cutoff)
                )
                ended_count += 1

        # 4. Start sessions we are not tracking, and split ones that spanned a
        #    long outage so the gap itself is not counted.
        started_count = 0
        resumed_count = 0
        for guild_id, user_id, channel_id, channel_name in actual_active:
            if (guild_id, user_id) not in db_active_map:
                await Database.start_session(guild_id, user_id, channel_id, channel_name)
                started_count += 1
            elif long_outage:
                prior = db_active_map[(guild_id, user_id)]
                await Database.end_session(
                    guild_id,
                    user_id,
                    "offline_gap",
                    end_time=settle_time(prior["joined_at"], cutoff)
                )
                await Database.start_session(guild_id, user_id, channel_id, channel_name)
                resumed_count += 1

        log.info(
            f"Reconciliation complete: ended {ended_count} stale, "
            f"started {started_count} new, resumed {resumed_count} across a "
            f"{int(gap)}s gap."
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ):
        """Track voice channel join/leave/move events."""
        # Ignore bots
        if member.bot:
            return

        # Ignore if the user is being disconnected by the bot
        if member.id in self.bot.disconnecting_users:
            return

        # User joined a voice channel
        if not before.channel and after.channel:
            await Database.start_session(
                guild_id=member.guild.id,
                user_id=member.id,
                channel_id=after.channel.id,
                channel_name=after.channel.name
            )

        # User left a voice channel
        elif before.channel and not after.channel:
            await Database.end_session(
                guild_id=member.guild.id,
                user_id=member.id,
                disconnect_type="manual"
            )

        # User moved to a different channel
        elif before.channel and after.channel and before.channel != after.channel:
            # End old session
            await Database.end_session(
                guild_id=member.guild.id,
                user_id=member.id,
                disconnect_type="moved"
            )
            # Start new session
            await Database.start_session(
                guild_id=member.guild.id,
                user_id=member.id,
                channel_id=after.channel.id,
                channel_name=after.channel.name
            )

    @commands.hybrid_command(name="stats", description="View voice channel stats")
    @app_commands.describe(member="User to view stats for (leave empty for yourself)")
    @commands.cooldown(3, 20, commands.BucketType.user)
    @commands.guild_only()
    async def stats(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """View all-time voice channel statistics."""
        await ctx.defer()

        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            return

        target = member or ctx.author

        # Check permissions for viewing others' stats
        if target != ctx.author:
            if not ctx.author.guild_permissions.manage_guild:
                await ctx.send(embed=embed_error(
                    "You need `Manage Server` permission to view others' stats."
                ))
                return

        # Any in-progress session is already folded in by the database layer, so
        # stats and the leaderboard cannot disagree.
        stats = await Database.get_user_stats(ctx.guild.id, target.id)

        stats_embed = discord.Embed(
            title=f"Voice Stats: {target.display_name}",
            color=0x202225
        )

        stats_embed.set_thumbnail(url=target.display_avatar.url)

        stats_embed.add_field(
            name="Total Time",
            value=format_duration(stats["total_time"]),
            inline=True
        )

        stats_embed.add_field(
            name="Sessions",
            value=str(stats["session_count"]),
            inline=True
        )

        if stats["channels"]:
            channels_str = ", ".join(stats["channels"][:5])
            if len(stats["channels"]) > 5:
                channels_str += f" +{len(stats['channels']) - 5} more"
            stats_embed.add_field(
                name="Channels",
                value=channels_str,
                inline=False
            )

        session = await Database.get_active_session(ctx.guild.id, target.id)
        if session:
            stats_embed.add_field(
                name="Currently In",
                value=session["channel_name"],
                inline=True
            )

        await ctx.send(embed=stats_embed)

    @commands.hybrid_command(name="top", description="Voice channel leaderboard")
    @app_commands.describe(limit="Number of users to show (default 10)")
    @commands.cooldown(2, 20, commands.BucketType.guild)
    @commands.guild_only()
    async def top(self, ctx: commands.Context, limit: int = 10):
        """Show the voice channel time leaderboard."""
        await ctx.defer()

        if not ctx.guild:
            return

        limit = min(max(limit, 1), 25)  # Clamp between 1-25

        leaderboard = await Database.get_guild_leaderboard(ctx.guild.id, limit)

        if not leaderboard:
            await ctx.send(embed=embed("No voice activity recorded yet."))
            return

        top_embed = discord.Embed(
            title="Voice Channel Leaderboard",
            color=0x202225
        )

        lines = []
        for i, entry in enumerate(leaderboard):
            user = ctx.guild.get_member(entry["_id"])
            name = user.display_name if user else f"User {entry['_id']}"
            time_str = format_duration(entry["total_time"])
            lines.append(f"**{i + 1}.** {name} - {time_str}")

        top_embed.description = "\n".join(lines)

        await ctx.send(embed=top_embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tracker(bot))  # type: ignore[arg-type]
