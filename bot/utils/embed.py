import discord
from typing import Optional

# Default dark embed color
EMBED_COLOR = 0x202225
SUCCESS_COLOR = 0x2ECC71
ERROR_COLOR = 0xE74C3C
INFO_COLOR = 0x3498DB


def make(description: str, title: Optional[str] = None) -> discord.Embed:
    """Create a standard embed."""
    return discord.Embed(
        title=title,
        description=description,
        color=EMBED_COLOR
    )


def success(description: str, title: Optional[str] = None) -> discord.Embed:
    """Success embed."""
    return discord.Embed(
        title=title,
        description=description,
        color=SUCCESS_COLOR
    )


def error(description: str, title: Optional[str] = None) -> discord.Embed:
    """Error embed."""
    return discord.Embed(
        title=title,
        description=description,
        color=ERROR_COLOR
    )


def info(description: str, title: Optional[str] = None) -> discord.Embed:
    """Info embed."""
    return discord.Embed(
        title=title,
        description=description,
        color=INFO_COLOR
    )
