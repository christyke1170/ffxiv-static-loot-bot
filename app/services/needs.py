"""Read-only remaining-BiS-needs calculation."""

from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import (
    BisSet,
    BisSetItem,
    Character,
    CharacterAugmentationInventory,
    CharacterBisSelection,
    CharacterFloorBookBalance,
    CharacterGearSlot,
    GearClassification,
    GearSlot,
    GearSlotCode,
    LootCategory,
    LootType,
    RaidFloor,
    RaidTier,
)
from app.schemas.needs import (
    AugmentationNeed,
    BookAvailability,
    BookRequirement,
    CharacterNeedsResult,
    MaterialOwnership,
    NeedStatus,
    OwnedCofferAvailability,
    SavageLootNeed,
    SlotNeedResult,
)


@dataclass(slots=True)
class _LoadedState:
    character: Character
    tier: RaidTier
    selection: CharacterBisSelection | None
    slots: list[GearSlot]
    material_owned: dict[int, int]
    books_owned: dict[int, int]


def calculate_character_needs(
    session: Session, character_id: int, raid_tier_id: int
) -> CharacterNeedsResult:
    """Calculate one character's selected BiS needs without writing or autoflushing."""
    with session.no_autoflush:
        state = _load_state(session, character_id, raid_tier_id)
        return _calculate(state)


def calculate_characters_needs(
    session: Session,
    character_ids: list[int] | tuple[int, ...],
    raid_tier_id: int,
    *,
    include_books: bool = True,
) -> dict[int, CharacterNeedsResult]:
    """Calculate multiple characters with shared eager loads and no writes."""
    ordered_ids = list(dict.fromkeys(character_ids))
    if not ordered_ids:
        return {}
    with session.no_autoflush:
        characters = list(
            session.scalars(
                select(Character)
                .where(Character.id.in_(ordered_ids))
                .execution_options(populate_existing=True)
                .options(
                    selectinload(Character.gear_slots).joinedload(CharacterGearSlot.gear_slot),
                    selectinload(Character.inventory_items),
                )
            )
        )
        by_id = {character.id: character for character in characters}
        missing = [character_id for character_id in ordered_ids if character_id not in by_id]
        if missing:
            raise LookupError(f"unknown character id {missing[0]}")
        tier = session.scalar(
            select(RaidTier)
            .where(RaidTier.id == raid_tier_id)
            .options(
                selectinload(RaidTier.floors).selectinload(RaidFloor.loot_rules),
                selectinload(RaidTier.loot_types).joinedload(LootType.item),
                selectinload(RaidTier.augmentation_material_types),
            )
        )
        if tier is None:
            raise LookupError(f"unknown raid tier id {raid_tier_id}")
        selections = {
            row.character_id: row
            for row in session.scalars(
                select(CharacterBisSelection)
                .where(
                    CharacterBisSelection.character_id.in_(ordered_ids),
                    CharacterBisSelection.raid_tier_id == raid_tier_id,
                )
                .options(
                    joinedload(CharacterBisSelection.bis_set)
                    .selectinload(BisSet.items)
                    .options(
                        joinedload(BisSetItem.gear_slot),
                        joinedload(BisSetItem.raid_floor),
                        joinedload(BisSetItem.loot_type).joinedload(LootType.item),
                        joinedload(BisSetItem.augmentation_material_type),
                    )
                )
            )
        }
        slots = list(session.scalars(select(GearSlot).order_by(GearSlot.sort_order)))
        material_rows = session.scalars(
            select(CharacterAugmentationInventory).where(
                CharacterAugmentationInventory.character_id.in_(ordered_ids)
            )
        )
        materials: dict[int, dict[int, int]] = defaultdict(dict)
        for row in material_rows:
            materials[row.character_id][row.augmentation_material_type_id] = row.quantity
        books: dict[int, dict[int, int]] = defaultdict(dict)
        if include_books:
            for row in session.scalars(
                select(CharacterFloorBookBalance).where(
                    CharacterFloorBookBalance.character_id.in_(ordered_ids)
                )
            ):
                books[row.character_id][row.raid_floor_id] = max(row.available, 0)
        return {
            character_id: _calculate(
                _LoadedState(
                    by_id[character_id],
                    tier,
                    selections.get(character_id),
                    slots,
                    materials[character_id],
                    books[character_id],
                )
            )
            for character_id in ordered_ids
        }


def _load_state(session: Session, character_id: int, raid_tier_id: int) -> _LoadedState:
    character = session.scalar(
        select(Character)
        .where(Character.id == character_id)
        .execution_options(populate_existing=True)
        .options(
            selectinload(Character.gear_slots).joinedload(CharacterGearSlot.gear_slot),
            selectinload(Character.inventory_items),
        )
    )
    if character is None:
        raise LookupError(f"unknown character id {character_id}")

    tier = session.scalar(
        select(RaidTier)
        .where(RaidTier.id == raid_tier_id)
        .options(
            selectinload(RaidTier.floors).selectinload(RaidFloor.loot_rules),
            selectinload(RaidTier.loot_types).joinedload(LootType.item),
            selectinload(RaidTier.augmentation_material_types),
        )
    )
    if tier is None:
        raise LookupError(f"unknown raid tier id {raid_tier_id}")

    selection = session.scalar(
        select(CharacterBisSelection)
        .where(
            CharacterBisSelection.character_id == character_id,
            CharacterBisSelection.raid_tier_id == raid_tier_id,
        )
        .options(
            joinedload(CharacterBisSelection.bis_set)
            .selectinload(BisSet.items)
            .options(
                joinedload(BisSetItem.gear_slot),
                joinedload(BisSetItem.raid_floor),
                joinedload(BisSetItem.loot_type).joinedload(LootType.item),
                joinedload(BisSetItem.augmentation_material_type),
            )
        )
    )
    slots = list(session.scalars(select(GearSlot).order_by(GearSlot.sort_order)))
    material_owned = {
        row.augmentation_material_type_id: row.quantity
        for row in session.scalars(
            select(CharacterAugmentationInventory).where(
                CharacterAugmentationInventory.character_id == character_id
            )
        )
    }
    books_owned = {
        row.raid_floor_id: max(row.available, 0)
        for row in session.scalars(
            select(CharacterFloorBookBalance).where(
                CharacterFloorBookBalance.character_id == character_id
            )
        )
    }
    return _LoadedState(character, tier, selection, slots, material_owned, books_owned)


def _calculate(state: _LoadedState) -> CharacterNeedsResult:
    warnings: list[str] = []
    if state.selection is None:
        warnings.append(
            f"Character {state.character.name} has no selected BiS set for tier {state.tier.name}."
        )
        return CharacterNeedsResult(
            character=state.character,
            raid_tier=state.tier,
            selected_bis_set=None,
            slot_results=[],
            complete_slot_count=0,
            total_applicable_slot_count=0,
            is_full_bis=False,
            savage_loot_needs=[],
            augmentation_needs=[],
            materials_owned=_material_ownership(state),
            book_requirements=[],
            owned_unopened_coffers=[],
            configuration_warnings=warnings,
        )

    bis_set = state.selection.bis_set
    if bis_set.raid_tier_id != state.tier.id:
        warnings.append("Selected BiS set belongs to another raid tier.")

    requirements: dict[int, list[BisSetItem]] = defaultdict(list)
    for requirement in bis_set.items:
        requirements[requirement.gear_slot_id].append(requirement)

    inventory = Counter(
        {
            row.loot_type_id: row.quantity
            for row in state.character.inventory_items
            if row.loot_type_id is not None and row.quantity > 0
        }
    )
    equipped = {row.gear_slot_id: row for row in state.character.gear_slots}
    remaining_materials = dict(state.material_owned)
    remaining_books = dict(state.books_owned)
    remaining_coffers = Counter(inventory)
    results: list[SlotNeedResult] = []

    for slot in state.slots:
        rows = requirements.get(slot.id, [])
        if len(rows) != 1:
            message = (
                f"BiS set {bis_set.name} has no requirement for {slot.display_name}."
                if not rows
                else f"BiS set {bis_set.name} has duplicate requirements for {slot.display_name}."
            )
            warnings.append(message)
            results.append(
                SlotNeedResult(
                    character=state.character,
                    bis_set=bis_set,
                    slot=slot,
                    desired_classification=None,
                    current_classification=equipped.get(slot.id).current_classification
                    if slot.id in equipped
                    else None,
                    status=NeedStatus.INVALID_CONFIGURATION,
                    explanation=message,
                    validation_warnings=[message],
                )
            )
            continue
        result = _calculate_slot(
            state,
            bis_set,
            rows[0],
            equipped.get(slot.id),
            inventory,
            remaining_materials,
            remaining_coffers,
        )
        _allocate_books(result, remaining_books)
        results.append(result)
        warnings.extend(result.validation_warnings)

    complete = sum(result.is_complete and result.is_applicable for result in results)
    applicable = sum(result.is_applicable for result in results)
    return CharacterNeedsResult(
        character=state.character,
        raid_tier=state.tier,
        selected_bis_set=bis_set,
        slot_results=results,
        complete_slot_count=complete,
        total_applicable_slot_count=applicable,
        is_full_bis=complete == applicable,
        savage_loot_needs=_group_savage(results),
        augmentation_needs=_group_augmentation(results, state.material_owned),
        materials_owned=_material_ownership(state),
        book_requirements=_group_books(results, state.books_owned),
        owned_unopened_coffers=_group_coffers(results, inventory),
        configuration_warnings=list(dict.fromkeys(warnings)),
    )


def _calculate_slot(
    state: _LoadedState,
    bis_set: BisSet,
    requirement: BisSetItem,
    gear: object | None,
    inventory: Counter[int],
    remaining_materials: dict[int, int],
    remaining_coffers: Counter[int],
) -> SlotNeedResult:
    manually_complete = bool(getattr(gear, "manually_complete", False))
    result = SlotNeedResult(
        character=state.character,
        bis_set=bis_set,
        slot=requirement.gear_slot,
        desired_classification=requirement.classification,
        current_classification=getattr(gear, "current_classification", None),
        status=NeedStatus.NEEDS_CATEGORY,
        required_raid_floor=requirement.raid_floor,
        required_loot_type=requirement.loot_type,
        required_augmentation_material=requirement.augmentation_material_type,
        book_cost=_book_cost(state.tier, requirement),
    )
    _validate_requirement(state.tier, requirement, result.validation_warnings)
    if result.validation_warnings:
        result.status = NeedStatus.INVALID_CONFIGURATION
        result.explanation = "Invalid BiS requirement configuration."
        return result
    if requirement.classification is GearClassification.NOT_APPLICABLE:
        result.status = NeedStatus.NOT_APPLICABLE
        result.explanation = "This slot is not applicable to the selected BiS set."
        return result
    if manually_complete:
        result.status = NeedStatus.MANUALLY_COMPLETE
        result.explanation = "This slot is manually marked complete."
        return result
    current = getattr(gear, "current_classification", None)
    if current is requirement.classification:
        result.status = NeedStatus.COMPLETE
        result.explanation = "Current gear exactly matches the desired category."
        return result

    if requirement.classification is GearClassification.SAVAGE:
        _set_savage_need(result, remaining_coffers)
    elif requirement.classification is GearClassification.AUGMENTED_TOME:
        _set_augmentation_need(result, current, remaining_materials)
    else:
        result.explanation = "Current gear does not match the desired category."
    return result


def _validate_requirement(tier: RaidTier, requirement: BisSetItem, warnings: list[str]) -> None:
    prefix = requirement.gear_slot.display_name
    if requirement.gear_slot.code is GearSlotCode.OFFHAND:
        job = requirement.bis_set.job
        uses_offhand = job.uses_offhand
        if uses_offhand and requirement.classification is GearClassification.NOT_APPLICABLE:
            warnings.append(f"{prefix}: offhand-capable job requires an applicable category.")
        elif (
            not uses_offhand and requirement.classification is not GearClassification.NOT_APPLICABLE
        ):
            warnings.append(f"{prefix}: {job.abbreviation} offhand must be NOT_APPLICABLE.")
    if (
        requirement.classification is GearClassification.AUGMENTED_TOME
        and requirement.augmentation_material_type_id is None
    ):
        warnings.append(f"{prefix}: augmented tome requirement has no material type.")
    for reference, label in (
        (requirement.raid_floor, "raid floor"),
        (requirement.loot_type, "loot type"),
        (requirement.augmentation_material_type, "augmentation material"),
    ):
        if reference is not None and reference.raid_tier_id != tier.id:
            warnings.append(f"{prefix}: {label} belongs to another raid tier.")
    if requirement.book_cost is not None and requirement.raid_floor_id is None:
        warnings.append(f"{prefix}: book cost exists without a raid floor.")
    if requirement.classification is GearClassification.SAVAGE:
        if requirement.raid_floor_id is None:
            warnings.append(f"{prefix}: Savage requirement has no raid floor.")
        if requirement.loot_type_id is None:
            warnings.append(f"{prefix}: Savage requirement has no loot type.")


def _book_cost(tier: RaidTier, requirement: BisSetItem) -> int | None:
    if requirement.book_cost is not None:
        return requirement.book_cost
    if requirement.raid_floor_id is None or requirement.loot_type_id is None:
        return None
    return next(
        (
            rule.book_cost
            for floor in tier.floors
            for rule in floor.loot_rules
            if rule.raid_floor_id == requirement.raid_floor_id
            and rule.loot_type_id == requirement.loot_type_id
        ),
        None,
    )


def _set_savage_need(result: SlotNeedResult, remaining_coffers: Counter[int]) -> None:
    loot_type = result.required_loot_type
    if loot_type is None or loot_type.category is not LootCategory.COFFER:
        result.status = NeedStatus.NEEDS_SAVAGE_DROP
        result.explanation = "The Savage category is still required."
        return
    if remaining_coffers[loot_type.id] > 0:
        remaining_coffers[loot_type.id] -= 1
        result.matching_unopened_coffer_owned = True
        result.status = NeedStatus.OWNED_COFFER_AVAILABLE
        result.explanation = "A matching unopened coffer is owned but has not been redeemed."
    else:
        result.status = NeedStatus.NEEDS_SAVAGE_DROP
        result.explanation = "The required Savage coffer is not owned."


def _set_augmentation_need(
    result: SlotNeedResult,
    current: GearClassification | None,
    remaining_materials: dict[int, int],
) -> None:
    result.base_tome_item_owned = current is GearClassification.TOME
    material_id = result.required_augmentation_material.id  # validated as present
    available = remaining_materials.get(material_id, 0)
    result.enough_augmentation_material = available > 0
    if available > 0:
        remaining_materials[material_id] = available - 1
    if not result.base_tome_item_owned:
        result.status = NeedStatus.NEEDS_BASE_TOME_ITEM
        result.explanation = "The current category is not Tome; the base Tome item is missing."
    elif result.enough_augmentation_material:
        result.status = NeedStatus.READY_TO_AUGMENT
        result.explanation = "The base tome item and an allocated augmentation material are owned."
    else:
        result.status = NeedStatus.NEEDS_AUGMENTATION
        result.explanation = "The base tome item is owned, but no unallocated material remains."


def _allocate_books(result: SlotNeedResult, remaining_books: dict[int, int]) -> None:
    if result.is_complete or result.book_cost is None or result.required_raid_floor is None:
        return
    floor_id = result.required_raid_floor.id
    available = remaining_books.get(floor_id, 0)
    result.effective_books_available = available
    if available >= result.book_cost:
        result.book_availability = BookAvailability.PURCHASABLE_WITH_BOOKS
        remaining_books[floor_id] = available - result.book_cost
    else:
        result.book_availability = BookAvailability.NEEDS_MORE_BOOKS
        result.additional_books_needed = result.book_cost - available
        remaining_books[floor_id] = 0


def _group_savage(results: list[SlotNeedResult]) -> list[SavageLootNeed]:
    grouped: dict[tuple[int, int], list[SlotNeedResult]] = defaultdict(list)
    for result in results:
        if (
            not result.is_complete
            and result.desired_classification is GearClassification.SAVAGE
            and result.required_raid_floor is not None
            and result.required_loot_type is not None
            and result.status is not NeedStatus.INVALID_CONFIGURATION
        ):
            grouped[(result.required_raid_floor.id, result.required_loot_type.id)].append(result)
    return [
        SavageLootNeed(
            rows[0].required_raid_floor,
            rows[0].required_loot_type,
            len(rows),
            [result.slot for result in rows],
        )
        for rows in grouped.values()
    ]


def _group_augmentation(
    results: list[SlotNeedResult], owned: dict[int, int]
) -> list[AugmentationNeed]:
    grouped: dict[int, list[SlotNeedResult]] = defaultdict(list)
    for result in results:
        if (
            not result.is_complete
            and result.desired_classification is GearClassification.AUGMENTED_TOME
            and result.required_augmentation_material is not None
            and result.status is not NeedStatus.INVALID_CONFIGURATION
        ):
            grouped[result.required_augmentation_material.id].append(result)
    values = []
    for material_id, rows in grouped.items():
        units_owned = owned.get(material_id, 0)
        allocated = min(units_owned, len(rows))
        values.append(
            AugmentationNeed(
                rows[0].required_augmentation_material,
                len(rows),
                units_owned,
                allocated,
                max(len(rows) - units_owned, 0),
                [row.slot for row in rows],
            )
        )
    return values


def _material_ownership(state: _LoadedState) -> list[MaterialOwnership]:
    return [
        MaterialOwnership(material, state.material_owned.get(material.id, 0))
        for material in state.tier.augmentation_material_types
        if state.material_owned.get(material.id, 0) > 0
    ]


def _group_books(results: list[SlotNeedResult], owned: dict[int, int]) -> list[BookRequirement]:
    grouped: dict[int, list[SlotNeedResult]] = defaultdict(list)
    for result in results:
        if not result.is_complete and result.book_cost is not None and result.required_raid_floor:
            grouped[result.required_raid_floor.id].append(result)
    values = []
    for floor_id, rows in grouped.items():
        total = sum(row.book_cost or 0 for row in rows)
        available = owned.get(floor_id, 0)
        values.append(
            BookRequirement(
                rows[0].required_raid_floor,
                total,
                available,
                min(total, available),
                max(total - available, 0),
                [row.slot for row in rows],
            )
        )
    return values


def _group_coffers(
    results: list[SlotNeedResult], inventory: Counter[int]
) -> list[OwnedCofferAvailability]:
    grouped: dict[int, list[SlotNeedResult]] = defaultdict(list)
    for result in results:
        if result.matching_unopened_coffer_owned and result.required_loot_type is not None:
            grouped[result.required_loot_type.id].append(result)
    return [
        OwnedCofferAvailability(
            rows[0].required_loot_type,
            inventory[rows[0].required_loot_type.id],
            len(rows),
            [row.slot for row in rows],
        )
        for rows in grouped.values()
    ]
