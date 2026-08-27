"""Focused Step 8 Discord workflow tests."""

from datetime import UTC, datetime
from types import SimpleNamespace

from discord import app_commands
from sqlalchemy import select

from app.models import (
    AuditLog,
    ClearMode,
    LootPlan,
    UserStaticPreference,
    WeeklyLootPlanStatus,
)
from app.services import generate_and_persist_loot_plan, load_persisted_loot_plan
from bot.commands.reclear import Reclear
from bot.views.loot_plan import LootPlanView, plan_pages
from tests.bot.fakes import invoke_registered, registered_command
from tests.test_regular_loot_planning import RegularFixture


def _choice(value: str) -> app_commands.Choice[str]:
    return app_commands.Choice(name=value.title(), value=value)


def test_plan_command_exposes_regular_and_split_choices(bot):
    command = registered_command(Reclear(bot), "plan")
    assert [choice.value for choice in command._params["mode"].choices] == ["REGULAR", "SPLIT"]


async def test_plan_view_without_active_plan_is_clear(bot, interaction_factory):
    with bot.session_factory() as session:
        fixture = RegularFixture(session)
        session.add(
            UserStaticPreference(
                guild_id=fixture.guild.id, discord_user_id=200, static_id=fixture.static.id
            )
        )
        session.commit()
    interaction = interaction_factory(guild_id=771001, roles=())
    await invoke_registered(Reclear(bot), "plan-view", interaction)
    assert "Unable to load active plan" in interaction.messages[0]["content"]
    assert interaction.response.deferrals == [{"ephemeral": True}]


def test_persisted_plan_pages_hide_internal_and_excluded_loot():
    class Result:
        static_name = "Alpha"
        tier_name = "Fictional Arc"
        target_week = 2
        mode = ClearMode.REGULAR
        status = WeeklyLootPlanStatus.READY
        created_at = datetime.now(UTC)
        creator_discord_user_id = 10
        staleness = SimpleNamespace(value="CURRENT")
        validation_warnings = ()
        stale_reasons = ()
        runs = ()

    text = "\n".join(plan_pages(Result()))
    assert "Plan State: Current" in text
    assert "DRAFT" not in text
    assert "loot_type_id" not in text


async def test_plan_generation_uses_persistence_and_does_not_mutate_week(bot, interaction_factory):
    with bot.session_factory() as session:
        fixture = RegularFixture(session)
        session.add(UserStaticPreference(
            guild_id=fixture.guild.id, discord_user_id=200, static_id=fixture.static.id
        ))
        session.commit()
        static_id = fixture.static.id
    interaction = interaction_factory(guild_id=771001)
    await invoke_registered(
        Reclear(bot), "plan", interaction, _choice("REGULAR")
    )
    with bot.session_factory() as session:
        plan = session.scalar(
            select(LootPlan).where(LootPlan.reclear_week.has(static_id=static_id))
        )
        assert plan is not None
        assert plan.status is WeeklyLootPlanStatus.READY
        assert session.scalar(select(AuditLog).where(AuditLog.action == "LOOT_PLAN_CREATED"))
    assert interaction.followup.messages
    assert isinstance(interaction.followup.messages[0]["view"], LootPlanView)


async def test_cancel_command_shows_confirmation_and_keep_does_not_write(bot, interaction_factory):
    with bot.session_factory() as session:
        fixture = RegularFixture(session)
        session.add(UserStaticPreference(
            guild_id=fixture.guild.id, discord_user_id=200, static_id=fixture.static.id
        ))
        result = generate_and_persist_loot_plan(
            session, fixture.static.id, ClearMode.REGULAR, 8801
        )
        session.commit()
    interaction = interaction_factory(guild_id=771001)
    await invoke_registered(Reclear(bot), "plan-cancel", interaction)
    view = interaction.followup.messages[0]["view"]
    assert view.__class__.__name__ == "LootPlanCancelView"
    callback = next(
        item for item in view.walk_children() if getattr(item, "label", None) == "Keep Plan"
    )
    callback_interaction = interaction_factory(guild_id=771001)
    assert await view.interaction_check(callback_interaction)
    await callback.callback(callback_interaction)
    with bot.session_factory() as session:
        assert session.get(LootPlan, result.plan_id).status is WeeklyLootPlanStatus.READY
        assert (
            session.scalar(select(AuditLog).where(AuditLog.action == "LOOT_PLAN_CANCELLED"))
            is None
        )


async def test_plan_view_component_rejects_another_user(bot, interaction_factory):
    with bot.session_factory() as session:
        fixture = RegularFixture(session)
        session.add(UserStaticPreference(
            guild_id=fixture.guild.id, discord_user_id=200, static_id=fixture.static.id
        ))
        result = generate_and_persist_loot_plan(
            session, fixture.static.id, ClearMode.REGULAR, 8802
        )
        session.commit()
    owner = interaction_factory(guild_id=771001)
    with bot.session_factory() as session:
        loaded = load_persisted_loot_plan(session, result.plan_id)
    view = LootPlanView(bot, loaded, owner.user.id)
    other = interaction_factory(guild_id=771001, user_id=999)
    assert not await view.interaction_check(other)
    assert "Only the user" in other.messages[0]["content"]