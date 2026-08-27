"""Transactional JSON-compatible raid-tier and BiS import services."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AugmentationMaterialType,
    BisSet,
    BisSetItem,
    CharacterAugmentationInventory,
    CharacterBisSelection,
    CharacterFloorBookBalance,
    FloorLootRule,
    GearClassification,
    GearSlot,
    GearSlotCode,
    Item,
    Job,
    LootAssignment,
    LootAssignmentCompletionItem,
    LootCategory,
    LootType,
    RaidFloor,
    RaidTier,
    ReclearWeek,
    Static,
    WeeklyLockout,
)

JsonSource = Mapping[str, Any] | str | Path


class ImportValidationError(ValueError):
    """One or more contextual import validation errors."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True, slots=True)
class ImportCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0


class ImportedBisSets(list[BisSet]):
    """List-compatible import result with correction counts."""

    def __init__(self, values=(), counts: ImportCounts | None = None):
        super().__init__(values)
        self.counts = counts or ImportCounts()


def _load(source: JsonSource) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    with Path(source).open(encoding="utf-8") as file:
        return json.load(file)


def _required(data: Mapping[str, Any], field: str, errors: list[str], context: str) -> Any:
    value = data.get(field)
    if value is None or value == "":
        errors.append(f"{context}.{field}: required")
    return value


def import_raid_tier(
    session: Session, source: JsonSource, *, dry_run: bool = False
) -> RaidTier | None:
    """Validate and atomically import one complete data-driven tier configuration."""
    data = _load(source)
    errors: list[str] = []
    code = _required(data, "code", errors, "tier")
    name = _required(data, "name", errors, "tier")
    floors = data.get("floors", [])
    loot_types = data.get("loot_types", [])
    materials = data.get("augmentation_material_types", [])
    rules = data.get("floor_loot_rules", [])

    existing = session.scalar(select(RaidTier).where(RaidTier.code == code)) if code else None
    floor_numbers: set[int] = set()
    for index, floor in enumerate(floors):
        context = f"tier.floors[{index}]"
        number = _required(floor, "number", errors, context)
        _required(floor, "name", errors, context)
        if not isinstance(number, int) or number <= 0:
            errors.append(f"{context}.number: must be a positive integer")
        elif number in floor_numbers:
            errors.append(f"{context}.number: duplicate floor")
        else:
            floor_numbers.add(number)
    loot_codes: set[str] = set()
    for index, loot in enumerate(loot_types):
        context = f"tier.loot_types[{index}]"
        loot_code = _required(loot, "code", errors, context)
        _required(loot, "name", errors, context)
        category = _required(loot, "category", errors, context)
        if category and category not in LootCategory.__members__:
            errors.append(f"{context}.category: unknown loot category {category}")
        if loot_code in loot_codes:
            errors.append(f"{context}.code: duplicate loot type")
        loot_codes.add(loot_code)
    material_codes: set[str] = set()
    for index, material in enumerate(materials):
        context = f"tier.augmentation_material_types[{index}]"
        material_code = _required(material, "code", errors, context)
        _required(material, "name", errors, context)
        if material_code in material_codes:
            errors.append(f"{context}.code: duplicate augmentation material")
        material_codes.add(material_code)
    for index, rule in enumerate(rules):
        context = f"tier.floor_loot_rules[{index}]"
        floor = rule.get("floor")
        loot_type = rule.get("loot_type")
        quantity = rule.get("expected_quantity")
        material = rule.get("augmentation_material")
        book_cost = rule.get("book_cost")
        if floor not in floor_numbers:
            errors.append(f"{context}.floor: unknown floor {floor}")
        if loot_type not in loot_codes:
            errors.append(f"{context}.loot_type: unknown loot type {loot_type}")
        if not isinstance(quantity, int) or quantity < 0:
            errors.append(f"{context}.expected_quantity: must be nonnegative")
        if material is not None and material not in material_codes:
            errors.append(f"{context}.augmentation_material: unknown material {material}")
        if book_cost is not None and (not isinstance(book_cost, int) or book_cost < 0):
            errors.append(f"{context}.book_cost: must be nonnegative")
    if errors:
        raise ImportValidationError(errors)
    action = "inserted"
    if existing is not None:
        if _tier_matches(existing, data):
            action = "unchanged"
        elif _tier_referenced(session, existing.id):
            action = "rejected"
        else:
            action = "updated"
    if dry_run:
        return None

    try:
        counts = ImportCounts(**{action: 1})
        if action in {"unchanged", "rejected"}:
            existing.import_counts = counts
            return existing
        starts_on = data.get("starts_on")
        tier = existing or RaidTier(code=code)
        tier.name = name
        tier.starts_on = date.fromisoformat(starts_on) if starts_on else None
        tier.active = data.get("active", True)
        if existing is not None:
            tier.floors.clear()
            tier.loot_types.clear()
            tier.augmentation_material_types.clear()
        floor_map = {
            row["number"]: RaidFloor(floor_number=row["number"], name=row["name"]) for row in floors
        }
        loot_map = {
            row["code"]: LootType(
                code=row["code"],
                name=row["name"],
                category=LootCategory[row["category"]],
                item=_item(session, row.get("item")),
            )
            for row in loot_types
        }
        material_map = {
            row["code"]: AugmentationMaterialType(
                code=row["code"], name=row["name"], item=_item(session, row.get("item"))
            )
            for row in materials
        }
        tier.floors.extend(floor_map.values())
        tier.loot_types.extend(loot_map.values())
        tier.augmentation_material_types.extend(material_map.values())
        for row in rules:
            floor_map[row["floor"]].loot_rules.append(
                FloorLootRule(
                    loot_type=loot_map[row["loot_type"]],
                    expected_quantity=row["expected_quantity"],
                    book_cost=row.get("book_cost"),
                    augmentation_material_type=material_map.get(row.get("augmentation_material")),
                )
            )
        session.add(tier)
        session.flush()
        tier.import_counts = counts
        return tier
    except Exception:
        session.rollback()
        raise


def import_bis_sets(session: Session, source: JsonSource, *, dry_run: bool = False) -> list[BisSet]:
    """Validate and atomically import fictional or real BiS set definitions."""
    data = _load(source)
    rows = data.get("sets", [])
    errors: list[str] = []
    prepared: list[tuple[Mapping[str, Any], RaidTier | None, Job | None]] = []
    for set_index, row in enumerate(rows):
        set_name = row.get("name", f"index {set_index}")
        context = f"set {set_name}"
        tier_code = _required(row, "tier_code", errors, context)
        job_code = _required(row, "job", errors, context)
        _required(row, "name", errors, context)
        tier = (
            session.scalar(select(RaidTier).where(RaidTier.code == tier_code))
            if tier_code
            else None
        )
        job = session.scalar(select(Job).where(Job.abbreviation == job_code)) if job_code else None
        if tier_code and tier is None:
            errors.append(f"{context}.tier_code: unknown tier {tier_code}")
        if job_code and job is None:
            errors.append(f"{context}.job: unknown job {job_code}")
        slots_seen: set[str] = set()
        for item_index, item in enumerate(row.get("items", [])):
            slot = item.get("slot")
            item_context = f"{context}, slot {slot or item_index}"
            classification = item.get("classification")
            if slot not in GearSlotCode.__members__:
                errors.append(f"{item_context}.slot: unknown slot {slot}")
            elif slot in slots_seen:
                errors.append(f"{item_context}.slot: duplicate slot")
            slots_seen.add(slot)
            if classification not in GearClassification.__members__:
                errors.append(
                    f"{item_context}.classification: unknown classification {classification}"
                )
                continue
            references = (
                item.get("floor"),
                item.get("loot_type"),
                item.get("tome_cost"),
                item.get("augmentation_material"),
                item.get("book_cost"),
            )
            if classification == "NOT_APPLICABLE" and any(
                value is not None for value in references
            ):
                errors.append(
                    f"{item_context}.classification: contradictory NOT_APPLICABLE requirement"
                )
            if slot == "OFFHAND" and job is not None:
                uses_offhand = job.uses_offhand
                if uses_offhand and classification == "NOT_APPLICABLE":
                    errors.append(
                        f"{item_context}.classification: {job.abbreviation} OFFHAND must define "
                        "an applicable category"
                    )
                elif not uses_offhand and classification != "NOT_APPLICABLE":
                    errors.append(
                        f"{item_context}.classification: {job.abbreviation} OFFHAND must be "
                        "NOT_APPLICABLE"
                    )
            for field in ("tome_cost", "book_cost"):
                value = item.get(field)
                if value is not None and (not isinstance(value, int) or value < 0):
                    errors.append(f"{item_context}.{field}: must be nonnegative")
            if classification == "AUGMENTED_TOME" and not item.get("augmentation_material"):
                errors.append(
                    f"{item_context}.classification: AUGMENTED_TOME requires augmentation_material"
                )
            if tier is not None:
                if item.get("floor") is not None and not any(
                    floor.floor_number == item["floor"] for floor in tier.floors
                ):
                    errors.append(f"{item_context}.floor: unknown floor {item['floor']}")
                if item.get("loot_type") and not any(
                    loot.code == item["loot_type"] for loot in tier.loot_types
                ):
                    errors.append(
                        f"{item_context}.loot_type: unknown loot type {item['loot_type']}"
                    )
                if item.get("augmentation_material") and not any(
                    material.code == item["augmentation_material"]
                    for material in tier.augmentation_material_types
                ):
                    errors.append(
                        f"{item_context}.augmentation_material: unknown augmentation material "
                        f"{item['augmentation_material']}"
                    )
        prepared.append((row, tier, job))
    if errors:
        raise ImportValidationError(errors)
    if dry_run:
        return ImportedBisSets()

    imported: list[BisSet] = []
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "rejected": 0}
    try:
        slots = {slot.code.name: slot for slot in session.scalars(select(GearSlot))}
        for row, tier, job in prepared:
            bis_set = session.scalar(
                select(BisSet).where(
                    BisSet.job_id == job.id,
                    BisSet.raid_tier_id == tier.id,
                    BisSet.name == row["name"],
                )
            )
            if bis_set is not None and _bis_matches(bis_set, row):
                counts["unchanged"] += 1
                imported.append(bis_set)
                continue
            if bis_set is not None and _bis_referenced(session, bis_set.id):
                counts["rejected"] += 1
                continue
            if bis_set is None:
                bis_set = BisSet(job=job, raid_tier=tier, name=row["name"])
                session.add(bis_set)
                counts["inserted"] += 1
            else:
                bis_set.items.clear()
                counts["updated"] += 1
            bis_set.gcd_label = row.get("gcd_label")
            bis_set.gear_set_url = row.get("gear_set_url")
            bis_set.description = row.get("description")
            bis_set.active = row.get("active", True)
            for item_data in row.get("items", []):
                classification = GearClassification[item_data["classification"]]
                bis_set.items.append(
                    BisSetItem(
                        gear_slot=slots[item_data["slot"]],
                        classification=classification,
                        raid_floor=next(
                            (
                                floor
                                for floor in tier.floors
                                if floor.floor_number == item_data.get("floor")
                            ),
                            None,
                        ),
                        loot_type=next(
                            (
                                loot
                                for loot in tier.loot_types
                                if loot.code == item_data.get("loot_type")
                            ),
                            None,
                        ),
                        tome_cost=item_data.get("tome_cost"),
                        augmentation_material_type=next(
                            (
                                material
                                for material in tier.augmentation_material_types
                                if material.code == item_data.get("augmentation_material")
                            ),
                            None,
                        ),
                        book_cost=item_data.get("book_cost"),
                        notes=item_data.get("notes"),
                    )
                )
            imported.append(bis_set)
        session.flush()
        return ImportedBisSets(imported, ImportCounts(**counts))
    except Exception:
        session.rollback()
        raise


def _item(session: Session, name: str | None) -> Item | None:
    if not name:
        return None
    existing = session.scalar(select(Item).where(Item.name == name))
    if existing is not None:
        return existing
    item = Item(name=name)
    session.add(item)
    return item


def _tier_matches(tier: RaidTier, data: Mapping[str, Any]) -> bool:
    starts_on = date.fromisoformat(data["starts_on"]) if data.get("starts_on") else None
    if (tier.name, tier.starts_on, tier.active) != (
        data["name"],
        starts_on,
        data.get("active", True),
    ):
        return False
    floors = sorted((row.floor_number, row.name) for row in tier.floors)
    expected_floors = sorted((row["number"], row["name"]) for row in data.get("floors", []))
    loot = sorted(
        (row.code, row.name, row.category.value, row.item.name if row.item else None)
        for row in tier.loot_types
    )
    expected_loot = sorted(
        (row["code"], row["name"], row["category"], row.get("item"))
        for row in data.get("loot_types", [])
    )
    materials = sorted(
        (row.code, row.name, row.item.name if row.item else None)
        for row in tier.augmentation_material_types
    )
    expected_materials = sorted(
        (row["code"], row["name"], row.get("item"))
        for row in data.get("augmentation_material_types", [])
    )
    rules = sorted(
        (
            row.raid_floor.floor_number,
            row.loot_type.code,
            row.expected_quantity,
            row.book_cost,
            row.augmentation_material_type.code if row.augmentation_material_type else None,
        )
        for floor in tier.floors
        for row in floor.loot_rules
    )
    expected_rules = sorted(
        (
            row["floor"],
            row["loot_type"],
            row["expected_quantity"],
            row.get("book_cost"),
            row.get("augmentation_material"),
        )
        for row in data.get("floor_loot_rules", [])
    )
    return (floors, loot, materials, rules) == (
        expected_floors,
        expected_loot,
        expected_materials,
        expected_rules,
    )


def _tier_referenced(session: Session, tier_id: int) -> bool:
    checks = (
        select(Static.id).where(Static.active_raid_tier_id == tier_id),
        select(ReclearWeek.id).where(ReclearWeek.raid_tier_id == tier_id),
        select(BisSet.id).where(BisSet.raid_tier_id == tier_id),
        select(CharacterFloorBookBalance.id)
        .join(RaidFloor)
        .where(RaidFloor.raid_tier_id == tier_id),
        select(CharacterAugmentationInventory.id)
        .join(AugmentationMaterialType)
        .where(AugmentationMaterialType.raid_tier_id == tier_id),
        select(WeeklyLockout.id).join(RaidFloor).where(RaidFloor.raid_tier_id == tier_id),
        select(LootAssignment.id).join(RaidFloor).where(RaidFloor.raid_tier_id == tier_id),
    )
    return any(session.scalar(statement.limit(1)) is not None for statement in checks)


def _bis_matches(bis_set: BisSet, data: Mapping[str, Any]) -> bool:
    if (
        bis_set.gcd_label,
        bis_set.gear_set_url,
        bis_set.description,
        bis_set.active,
    ) != (
        data.get("gcd_label"),
        data.get("gear_set_url"),
        data.get("description"),
        data.get("active", True),
    ):
        return False

    def model_item(row: BisSetItem):
        return (
            row.gear_slot.code.value,
            row.classification.value,
            row.raid_floor.floor_number if row.raid_floor else None,
            row.loot_type.code if row.loot_type else None,
            row.tome_cost,
            row.augmentation_material_type.code if row.augmentation_material_type else None,
            row.book_cost,
            row.notes,
        )

    def imported_item(row: Mapping[str, Any]):
        return (
            row["slot"],
            row["classification"],
            row.get("floor"),
            row.get("loot_type"),
            row.get("tome_cost"),
            row.get("augmentation_material"),
            row.get("book_cost"),
            row.get("notes"),
        )

    return sorted(model_item(row) for row in bis_set.items) == sorted(
        imported_item(row) for row in data.get("items", [])
    )


def _bis_referenced(session: Session, bis_set_id: int) -> bool:
    selected = session.scalar(
        select(CharacterBisSelection.id)
        .where(CharacterBisSelection.bis_set_id == bis_set_id)
        .limit(1)
    )
    assigned = session.scalar(
        select(LootAssignment.id)
        .join(BisSetItem, LootAssignment.intended_bis_set_item_id == BisSetItem.id)
        .where(BisSetItem.bis_set_id == bis_set_id)
        .limit(1)
    )
    bundled = session.scalar(
        select(LootAssignmentCompletionItem.id)
        .join(BisSetItem, LootAssignmentCompletionItem.bis_set_item_id == BisSetItem.id)
        .where(BisSetItem.bis_set_id == bis_set_id)
        .limit(1)
    )
    return selected is not None or assigned is not None or bundled is not None
