from typing import Union

import discord


def can_disconnect(member: Union[discord.Member, discord.User]) -> bool:
    """Check if member can disconnect others from voice."""
    if isinstance(member, discord.User):
        return False
    return (
        member.guild_permissions.move_members or
        member.guild_permissions.administrator
    )


def can_target(
    author: discord.Member,
    target: discord.Member,
    me: discord.Member
) -> tuple[bool, str]:
    """Check whether `author` may set a disconnect timer on `target`.

    Returns (allowed, reason). The reason is empty when allowed, otherwise it is
    a user-facing explanation.

    Validating the bot's own permissions here means a doomed timer is refused at
    command time rather than silently failing with Forbidden when it expires.
    """
    # Self-targeting skips the moderation checks, but still needs the bot to be
    # able to perform the move.
    if author.id != target.id:
        if not can_disconnect(author):
            return False, "You need `Move Members` permission to set timers for others."

        if target.bot:
            return False, "You can't set a timer on a bot."

        if target.id == target.guild.owner_id:
            return False, "You can't set a timer on the server owner."

        # The guild owner outranks everyone, so skip the role comparison for them.
        if author.id != author.guild.owner_id and author.top_role <= target.top_role:
            return False, "You can't set a timer on someone with an equal or higher role."

    if not me.guild_permissions.move_members:
        return False, "I need `Move Members` permission to disconnect people."

    channel = target.voice.channel if target.voice else None
    if channel is not None and not channel.permissions_for(me).move_members:
        return False, f"I can't disconnect people from {channel.mention}."

    return True, ""
