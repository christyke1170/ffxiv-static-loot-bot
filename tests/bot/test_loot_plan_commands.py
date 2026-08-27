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
from bot.views.loot_plan import (
    LootPlanConfirmationView,
    LootPlanView,
    confirmation_preview,
    plan_pages,
)
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
        session.add(
            UserStaticPreference(
                guild_id=fixture.guild.id, discord_user_id=200, static_id=fixture.static.id
            )
        )
        session.commit()
        static_id = fixture.static.id
    interaction = interaction_factory(guild_id=771001)
    await invoke_registered(Reclear(bot), "plan", interaction, _choice("REGULAR"))
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
        session.add(
            UserStaticPreference(
                guild_id=fixture.guild.id, discord_user_id=200, static_id=fixture.static.id
            )
        )
        result = generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.REGULAR, 8801)
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
            session.scalar(select(AuditLog).where(AuditLog.action == "LOOT_PLAN_CANCELLED")) is None
        )


async def test_plan_view_component_rejects_another_user(bot, interaction_factory):
    with bot.session_factory() as session:
        fixture = RegularFixture(session)
        session.add(
            UserStaticPreference(
                guild_id=fixture.guild.id, discord_user_id=200, static_id=fixture.static.id
            )
        )
        result = generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.REGULAR, 8802)
        session.commit()
    owner = interaction_factory(guild_id=771001)
    with bot.session_factory() as session:
        loaded = load_persisted_loot_plan(session, result.plan_id)
    view = LootPlanView(bot, loaded, owner.user.id)
    other = interaction_factory(guild_id=771001, user_id=999)
    assert not await view.interaction_check(other)
    assert "Only the user" in other.messages[0]["content"]


async def _ready_plan(bot, user_static_preference=True, user_id=200):
    with bot.session_factory() as session:
        fixture = RegularFixture(session)
        if user_static_preference:
            session.add(
                UserStaticPreference(
                    guild_id=fixture.guild.id,
                    discord_user_id=user_id,
                    static_id=fixture.static.id,
                )
            )
        result = generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.REGULAR, 8803)
        session.commit()
    return result


async def test_plan_confirm_command_defers_and_opens_preview_without_applying(
    bot, interaction_factory
):
    result = await _ready_plan(bot)
    interaction = interaction_factory(guild_id=771001)

    await invoke_registered(Reclear(bot), "plan-confirm", interaction)

    assert interaction.response.deferrals == [{"ephemeral": True}]
    assert interaction.followup.messages
    message = interaction.followup.messages[0]
    assert "Confirm Persisted Loot Plan" in message["content"]
    assert "Plan ID:" in message["content"]
    assert "Expected earned-book increments:" in message["content"]
    assert isinstance(message["view"], LootPlanConfirmationView)
    with bot.session_factory() as session:
        assert session.get(LootPlan, result.plan_id).status is WeeklyLootPlanStatus.READY


async def test_plan_confirm_unauthorized_user_is_rejected(bot, interaction_factory):
    await _ready_plan(bot)
    interaction = interaction_factory(guild_id=771001, roles=())

    await invoke_registered(Reclear(bot), "plan-confirm", interaction)

    assert "permission" in interaction.messages[0]["content"].lower()


async def test_plan_view_confirm_action_uses_shared_confirmation_view(bot, interaction_factory):
    result = await _ready_plan(bot)
    owner = interaction_factory(guild_id=771001)
    with bot.session_factory() as session:
        loaded = load_persisted_loot_plan(session, result.plan_id)
    view = LootPlanView(bot, loaded, owner.user.id)
    button = next(
        item for item in view.walk_children() if getattr(item, "label", None) == "Confirm Plan"
    )
    await view.open_confirmation(owner)

    assert isinstance(owner.response.edits[0]["view"], LootPlanConfirmationView)
    assert button.label == "Confirm Plan"


async def test_keep_plan_does_not_mutate_or_cancel(bot, interaction_factory):
    result = await _ready_plan(bot)
    owner = interaction_factory(guild_id=771001)
    with bot.session_factory() as session:
        loaded = load_persisted_loot_plan(session, result.plan_id)
    view = LootPlanConfirmationView(bot, loaded, owner.user.id)
    keep = next(
        item for item in view.walk_children() if getattr(item, "label", None) == "Keep Plan"
    )

    await keep.callback(owner)

    assert "kept" in owner.response.edits[0]["content"].lower()
    with bot.session_factory() as session:
        assert session.get(LootPlan, result.plan_id).status is WeeklyLootPlanStatus.READY


async def test_confirm_button_calls_step9_and_disables_controls(
    bot, interaction_factory, monkeypatch
):
    result = await _ready_plan(bot)
    owner = interaction_factory(guild_id=771001)
    with bot.session_factory() as session:
        loaded = load_persisted_loot_plan(session, result.plan_id)
    view = LootPlanConfirmationView(bot, loaded, owner.user.id)
    calls = []

    from app.schemas.loot_plan_confirmation import LootPlanConfirmationResult

    def fake_confirm(session, plan_id, actor):
        calls.append((plan_id, actor))
        return LootPlanConfirmationResult(
            plan_id,
            WeeklyLootPlanStatus.READY,
            WeeklyLootPlanStatus.APPLIED,
            True,
            False,
            2,
            1,
            1,
            0,
            32,
            4,
            1,
            2,
            None,
        )

    monkeypatch.setattr("bot.views.loot_plan.confirm_loot_plan", fake_confirm)
    await view.confirm(owner)

    assert calls == [(result.plan_id, owner.user.id)]
    assert "applied successfully" in owner.response.edits[0]["content"].lower()
    assert all(
        getattr(item, "disabled", False)
        for item in view.walk_children()
        if hasattr(item, "disabled")
    )


async def test_stale_plan_confirmation_is_disabled(bot, interaction_factory):
    result = await _ready_plan(bot)
    with bot.session_factory() as session:
        plan = session.get(LootPlan, result.plan_id)
        plan.source_snapshot_version = None
        session.commit()
        loaded = load_persisted_loot_plan(session, result.plan_id)
    view = LootPlanConfirmationView(bot, loaded, 200)
    labels = [getattr(item, "label", None) for item in view.walk_children()]
    assert "Confirm Plan" in labels
    confirm = next(
        item for item in view.walk_children() if getattr(item, "label", None) == "Confirm Plan"
    )
    assert confirm.disabled is True
    assert "unverifiable" in confirmation_preview(loaded).lower()


async def test_plan_confirm_callback_uses_real_step9_service_and_commits(bot, interaction_factory):
    result = await _ready_plan(bot)
    owner = interaction_factory(guild_id=771001)
    with bot.session_factory() as session:
        loaded = load_persisted_loot_plan(session, result.plan_id)
    view = LootPlanConfirmationView(bot, loaded, owner.user.id)
    assert await view.interaction_check(owner)

    await view.confirm(owner)

    assert "applied successfully" in owner.response.edits[0]["content"].lower()
    with bot.session_factory() as session:
        plan = session.get(LootPlan, result.plan_id)
        assert plan.status is WeeklyLootPlanStatus.APPLIED
        assert (
            session.scalar(select(AuditLog).where(AuditLog.action == "LOOT_PLAN_APPLIED"))
            is not None
        )
