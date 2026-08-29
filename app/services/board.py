"""Build full static gear boards from the authoritative needs engine."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Character,
    CharacterGearSlot,
    CharacterKind,
    GearClassification,
    GearSlot,
    GearSlotCode,
    ReclearWeek,
    Static,
    StaticMember,
)
from app.schemas.board import (
    BoardBook,
    BoardMaterial,
    BoardPlayer,
    BoardSlot,
    DisplayStatus,
    StaticGearBoard,
)
from app.schemas.needs_v2 import NeedsV2Status
from app.services.item_level import calculate_roster_item_levels
from app.services.needs_state import load_character_needs_state
from app.services.needs_v2 import calculate_character_needs_v2


def display_status(result) -> DisplayStatus:
    status = result.status
    current = getattr(result, "current", getattr(result, "current_classification", None))
    slot_code = getattr(result, "gear_slot", getattr(result, "slot", None))
    if (
        getattr(slot_code, "code", slot_code) is GearSlotCode.OFFHAND
        and getattr(result, "character", None) is not None
        and getattr(result.character, "job", None) is not None
        and not result.character.job.uses_offhand
    ):
        return DisplayStatus.NA
    status_value = getattr(status, "value", status)
    if status_value == "NOT_APPLICABLE" and current is GearClassification.GARBAGE:
        return DisplayStatus.NEEDS_REPLACEMENT
    if not isinstance(status, NeedsV2Status):
        if status_value in {"COMPLETE", "MANUALLY_COMPLETE"}:
            return DisplayStatus.BIS
        if status_value == "INVALID_CONFIGURATION":
            return DisplayStatus.NEEDS_REPLACEMENT
        if current is GearClassification.CRAFTED_EX:
            return DisplayStatus.CRAFTED_EX
        if current is GearClassification.TOME:
            return DisplayStatus.TOME_NEEDS_AUGMENT
        if current in {GearClassification.SAVAGE, GearClassification.AUGMENTED_TOME}:
            return DisplayStatus.ALTERNATE
        return DisplayStatus.NEEDS_REPLACEMENT
    if status in {NeedsV2Status.COMPLETE, NeedsV2Status.MANUALLY_COMPLETE}:
        return DisplayStatus.BIS
    if getattr(status, "value", status) == "NOT_APPLICABLE":
        return DisplayStatus.NA
    if getattr(status, "value", status) == "INVALID_CONFIGURATION":
        return DisplayStatus.NEEDS_REPLACEMENT
    if current is GearClassification.CRAFTED_EX:
        return DisplayStatus.CRAFTED_EX
    if current is GearClassification.TOME:
        return DisplayStatus.TOME_NEEDS_AUGMENT
    if current in {GearClassification.SAVAGE, GearClassification.AUGMENTED_TOME}:
        return DisplayStatus.ALTERNATE
    return DisplayStatus.NEEDS_REPLACEMENT


def build_static_gear_board(session: Session, static_id: int) -> StaticGearBoard:
    static = session.scalar(
        select(Static)
        .where(Static.id == static_id)
        .options(
            selectinload(Static.members).selectinload(StaticMember.characters),
        )
    )
    if static is None or not static.active:
        raise LookupError("The selected static is stale or has been deleted.")
    mains = sorted(
        (
            character
            for member in static.members
            if member.active
            for character in member.characters
            if character.active and character.kind is CharacterKind.MAIN
        ),
        key=lambda character: (character.static_member_id, character.id),
    )
    warnings = []
    if len(mains) != 8:
        warnings.append(f"Expected 8 active mains; found {len(mains)}.")
    if len(mains) > 8:
        warnings.append("Only the first 8 ordered active mains are displayed.")
    item_levels = calculate_roster_item_levels(session, static.id)
    players = tuple(
        _build_player(
            session,
            character,
            item_levels[character.id],
        )
        for character in mains[:8]
    )
    return StaticGearBoard(
        static.id,
        static.name,
        static.guild.discord_guild_id,
        tuple(member.discord_user_id for member in static.members if member.active),
        players,
        datetime.now(UTC),
        tuple(warnings),
        _current_week_number(session, static.id),
    )


def _current_week_number(session: Session, static_id: int) -> int | None:
    count = session.scalar(
        select(func.count()).select_from(ReclearWeek).where(ReclearWeek.static_id == static_id)
    )
    return 2 + (count or 0)


def _build_player(session: Session, character: Character, item_level) -> BoardPlayer:
    state = load_character_needs_state(session, character.id)
    needs = calculate_character_needs_v2(session, character.id)
    gear = {
        row.gear_slot_id: row
        for row in session.scalars(
            select(CharacterGearSlot).where(CharacterGearSlot.character_id == character.id)
        )
    }
    slots = tuple(
        BoardSlot(
            result.gear_slot,
            result.slot_name,
            result.sort_order,
            result.desired,
            result.current,
            result.status,
            display_status(result),
            result.required_floor_number,
            result.required_loot_type_code,
            gear[result.gear_slot_id].updated_at if result.gear_slot_id in gear else None,
            result.explanation,
            result.required_loot_type_code,
        )
        for result in needs.slot_results
    )
    if not slots:
        all_slots = list(session.scalars(select(GearSlot).order_by(GearSlot.sort_order)))
        slots = tuple(
            BoardSlot(
                slot.code,
                slot.display_name,
                slot.sort_order,
                None,
                gear[slot.id].current_classification if slot.id in gear else None,
                NeedsV2Status.INVALID_CONFIGURATION,
                (
                    DisplayStatus.NA
                    if slot.code is GearSlotCode.OFFHAND
                    and character.job
                    and not character.job.uses_offhand
                    else DisplayStatus.NEEDS_REPLACEMENT
                ),
                None,
                None,
                gear[slot.id].updated_at if slot.id in gear else None,
                "No Static + Job BiS set is configured.",
                None,
            )
            for slot in all_slots
        )
    books_owned = dict(state.books)
    books = tuple(
        BoardBook(
            floor.floor_number,
            0,
            0,
            0,
            books_owned.get(floor.floor_number, 0),
        )
        for floor in sorted(state.floors, key=lambda value: value.floor_number)
    )
    owned = dict(state.material_quantities)
    required = {row.material_type_id: row.additional_needed for row in needs.material_needs}
    materials = tuple(
        BoardMaterial(
            material.code,
            material.name,
            owned.get(material.material_type_id, 0),
            required.get(material.material_type_id, 0),
        )
        for material in sorted(state.materials, key=lambda value: value.code)
    )
    return BoardPlayer(
        character.id,
        character.static_member.display_name,
        character.name,
        character.world,
        character.kind,
        state.job_abbreviation,
        state.bis_set_name,
        slots,
        books,
        materials,
        needs.complete_slot_count,
        needs.applicable_slot_count,
        item_level.average_item_level,
        item_level.warnings,
        tuple(needs.configuration_warnings),
    )
