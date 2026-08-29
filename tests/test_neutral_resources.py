"""Database-backed tests for the neutral current-resource boundary."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import (
    Character,
    CharacterKind,
    DiscordGuild,
    Job,
    Static,
    StaticMember,
    V2ResourceBalance,
)
from app.services.neutral_resources import (
    SUPPORTED_RESOURCE_KEYS,
    adjust_current_balance,
    current_balance,
    set_current_balance,
    validate_quantity,
    validate_resource_key,
)
from tests.test_v2_planning_state import _static


@pytest.mark.parametrize("key", sorted(SUPPORTED_RESOURCE_KEYS))
def test_every_supported_logical_key_validates(key):
    assert validate_resource_key(key) == key


def test_unsupported_resource_key_is_rejected():
    with pytest.raises(ValueError, match="Unsupported"):
        validate_resource_key("RETIRED_RESOURCE")


@pytest.mark.parametrize("quantity", [-1, True, 1.5])
def test_negative_and_non_integer_balances_are_rejected(quantity):
    with pytest.raises(ValueError):
        validate_quantity(quantity)


def test_current_balance_is_unique_per_static_recipient_and_key(session):
    static = _static(session)
    character = static.members[0].characters[0]
    set_current_balance(session, static, character, "HEAD_COFFER", 1)
    set_current_balance(session, static, character, "HEAD_COFFER", 3)
    session.commit()
    assert (
        session.scalar(
            select(func.count())
            .select_from(V2ResourceBalance)
            .where(V2ResourceBalance.static_id == static.id)
        )
        == 1
    )
    assert current_balance(session, static.id, character.id, "HEAD_COFFER").quantity == 3


def test_atomic_adjustment_cannot_make_balance_negative(session):
    static = _static(session)
    character = static.members[0].characters[0]
    set_current_balance(session, static, character, "HEAD_COFFER", 1)
    with pytest.raises(ValueError, match="negative"):
        adjust_current_balance(session, static.id, character.id, "HEAD_COFFER", -2)
    assert current_balance(session, static.id, character.id, "HEAD_COFFER").quantity == 1


def test_positive_adjustment_is_quantity_preserving(session):
    static = _static(session)
    character = static.members[0].characters[0]
    adjust_current_balance(session, static.id, character.id, "ARMOR_TWINE", 2)
    adjust_current_balance(session, static.id, character.id, "ARMOR_TWINE", 3)
    session.commit()
    assert current_balance(session, static.id, character.id, "ARMOR_TWINE").quantity == 5


def test_other_static_and_recipient_balances_are_isolated(session):
    first = _static(session)
    second = Static(guild=DiscordGuild(discord_guild_id=992, name="Other"), name="Other")
    job = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
    member = StaticMember(static=second, discord_user_id=2000, display_name="Other")
    other = Character(
        static_member=member, job=job, name="Other", world="Other", kind=CharacterKind.MAIN
    )
    session.add(other)
    session.flush()
    set_current_balance(session, first, first.members[0].characters[0], "HEAD_COFFER", 2)
    set_current_balance(session, second, other, "HEAD_COFFER", 7)
    session.commit()
    assert current_balance(session, first.id, other.id, "HEAD_COFFER") is None
    assert (
        current_balance(session, second.id, first.members[0].characters[0].id, "HEAD_COFFER")
        is None
    )


def test_current_scope_and_plan_scope_cannot_coexist_for_one_row(session):
    static = _static(session)
    character = static.members[0].characters[0]
    session.add(
        V2ResourceBalance(
            static_id=static.id,
            plan_id=1,
            recipient_id=character.id,
            resource_key="HEAD_COFFER",
            quantity=1,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_character_outside_static_is_rejected(session):
    first = _static(session)
    second = Static(guild=DiscordGuild(discord_guild_id=993, name="Other"), name="Other")
    job = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
    member = StaticMember(static=second, discord_user_id=3000, display_name="Other")
    character = Character(
        static_member=member, job=job, name="Other2", world="Other", kind=CharacterKind.MAIN
    )
    session.add(character)
    session.flush()
    with pytest.raises(ValueError, match="selected static"):
        set_current_balance(session, first, character, "HEAD_COFFER", 1)
