"""Application emoji ownership, idempotent provisioning, and rendered payloads."""

from dataclasses import replace
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from app.domain.jobs import JOB_CATALOG
from app.services.board import build_static_gear_board
from app.services.formatting import overview_table
from app.services.job_labels import job_label
from bot.commands.bis import Bis
from bot.commands.character import Character as CharacterCog
from bot.commands.member import Member
from bot.services.job_icons import sync_job_emojis
from bot.views.gearboard import GearBoardView
from tests.bot.fakes import invoke_registered
from tests.bot.test_gear_editor import _arrange_character


def fake_emoji(code, identifier):
    return SimpleNamespace(name=f"job_{code.lower()}", id=identifier, animated=False)


async def test_application_emojis_are_reused_without_uploading_or_using_guild_cache():
    icons = [fake_emoji(job.abbreviation, index + 100) for index, job in enumerate(JOB_CATALOG)]
    client = SimpleNamespace(
        application_id=1,
        fetch_application_emojis=AsyncMock(return_value=icons),
        create_application_emoji=AsyncMock(),
    )
    await sync_job_emojis(client)
    assert set(client.job_emojis) == {job.abbreviation for job in JOB_CATALOG}
    client.create_application_emoji.assert_not_awaited()
    assert str(client.job_emojis["SAM"]) == "<:job_sam:100>"


async def test_missing_job_icon_uploads_once_and_next_start_reuses_it():
    icons = [
        fake_emoji(job.abbreviation, index + 100)
        for index, job in enumerate(JOB_CATALOG)
        if job.abbreviation != "SAM"
    ]
    client = SimpleNamespace(
        application_id=1,
        fetch_application_emojis=AsyncMock(return_value=icons),
        create_application_emoji=AsyncMock(return_value=fake_emoji("SAM", 999)),
    )
    await sync_job_emojis(client)
    request = client.create_application_emoji.await_args.kwargs
    assert request["name"] == "job_sam"
    assert request["image"].startswith(b"\x89PNG\r\n\x1a\n")
    assert len(request["image"]) <= 256 * 1024
    client.fetch_application_emojis.return_value = icons + [fake_emoji("SAM", 999)]
    await sync_job_emojis(client)
    assert client.create_application_emoji.await_count == 1


async def test_application_fetch_failure_falls_back_to_plain_jobs():
    response = SimpleNamespace(status=403, reason="Forbidden")
    client = SimpleNamespace(
        application_id=1,
        fetch_application_emojis=AsyncMock(
            side_effect=discord.Forbidden(response, "Not available")
        ),
        create_application_emoji=AsyncMock(),
    )
    await sync_job_emojis(client)
    assert client.job_emojis == {}
    assert job_label("SAM", client.job_emojis) == "SAM"
    client.create_application_emoji.assert_not_awaited()


async def test_each_application_keeps_its_own_emoji_ids():
    clients = []
    for identifier in (100, 200):
        icons = [
            fake_emoji(job.abbreviation, identifier + index)
            for index, job in enumerate(JOB_CATALOG)
        ]
        client = SimpleNamespace(
            application_id=identifier,
            fetch_application_emojis=AsyncMock(return_value=icons),
            create_application_emoji=AsyncMock(),
        )
        await sync_job_emojis(client)
        clients.append(client)
    assert clients[0].job_emojis["SAM"].id != clients[1].job_emojis["SAM"].id
    assert job_label("NEW", clients[0].job_emojis) == "NEW"


def test_all_catalog_jobs_have_packaged_pngs_under_discord_size_limit():
    for job in JOB_CATALOG:
        data = (
            files("bot").joinpath("assets", "jobs", f"{job.abbreviation.lower()}.png").read_bytes()
        )
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(data) <= 256 * 1024


async def test_bis_and_character_dropdowns_use_emoji_field_and_keep_plain_values(
    bot, interaction_factory
):
    _arrange_character(bot)
    bot.job_emojis = {"SAM": discord.PartialEmoji(name="job_sam", id=123456789012345678)}
    for cog in (Bis(bot), CharacterCog(bot)):
        interaction = interaction_factory()
        await invoke_registered(cog, "setup", interaction)
        view = interaction.followup.messages[0]["view"]
        option = next(option for option in view.picker.options if "SAM" in option.label)
        assert option.emoji.id == 123456789012345678
        assert option.value.isdigit()
        assert "<:" not in option.label
        assert interaction.followup.messages[0]["content"] is None
        assert view.total_children_count <= 40


async def test_character_and_member_lists_preserve_custom_emoji_markup(bot, interaction_factory):
    _arrange_character(bot)
    bot.job_emojis = {"SAM": discord.PartialEmoji(name="job_sam", id=123456789012345678)}
    for cog in (Member(bot), CharacterCog(bot)):
        interaction = interaction_factory()
        await invoke_registered(cog, "list", interaction)
        text = interaction.messages[0]["content"]
        assert "<:job_sam:123456789012345678> SAM" in text
        assert "job\\_sam" not in text


def test_gearboard_icons_are_outside_code_blocks_and_native_on_player_options(bot):
    static_id, _, _ = _arrange_character(bot)
    bot.job_emojis = {"SAM": discord.PartialEmoji(name="job_sam", id=123456789012345678)}
    with bot.session_factory() as session:
        board = build_static_gear_board(session, static_id)
    # Exercise the longest normal overview page with four long player names.
    board = replace(
        board,
        players=tuple(
            replace(board.players[0], display_name="X" * 100, character_id=index)
            for index in range(4)
        ),
    )
    text, _ = overview_table(board, job_icons=bot.job_emojis)
    assert len(text) <= 2000
    parts = text.split("```")
    assert all("<:job_sam:" not in code for code in parts[1::2])
    assert sum("<:job_sam:" in heading for heading in parts[::2]) == 4
    view = GearBoardView(bot, board)
    picker = next(child for child in view.walk_children() if isinstance(child, discord.ui.Select))
    assert all(option.emoji.id == 123456789012345678 for option in picker.options[1:])
    assert view.total_children_count <= 40
