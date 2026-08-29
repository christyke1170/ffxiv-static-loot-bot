from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.models import (
    AuditLog,
    BisSet,
    BisSetItem,
    Character,
    CharacterKind,
    GearClassification,
    GearSlot,
    Job,
    ReclearWeek,
    ReclearWorkflowState,
    Static,
    StaticMember,
    V2Confirmation,
    V2EffectLedger,
    V2Plan,
    V2PlanRun,
    V2ResourceBalance,
)
from app.services.hierarchy import ensure_default_hierarchy
from bot.client import StaticLootClient
from bot.commands.loot import Loot
from bot.commands.reclear import Reclear, _next_confirmation
from bot.commands.setup import Setup
from bot.commands.static import Static as StaticCog
from bot.views.v2_confirmation import V2ConfirmationView
from tests.bot.conftest import RAID_LEADER_ROLE
from tests.bot.fakes import (
    FakeDiscordMember,
    FakeGuild,
    FakeInteraction,
    FakeRole,
    invoke_registered,
)


def _bot(engine):
    from app.database import create_session_factory

    return SimpleNamespace(
        settings=Settings(
            database_url="sqlite:///:memory:",
            bot_admin_role_ids=(10,),
            raid_leader_role_ids=(RAID_LEADER_ROLE,),
        ),
        session_factory=create_session_factory(engine),
    )


def _interaction(bot, *, user_id=200, administrator=False):
    return FakeInteraction(
        bot,
        guild=FakeGuild(100, "E2E Guild"),
        user=FakeDiscordMember(user_id, [FakeRole(RAID_LEADER_ROLE)], administrator),
    )


def _roster(session, static, *, pairs=False):
    jobs = {
        code: session.scalar(select(Job).where(Job.abbreviation == code))
        for code in ("PLD", "WAR", "WHM", "SCH", "MNK", "DRG", "NIN", "BRD")
    }
    codes = ("PLD", "WAR", "WHM", "SCH", "MNK", "DRG", "NIN", "BRD")
    for index, code in enumerate(codes):
        member = StaticMember(static=static, discord_user_id=5000 + index, display_name=f"P{index}")
        main = Character(
            static_member=member,
            job=jobs[code],
            name=f"Main{index}",
            world="Neutral",
            kind=CharacterKind.MAIN,
        )
        session.add(main)
        if pairs:
            session.add(
                Character(
                    static_member=member,
                    job=jobs[code],
                    name=f"Alt{index}",
                    world="Neutral",
                    kind=CharacterKind.ALT,
                )
            )
    session.flush()
    slots = session.scalars(select(GearSlot).order_by(GearSlot.sort_order)).all()
    for code in codes:
        job = jobs[code]
        bis = BisSet(static=static, job=job, name=f"{code} neutral BiS")
        bis.items = [
            BisSetItem(bis_set=bis, gear_slot=slot, classification=GearClassification.SAVAGE)
            for slot in slots
        ]
        session.add(bis)
    ensure_default_hierarchy(session, static)
    session.flush()


def _selected_static(session, guild_id=100):
    return session.scalar(select(Static).where(Static.guild.has(discord_guild_id=guild_id)))


async def _create_static(bot, *, pairs=False):
    await invoke_registered(
        Setup(bot), "seed", _interaction(bot, administrator=True), handle_errors=False
    )
    create = _interaction(bot)
    await invoke_registered(StaticCog(bot), "create", create, "E2E Static", 710)
    with bot.session_factory() as session:
        static = _selected_static(session)
        _roster(session, static, pairs=pairs)
        session.commit()
        static_id = static.id
    select_interaction = _interaction(bot)
    await invoke_registered(StaticCog(bot), "select", select_interaction, static_id)
    return static_id


def test_production_client_constructs_without_network():
    bot = StaticLootClient(Settings(database_url="sqlite:///:memory:"))
    assert bot.session_factory is not None
    bot.database_engine.dispose()


async def test_regular_command_workflow_reaches_closed_v2_week(engine):
    bot = _bot(engine)
    static_id = await _create_static(bot)
    setup = _interaction(bot)
    await invoke_registered(Reclear(bot), "setup", setup, "Regular")
    setup_view = setup.messages[0]["view"]
    await setup_view.confirm(_interaction(bot))
    with bot.session_factory() as session:
        week = session.scalar(
            select(__import__("app.models", fromlist=["ReclearWeek"]).ReclearWeek)
        )
        assert week.workflow_state is ReclearWorkflowState.DRAFT
        assert [row.floor_number for row in week.neutral_floors] == [1, 2, 3, 4]
        assert session.scalar(select(V2Plan)) is None

    plan_interaction = _interaction(bot)
    await invoke_registered(Reclear(bot), "plan", plan_interaction)
    assert plan_interaction.followup.messages[0]["view"] is not None
    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(V2Plan)) == 1
        assignment = session.scalar(
            __import__("sqlalchemy", fromlist=["select"]).select(
                __import__("app.models", fromlist=["V2PlanAssignment"]).V2PlanAssignment
            )
        )
        assignment_id, resource_key = assignment.id, assignment.loot_key
    retry_plan = _interaction(bot)
    await invoke_registered(Reclear(bot), "plan", retry_plan)
    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(V2Plan)) == 1
        assert session.scalar(select(ReclearWeek)).workflow_state is ReclearWorkflowState.DRAFT
    status = _interaction(bot)
    await invoke_registered(Reclear(bot), "status", status)
    assert "V2 plan:** present" in "\n".join(m["content"] or "" for m in status.messages)
    await invoke_registered(Reclear(bot), "complete", _interaction(bot), 2)
    confirmation = V2ConfirmationView(bot, assignment_id, static_id, 200, resource_key)
    await confirmation.receipt_success(_interaction(bot, administrator=True))
    with bot.session_factory() as session:
        assert (
            session.scalar(
                select(V2ResourceBalance).where(V2ResourceBalance.resource_key == resource_key)
            ).quantity
            == 1
        )
        assert (
            session.scalar(
                select(V2Confirmation).where(V2Confirmation.assignment_id == assignment_id)
            )
            is not None
        )
    await confirmation.application(_interaction(bot, administrator=True))
    with bot.session_factory() as session:
        assert (
            session.scalar(
                select(V2ResourceBalance).where(V2ResourceBalance.resource_key == resource_key)
            ).quantity
            == 0
        )
        assert session.scalar(select(func.count()).select_from(V2EffectLedger)) == 1
    blocked = _interaction(bot)
    await invoke_registered(Reclear(bot), "close", blocked)
    assert "confirmation questions remain" in blocked.messages[0]["content"]
    with bot.session_factory() as session:
        remaining = session.scalars(
            select(__import__("app.models", fromlist=["V2PlanAssignment"]).V2PlanAssignment).where(
                __import__("app.models", fromlist=["V2PlanAssignment"]).V2PlanAssignment.plan_id
                == session.scalar(select(V2Plan)).id
            )
        ).all()
    for pending in remaining[1:]:
        if pending.material_key is None:
            await V2ConfirmationView(
                bot, pending.id, static_id, 200, pending.loot_key
            ).receipt_success(_interaction(bot, administrator=True))
            await V2ConfirmationView(bot, pending.id, static_id, 200, pending.loot_key).application(
                _interaction(bot, administrator=True)
            )
        else:
            await V2ConfirmationView(
                bot, pending.id, static_id, 200, pending.material_key
            ).receipt_success(_interaction(bot, administrator=True))
    close = _interaction(bot)
    await invoke_registered(Reclear(bot), "close", close)
    with bot.session_factory() as session:
        assert (
            session.scalar(
                select(__import__("app.models", fromlist=["ReclearWeek"]).ReclearWeek)
            ).workflow_state
            is ReclearWorkflowState.CLOSED
        )
        assert (
            session.scalar(select(AuditLog).where(AuditLog.action == "V2_RECLEAR_WEEK_CLOSED"))
            is not None
        )
    rejected = _interaction(bot, administrator=True)
    await V2ConfirmationView(bot, assignment_id, static_id, 200, resource_key).receipt_success(
        rejected
    )
    assert rejected.response.messages[0]["ephemeral"] is True
    retry = _interaction(bot)
    await invoke_registered(Reclear(bot), "resume", retry)
    assert "No pending" in retry.followup.messages[0]["content"]


async def test_split_command_workflow_persists_two_optimizer_runs_and_paired_resources(engine):
    bot = _bot(engine)
    static_id = await _create_static(bot, pairs=True)
    setup = _interaction(bot)
    await invoke_registered(Reclear(bot), "setup", setup, "Split")
    await setup.messages[0]["view"].confirm(_interaction(bot))
    plan_interaction = _interaction(bot)
    await invoke_registered(Reclear(bot), "plan", plan_interaction)
    with bot.session_factory() as session:
        plan = session.scalar(select(V2Plan))
        runs = session.scalars(select(V2PlanRun).where(V2PlanRun.plan_id == plan.id)).all()
        assert plan.partitions_evaluated == 35
        assert len(runs) == 2
        assert all(len(run.participants) == 8 for run in runs)
        assert all(
            sum(p.character.job.role == "Tank" for p in run.participants) == 2 for run in runs
        )
        assert all(
            sum(p.character.job.role == "Healer" for p in run.participants) == 2 for run in runs
        )
        assert all(
            sum(p.character.job.role.endswith("DPS") for p in run.participants) == 4 for run in runs
        )
        pairs = {
            next(c for c in member.characters if c.kind is CharacterKind.MAIN).id: next(
                c for c in member.characters if c.kind is CharacterKind.ALT
            ).id
            for member in session.scalars(select(StaticMember)).all()
        }
        run_a = {p.character_id for p in runs[0].participants}
        assert all((main_id in run_a) != (alt_id in run_a) for main_id, alt_id in pairs.items()), (
            pairs,
            run_a,
            [{p.character_id for p in run.participants} for run in runs],
        )
        assignments = session.scalars(
            __import__("sqlalchemy", fromlist=["select"])
            .select(__import__("app.models", fromlist=["V2PlanAssignment"]).V2PlanAssignment)
            .where(
                __import__("app.models", fromlist=["V2PlanAssignment"]).V2PlanAssignment.plan_id
                == plan.id
            )
        ).all()
        assert assignments
        paired = next(row for row in assignments if "TOME" in row.loot_key.upper())
        assert paired.recipient_id is not None
        assignment_id = paired.id
    view = V2ConfirmationView(bot, assignment_id, static_id, 200, "WEAPON_TOMESTONE")
    await view.receipt_success(_interaction(bot, administrator=True))
    with bot.session_factory() as session:
        assert (
            session.scalar(
                select(V2ResourceBalance).where(
                    V2ResourceBalance.resource_key == "WEAPON_TOMESTONE"
                )
            )
            is not None
        )
    partial = V2ConfirmationView(bot, assignment_id, static_id, 200, "WEAPON_AUGMENT")
    partial_application = _interaction(bot, administrator=True)
    await partial.application(partial_application)
    assert partial_application.response.messages[0]["ephemeral"] is True
    assert "Both paired" in partial_application.response.messages[0]["content"]
    with bot.session_factory() as session:
        assert (
            session.scalar(
                select(V2ResourceBalance).where(
                    V2ResourceBalance.recipient_id == paired.recipient_id,
                    V2ResourceBalance.resource_key == "WEAPON_TOMESTONE",
                )
            ).quantity
            == 1
        )
    await partial.receipt_success(_interaction(bot, administrator=True))
    await partial.application(_interaction(bot, administrator=True))
    with bot.session_factory() as session:
        balances = {
            row.resource_key: row.quantity
            for row in session.scalars(
                select(V2ResourceBalance).where(
                    V2ResourceBalance.recipient_id == paired.recipient_id
                )
            )
        }
        assert balances["WEAPON_TOMESTONE"] == 0
        assert balances["WEAPON_AUGMENT"] == 0
        assert session.scalar(select(func.count()).select_from(V2EffectLedger)) >= 1
        gear = session.scalar(
            select(
                __import__("app.models", fromlist=["CharacterGearSlot"]).CharacterGearSlot
            ).where(
                __import__(
                    "app.models", fromlist=["CharacterGearSlot"]
                ).CharacterGearSlot.character_id
                == paired.recipient_id
            )
        )
        assert gear is not None
    correction = _interaction(bot)
    reversal_view = V2ConfirmationView(bot, assignment_id, static_id, 200, "WEAPON_AUGMENT")
    await reversal_view.reverse(correction)
    modal = correction.response.modals[0]
    modal.reason._value = "reverse Split application"
    await modal.on_submit(_interaction(bot, administrator=True))
    with bot.session_factory() as session:
        assert (
            session.scalar(
                select(V2ResourceBalance).where(
                    V2ResourceBalance.resource_key == "WEAPON_TOMESTONE"
                )
            ).quantity
            == 1
        )
        assert (
            session.scalar(select(V2EffectLedger)).after_category
            == GearClassification.AUGMENTED_TOME.value
        )
    correction = _interaction(bot)
    await invoke_registered(
        Loot(bot), "correction", correction, assignment_id, "Receipt", False, "blocked dependency"
    )
    assert "correction recorded" in correction.followup.messages[0]["content"]
    cancel = _interaction(bot)
    await invoke_registered(Reclear(bot), "cancel", cancel, "already started")
    with pytest.raises(ValueError, match="cannot be cancelled"):
        await cancel.messages[0]["view"].confirm(_interaction(bot))
    for _ in range(100):
        with bot.session_factory() as session:
            week = session.scalar(select(ReclearWeek))
            pending = _next_confirmation(bot, session, week, 200)
        if pending is None:
            break
        recipient_select = next(
            (
                child
                for child in pending.walk_children()
                if getattr(child, "custom_id", None) == f"v2:{pending.assignment_id}:recipient"
            ),
            None,
        )
        if recipient_select is not None:
            recipient_select._values = [str(recipient_select.options[0].value)]
            await pending.select_recipient(_interaction(bot, administrator=True))
        await pending.receipt_success(_interaction(bot, administrator=True))
        with bot.session_factory() as session:
            assignment = session.get(
                __import__("app.models", fromlist=["V2PlanAssignment"]).V2PlanAssignment,
                pending.assignment_id,
            )
        if assignment.material_key is None:
            await pending.application(_interaction(bot, administrator=True))
    close = _interaction(bot)
    await invoke_registered(Reclear(bot), "close", close)
    with bot.session_factory() as session:
        assert session.scalar(select(ReclearWeek)).workflow_state is ReclearWorkflowState.CLOSED
