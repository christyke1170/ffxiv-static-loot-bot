"""Current gear, resources, auditing, and current-state import tests."""

import json

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditLog,
    AugmentationMaterialType,
    Character,
    CharacterAugmentationInventory,
    CharacterFloorBookBalance,
    CharacterGearSlot,
    CharacterKind,
    DiscordGuild,
    GearClassification,
    GearSlot,
    GearSlotCode,
    InventoryItem,
    Job,
    RaidFloor,
    RaidTier,
    Static,
    StaticMember,
)
from app.services.gear import (
    clear_gear,
    import_current_state,
    set_augmentation_material,
    set_available_books,
    set_books,
    set_gear,
    set_inventory,
    set_manual_completion,
)
from app.services.imports import ImportValidationError


@pytest.fixture
def gear_state(session):
    guild = DiscordGuild(discord_guild_id=123, name="Fictional Guild")
    tier = RaidTier(code="CURRENT", name="Fictional Current Tier")
    floor = RaidFloor(raid_tier=tier, floor_number=1, name="Fictional One")
    material = AugmentationMaterialType(raid_tier=tier, code="TWINE", name="Fictional Twine")
    static = Static(guild=guild, name="Fictional Static", active_raid_tier=tier)
    member = StaticMember(static=static, discord_user_id=10, display_name="Melon")
    job = Job(abbreviation="PLD", name="Paladin", role="Tank")
    character = Character(
        static_member=member, job=job, name="Melon Hero", world="Sample", kind=CharacterKind.MAIN
    )
    slot = GearSlot(code=GearSlotCode.HEAD, display_name="Head", sort_order=1)
    session.add_all([static, character, slot, floor, material])
    session.commit()
    return static, character, slot, floor, material


def test_current_classification_persists_separately(session, gear_state):
    static, character, slot, *_ = gear_state
    row = set_gear(session, static, character, slot, GearClassification.CRAFTED, 99)
    session.commit()
    session.expire_all()
    persisted = session.get(CharacterGearSlot, row.id)
    assert persisted.current_classification is GearClassification.CRAFTED
    assert not hasattr(persisted, "item_id")


def test_ex_weapon_is_only_valid_for_weapon_slot(session, gear_state):
    static, character, *_ = gear_state
    weapon = GearSlot(code=GearSlotCode.WEAPON, display_name="Weapon", sort_order=0)
    offhand = GearSlot(code=GearSlotCode.OFFHAND, display_name="Offhand", sort_order=2)
    session.add_all([weapon, offhand])
    session.flush()
    row = set_gear(session, static, character, weapon, GearClassification.EX_WEAPON, 99)
    assert row.current_classification is GearClassification.EX_WEAPON
    with pytest.raises(ValueError, match="EX_WEAPON is only valid"):
        set_gear(session, static, character, offhand, GearClassification.EX_WEAPON, 99)


@pytest.mark.parametrize("slot", list(GearSlotCode))
def test_current_state_import_rejects_ex_weapon_outside_weapon(session, gear_state, slot):
    if slot is GearSlotCode.WEAPON:
        pytest.skip("EX_WEAPON is valid for Weapon")
    static, character, *_ = gear_state
    data = import_data()
    data["characters"][0]["gear_slots"] = [
        {
            "slot": slot.name,
            "current_classification": "EX_WEAPON",
        }
    ]
    with pytest.raises(ImportValidationError, match="EX_WEAPON is only valid for Weapon"):
        import_current_state(session, static, data, 99, dry_run=True)


def test_gear_set_change_clears_manual_completion(session, gear_state):
    static, character, slot, *_ = gear_state
    row = set_gear(session, static, character, slot, GearClassification.CRAFTED, 99)
    row.manually_complete = True
    set_gear(session, static, character, slot, GearClassification.SAVAGE, 99)
    assert row.current_classification is GearClassification.SAVAGE
    assert not row.manually_complete


def test_gear_complete_and_uncomplete_are_audited(session, gear_state):
    static, character, slot, *_ = gear_state
    row = set_gear(session, static, character, slot, GearClassification.GARBAGE, 99)
    set_manual_completion(session, static, character, slot, True, 99, "accepted substitute")
    assert row.manually_complete
    set_manual_completion(session, static, character, slot, False, 99, "override removed")
    assert not row.manually_complete
    actions = list(session.scalars(select(AuditLog.action).order_by(AuditLog.id)))
    assert actions[-2:] == ["GEAR_MANUAL_COMPLETE", "GEAR_MANUAL_UNCOMPLETE"]


def test_gear_clear_removes_state_but_retains_audit(session, gear_state):
    static, character, slot, *_ = gear_state
    set_gear(session, static, character, slot, GearClassification.GARBAGE, 99)
    clear_gear(session, static, character, slot, 99)
    session.flush()
    assert session.scalar(select(CharacterGearSlot)) is None
    assert session.scalar(select(AuditLog).where(AuditLog.action == "GEAR_CLEARED"))


def test_inventory_quantity_update_and_zero_removes(session, gear_state):
    static, character, *_ = gear_state
    row = set_inventory(session, static, character, "Potion", 4, 99)
    assert row.quantity == 4
    set_inventory(session, static, character, "Potion", 2, 99)
    assert row.quantity == 2
    set_inventory(session, static, character, "Potion", 0, 99)
    session.flush()
    assert session.scalar(select(InventoryItem)) is None


def test_material_quantity_update(session, gear_state):
    static, character, _, _, material = gear_state
    row = set_augmentation_material(session, static, character, material, 3, 99)
    set_augmentation_material(session, static, character, material, 1, 99)
    assert row.quantity == 1


def test_book_values_and_effective_available(session, gear_state):
    static, character, _, floor, _ = gear_state
    row = set_books(session, static, character, floor, 8, 3, -1, 99)
    assert (row.earned, row.spent, row.manual_adjustment, row.available) == (8, 3, -1, 4)


def test_set_available_books_preserves_accounting_creates_rows_and_audits(session, gear_state):
    static, character, _, first, _ = gear_state
    second = RaidFloor(raid_tier=static.active_raid_tier, floor_number=2, name="Fictional Two")
    session.add(second)
    session.flush()
    existing = set_books(session, static, character, first, 4, 2, 0, 98)
    session.flush()
    session.query(AuditLog).delete()

    rows = set_available_books(
        session, static, character, {first.id: 5, second.id: 0}, actor_id=99
    )
    session.commit()

    assert [(row.earned, row.spent, row.manual_adjustment, row.available) for row in rows] == [
        (4, 2, 3, 5),
        (0, 0, 0, 0),
    ]
    assert existing.earned == 4 and existing.spent == 2
    audits = list(session.scalars(select(AuditLog).order_by(AuditLog.id)))
    assert len(audits) == 2
    assert all(
        (row.action, row.actor_discord_user_id, row.entity_type)
        == ("BOOK_AVAILABLE_ADJUSTED", 99, "CharacterFloorBookBalance")
        for row in audits
    )
    details = [json.loads(row.details) for row in audits]
    assert details[0] == {
        "character_id": character.id,
        "floor_number": 1,
        "previous_manual_adjustment": 0,
        "new_manual_adjustment": 3,
        "previous_effective_balance": 2,
        "new_effective_balance": 5,
    }
    assert details[1]["floor_number"] == 2
    assert all(row.created_at is not None for row in audits)


@pytest.mark.parametrize("bad", [-1, 1.5, "1", True, 1_000_001])
def test_set_available_books_validation_is_atomic(session, gear_state, bad):
    static, character, _, first, _ = gear_state
    second = RaidFloor(raid_tier=static.active_raid_tier, floor_number=2, name="Fictional Two")
    session.add(second)
    session.flush()
    original = set_books(session, static, character, first, 4, 2, 0, 98)
    session.flush()

    with pytest.raises(ValueError):
        set_available_books(session, static, character, {first.id: 7, second.id: bad}, 99)

    assert (original.earned, original.spent, original.manual_adjustment, original.available) == (
        4,
        2,
        0,
        2,
    )
    assert session.scalar(
        select(CharacterFloorBookBalance).where(
            CharacterFloorBookBalance.raid_floor_id == second.id
        )
    ) is None


@pytest.mark.parametrize("resource", ["inventory", "material", "earned", "spent"])
def test_negative_quantities_rejected(session, gear_state, resource):
    static, character, _, floor, material = gear_state
    with pytest.raises(ValueError, match="negative"):
        if resource == "inventory":
            set_inventory(session, static, character, "Bad", -1, 99)
        elif resource == "material":
            set_augmentation_material(session, static, character, material, -1, 99)
        elif resource == "earned":
            set_books(session, static, character, floor, -1, 0, 0, 99)
        else:
            set_books(session, static, character, floor, 0, -1, 0, 99)


def import_data():
    return {
        "characters": [
            {
                "name": "Melon Hero",
                "world": "Sample",
                "gear_slots": [
                    {
                        "slot": "HEAD",
                        "current_classification": "CRAFTED",
                    }
                ],
                "inventory_items": [{"item": "Imported Token", "quantity": 2}],
                "books": [{"floor": 1, "earned": 5, "spent": 1, "manual_adjustment": 2}],
                "augmentation_materials": [{"material": "TWINE", "quantity": 3}],
            }
        ]
    }


def test_current_state_json_dry_run_has_no_writes(session, gear_state):
    static, *_ = gear_state
    counts = import_current_state(session, static, import_data(), 99, dry_run=True)
    assert (
        counts.characters,
        counts.gear_slots,
        counts.inventory_items,
        counts.book_balances,
        counts.augmentation_materials,
    ) == (1, 1, 1, 1, 1)
    assert session.scalar(select(func.count()).select_from(CharacterGearSlot)) == 0


def test_successful_current_state_import(session, gear_state):
    static, *_ = gear_state
    counts = import_current_state(session, static, import_data(), 99)
    session.flush()
    assert counts.gear_slots == 1
    assert not session.scalar(select(CharacterGearSlot)).manually_complete
    assert session.scalar(select(InventoryItem)).quantity == 2
    assert session.scalar(select(CharacterFloorBookBalance)).available == 6
    assert session.scalar(select(CharacterAugmentationInventory)).quantity == 3


def test_gear_only_current_state_import_does_not_require_active_tier(session, gear_state):
    static, *_ = gear_state
    static.active_raid_tier = None
    data = import_data()
    data["characters"][0].pop("books")
    data["characters"][0].pop("augmentation_materials")

    counts = import_current_state(session, static, data, 99)

    assert counts.gear_slots == 1
    assert (
        session.scalar(select(CharacterGearSlot)).current_classification
        is GearClassification.CRAFTED
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("current_item_name", "Old Hat"),
        ("current_item", "Old Hat"),
        ("external_item_id", 123),
        ("item_level", 700),
        ("current_raid_tier", "CURRENT"),
        ("note", "old note"),
        ("manually_complete", True),
    ],
)
def test_current_state_import_rejects_tier_and_item_level(session, gear_state, field, value):
    static, *_ = gear_state
    data = import_data()
    data["characters"][0]["gear_slots"][0][field] = value
    with pytest.raises(ImportValidationError, match=f"{field}: not accepted"):
        import_current_state(session, static, data, 99, dry_run=True)


def test_failed_import_validation_writes_nothing(session, gear_state):
    static, *_ = gear_state
    data = import_data()
    data["characters"][0]["gear_slots"].append(dict(data["characters"][0]["gear_slots"][0]))
    with pytest.raises(ImportValidationError, match="duplicate character/slot"):
        import_current_state(session, static, data, 99)
    assert session.scalar(select(func.count()).select_from(CharacterGearSlot)) == 0


def test_obsolete_current_gear_identity_is_rejected_before_writes(session, gear_state):
    static, *_ = gear_state
    data = import_data()
    data["characters"][0]["gear_slots"][0]["external_item_id"] = 777
    with pytest.raises(ImportValidationError, match="external_item_id: not accepted"):
        import_current_state(session, static, data, 99)
    assert session.scalar(select(CharacterGearSlot)) is None


def test_non_pld_offhand_is_rejected_by_service_and_import(session, gear_state):
    static, character, *_ = gear_state
    character.job.abbreviation = "WAR"
    offhand = GearSlot(code=GearSlotCode.OFFHAND, display_name="Offhand", sort_order=2)
    session.add(offhand)
    session.flush()
    with pytest.raises(ValueError, match="N/A for non-PLD"):
        set_gear(session, static, character, offhand, GearClassification.CRAFTED, 99)
    data = import_data()
    data["characters"][0]["gear_slots"] = [{"slot": "OFFHAND", "current_classification": "CRAFTED"}]
    with pytest.raises(ImportValidationError, match="Offhand is N/A for non-PLD"):
        import_current_state(session, static, data, 99, dry_run=True)
