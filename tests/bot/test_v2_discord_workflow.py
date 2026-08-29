"""Focused fake-Discord tests for the unregistered neutral V2 workflow."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models import CharacterKind, ClearMode, GearClassification, GearSlotCode
from app.schemas.regular_planning_v2 import (
    ProposedGearEffect,
    RegularAssignment,
    RegularPlanProposal,
    RegularScore,
    UnassignedRegularLoot,
)
from app.schemas.split_planning_v2 import (
    SplitAssignment,
    SplitGroupProposal,
    SplitPlanProposal,
)
from app.schemas.v2_confirmation import V2ConfirmationReadback, V2ConfirmationState
from bot.views.v2_confirmation import (
    V2ConfirmationView,
    confirmation_state_text,
    correction_history_text,
)
from bot.views.v2_plan import V2PlanView, v2_plan_pages


def _regular_result():
    assignment = RegularAssignment(
        4,
        "WEAPON_COFFER",
        GearSlotCode.WEAPON,
        None,
        10,
        "PLD",
        CharacterKind.MAIN,
        1,
        (
            ProposedGearEffect(GearSlotCode.WEAPON, GearClassification.SAVAGE),
            ProposedGearEffect(GearSlotCode.OFFHAND, GearClassification.SAVAGE),
        ),
        RegularScore(1, 0, 0, 0, 1, 10),
        "weapon and applicable offhand",
    )
    proposal = RegularPlanProposal(
        1,
        2,
        35,
        ClearMode.REGULAR,
        "f" * 64,
        (assignment,),
        (UnassignedRegularLoot(3, "ARMOR_TWINE", None, "ARMOR_TWINE", "not assigned"),),
        ("configuration warning",),
    )
    return SimpleNamespace(plan_id=9, proposal=proposal)


def _split_result():
    assignment = SplitAssignment(
        1,
        1,
        4,
        "TOME_WEAPON_RESOURCES",
        GearSlotCode.WEAPON,
        None,
        10,
        "PLD",
        CharacterKind.ALT,
        None,
        1,
        1,
        0,
        (ProposedGearEffect(GearSlotCode.WEAPON, GearClassification.AUGMENTED_TOME),),
        1,
        None,
        "paired Tome resources",
    )
    proposal = SplitPlanProposal(
        1,
        2,
        35,
        ClearMode.SPLIT,
        "a" * 64,
        (SplitGroupProposal(1, 1, (1, 2), (assignment,)), SplitGroupProposal(2, 2, (3, 4), ())),
        (),
        (),
    )
    return SimpleNamespace(plan_id=10, proposal=proposal)


def test_regular_formatting_includes_neutral_assignments_effects_and_warning():
    text = "\n".join(v2_plan_pages(_regular_result(), {10: "Main One"}))
    assert "Weekly Loot Plan" in text
    assert "Weapon Coffer" in text
    assert "Weapon -> Savage" in text
    assert "Offhand -> Savage" in text
    assert "Armor Twine" in text
    assert "configuration warning" in text
    assert "tier" not in text.lower()
    assert "item_id" not in text.lower()
    assert "book" not in text.lower()


def test_split_formatting_shows_two_generated_runs_and_main_alt_labels():
    text = "\n".join(
        v2_plan_pages(_split_result(), {1: "Main A", 2: "Alt A", 3: "Main B", 4: "Alt B"})
    )
    assert "Run A roster" in text and "Run B roster" in text
    assert "Tome Weapon Resources" in text
    assert "Main A" in text
    assert "Alt A" in text


def test_plan_view_is_unregistered_paginated_and_times_out(bot, interaction_factory):
    view = V2PlanView(bot, _regular_result(), 200)
    assert view.timeout == 300
    assert len(view.pages) >= 1
    assert not hasattr(view, "__cog_app_commands__")
    assert view.interaction_check


def test_confirmation_state_displays_partial_completed_rejected_and_owned_resources():
    state = V2ConfirmationReadback(
        1,
        (
            V2ConfirmationState(1, 1, "WEAPON_TOMESTONE", "RECEIPT", True, 10, 1, 2, None, None),
            V2ConfirmationState(2, 1, "WEAPON_AUGMENT", "RECEIPT", False, 10, 1, 2, None, None),
        ),
        (),
        (("WEAPON_TOMESTONE", 1),),
    )
    text = confirmation_state_text(state)
    assert "confirmed" in text and "rejected" in text
    assert "Weapon Tomestone" in text


@pytest.mark.asyncio
async def test_confirmation_view_requires_admin_and_preserves_resource_key(
    bot, interaction_factory
):
    view = V2ConfirmationView(bot, 1, 1, 200, "WEAPON_TOMESTONE")
    denied = interaction_factory(administrator=False, roles=())
    assert await view.interaction_check(denied) is False
    assert denied.response.messages[0]["ephemeral"] is True
    assert view.resource_key == "WEAPON_TOMESTONE"


def test_correction_history_formatter_requires_no_business_logic(session):
    with patch("bot.views.v2_confirmation.read_v2_correction_history", return_value=()):
        assert "No V2 correction history" in correction_history_text(session, 1)


@pytest.mark.asyncio
async def test_confirmation_retry_callback_calls_only_v2_receipt_and_resumes_state(
    bot, interaction_factory
):
    view = V2ConfirmationView(bot, 1, 1, 200, "ARMOR_TWINE")
    interaction = interaction_factory(administrator=True)
    state = V2ConfirmationReadback(1, (), (), (("ARMOR_TWINE", 2),))
    with (
        patch("bot.views.v2_confirmation.confirm_v2_receipt") as receipt,
        patch("bot.views.v2_confirmation.read_v2_confirmation_state", return_value=state),
    ):
        await view._run(
            interaction,
            lambda session, actor: receipt(session, 1, "ARMOR_TWINE", True, actor_id=actor),
        )
    receipt.assert_called_once()
    assert "content" not in interaction.response.edits[0]
    assert interaction.response.edits[0]["view"] is view


def test_view_modules_do_not_import_legacy_plan_or_confirmation_services():
    import bot.views.v2_confirmation as confirmation
    import bot.views.v2_plan as plan

    for module in (confirmation, plan):
        names = set(vars(module))
        assert "generate_weekly_loot_plan" not in names
        assert "confirm_loot_plan" not in names
        assert "load_persisted_loot_plan" not in names
