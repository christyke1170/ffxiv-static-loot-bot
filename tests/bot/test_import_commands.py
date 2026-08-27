import json

import pytest
from sqlalchemy import func, select

from app.models import BisSet, RaidTier
from bot.commands.bis import Bis
from bot.commands.tier import Tier
from bot.services.commands import MAX_ATTACHMENT_BYTES
from tests.bot.fakes import FakeAttachment, invoke_registered
from tests.bot.helpers import BIS_DATA, TIER_DATA, arrange_imports


async def test_tier_import_executes_dry_run_and_real_import(bot, interaction_factory, monkeypatch):
    import bot.commands.tier as module

    calls = []
    real = module.import_raid_tier

    def tracking(session, data, *, dry_run=False):
        calls.append(dry_run)
        return real(session, data, dry_run=dry_run)

    monkeypatch.setattr(module, "import_raid_tier", tracking)
    attachment = FakeAttachment(json.dumps(TIER_DATA).encode())
    interaction = interaction_factory()

    await invoke_registered(Tier(bot), "import", interaction, attachment)

    assert calls == [True, False]
    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RaidTier)) == 1
    assert attachment.read_called
    assert "Imported tier" in interaction.messages[0]["content"]


async def test_invalid_tier_extension_is_rejected_before_read(bot, interaction_factory):
    attachment = FakeAttachment(b"{}", filename="tier.txt")
    interaction = interaction_factory()

    await invoke_registered(Tier(bot), "import", interaction, attachment)

    assert attachment.read_called is False
    assert "must end with `.json`" in interaction.messages[0]["content"]


async def test_oversized_attachment_is_rejected_before_read(bot, interaction_factory):
    attachment = FakeAttachment(b"{}", size=MAX_ATTACHMENT_BYTES + 1)
    interaction = interaction_factory()

    await invoke_registered(Tier(bot), "import", interaction, attachment)

    assert attachment.read_called is False
    assert "no larger than 1 MiB" in interaction.messages[0]["content"]


@pytest.mark.parametrize(
    ("content", "message"),
    [(b"\xff", "valid UTF-8"), (b"{broken", "valid JSON")],
)
async def test_invalid_encoding_and_json_are_rejected(bot, interaction_factory, content, message):
    interaction = interaction_factory()

    await invoke_registered(Tier(bot), "import", interaction, FakeAttachment(content))

    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RaidTier)) == 0
    assert message in interaction.messages[0]["content"]


async def test_failed_tier_dry_run_leaves_database_unchanged(bot, interaction_factory):
    invalid = {"code": "BROKEN", "name": "Broken", "floors": [{"number": -1}]}
    interaction = interaction_factory()

    await invoke_registered(
        Tier(bot), "import", interaction, FakeAttachment(json.dumps(invalid).encode())
    )

    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RaidTier)) == 0
    assert "positive integer" in interaction.messages[0]["content"]


async def test_bis_import_executes_dry_run_and_real_import(bot, interaction_factory, monkeypatch):
    arrange_imports(bot)
    import bot.commands.bis as module

    calls = []
    real = module.import_bis_sets

    def tracking(session, data, *, dry_run=False):
        calls.append(dry_run)
        return real(session, data, dry_run=dry_run)

    monkeypatch.setattr(module, "import_bis_sets", tracking)
    interaction = interaction_factory()

    await invoke_registered(
        Bis(bot), "import", interaction, FakeAttachment(json.dumps(BIS_DATA).encode())
    )

    assert calls == [True, False]
    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(BisSet)) == 1
    assert "Imported 1 BiS sets and 3 items" in interaction.messages[0]["content"]


async def test_failed_bis_validation_leaves_database_unchanged(bot, interaction_factory):
    arrange_imports(bot)
    invalid = {"sets": [{"tier_code": "FICTIONAL_ARC", "job": "NOPE", "name": "Bad"}]}
    interaction = interaction_factory()

    await invoke_registered(
        Bis(bot), "import", interaction, FakeAttachment(json.dumps(invalid).encode())
    )

    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(BisSet)) == 0
    assert "unknown job" in interaction.messages[0]["content"]
