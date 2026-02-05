import discord

# Discord dark mode embed color
EMBED_COLOR = 0x2b2d31


def success(description: str, title: str = None) -> discord.Embed:
    """Success embed."""
    return discord.Embed(
        title=title,
        description=description,
        color=EMBED_COLOR
    )


def error(description: str, title: str = None) -> discord.Embed:
    """Error embed."""
    return discord.Embed(
        title=title,
        description=description,
        color=EMBED_COLOR
    )


def info(description: str, title: str = None) -> discord.Embed:
    """Info embed."""
    return discord.Embed(
        title=title,
        description=description,
        color=EMBED_COLOR
    )
