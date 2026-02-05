import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

from bot.db import Database
from bot.utils.checks import can_disconnect
from bot.utils.embed import make as embed


def parse_duration(duration_str: str) -> Optional[int]:
    """Parse duration string to seconds (e.g., '5m', '1h', '30s', '90')."""
    duration_str = duration_str.strip().lower()
    
    # Pure number = seconds
    if duration_str.isdigit():
        return int(duration_str)
    
    # Match patterns like 5m, 1h, 30s
    match = re.match(r"^(\d+)([smh])$", duration_str)
    if not match:
        return None
    
    value, unit = int(match.group(1)), match.group(2)
    
    multipliers = {"s": 1, "m": 60, "h": 3600}
    return value * multipliers[unit]


class Timer(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tasks: dict[str, asyncio.Task] = {}  # timer_id -> task
        self._ready = False
    
    @commands.Cog.listener()
    async def on_ready(self):
        """Restore active timers on bot start."""
        if self._ready:
            return  # Avoid duplicate restoration on reconnect
        self._ready = True
        
        timers = await Database.get_all_active_timers()
        
        for timer in timers:
            timer_id = str(timer["_id"])
            remaining = (timer["expires_at"] - datetime.now(timezone.utc)).total_seconds()
            
            if remaining > 0:
                task = asyncio.create_task(self._run_timer(timer_id, remaining))
                self.tasks[timer_id] = task
            else:
                # Timer expired while bot was offline
                await self._execute_disconnect(timer)
        
        if timers:
            print(f"Restored {len(timers)} active timers")
    
    def cog_unload(self):
        """Cancel all tasks on unload."""
        for task in self.tasks.values():
            task.cancel()
    
    @commands.hybrid_command(name="dc", description="Set a disconnect timer")
    @app_commands.describe(
        member="User to disconnect (leave empty for yourself)",
        duration="Duration (e.g., 5m, 1h, 30s, or seconds)"
    )
    @commands.guild_only()
    async def dc(
        self,
        ctx: commands.Context,
        duration: str,
        member: Optional[discord.Member] = None
    ):
        """Set a disconnect timer for a user in voice chat."""
        await ctx.defer()
        
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            return
        
        # Default to self
        target = member or ctx.author
        
        # Check permissions
        if target != ctx.author:
            if not can_disconnect(ctx.author):
                await ctx.send(embed=embed("You need `Move Members` permission to set timers for others."))
                return
        
        # Check if target is in voice
        if not target.voice or not target.voice.channel:
            await ctx.send(embed=embed(f"{target.mention} is not in a voice channel."))
            return
        
        # Parse duration
        seconds = parse_duration(duration)
        if not seconds or seconds < 10:
            await ctx.send(embed=embed("Invalid duration. Use formats like `30s`, `5m`, `1h` (min 10s)."))
            return
        
        if seconds > 86400:  # 24 hours max
            await ctx.send(embed=embed("Maximum duration is 24 hours."))
            return
        
        # Check for existing timer
        existing = await Database.get_user_timer(ctx.guild.id, target.id)
        if existing:
            await ctx.send(embed=embed(f"{target.mention} already has an active timer. Use `cancel` first."))
            return
        
        # Create timer
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        timer_id = await Database.add_timer(
            guild_id=ctx.guild.id,
            channel_id=target.voice.channel.id,
            user_id=target.id,
            set_by=ctx.author.id,
            expires_at=expires_at,
            duration=seconds
        )
        
        # Start timer task
        task = asyncio.create_task(self._run_timer(timer_id, seconds))
        self.tasks[timer_id] = task
        log.info(f"Timer {timer_id} started for {target} ({seconds}s)")

        # Format duration for display
        mins, secs = divmod(seconds, 60)
        hours, mins = divmod(mins, 60)
        if hours:
            time_str = f"{hours}h {mins}m"
        elif mins:
            time_str = f"{mins}m {secs}s" if secs else f"{mins}m"
        else:
            time_str = f"{secs}s"
        
        await ctx.send(embed=embed(f"{target.mention} will be disconnected in **{time_str}**."))
    
    @commands.hybrid_command(name="cancel", description="Cancel a disconnect timer")
    @app_commands.describe(member="User whose timer to cancel (leave empty for yourself)")
    @commands.guild_only()
    async def cancel(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Cancel an active disconnect timer."""
        await ctx.defer()
        
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            return
        
        target = member or ctx.author
        
        # Check permissions for cancelling others' timers
        if target != ctx.author:
            if not can_disconnect(ctx.author):
                await ctx.send(embed=embed("You need `Move Members` permission to cancel others' timers."))
                return
        
        # Find timer
        timer = await Database.get_user_timer(ctx.guild.id, target.id)
        if not timer:
            await ctx.send(embed=embed(f"No active timer for {target.mention}."))
            return
        
        # Check if author set the timer (can cancel own timers)
        timer_id = str(timer["_id"])
        if timer["set_by"] != ctx.author.id and target != ctx.author:
            if not can_disconnect(ctx.author):
                await ctx.send(embed=embed("You can only cancel timers you set."))
                return
        
        # Cancel task
        if timer_id in self.tasks:
            self.tasks[timer_id].cancel()
            del self.tasks[timer_id]
        
        # Update database
        await Database.cancel_timer(timer_id)
        await ctx.send(embed=embed(f"Timer cancelled for {target.mention}."))
    
    @commands.hybrid_command(name="timers", description="List active timers")
    @commands.guild_only()
    async def timers(self, ctx: commands.Context):
        """List all active disconnect timers in this server."""
        await ctx.defer()
        
        if not ctx.guild:
            return
        
        timers = await Database.get_guild_timers(ctx.guild.id)
        
        if not timers:
            await ctx.send(embed=embed("No active timers."))
            return
        
        timers_embed = discord.Embed(
            title="Active Timers",
            color=0x202225
        )
        
        now = datetime.now(timezone.utc)
        for timer in timers:
            user = ctx.guild.get_member(timer["user_id"])
            remaining = (timer["expires_at"] - now).total_seconds()
            
            if remaining > 0:
                mins, secs = divmod(int(remaining), 60)
                hours, mins = divmod(mins, 60)
                
                if hours:
                    time_str = f"{hours}h {mins}m"
                elif mins:
                    time_str = f"{mins}m {secs}s"
                else:
                    time_str = f"{secs}s"
                
                name = user.display_name if user else f"User {timer['user_id']}"
                timers_embed.add_field(
                    name=name,
                    value=f"{time_str} remaining",
                    inline=True
                )
        
        await ctx.send(embed=timers_embed)
    
    async def _run_timer(self, timer_id: str, seconds: float):
        """Run a timer and disconnect user when it expires."""
        try:
            await asyncio.sleep(seconds)
            
            timer = await Database.get_timer(timer_id)
            if timer and timer["status"] == "active":
                await self._execute_disconnect(timer)
        except asyncio.CancelledError:
            pass
        finally:
            self.tasks.pop(timer_id, None)
    
    async def _execute_disconnect(self, timer: dict):
        """Disconnect the user from voice."""
        timer_id = str(timer["_id"])
        log.info(f"Executing disconnect for timer {timer_id}")
        guild = self.bot.get_guild(timer["guild_id"])
        
        if not guild:
            log.warning(f"Timer {timer_id}: Guild not found")
            await Database.complete_timer(timer_id, "guild_not_found")
            return
        
        member = guild.get_member(timer["user_id"])
        
        if not member:
            log.warning(f"Timer {timer_id}: Member not found")
            await Database.complete_timer(timer_id, "member_not_found")
            return
        
        if not member.voice:
            log.warning(f"Timer {timer_id}: Member not in voice")
            await Database.complete_timer(timer_id, "not_in_voice")
            return
        
        try:
            # End voice session with bot_timer type
            await Database.end_session(guild.id, member.id, "bot_timer")
            
            # Disconnect user
            await member.move_to(None, reason="LastCall: Timer expired")
            log.info(f"Timer {timer_id}: Disconnected {member}")
            await Database.complete_timer(timer_id, "disconnected")
        except discord.Forbidden:
            log.error(f"Timer {timer_id}: No permission to disconnect")
            await Database.complete_timer(timer_id, "no_permission")
        except Exception as e:
            log.error(f"Timer {timer_id}: Error - {e}")
            await Database.complete_timer(timer_id, f"error: {e}")
    
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ):
        """Cancel timer if user leaves voice manually."""
        # User left voice channel
        if before.channel and not after.channel:
            timer = await Database.get_user_timer(member.guild.id, member.id)
            if timer:
                timer_id = str(timer["_id"])
                if timer_id in self.tasks:
                    self.tasks[timer_id].cancel()
                    del self.tasks[timer_id]
                await Database.complete_timer(timer_id, "user_left")


async def setup(bot: commands.Bot):
    await bot.add_cog(Timer(bot))
