import discord
from typing import Union


def can_disconnect(member: Union[discord.Member, discord.User]) -> bool:
    """Check if member can disconnect others from voice."""
    if isinstance(member, discord.User):
        return False
    return (
        member.guild_permissions.move_members or
        member.guild_permissions.administrator
    )
