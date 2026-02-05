import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

# Link URLs from environment
INVITE_URL = os.getenv("BOT_INVITE_URL", "")
SUPPORT_URL = os.getenv("SUPPORT_SERVER_URL", "")


def _get_link_buttons() -> list[discord.ui.Button]:
    """Get link buttons for invite and support."""
    buttons = []
    if INVITE_URL:
        buttons.append(discord.ui.Button(
            label="Invite Bot",
            style=discord.ButtonStyle.link,
            url=INVITE_URL
        ))
    if SUPPORT_URL:
        buttons.append(discord.ui.Button(
            label="Support Server",
            style=discord.ButtonStyle.link,
            url=SUPPORT_URL
        ))
    return buttons


class LinkOnlyView(discord.ui.View):
    """View with only link buttons (no timeout needed)."""
    
    def __init__(self):
        super().__init__(timeout=None)
        for btn in _get_link_buttons():
            self.add_item(btn)


class HelpView(discord.ui.View):
    """View with buttons for help destination."""
    
    def __init__(self, author: discord.Member, embed: discord.Embed):
        super().__init__(timeout=60)
        self.author = author
        self.embed = embed
        self.message: Optional[discord.Message] = None
        
        # Add link buttons if URLs are configured
        for btn in _get_link_buttons():
            self.add_item(btn)
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only allow the command author to use buttons."""
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "This menu is not for you.",
                ephemeral=True
            )
            return False
        return True
    
    async def on_timeout(self):
        """Disable buttons when view times out."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass
    
    @discord.ui.button(label="Send to DM", style=discord.ButtonStyle.primary)
    async def send_dm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Send help to user's DMs."""
        await interaction.response.defer()
        try:
            await interaction.user.send(embed=self.embed, view=LinkOnlyView())
            await interaction.edit_original_response(
                content="Check your DMs!",
                embed=None,
                view=LinkOnlyView()
            )
        except discord.Forbidden:
            await interaction.edit_original_response(
                content="Couldn't send DM. Here's the help menu:",
                embed=self.embed,
                view=LinkOnlyView()
            )
        self.stop()
    
    @discord.ui.button(label="Send Here", style=discord.ButtonStyle.secondary)
    async def send_here(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Send help in current channel."""
        await interaction.response.defer()
        await interaction.edit_original_response(
            content=None,
            embed=self.embed,
            view=LinkOnlyView()
        )
        self.stop()


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    def _build_help_embed(self, prefix: str) -> discord.Embed:
        """Build the help embed."""
        help_embed = discord.Embed(
            title="LastCall Bot",
            description="Voice channel disconnect timer and activity tracker.",
            color=0x202225
        )
        
        commands_text = (
            f"`{prefix}dc <time> [@user]` - Set disconnect timer\n"
            f"`{prefix}cancel [@user]` - Cancel a timer\n"
            f"`{prefix}timers` - List active timers\n"
            f"`{prefix}stats [@user]` - View voice channel stats\n"
            f"`{prefix}top` - Server VC leaderboard\n"
            f"`{prefix}prefix <new>` - Change bot prefix\n"
            f"`{prefix}help` - Show this menu"
        )
        help_embed.add_field(name="Commands", value=commands_text, inline=False)
        
        examples_text = (
            f"`{prefix}dc 30m` - Disconnect yourself in 30 minutes\n"
            f"`{prefix}dc 1h @user` - Disconnect user in 1 hour\n"
            f"`{prefix}dc 90` - Disconnect yourself in 90 seconds\n"
            f"`{prefix}cancel` - Cancel your own timer"
        )
        help_embed.add_field(name="Examples", value=examples_text, inline=False)
        
        time_text = "`30s` (seconds) | `5m` (minutes) | `1h` (hours) | `90` (seconds)"
        help_embed.add_field(name="Time Formats", value=time_text, inline=False)
        
        perms_text = (
            "Anyone can set timers on themselves\n"
            "`Move Members` required to timer others\n"
            "`Manage Server` required to change prefix or view others' stats"
        )
        help_embed.add_field(name="Permissions", value=perms_text, inline=False)
        
        help_embed.set_footer(text="LastCall v1.0.0")
        
        return help_embed
    
    @commands.hybrid_command(name="help", description="Show bot help menu")
    async def help(self, ctx: commands.Context):
        """Show the help menu with destination options."""
        await ctx.defer()
        
        if ctx.guild:
            from bot.db import Database
            prefix = await Database.get_prefix(ctx.guild.id)
        else:
            prefix = "!"
        
        help_embed = self._build_help_embed(prefix)
        
        if not ctx.guild:
            await ctx.send(embed=help_embed)
            return
        
        if not isinstance(ctx.author, discord.Member):
            await ctx.send(embed=help_embed)
            return
        
        view = HelpView(ctx.author, help_embed)
        prompt_embed = discord.Embed(
            description="Where would you like to see the help menu?",
            color=0x202225
        )
        view.message = await ctx.send(embed=prompt_embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
