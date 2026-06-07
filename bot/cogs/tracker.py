from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.db import Database
from bot.utils.embed import make as embed


def format_duration(seconds: int) -> str:
    """Format seconds into human readable string."""
    if seconds < 60:
        return f"{seconds}s"
    
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    days, hours = divmod(hours, 24)
    
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins}m")
    if secs and not days:  # Skip seconds if showing days
        parts.append(f"{secs}s")
    
    return " ".join(parts) if parts else "0s"


class Tracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready = False
    
    @commands.Cog.listener()
    async def on_ready(self):
        """Reconcile active sessions on bot startup."""
        if self._ready:
            return
        self._ready = True
        await self.reconcile_sessions()
        
    async def reconcile_sessions(self):
        """Reconcile active sessions in database with actual voice states on startup."""
        print("Reconciling voice sessions...")
        # 1. Get all tracked active sessions from DB
        db_sessions = await Database.get_all_active_sessions()
        db_active_map = {(s["guild_id"], s["user_id"]): s for s in db_sessions}
        
        # 2. Get all actual voice states from discord guilds
        actual_active = set()
        for guild in self.bot.guilds:
            for voice_state in guild.voice_states.values():
                member = guild.get_member(voice_state.user_id)
                if member and not member.bot and voice_state.channel:
                    actual_active.add((guild.id, member.id, voice_state.channel.id, voice_state.channel.name))
        
        # 3. For any session in DB that is NOT actually active, end it
        actual_keys = {(g_id, u_id) for g_id, u_id, _, _ in actual_active}
        ended_count = 0
        for (guild_id, user_id), session in db_active_map.items():
            if (guild_id, user_id) not in actual_keys:
                await Database.end_session(guild_id, user_id, "offline_disconnect")
                ended_count += 1
                
        # 4. For any session that is actually active but NOT in DB, start it
        started_count = 0
        for guild_id, user_id, channel_id, channel_name in actual_active:
            if (guild_id, user_id) not in db_active_map:
                await Database.start_session(guild_id, user_id, channel_id, channel_name)
                started_count += 1
                
        print(f"Reconciliation complete: ended {ended_count} stale sessions, started {started_count} new sessions.")
    
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
                await ctx.send(embed=embed("You need `Manage Server` permission to view others' stats."))
                return
        
        stats = await Database.get_user_stats(ctx.guild.id, target.id)
        
        # Check if currently in voice and dynamically add current session duration
        session = await Database.get_active_session(ctx.guild.id, target.id)
        if session:
            now = datetime.now(timezone.utc)
            joined_at = session["joined_at"]
            if joined_at.tzinfo is None:
                joined_at = joined_at.replace(tzinfo=timezone.utc)
            current_duration = int((now - joined_at).total_seconds())
            
            stats["total_time"] = stats.get("total_time", 0) + current_duration
            stats["session_count"] = stats.get("session_count", 0) + 1
            if "channels" not in stats:
                stats["channels"] = []
            if session["channel_name"] not in stats["channels"]:
                stats["channels"] = list(stats["channels"])
                stats["channels"].append(session["channel_name"])
        
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
        
        # Check if currently in voice
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
            description="All-time voice activity rankings",
            color=0x202225
        )
        
        medals = ["1.", "2.", "3."]
        lines = []
        
        for i, entry in enumerate(leaderboard):
            user = ctx.guild.get_member(entry["_id"])
            name = user.display_name if user else f"User {entry['_id']}"
            time_str = format_duration(entry["total_time"])
            
            medal = medals[i] if i < 3 else f"{i+1}."
            lines.append(f"**{medal}** {name} - {time_str}")
        
        top_embed.description = "\n".join(lines)
        
        await ctx.send(embed=top_embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tracker(bot))
