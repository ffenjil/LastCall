from discord.ext import commands


class Owner(commands.Cog):
    """Owner-only commands for bot management."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    async def cog_check(self, ctx: commands.Context) -> bool:
        """Only allow bot owner to use these commands."""
        return await self.bot.is_owner(ctx.author)
    
    @commands.command(name="push")
    async def push(self, ctx: commands.Context):
        """Hot-reload all cogs without restarting the bot."""
        msg = await ctx.send("Reloading cogs...")
        
        cog_list = [
            "bot.cogs.timer",
            "bot.cogs.tracker",
            "bot.cogs.config",
            "bot.cogs.help",
            "bot.cogs.owner",
        ]
        
        success = []
        failed = []
        
        for cog in cog_list:
            try:
                await self.bot.reload_extension(cog)
                success.append(cog.split(".")[-1])
            except Exception as e:
                failed.append(f"{cog.split('.')[-1]}: {e}")
        
        # Sync slash commands
        try:
            synced = await self.bot.tree.sync()
            sync_msg = f"Synced {len(synced)} commands"
        except Exception as e:
            sync_msg = f"Sync failed: {e}"
        
        result = f"Reloaded: {', '.join(success)}" if success else "No cogs reloaded"
        if failed:
            result += f"\nFailed: {'; '.join(failed)}"
        result += f"\n{sync_msg}"
        
        await msg.edit(content=result)
    
    @commands.command(name="load")
    async def load(self, ctx: commands.Context, cog: str):
        """Load a specific cog."""
        try:
            await self.bot.load_extension(f"bot.cogs.{cog}")
            await ctx.send(f"Loaded: {cog}")
        except Exception as e:
            await ctx.send(f"Failed to load {cog}: {e}")
    
    @commands.command(name="unload")
    async def unload(self, ctx: commands.Context, cog: str):
        """Unload a specific cog."""
        if cog == "owner":
            await ctx.send("Cannot unload owner cog")
            return
        try:
            await self.bot.unload_extension(f"bot.cogs.{cog}")
            await ctx.send(f"Unloaded: {cog}")
        except Exception as e:
            await ctx.send(f"Failed to unload {cog}: {e}")
    
    @commands.command(name="reload")
    async def reload(self, ctx: commands.Context, cog: str):
        """Reload a specific cog."""
        try:
            await self.bot.reload_extension(f"bot.cogs.{cog}")
            await ctx.send(f"Reloaded: {cog}")
        except Exception as e:
            await ctx.send(f"Failed to reload {cog}: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Owner(bot))
