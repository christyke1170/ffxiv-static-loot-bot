"""Focused remaining-BiS-needs engine tests using fictional tier data."""

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import (
    AugmentationMaterialType,
    BisSet,
    BisSetItem,
    Character,
    CharacterAugmentationInventory,
    CharacterBisSelection,
    CharacterFloorBookBalance,
    CharacterGearSlot,
    CharacterKind,
    DiscordGuild,
    GearClassification,
    GearSlot,
    GearSlotCode,
    InventoryItem,
    Item,
    Job,
    LootCategory,
    LootType,
    RaidFloor,
    RaidTier,
    Static,
    StaticMember,
)
from app.schemas.needs import BookAvailability, NeedStatus
from app.services import calculate_character_needs, seed_reference_data


class NeedsFixture:
    """Small fictional character/tier builder that defaults unspecified slots to N/A."""

    def __init__(self, session: Session) -> None:
        self.session = session
        seed_reference_data(session)
        guild = DiscordGuild(discord_guild_id=777001, name="Needs Guild")
        static = Static(guild=guild, name="Needs Static")
        member = StaticMember(static=static, discord_user_id=777002, display_name="Needs Player")
        job = Job(abbreviation="NDS", name="Needs Test Job", role="Test")
        self.character = Character(
            static_member=member,
            job=job,
            name="Needs Character",
            world="Fictional World",
            kind=CharacterKind.MAIN,
        )
        self.tier = RaidTier(code="NEEDS_TIER", name="Fictional Needs Tier")
        self.floor_one = RaidFloor(raid_tier=self.tier, floor_number=1, name="Fictional Floor One")
        self.floor_two = RaidFloor(raid_tier=self.tier, floor_number=2, name="Fictional Floor Two")
        self.coffer_item = Item(name="Fictional Armor Coffer")
        self.coffer = LootType(
            raid_tier=self.tier,
            code="ARMOR_COFFER",
            name="Fictional Armor Coffer",
            category=LootCategory.COFFER,
            item=self.coffer_item,
        )
        self.direct_drop = LootType(
            raid_tier=self.tier,
            code="DIRECT_DROP",
            name="Fictional Direct Drop",
            category=LootCategory.GEAR,
        )
        self.material = AugmentationMaterialType(
            raid_tier=self.tier,
            code="GLOSS",
            name="Fictional Armor Gloss",
            item=Item(name="Fictional Armor Gloss"),
        )
        self.bis_set = BisSet(job=job, raid_tier=self.tier, name="Fictional Complete Slot Set")
        session.add_all([self.character, self.tier, self.bis_set])
        session.flush()
        self.slots = {
            slot.code: slot
            for slot in session.scalars(select(GearSlot).order_by(GearSlot.sort_order))
        }
        self.requirements: dict[GearSlotCode, BisSetItem] = {}

    def requirement(
        self,
        slot: GearSlotCode,
        classification: GearClassification,
        *,
        desired: Item | None = None,
        floor: RaidFloor | None = None,
        loot_type: LootType | None = None,
        base: Item | None = None,
        material: AugmentationMaterialType | None = None,
        book_cost: int | None = None,
    ) -> BisSetItem:
        row = BisSetItem(
            bis_set=self.bis_set,
            gear_slot=self.slots[slot],
            classification=classification,
            raid_floor=floor,
            loot_type=loot_type,
            augmentation_material_type=material,
            book_cost=book_cost,
        )
        self.requirements[slot] = row
        self.session.add(row)
        return row

    def finish(self, *, select_set: bool = True) -> None:
        for code in GearSlotCode:
            if code not in self.requirements:
                self.requirement(code, GearClassification.NOT_APPLICABLE)
        if select_set:
            self.session.add(
                CharacterBisSelection(
                    character=self.character, raid_tier=self.tier, bis_set=self.bis_set
                )
            )
        self.session.commit()

    def result(self):
        return calculate_character_needs(self.session, self.character.id, self.tier.id)


@pytest.fixture
def needs(session: Session) -> NeedsFixture:
    return NeedsFixture(session)


def slot_result(result, code: GearSlotCode):
    return next(row for row in result.slot_results if row.slot.code is code)


def add_equipped(
    needs: NeedsFixture,
    code: GearSlotCode,
    classification: GearClassification = GearClassification.GARBAGE,
    *,
    manual=False,
) -> None:
    needs.session.add(
        CharacterGearSlot(
            character=needs.character,
            gear_slot=needs.slots[code],
            current_classification=classification,
            manually_complete=manual,
        )
    )


def add_inventory(needs: NeedsFixture, item: Item, quantity: int = 1) -> None:
    if item is needs.coffer_item:
        needs.session.add(
            InventoryItem(character=needs.character, loot_type=needs.coffer, quantity=quantity)
        )
        return
    requirement = next(
        row
        for row in needs.requirements.values()
        if row.classification is GearClassification.AUGMENTED_TOME
        and not any(gear.gear_slot is row.gear_slot for gear in needs.character.gear_slots)
    )
    add_equipped(needs, requirement.gear_slot.code, GearClassification.TOME)


def test_no_selected_bis_set_returns_warning(needs: NeedsFixture) -> None:
    needs.finish(select_set=False)
    result = needs.result()
    assert result.selected_bis_set is None
    assert result.slot_results == []
    assert "no selected BiS set" in result.configuration_warnings[0]
    assert not result.is_full_bis


def test_unequipped_category_inventory_does_not_complete(needs: NeedsFixture) -> None:
    needs.requirement(
        GearSlotCode.HEAD,
        GearClassification.SAVAGE,
        floor=needs.floor_one,
        loot_type=needs.coffer,
    )
    needs.session.add(
        InventoryItem(
            character=needs.character,
            gear_slot=needs.slots[GearSlotCode.HEAD],
            classification=GearClassification.SAVAGE,
            quantity=1,
        )
    )
    needs.finish()

    row = slot_result(needs.result(), GearSlotCode.HEAD)
    assert row.status is NeedStatus.NEEDS_SAVAGE_DROP
    assert not row.is_complete


def test_nonmatching_category_remains_incomplete(needs: NeedsFixture) -> None:
    desired = Item(name="Unused Fictional Resource Name")
    needs.requirement(
        GearSlotCode.HEAD,
        GearClassification.SAVAGE,
        desired=desired,
        floor=needs.floor_one,
        loot_type=needs.coffer,
    )
    add_equipped(needs, GearSlotCode.HEAD)
    needs.finish()

    row = slot_result(needs.result(), GearSlotCode.HEAD)
    assert row.status is NeedStatus.NEEDS_SAVAGE_DROP
    assert row.current_classification is GearClassification.GARBAGE


def test_manual_completion_override(needs: NeedsFixture) -> None:
    desired = Item(name="Fictional Manual Desired Hat")
    needs.requirement(GearSlotCode.HEAD, GearClassification.GARBAGE, desired=desired)
    add_equipped(needs, GearSlotCode.HEAD, manual=True)
    needs.finish()
    assert slot_result(needs.result(), GearSlotCode.HEAD).status is NeedStatus.MANUALLY_COMPLETE


def test_not_applicable_is_complete_but_not_counted(needs: NeedsFixture) -> None:
    needs.finish()
    result = needs.result()
    row = slot_result(result, GearSlotCode.OFFHAND)
    assert row.status is NeedStatus.NOT_APPLICABLE and row.is_complete
    assert result.total_applicable_slot_count == 0
    assert result.complete_slot_count == 0
    assert result.is_full_bis


def test_direct_savage_need(needs: NeedsFixture) -> None:
    needs.requirement(
        GearSlotCode.WEAPON,
        GearClassification.SAVAGE,
        desired=Item(name="Fictional Savage Weapon"),
        floor=needs.floor_two,
        loot_type=needs.direct_drop,
    )
    needs.finish()
    row = slot_result(needs.result(), GearSlotCode.WEAPON)
    assert row.status is NeedStatus.NEEDS_SAVAGE_DROP
    assert not row.matching_unopened_coffer_owned


def test_matching_unopened_coffer_is_available_not_complete(needs: NeedsFixture) -> None:
    needs.requirement(
        GearSlotCode.HEAD,
        GearClassification.SAVAGE,
        desired=Item(name="Fictional Savage Helm"),
        floor=needs.floor_one,
        loot_type=needs.coffer,
    )
    add_inventory(needs, needs.coffer_item)
    needs.finish()
    result = needs.result()
    row = slot_result(result, GearSlotCode.HEAD)
    assert row.status is NeedStatus.OWNED_COFFER_AVAILABLE
    assert row.matching_unopened_coffer_owned and not row.is_complete
    assert result.owned_unopened_coffers[0].units_allocated == 1


def test_one_coffer_cannot_satisfy_two_slots(needs: NeedsFixture) -> None:
    for code in (GearSlotCode.HEAD, GearSlotCode.BODY):
        needs.requirement(
            code,
            GearClassification.SAVAGE,
            desired=Item(name=f"Fictional Savage {code.value}"),
            floor=needs.floor_one,
            loot_type=needs.coffer,
        )
    add_inventory(needs, needs.coffer_item)
    needs.finish()
    result = needs.result()
    assert slot_result(result, GearSlotCode.HEAD).status is NeedStatus.OWNED_COFFER_AVAILABLE
    assert slot_result(result, GearSlotCode.BODY).status is NeedStatus.NEEDS_SAVAGE_DROP


def add_augmented_requirement(
    needs: NeedsFixture, code: GearSlotCode, suffix: str
) -> tuple[Item, Item]:
    base = Item(name=f"Fictional Base Tome {suffix}")
    final = Item(name=f"Fictional Augmented {suffix}")
    needs.requirement(
        code,
        GearClassification.AUGMENTED_TOME,
        desired=final,
        base=base,
        material=needs.material,
    )
    return base, final


def test_base_tome_item_missing_even_when_material_owned(needs: NeedsFixture) -> None:
    add_augmented_requirement(needs, GearSlotCode.HEAD, "Hat")
    needs.session.add(
        CharacterAugmentationInventory(
            character=needs.character,
            augmentation_material_type=needs.material,
            quantity=1,
        )
    )
    needs.finish()
    row = slot_result(needs.result(), GearSlotCode.HEAD)
    assert row.status is NeedStatus.NEEDS_BASE_TOME_ITEM
    assert not row.base_tome_item_owned and row.enough_augmentation_material


def test_base_tome_item_owned_in_inventory_but_material_missing(needs: NeedsFixture) -> None:
    base, _ = add_augmented_requirement(needs, GearSlotCode.HEAD, "inventory")
    add_inventory(needs, base)
    needs.finish()
    row = slot_result(needs.result(), GearSlotCode.HEAD)
    assert row.status is NeedStatus.NEEDS_AUGMENTATION
    assert row.base_tome_item_owned and not row.enough_augmentation_material


def test_ready_to_augment(needs: NeedsFixture) -> None:
    base, _ = add_augmented_requirement(needs, GearSlotCode.HEAD, "Ready Hat")
    add_inventory(needs, base)
    needs.session.add(
        CharacterAugmentationInventory(
            character=needs.character,
            augmentation_material_type=needs.material,
            quantity=1,
        )
    )
    needs.finish()
    assert slot_result(needs.result(), GearSlotCode.HEAD).status is NeedStatus.READY_TO_AUGMENT


def test_one_augmentation_material_cannot_satisfy_multiple_slots(needs: NeedsFixture) -> None:
    for code in (GearSlotCode.HEAD, GearSlotCode.BODY):
        base, _ = add_augmented_requirement(needs, code, code.value)
        add_inventory(needs, base)
    needs.session.add(
        CharacterAugmentationInventory(
            character=needs.character,
            augmentation_material_type=needs.material,
            quantity=1,
        )
    )
    needs.finish()
    result = needs.result()
    assert slot_result(result, GearSlotCode.HEAD).status is NeedStatus.READY_TO_AUGMENT
    assert slot_result(result, GearSlotCode.BODY).status is NeedStatus.NEEDS_AUGMENTATION


def test_three_materials_needed_with_one_owned_has_aggregate_accounting(
    needs: NeedsFixture,
) -> None:
    for code in (GearSlotCode.HEAD, GearSlotCode.BODY, GearSlotCode.HANDS):
        base, _ = add_augmented_requirement(needs, code, code.value)
        add_inventory(needs, base)
    needs.session.add(
        CharacterAugmentationInventory(
            character=needs.character,
            augmentation_material_type=needs.material,
            quantity=1,
        )
    )
    needs.finish()
    aggregate = needs.result().augmentation_needs[0]
    assert aggregate.total_units_required == 3
    assert aggregate.units_owned == 1
    assert aggregate.units_allocated == 1
    assert aggregate.additional_units_needed == 2


def add_book_savage(needs: NeedsFixture, code: GearSlotCode, cost: int) -> None:
    needs.requirement(
        code,
        GearClassification.SAVAGE,
        desired=Item(name=f"Fictional Book Savage {code.value}"),
        floor=needs.floor_one,
        loot_type=needs.coffer,
        book_cost=cost,
    )


def add_books(needs: NeedsFixture, quantity: int) -> None:
    needs.session.add(
        CharacterFloorBookBalance(
            character=needs.character,
            raid_floor=needs.floor_one,
            earned=quantity + 2,
            spent=1,
            manual_adjustment=-1,
        )
    )


def test_enough_books_for_one_item(needs: NeedsFixture) -> None:
    add_book_savage(needs, GearSlotCode.HEAD, 4)
    add_books(needs, 4)
    needs.finish()
    row = slot_result(needs.result(), GearSlotCode.HEAD)
    assert row.book_availability is BookAvailability.PURCHASABLE_WITH_BOOKS
    assert row.effective_books_available == 4


def test_books_cannot_be_reused_for_multiple_items(needs: NeedsFixture) -> None:
    add_book_savage(needs, GearSlotCode.HEAD, 4)
    add_book_savage(needs, GearSlotCode.BODY, 4)
    add_books(needs, 4)
    needs.finish()
    result = needs.result()
    assert (
        slot_result(result, GearSlotCode.HEAD).book_availability
        is BookAvailability.PURCHASABLE_WITH_BOOKS
    )
    body = slot_result(result, GearSlotCode.BODY)
    assert body.book_availability is BookAvailability.NEEDS_MORE_BOOKS
    assert body.effective_books_available == 0 and body.additional_books_needed == 4


def test_additional_books_needed(needs: NeedsFixture) -> None:
    add_book_savage(needs, GearSlotCode.HEAD, 6)
    add_books(needs, 2)
    needs.finish()
    row = slot_result(needs.result(), GearSlotCode.HEAD)
    assert row.book_availability is BookAvailability.NEEDS_MORE_BOOKS
    assert row.additional_books_needed == 4
    assert needs.result().book_requirements[0].additional_books_needed == 4


def test_savage_primary_need_remains_with_book_alternative(needs: NeedsFixture) -> None:
    add_book_savage(needs, GearSlotCode.HEAD, 4)
    add_books(needs, 4)
    needs.finish()
    row = slot_result(needs.result(), GearSlotCode.HEAD)
    assert row.status is NeedStatus.NEEDS_SAVAGE_DROP
    assert row.book_availability is BookAvailability.PURCHASABLE_WITH_BOOKS


def test_full_bis_detection(needs: NeedsFixture) -> None:
    desired = Item(name="Fictional Full BiS Hat")
    needs.requirement(GearSlotCode.HEAD, GearClassification.CRAFTED_EX, desired=desired)
    add_equipped(needs, GearSlotCode.HEAD, GearClassification.CRAFTED_EX)
    needs.finish()
    result = needs.result()
    assert result.is_full_bis
    assert result.complete_slot_count == result.total_applicable_slot_count == 1


@pytest.mark.parametrize(
    ("classification", "slot"),
    [
        (GearClassification.CRAFTED_EX, GearSlotCode.LEGS),
        (GearClassification.CRAFTED_EX, GearSlotCode.WEAPON),
        (GearClassification.SAVAGE, GearSlotCode.HEAD),
        (GearClassification.TOME, GearSlotCode.HANDS),
        (GearClassification.AUGMENTED_TOME, GearSlotCode.BODY),
    ],
)
def test_same_source_different_item_is_complete(needs: NeedsFixture, classification, slot) -> None:
    desired = Item(name="Desired source item")
    if classification is GearClassification.SAVAGE:
        needs.requirement(
            slot,
            classification,
            desired=desired,
            floor=needs.floor_one,
            loot_type=needs.direct_drop,
        )
    elif classification is GearClassification.AUGMENTED_TOME:
        needs.requirement(
            slot,
            classification,
            desired=desired,
            base=Item(name="Matching base Tome"),
            material=needs.material,
        )
    else:
        needs.requirement(slot, classification, desired=desired)
    add_equipped(needs, slot, classification)
    needs.finish()

    result = needs.result()
    row = slot_result(result, slot)
    assert row.status is NeedStatus.COMPLETE
    assert result.complete_slot_count == result.total_applicable_slot_count == 1
    assert result.is_full_bis
    assert result.savage_loot_needs == []
    assert result.augmentation_needs == []


def test_remaining_needs_grouped_by_floor_and_loot_type(needs: NeedsFixture) -> None:
    for code in (GearSlotCode.HEAD, GearSlotCode.BODY):
        add_book_savage(needs, code, 4)
    needs.finish()
    grouped = needs.result().savage_loot_needs
    assert len(grouped) == 1
    assert grouped[0].raid_floor is needs.floor_one
    assert grouped[0].loot_type is needs.coffer
    assert grouped[0].quantity == 2


def test_cross_tier_invalid_configuration_returns_warning(needs: NeedsFixture) -> None:
    add_book_savage(needs, GearSlotCode.HEAD, 4)
    needs.finish()
    other = RaidTier(code="OTHER_NEEDS", name="Other Fictional Tier")
    other_floor = RaidFloor(raid_tier=other, floor_number=1, name="Other Floor")
    needs.session.add(other)
    needs.session.commit()
    needs.session.execute(
        text("UPDATE bis_set_items SET raid_floor_id = :floor WHERE id = :requirement"),
        {"floor": other_floor.id, "requirement": needs.requirements[GearSlotCode.HEAD].id},
    )
    needs.session.commit()
    needs.session.expire_all()

    result = needs.result()
    row = slot_result(result, GearSlotCode.HEAD)
    assert row.status is NeedStatus.INVALID_CONFIGURATION
    assert any("another raid tier" in warning for warning in result.configuration_warnings)


def test_missing_required_slots_returns_validation_warnings(needs: NeedsFixture) -> None:
    desired = Item(name="Only Fictional Requirement")
    needs.requirement(GearSlotCode.HEAD, GearClassification.GARBAGE, desired=desired)
    needs.session.add(
        CharacterBisSelection(
            character=needs.character, raid_tier=needs.tier, bis_set=needs.bis_set
        )
    )
    needs.session.commit()
    result = needs.result()
    assert len(result.slot_results) == 12
    assert slot_result(result, GearSlotCode.BODY).status is NeedStatus.INVALID_CONFIGURATION
    assert any("no requirement" in warning for warning in result.configuration_warnings)


def test_service_performs_no_database_writes(needs: NeedsFixture, engine: Engine) -> None:
    add_book_savage(needs, GearSlotCode.HEAD, 4)
    add_books(needs, 4)
    needs.finish()
    writes: list[str] = []

    def record_writes(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", record_writes)
    try:
        needs.result()
    finally:
        event.remove(engine, "before_cursor_execute", record_writes)
    assert writes == []
    assert needs.session.scalar(select(func.count()).select_from(Character)) == 1
