"""SQLite-backed command fixtures."""

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.database import create_session_factory
from tests.bot.fakes import FakeDiscordMember, FakeGuild, FakeInteraction, FakeRole

BOT_ADMIN_ROLE = 10
RAID_LEADER_ROLE = 20


@pytest.fixture
def bot(engine):
    return SimpleNamespace(
        settings=Settings(
            database_url="sqlite:///:memory:",
            bot_admin_role_ids=(BOT_ADMIN_ROLE,),
            raid_leader_role_ids=(RAID_LEADER_ROLE,),
        ),
        session_factory=create_session_factory(engine),
    )


@pytest.fixture
def interaction_factory(bot):
    def factory(
        *,
        guild_id: int = 100,
        user_id: int = 200,
        roles: tuple[int, ...] = (RAID_LEADER_ROLE,),
        administrator: bool = False,
    ) -> FakeInteraction:
        return FakeInteraction(
            bot,
            guild=FakeGuild(guild_id, f"Guild {guild_id}"),
            user=FakeDiscordMember(
                user_id, [FakeRole(role_id) for role_id in roles], administrator
            ),
        )

    return factory
