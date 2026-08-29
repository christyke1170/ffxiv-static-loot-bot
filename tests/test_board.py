from types import SimpleNamespace

from sqlalchemy import select

from app.models import (
    BisSet,
    BisSetItem,
    Character,
    CharacterKind,
    DiscordGuild,
    GearClassification,
    GearSlot,
    GearSlotCode,
    Job,
    Static,
    StaticMember,
)
from app.schemas.board import DisplayStatus
from app.schemas.needs_v2 import NeedsV2Status
from app.services.board import build_static_gear_board, display_status
from app.services.seed import seed_reference_data


def test_board_contains_mains_and_category_state(session):
    seed_reference_data(session)
    g = DiscordGuild(discord_guild_id=501, name="G")
    j = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
    s = Static(guild=g, name="S")
    m = StaticMember(static=s, discord_user_id=502, display_name="P")
    c = Character(static_member=m, job=j, name="Main", world="W", kind=CharacterKind.MAIN)
    b = BisSet(static=s, job=j, name="PLD")
    session.add_all([c, b])
    session.flush()
    session.add_all(
        [
            BisSetItem(bis_set=b, gear_slot=x, classification=GearClassification.CRAFTED_EX)
            for x in session.scalars(select(GearSlot))
        ]
    )
    session.commit()
    board = build_static_gear_board(session, s.id)
    assert len(board.players) == 1 and board.players[0].character_kind is CharacterKind.MAIN


def test_crafted_current_state_is_not_needs_replacement_when_bis_differs():
    result = SimpleNamespace(
        status=NeedsV2Status.INVALID_CONFIGURATION,
        current=GearClassification.CRAFTED_EX,
        desired=GearClassification.AUGMENTED_TOME,
        gear_slot=GearSlotCode.HEAD,
        character=None,
    )

    assert display_status(result) is DisplayStatus.CRAFTED_EX


def test_missing_current_state_with_invalid_configuration_needs_replacement():
    result = SimpleNamespace(
        status=NeedsV2Status.INVALID_CONFIGURATION,
        current=None,
        desired=GearClassification.AUGMENTED_TOME,
        gear_slot=GearSlotCode.HEAD,
        character=None,
    )

    assert display_status(result) is DisplayStatus.NEEDS_REPLACEMENT


def test_matching_savage_and_augmented_tome_are_bis_not_alternate():
    for category in (GearClassification.SAVAGE, GearClassification.AUGMENTED_TOME):
        result = SimpleNamespace(
            status=NeedsV2Status.NEEDS_SAVAGE_DROP,
            current=category,
            desired=category,
            gear_slot=GearSlotCode.HEAD,
            character=None,
        )

        assert display_status(result) is DisplayStatus.BIS


def test_alternate_is_only_the_savage_augmented_tome_pair():
    for desired, current in (
        (GearClassification.SAVAGE, GearClassification.AUGMENTED_TOME),
        (GearClassification.AUGMENTED_TOME, GearClassification.SAVAGE),
    ):
        result = SimpleNamespace(
            status=NeedsV2Status.NEEDS_SAVAGE_DROP,
            current=current,
            desired=desired,
            gear_slot=GearSlotCode.HEAD,
            character=None,
        )

        assert display_status(result) is DisplayStatus.ALTERNATE
