"""Tests for can_target using lightweight stubs.

The check only ever touches a handful of attributes, so stubbing them keeps
these tests free of discord.py objects and a running gateway.
"""

import pytest

from bot.utils.checks import can_disconnect, can_target

GUILD_OWNER_ID = 1
ADMIN_ID = 2
MOD_ID = 3
MEMBER_ID = 4
BOT_ID = 5


class Perms:
    def __init__(self, move_members=False, administrator=False):
        self.move_members = move_members
        self.administrator = administrator


class Role:
    def __init__(self, position):
        self.position = position

    def __le__(self, other):
        return self.position <= other.position

    def __gt__(self, other):
        return self.position > other.position


class Channel:
    mention = "#general-voice"

    def __init__(self, bot_can_move=True):
        self.bot_can_move = bot_can_move

    def permissions_for(self, member):
        return Perms(move_members=self.bot_can_move)


class VoiceState:
    def __init__(self, channel):
        self.channel = channel


class Guild:
    def __init__(self, owner_id=GUILD_OWNER_ID):
        self.owner_id = owner_id


class Member:
    def __init__(
        self,
        id,
        perms=None,
        role_position=1,
        bot=False,
        in_voice=True,
        bot_can_move=True,
        guild=None,
    ):
        self.id = id
        self.guild_permissions = perms or Perms()
        self.top_role = Role(role_position)
        self.bot = bot
        self.guild = guild or Guild()
        self.voice = VoiceState(Channel(bot_can_move)) if in_voice else None


@pytest.fixture
def me():
    """The bot, fully able to move people."""
    return Member(99, perms=Perms(move_members=True))


class TestCanDisconnect:
    def test_move_members_allows(self):
        assert can_disconnect(Member(MOD_ID, perms=Perms(move_members=True)))

    def test_administrator_allows(self):
        assert can_disconnect(Member(ADMIN_ID, perms=Perms(administrator=True)))

    def test_plain_member_denied(self):
        assert not can_disconnect(Member(MEMBER_ID))


class TestSelfTargeting:
    def test_plain_member_can_target_self(self, me):
        member = Member(MEMBER_ID)
        allowed, reason = can_target(member, member, me)
        assert allowed, reason

    def test_refused_when_bot_lacks_guild_permission(self):
        member = Member(MEMBER_ID)
        powerless = Member(99, perms=Perms(move_members=False))
        allowed, reason = can_target(member, member, powerless)
        assert not allowed
        assert "Move Members" in reason

    def test_refused_when_channel_denies_bot(self, me):
        # The bot has the guild permission but it is denied on this channel, so
        # the timer would fail with Forbidden at expiry.
        member = Member(MEMBER_ID, bot_can_move=False)
        allowed, reason = can_target(member, member, me)
        assert not allowed
        assert "can't disconnect" in reason

    def test_allowed_when_target_not_in_voice(self, me):
        # The voice channel check is skipped; the caller handles "not in voice".
        member = Member(MEMBER_ID, in_voice=False)
        allowed, reason = can_target(member, member, me)
        assert allowed, reason


class TestTargetingOthers:
    def test_plain_member_cannot_target_others(self, me):
        author = Member(MEMBER_ID, role_position=1)
        target = Member(MOD_ID, role_position=1)
        allowed, reason = can_target(author, target, me)
        assert not allowed
        assert "Move Members" in reason

    def test_cannot_target_a_bot(self, me):
        author = Member(MOD_ID, perms=Perms(move_members=True), role_position=5)
        target = Member(BOT_ID, role_position=1, bot=True)
        allowed, reason = can_target(author, target, me)
        assert not allowed
        assert "bot" in reason

    def test_cannot_target_guild_owner(self, me):
        author = Member(MOD_ID, perms=Perms(move_members=True), role_position=5)
        target = Member(GUILD_OWNER_ID, role_position=1)
        allowed, reason = can_target(author, target, me)
        assert not allowed
        assert "server owner" in reason

    def test_cannot_target_equal_role(self, me):
        author = Member(MOD_ID, perms=Perms(move_members=True), role_position=5)
        target = Member(MEMBER_ID, role_position=5)
        allowed, reason = can_target(author, target, me)
        assert not allowed
        assert "higher role" in reason

    def test_cannot_target_higher_role(self, me):
        # A moderator must not be able to timer an admin above them.
        author = Member(MOD_ID, perms=Perms(move_members=True), role_position=5)
        target = Member(ADMIN_ID, role_position=9)
        allowed, reason = can_target(author, target, me)
        assert not allowed
        assert "higher role" in reason

    def test_can_target_lower_role(self, me):
        author = Member(MOD_ID, perms=Perms(move_members=True), role_position=9)
        target = Member(MEMBER_ID, role_position=1)
        allowed, reason = can_target(author, target, me)
        assert allowed, reason

    def test_guild_owner_bypasses_role_hierarchy(self, me):
        # The owner outranks everyone regardless of role position.
        author = Member(GUILD_OWNER_ID, perms=Perms(administrator=True), role_position=1)
        target = Member(ADMIN_ID, role_position=99)
        allowed, reason = can_target(author, target, me)
        assert allowed, reason
