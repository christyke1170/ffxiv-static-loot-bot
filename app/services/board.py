"""Build full static gear boards from the authoritative needs engine."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Character,
    CharacterFloorBookBalance,
    CharacterGearSlot,
    CharacterKind,
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
from app.schemas.needs import NeedStatus, SlotNeedResult
from app.services.gearboard import classify_gear_state
from app.services.item_level import calculate_roster_item_levels
from app.services.needs import calculate_character_needs


def display_status(result: SlotNeedResult) -> DisplayStatus:
    return classify_gear_state(result)


def build_static_gear_board(session: Session, static_id: int) -> StaticGearBoard:
    static = session.scalar(
        select(Static)
        .where(Static.id == static_id)
        .options(
            selectinload(Static.members).selectinload(StaticMember.characters),
            selectinload(Static.active_raid_tier),
        )
    )
    if static is None or not static.active:
        raise LookupError("The selected static is stale or has been deleted.")
    if static.active_raid_tier is None:
        raise ValueError("The selected static has no active raid tier.")
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
            static.active_raid_tier_id,
            item_levels[character.id],
        )
        for character in mains[:8]
    )
    return StaticGearBoard(
        static.id,
        static.name,
        static.guild.discord_guild_id,
        static.active_raid_tier.id,
        static.active_raid_tier.name,
        tuple(member.discord_user_id for member in static.members if member.active),
        players,
        datetime.now(UTC),
        tuple(warnings),
        _current_week_number(session, static.id),
    )


def _current_week_number(session: Session, static_id: int) -> int | None:
    """Return the working-static week number; the first reclear is Week 2."""
    count = session.scalar(
        select(func.count()).select_from(ReclearWeek).where(ReclearWeek.static_id == static_id)
    )
    return 2 + (count or 0)


def _build_player(session: Session, character: Character, tier_id: int, item_level) -> BoardPlayer:
    needs = calculate_character_needs(session, character.id, tier_id)
    gear = {
        row.gear_slot_id: row
        for row in session.scalars(
            select(CharacterGearSlot).where(CharacterGearSlot.character_id == character.id)
        )
    }
    slots = tuple(
        BoardSlot(
            result.slot.code,
            result.slot.display_name,
            result.slot.sort_order,
            result.desired_classification,
            result.current_classification,
            result.status,
            display_status(result),
            result.required_raid_floor.floor_number if result.required_raid_floor else None,
            result.required_loot_type.name if result.required_loot_type else None,
            gear[result.slot.id].updated_at if result.slot.id in gear else None,
            result.explanation,
            result.required_loot_type.code if result.required_loot_type else None,
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
                NeedStatus.INVALID_CONFIGURATION,
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
                "No selected BiS set is configured.",
                None,
            )
            for slot in all_slots
        )
    balances = {
        row.raid_floor_id: row
        for row in session.scalars(
            select(CharacterFloorBookBalance).where(
                CharacterFloorBookBalance.character_id == character.id
            )
        )
    }
    requirements = {row.raid_floor.id: row for row in needs.book_requirements}
    books = tuple(
        BoardBook(
            floor.floor_number,
            balances[floor.id].earned if floor.id in balances else 0,
            balances[floor.id].spent if floor.id in balances else 0,
            balances[floor.id].manual_adjustment if floor.id in balances else 0,
            balances[floor.id].available if floor.id in balances else 0,
            requirements[floor.id].additional_books_needed if floor.id in requirements else 0,
        )
        for floor in sorted(needs.raid_tier.floors, key=lambda value: value.floor_number)
    )
    owned = {row.material.id: row.units_owned for row in needs.materials_owned}
    required = {row.material.id: row.additional_units_needed for row in needs.augmentation_needs}
    materials = tuple(
        BoardMaterial(
            material.code, material.name, owned.get(material.id, 0), required.get(material.id, 0)
        )
        for material in sorted(
            needs.raid_tier.augmentation_material_types, key=lambda value: value.code
        )
    )
    selection = needs.selected_bis_set
    return BoardPlayer(
        character.id,
        character.static_member.display_name,
        character.name,
        character.world,
        character.kind,
        selection.job.abbreviation if selection else None,
        selection.name if selection else None,
        selection.gear_set_url if selection else None,
        slots,
        books,
        materials,
        needs.complete_slot_count,
        needs.total_applicable_slot_count,
        item_level.average_item_level,
        item_level.warnings,
        tuple(needs.configuration_warnings),
    )
