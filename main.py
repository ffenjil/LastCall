import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("lastcall")

import discord
from discord.ext import commands

from bot.db import Database


class LastCall(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.guilds = True
        intents.members = True
        
        super().__init__(
            command_prefix=self._get_prefix,
            intents=intents,
            help_command=None
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
            "bot.cogs.help"
        ]
        
        for cog in cogs:
            try:
                await self.load_extension(cog)
                print(f"Loaded: {cog}")
            except Exception as e:
                print(f"Failed to load {cog}: {e}")
        
        # Sync slash commands globally
        synced = await self.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    
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
        
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("This command can only be used in a server.")
            return
        
        # Log unexpected errors
        print(f"Error: {error}")
        await ctx.send("Something went wrong.")


async def main():
    # Get token
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN not found in .env")
        sys.exit(1)
    
    # Connect to MongoDB
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    mongo_db = os.getenv("MONGO_DB", "lastcall")
    
    try:
        await Database.connect(mongo_uri, mongo_db)
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
        sys.exit(1)
    
    # Start bot
    bot = LastCall()
    
    try:
        print("Starting bot...")
        await bot.start(token)
    except discord.LoginFailure:
        print("Error: Invalid Discord token")
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"Error: {e}")
    finally:
        print("Shutting down...")
        await Database.close()
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
