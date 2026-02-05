import discord
from discord.ext import commands
import os
from typing import Union

from bot.db import Database


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
    
    async def _get_prefix(self, bot: commands.Bot, message: discord.Message) -> list[str]:
        """Get guild prefix or default."""
        if not message.guild:
            return commands.when_mentioned_or(os.getenv("DEFAULT_PREFIX", "!"))(bot, message)
        
        prefix = await Database.get_prefix(message.guild.id)
        return commands.when_mentioned_or(prefix)(bot, message)
    
    async def setup_hook(self):
        """Load cogs and sync commands."""
        cogs = [
            "bot.cogs.timer",
            "bot.cogs.tracker",
            "bot.cogs.config",
            "bot.cogs.help",
            "bot.cogs.owner"
        ]
        
        for cog in cogs:
            await self.load_extension(cog)
            print(f"Loaded: {cog}")
        
        # Sync slash commands globally
        await self.tree.sync()
        print("Synced slash commands")
    
    async def on_ready(self):
        if not self.user:
            return
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Connected to {len(self.guilds)} guilds")
        
        # Rich presence status
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.guilds)} servers | @help"
            )
        )
    
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Global error handler."""
        if isinstance(error, commands.CommandNotFound):
            return
        
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You don't have permission to do that.")
            return
        
        if isinstance(error, commands.BotMissingPermissions):
            await ctx.send("I don't have permission to do that.")
            return
        
        if isinstance(error, commands.MemberNotFound):
            await ctx.send("Member not found.")
            return
        
        if isinstance(error, commands.BadArgument):
            await ctx.send(f"Invalid argument: {error}")
            return
        
        # Log unexpected errors
        print(f"Error: {error}")
        await ctx.send("Something went wrong.")
