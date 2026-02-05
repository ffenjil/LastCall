import discord
from discord import app_commands
from discord.ext import commands

from bot.db import Database
from bot.utils.embed import make as embed


class Config(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @commands.hybrid_command(name="prefix", description="Set the bot prefix for this server")
    @app_commands.describe(new_prefix="New prefix (1-5 characters)")
    @commands.has_permissions(manage_guild=True)
    async def prefix(self, ctx: commands.Context, new_prefix: str):
        """Set a custom command prefix for this server."""
        # Validate prefix
        if len(new_prefix) > 5:
            await ctx.send(embed=embed("Prefix must be 5 characters or less."))
            return
        
        if len(new_prefix) < 1:
            await ctx.send(embed=embed("Prefix cannot be empty."))
            return
        
        # Save to database
        await Database.set_prefix(ctx.guild.id, new_prefix, ctx.author.id)
        
        await ctx.send(embed=embed(f"Prefix set to `{new_prefix}`"))
    
    @prefix.error
    async def prefix_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=embed("You need `Manage Server` permission to change the prefix."))


async def setup(bot: commands.Bot):
    await bot.add_cog(Config(bot))
