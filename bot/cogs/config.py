from discord import app_commands
from discord.ext import commands

from bot.db import Database
from bot.utils.embed import error as embed_error
from bot.utils.embed import success as embed_success

MAX_PREFIX_LENGTH = 5


class Config(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="prefix", description="Set the bot prefix for this server")
    @app_commands.describe(new_prefix="New prefix (1-5 characters)")
    @commands.has_permissions(manage_guild=True)
    @commands.cooldown(3, 30, commands.BucketType.guild)
    @commands.guild_only()
    async def prefix(self, ctx: commands.Context, new_prefix: str):
        """Set a custom command prefix for this server."""
        await ctx.defer()

        if not ctx.guild:
            return

        # Validate prefix. Whitespace is stripped by the parser anyway, so a
        # blank prefix would make every message a command attempt.
        new_prefix = new_prefix.strip()

        if not new_prefix:
            await ctx.send(embed=embed_error("Prefix cannot be empty."))
            return

        if len(new_prefix) > MAX_PREFIX_LENGTH:
            await ctx.send(embed=embed_error(
                f"Prefix must be {MAX_PREFIX_LENGTH} characters or less."
            ))
            return

        if new_prefix.startswith("<@"):
            await ctx.send(embed=embed_error(
                "Prefix cannot be a mention. You can always mention me instead."
            ))
            return

        # Save to database
        await Database.set_prefix(ctx.guild.id, new_prefix, ctx.author.id)

        await ctx.send(embed=embed_success(f"Prefix set to `{new_prefix}`"))


async def setup(bot: commands.Bot):
    await bot.add_cog(Config(bot))
