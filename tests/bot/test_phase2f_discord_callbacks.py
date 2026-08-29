"""Retained V2 Discord command and callback coverage."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.schemas.v2_confirmation import V2ConfirmationReadback
from bot.client import StaticLootClient
from bot.commands.reclear import Reclear
from bot.views.v2_confirmation import V2ConfirmationView, V2CorrectionModal
from bot.views.v2_plan import V2PlanView
from tests.bot.helpers import arrange_static
from tests.bot.test_v2_discord_workflow import _regular_result, _split_result


def _commands(cog):
    return {
        command.name: command for group in cog.__cog_app_commands__ for command in group.commands
    }


def test_reclear_exposes_only_the_seven_retained_v2_commands(bot):
    assert set(_commands(Reclear(bot))) == {
        "setup",
        "status",
        "plan",
        "complete",
        "resume",
        "close",
        "cancel",
    }


def test_production_extensions_are_exactly_the_thirteen_retained_extensions():
    import inspect

    source = inspect.getsource(StaticLootClient.setup_hook)
    expected = {
        "bot.commands.setup",
        "bot.commands.static",
        "bot.commands.member",
        "bot.commands.character",
        "bot.commands.bis",
        "bot.commands.hierarchy",
        "bot.commands.gear",
        "bot.commands.resources",
        "bot.commands.needs",
        "bot.commands.gearboard",
        "bot.commands.reclear",
        "bot.commands.loot",
        "bot.commands.lootboard",
    }
    assert all(name in source for name in expected)
    assert "bot.commands.tier" not in source


@pytest.mark.asyncio
async def test_plan_view_timeout_disables_all_controls(bot):
    view = V2PlanView(bot, _regular_result(), 200)
    await view.on_timeout()
    assert all(not hasattr(child, "disabled") or child.disabled for child in view.walk_children())


@pytest.mark.asyncio
async def test_confirmation_timeout_disables_all_controls(bot):
    view = V2ConfirmationView(bot, 1, 1, 200, "WEAPON_TOMESTONE")
    await view.on_timeout()
    assert all(not hasattr(child, "disabled") or child.disabled for child in view.walk_children())


@pytest.mark.asyncio
async def test_plan_view_rejects_wrong_guild_ephemerally(bot, interaction_factory):
    arrange_static(bot)
    view = V2PlanView(bot, _regular_result(), 200)
    interaction = interaction_factory(guild_id=999)
    assert await view.interaction_check(interaction) is False
    assert interaction.response.messages[0]["ephemeral"] is True


@pytest.mark.asyncio
async def test_confirmation_callbacks_pass_assignment_and_independent_resource_key(
    bot, interaction_factory
):
    view = V2ConfirmationView(bot, 42, 7, 200, "WEAPON_AUGMENT")
    interaction = interaction_factory(administrator=True)
    with (
        patch("bot.views.v2_confirmation.confirm_v2_receipt") as receipt,
        patch(
            "bot.views.v2_confirmation.read_v2_confirmation_state",
            return_value=V2ConfirmationReadback(42, (), (), ()),
        ),
    ):
        await view.receipt_success(interaction)
    receipt.assert_called_once()
    assert receipt.call_args.args[:3] == (
        bot.session_factory,
        42,
        "WEAPON_AUGMENT",
    ) or receipt.call_args.args[1:3] == (42, "WEAPON_AUGMENT")


@pytest.mark.asyncio
async def test_correction_modal_dispatches_receipt_correction_with_actor_and_reason(
    bot, interaction_factory
):
    modal = V2CorrectionModal(SimpleNamespace(bot=bot), "receipt-failed", 55)
    modal.reason._value = "verified failure"
    interaction = interaction_factory(administrator=True)
    with patch("bot.views.v2_confirmation.correct_v2_receipt") as correct:
        await modal.on_submit(interaction)
    correct.assert_called_once()
    assert correct.call_args.args[1:4] == (55, False, 200)
    assert correct.call_args.args[4] == "verified failure"


@pytest.mark.asyncio
async def test_correction_modal_dispatches_reversal_and_conflicts_ephemerally(
    bot, interaction_factory
):
    modal = V2CorrectionModal(SimpleNamespace(bot=bot), "reverse", 56)
    modal.reason._value = "undo mistake"
    interaction = interaction_factory(administrator=True)
    with patch(
        "bot.views.v2_confirmation.reverse_v2_application",
        side_effect=ValueError("conflict"),
    ):
        await modal.on_submit(interaction)
    assert interaction.response.messages[0]["content"] == "conflict"
    assert interaction.response.messages[0]["ephemeral"] is True


def test_plan_pages_are_discord_safe_and_split_has_two_runs():
    from bot.views.v2_plan import v2_plan_pages

    regular = v2_plan_pages(_regular_result())
    split = v2_plan_pages(_split_result())
    assert regular and split
    assert all(len(page) <= 1990 for page in (*regular, *split))
    assert "Run A" in "\n".join(split) and "Run B" in "\n".join(split)
    assert "WEAPON_TOMESTONE" not in "\n".join(split) or "TOME_WEAPON_RESOURCES" not in "\n".join(
        split
    )
