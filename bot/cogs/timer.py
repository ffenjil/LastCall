import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.db import Database
from bot.utils.checks import can_target
from bot.utils.embed import make as embed
from bot.utils.embed import error as embed_error
from bot.utils.embed import info as embed_info
from bot.utils.embed import success as embed_success
from bot.utils.time import aware, format_duration, parse_duration

if TYPE_CHECKING:
    from bot.core import LastCall

log = logging.getLogger(__name__)

MIN_DURATION = 10
MAX_DURATION = 86400  # 24 hours
EXTEND_SECONDS = 600  # what the warning button grants
MAX_TIMER_FIELDS = 25  # Discord embed field / readable line cap

# Every message this cog sends is a nudge, never something worth a notification.
# silent sets Discord's SUPPRESS_NOTIFICATIONS flag; allowed_mentions keeps the
# mention rendering as a pill without pinging the person behind it.
QUIET = {
    "silent": True,
    "allowed_mentions": discord.AllowedMentions.none(),
}


def warn_lead(seconds: int) -> int:
    """How long before expiry to warn, for a timer of `seconds`.

    Capped at a third of the timer so short timers still get a usable warning
    instead of one that would fire before the timer even starts.
    """
    lead = min(int(os.getenv("WARN_SECONDS", "60")), seconds // 3)
    return lead if lead >= 5 else 0


class ExtendView(discord.ui.View):
    """Attached to the warning message: buys the target more time."""

    def __init__(self, cog: "Timer", timer_id: str, user_id: int, timeout: float):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.timer_id = timer_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This timer isn't yours.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Extend 10m", style=discord.ButtonStyle.primary)
    async def extend(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, message = await self.cog.apply_extension(self.timer_id, EXTEND_SECONDS)
        if ok:
            # Edit the warning back into a plain confirmation instead of posting
            # anything new. The next warning reuses this same message.
            await interaction.response.edit_message(
                content=None, embed=embed_success(message), view=None
            )
        else:
            button.disabled = True
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(message, ephemeral=True)
        self.stop()


class Timer(commands.Cog):
    def __init__(self, bot: "LastCall"):
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

        # _run_timer reads expiry from the database itself, so timers that
        # expired while the bot was down need no special handling here.
        for timer in timers:
            timer_id = str(timer["_id"])
            self.tasks[timer_id] = asyncio.create_task(self._run_timer(timer_id))

        if timers:
            log.info(f"Restored {len(timers)} active timers")

    def cog_unload(self):
        """Cancel all tasks on unload."""
        for task in self.tasks.values():
            task.cancel()

    @commands.hybrid_command(name="dc", description="Set a disconnect timer")
    @app_commands.describe(
        duration="Duration (e.g., 5m, 1h, 30s)",
        member="User to disconnect (leave empty for yourself)"
    )
    @commands.cooldown(3, 30, commands.BucketType.user)
    @commands.guild_only()
    async def dc(
        self,
        ctx: commands.Context,
        duration: str,
        member: Optional[discord.Member] = None
    ):
        """Set a disconnect timer for a user in voice chat.

        Usage: #dc <duration> [@user] OR #dc @user <duration>
        """
        await ctx.defer()

        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            return

        target = member or ctx.author

        # Check if duration looks like a mention (user put @user first)
        if duration.startswith("<@") and member is None:
            # User did "#dc @user 30s" but we parsed @user as duration
            await ctx.send(embed=embed_error(
                "Usage: `#dc <duration> [@user]`\nExample: `#dc 30s @user`"
            ))
            return

        # Check if target is in voice
        if not target.voice or not target.voice.channel:
            await ctx.send(embed=embed_error(f"{target.mention} is not in a voice channel."), **QUIET)
            return

        # Permission, hierarchy, and bot-capability checks all happen up front so
        # a timer that could never fire is refused now rather than in an hour.
        if ctx.guild.me is None:
            return
        allowed, reason = can_target(ctx.author, target, ctx.guild.me)
        if not allowed:
            await ctx.send(embed=embed_error(reason))
            return

        # Parse duration
        seconds = parse_duration(duration)
        if not seconds or seconds < MIN_DURATION:
            await ctx.send(embed=embed_error(
                f"Invalid duration. Use formats like `30s`, `5m`, `1h` "
                f"(min {MIN_DURATION}s)."
            ))
            return

        if seconds > MAX_DURATION:
            await ctx.send(embed=embed_error(
                f"Maximum duration is {format_duration(MAX_DURATION)}."
            ))
            return

        # Check for existing timer
        existing = await Database.get_user_timer(ctx.guild.id, target.id)
        if existing:
            await ctx.send(embed=embed_error(
                f"{target.mention} already has an active timer. Use `cancel` first."
            ))
            return

        # Create timer
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        timer_id = await Database.add_timer(
            guild_id=ctx.guild.id,
            channel_id=target.voice.channel.id,
            user_id=target.id,
            set_by=ctx.author.id,
            expires_at=expires_at,
            duration=seconds,
            text_channel_id=ctx.channel.id,
            warn_seconds=warn_lead(seconds)
        )

        # Post the confirmation and record it before the task starts, so the
        # warning always has a message to edit rather than racing to find one.
        confirmation = await ctx.send(
            embed=embed_success(
                f"{target.mention} will be disconnected in **{format_duration(seconds)}**."
            ),
            **QUIET
        )
        if confirmation:
            await Database.set_timer_message(timer_id, confirmation.id)

        self.tasks[timer_id] = asyncio.create_task(self._run_timer(timer_id))
        log.info(f"Timer {timer_id} started for {target} ({seconds}s)")

    @commands.hybrid_command(name="cancel", description="Cancel a disconnect timer")
    @app_commands.describe(member="User whose timer to cancel (leave empty for yourself)")
    @commands.cooldown(5, 30, commands.BucketType.user)
    @commands.guild_only()
    async def cancel(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Cancel an active disconnect timer."""
        await ctx.defer()

        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            return

        target = member or ctx.author

        # Check permissions for cancelling others' timers
        if target != ctx.author and ctx.guild.me is not None:
            allowed, reason = can_target(ctx.author, target, ctx.guild.me)
            if not allowed:
                await ctx.send(embed=embed_error(reason))
                return

        # Find timer
        timer = await Database.get_user_timer(ctx.guild.id, target.id)
        if not timer:
            await ctx.send(embed=embed_error(f"No active timer for {target.mention}."), **QUIET)
            return

        timer_id = str(timer["_id"])

        # Cancel task
        task = self.tasks.pop(timer_id, None)
        if task:
            task.cancel()

        # Update database
        await Database.cancel_timer(timer_id)

        # Settle the timer's own message so it does not sit there showing a
        # countdown and a live Extend button for a timer that is gone.
        await self._edit_timer_message(
            timer,
            content=None,
            embed=embed(f"Timer cancelled for {target.mention}."),
            view=None
        )

        await ctx.send(embed=embed_success(f"Timer cancelled for {target.mention}."), **QUIET)

    @commands.hybrid_command(name="extend", description="Add time to a disconnect timer")
    @app_commands.describe(
        duration="How much time to add (e.g., 10m, 1h)",
        member="User whose timer to extend (leave empty for yourself)"
    )
    @commands.cooldown(5, 30, commands.BucketType.user)
    @commands.guild_only()
    async def extend(
        self,
        ctx: commands.Context,
        duration: str,
        member: Optional[discord.Member] = None
    ):
        """Push an active disconnect timer further out."""
        await ctx.defer()

        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            return

        target = member or ctx.author

        if target != ctx.author and ctx.guild.me is not None:
            allowed, reason = can_target(ctx.author, target, ctx.guild.me)
            if not allowed:
                await ctx.send(embed=embed_error(reason))
                return

        seconds = parse_duration(duration)
        if not seconds:
            await ctx.send(embed=embed_error(
                "Invalid duration. Use formats like `30s`, `5m`, `1h`."
            ))
            return

        timer = await Database.get_user_timer(ctx.guild.id, target.id)
        if not timer:
            await ctx.send(embed=embed_error(f"No active timer for {target.mention}."), **QUIET)
            return

        timer_id = str(timer["_id"])
        ok, message = await self.apply_extension(timer_id, seconds)

        if ok:
            # Keep the timer's own message showing the new expiry.
            refreshed = await Database.get_timer(timer_id)
            if refreshed:
                await self._edit_timer_message(
                    refreshed,
                    content=None,
                    embed=embed_success(message),
                    view=None
                )

        await ctx.send(
            embed=embed_success(message) if ok else embed_error(message),
            **QUIET
        )

    @commands.hybrid_command(name="timers", description="List active timers")
    @commands.cooldown(2, 20, commands.BucketType.guild)
    @commands.guild_only()
    async def timers(self, ctx: commands.Context):
        """List all active disconnect timers in this server."""
        await ctx.defer()

        if not ctx.guild:
            return

        timers = await Database.get_guild_timers(ctx.guild.id)

        now = datetime.now(timezone.utc)
        pending = sorted(
            (t for t in timers if aware(t["expires_at"]) > now),
            key=lambda t: aware(t["expires_at"])
        )

        if not pending:
            await ctx.send(embed=embed("No active timers."))
            return

        # Rendered as description lines rather than embed fields: Discord caps
        # embeds at 25 fields and this query returns up to 100 timers.
        lines = []
        for timer in pending[:MAX_TIMER_FIELDS]:
            user = ctx.guild.get_member(timer["user_id"])
            name = user.display_name if user else f"User {timer['user_id']}"
            remaining = int((aware(timer["expires_at"]) - now).total_seconds())
            lines.append(f"**{name}** - {format_duration(remaining)} remaining")

        if len(pending) > MAX_TIMER_FIELDS:
            lines.append(f"*+{len(pending) - MAX_TIMER_FIELDS} more*")

        timers_embed = discord.Embed(
            title="Active Timers",
            description="\n".join(lines),
            color=0x202225
        )

        await ctx.send(embed=timers_embed)

    async def apply_extension(self, timer_id: str, seconds: int) -> tuple[bool, str]:
        """Push a timer back and reschedule its task.

        Shared by the `extend` command and the warning message button so both
        routes behave identically.
        """
        timer = await Database.get_timer(timer_id)
        if not timer or timer["status"] != "active":
            return False, "That timer is no longer active."

        new_expires_at = aware(timer["expires_at"]) + timedelta(seconds=seconds)
        remaining = (new_expires_at - datetime.now(timezone.utc)).total_seconds()
        if remaining > MAX_DURATION:
            return False, (
                f"That would leave more than {format_duration(MAX_DURATION)} "
                f"on the timer."
            )

        if not await Database.extend_timer(timer_id, new_expires_at):
            return False, "That timer is no longer active."

        task = self.tasks.pop(timer_id, None)
        if task:
            task.cancel()
        self.tasks[timer_id] = asyncio.create_task(self._run_timer(timer_id))

        log.info(f"Timer {timer_id} extended by {seconds}s")
        return True, (
            f"Timer extended by {format_duration(seconds)} - "
            f"{format_duration(int(max(0, remaining)))} remaining."
        )

    async def _run_timer(self, timer_id: str):
        """Warn, then disconnect, re-reading the timer before each stage.

        Expiry always comes from the database rather than being passed in, so an
        extension landing mid-sleep is honoured and restart recovery needs no
        special case for timers that expired while the bot was offline.
        """
        try:
            while True:
                timer = await Database.get_timer(timer_id)
                if not timer or timer["status"] != "active":
                    return

                remaining = (
                    aware(timer["expires_at"]) - datetime.now(timezone.utc)
                ).total_seconds()
                warn_seconds = timer.get("warn_seconds") or 0

                # Warning stage, only while there is runway left for it.
                if not timer.get("warned") and 0 < warn_seconds < remaining:
                    await asyncio.sleep(remaining - warn_seconds)
                    await self._warn(timer_id)
                    continue

                if remaining > 0:
                    await asyncio.sleep(remaining)

                timer = await Database.get_timer(timer_id)
                if not timer or timer["status"] != "active":
                    return
                # Extended while we slept: re-plan instead of firing early.
                if aware(timer["expires_at"]) > datetime.now(timezone.utc):
                    continue

                await self._execute_disconnect(timer)
                return
        except asyncio.CancelledError:
            pass
        finally:
            # Only clear our own entry - an extension may already have installed
            # a replacement task under this id.
            if self.tasks.get(timer_id) is asyncio.current_task():
                self.tasks.pop(timer_id, None)

    async def _warn(self, timer_id: str):
        """Send the pre-disconnect warning, unless the timer moved underneath us."""
        timer = await Database.get_timer(timer_id)
        if not timer or timer["status"] != "active" or timer.get("warned"):
            return

        warn_seconds = timer.get("warn_seconds") or 0
        remaining = (aware(timer["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
        if remaining > warn_seconds + 5:
            return  # Extended during the sleep; the caller re-plans.

        # Marked before sending so a delivery failure cannot cause a warn loop.
        await Database.mark_warned(timer_id)
        await self._send_warning(timer)

    async def _edit_timer_message(self, timer: dict, **kwargs) -> bool:
        """Update the message this timer already owns. True if it worked.

        Editing is what keeps a timer to a single message and, unlike a new
        message or a mention, never notifies anyone.
        """
        message_id = timer.get("message_id")
        channel_id = timer.get("text_channel_id")
        if not message_id or not channel_id:
            return False

        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return False

        try:
            message = await channel.fetch_message(message_id)
            await message.edit(**kwargs)
            return True
        except discord.HTTPException as e:
            log.warning(f"Timer {timer['_id']}: could not edit timer message - {e}")
            return False

    async def _send_warning(self, timer: dict):
        """Give the user a heads-up by editing the timer message in place.

        A warning that cannot be delivered must never cancel the disconnect, so
        every failure here is logged and swallowed.
        """
        guild = self.bot.get_guild(timer["guild_id"])
        if not guild:
            return
        member = guild.get_member(timer["user_id"])
        if not member:
            return

        remaining = max(0, int(
            (aware(timer["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
        ))
        warning = embed_info(
            f"{member.mention} will be disconnected in "
            f"**{format_duration(remaining)}**.",
            title="Last Call"
        )
        view = ExtendView(self, str(timer["_id"]), member.id, timeout=max(remaining, 1))

        if await self._edit_timer_message(timer, content=None, embed=warning, view=view):
            return

        # Only if the original message is gone. Still silent, still no ping.
        channel = self.bot.get_channel(timer.get("text_channel_id") or 0)
        if isinstance(channel, discord.abc.Messageable):
            try:
                await channel.send(embed=warning, view=view, **QUIET)
            except discord.HTTPException as e:
                log.warning(f"Timer {timer['_id']}: warning failed - {e}")

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

        user_id = member.id
        self.bot.disconnecting_users.add(user_id)

        try:
            # Disconnect user first
            await member.move_to(None, reason="LastCall: Timer expired")
            log.info(f"Timer {timer_id}: Disconnected {member}")

            # End voice session with bot_timer type
            await Database.end_session(guild.id, member.id, "bot_timer")

            await Database.complete_timer(timer_id, "disconnected")
            await self._edit_timer_message(
                timer,
                content=None,
                embed=embed(f"{member.mention} was disconnected."),
                view=None
            )
        except discord.Forbidden:
            log.error(f"Timer {timer_id}: No permission to disconnect")
            await Database.complete_timer(timer_id, "no_permission")
        except Exception as e:
            log.error(f"Timer {timer_id}: Error - {e}")
            await Database.complete_timer(timer_id, f"error: {e}")
        finally:
            # The gateway event this guard suppresses arrives after the HTTP call
            # returns, so the guard has to outlive this block.
            asyncio.get_running_loop().call_later(
                5, self.bot.disconnecting_users.discard, user_id
            )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ):
        """Cancel timer if user leaves voice manually."""
        # Ignore if the user is being disconnected by the bot
        if member.id in self.bot.disconnecting_users:
            return

        # User left voice channel
        if before.channel and not after.channel:
            timer = await Database.get_user_timer(member.guild.id, member.id)
            if timer:
                timer_id = str(timer["_id"])
                task = self.tasks.pop(timer_id, None)
                if task:
                    task.cancel()
                await Database.complete_timer(timer_id, "user_left")


async def setup(bot: commands.Bot):
    await bot.add_cog(Timer(bot))  # type: ignore[arg-type]
