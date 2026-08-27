"""Canonical authoritative source snapshots for generated loot plans."""

import json
from hashlib import sha256

from sqlalchemy import select

from app.models import (
    Character,
    CharacterAugmentationInventory,
    CharacterBisSelection,
    CharacterGearSlot,
    CharacterKind,
    ConfirmedReclearMaterialGrant,
    GearSlot,
    InventoryItem,
    Job,
    RaidTier,
    ReclearWorkflowState,
    Static,
)

SOURCE_SNAPSHOT_VERSION = 1


def build_source_snapshot(
    session,
    static_id: int,
    mode,
    target_week: int,
    tier_id: int | None = None,
    participant_ids: tuple[int, ...] | None = None,
) -> tuple[str, str]:
    static = session.get(Static, static_id)
    if static is None:
        raise ValueError("static not found")
    tier = session.get(RaidTier, tier_id or static.active_raid_tier_id)
    if tier is None:
        raise ValueError("active raid tier not found")

    members = sorted(static.members, key=lambda row: row.id)
    selected_ids = set(participant_ids or ())
    characters = (
        {
            row.id: row
            for row in session.scalars(select(Character).where(Character.id.in_(selected_ids)))
        }
        if selected_ids
        else {}
    )
    character_ids = sorted(selected_ids)
    jobs = {row.id: row for row in session.scalars(select(Job))}
    slots = sorted(session.scalars(select(GearSlot)), key=lambda row: row.sort_order)
    selections = (
        {
            row.character_id: row
            for row in session.scalars(
                select(CharacterBisSelection).where(
                    CharacterBisSelection.character_id.in_(character_ids)
                )
            )
        }
        if character_ids
        else {}
    )
    gear = (
        list(
            session.scalars(
                select(CharacterGearSlot).where(CharacterGearSlot.character_id.in_(character_ids))
            )
        )
        if character_ids
        else []
    )
    inventory = (
        list(
            session.scalars(
                select(InventoryItem).where(InventoryItem.character_id.in_(character_ids))
            )
        )
        if character_ids
        else []
    )
    material_inventory = (
        list(
            session.scalars(
                select(CharacterAugmentationInventory).where(
                    CharacterAugmentationInventory.character_id.in_(character_ids)
                )
            )
        )
        if character_ids
        else []
    )
    grants = (
        list(
            session.scalars(
                select(ConfirmedReclearMaterialGrant).where(
                    ConfirmedReclearMaterialGrant.character_id.in_(character_ids)
                )
            )
        )
        if character_ids
        else []
    )
    completed_week = sum(
        week.workflow_state is ReclearWorkflowState.CLOSED for week in static.reclear_weeks
    )
    snapshot = {
        "version": SOURCE_SNAPSHOT_VERSION,
        "scope": {
            "static_id": static.id,
            "tier_id": tier.id,
            "completed_week": completed_week,
            "target_week": target_week,
            "mode": mode.value,
        },
        "roster": [
            {
                "position": position,
                "member_id": member.id,
                "active": member.active,
                "main": _binding(member, CharacterKind.MAIN),
                "alt": _binding(member, CharacterKind.ALT),
            }
            for position, member in enumerate(members, 1)
        ],
        "characters": [
            _character_snapshot(
                characters[character_id],
                jobs,
                slots,
                selections,
                gear,
                inventory,
                material_inventory,
            )
            for character_id in character_ids
            if character_id in characters
        ],
        "material_grants": sorted(
            [
                {
                    "character_id": row.character_id,
                    "material_id": row.augmentation_material_type_id,
                    "quantity": row.quantity,
                    "assignment_id": row.loot_assignment_id,
                }
                for row in grants
            ],
            key=lambda row: (row["character_id"], row["material_id"], row["assignment_id"]),
        ),
        "configuration": {
            "floors": [
                {
                    "id": floor.id,
                    "number": floor.floor_number,
                    "name": floor.name,
                    "rules": sorted(
                        [
                            {
                                "id": rule.id,
                                "loot_type_id": rule.loot_type_id,
                                "quantity": rule.expected_quantity,
                                "material_id": rule.augmentation_material_type_id,
                            }
                            for rule in floor.loot_rules
                        ],
                        key=lambda row: row["id"],
                    ),
                }
                for floor in sorted(tier.floors, key=lambda row: row.floor_number)
            ],
            "loot_types": sorted(
                [
                    {"id": row.id, "code": row.code, "category": row.category.value}
                    for row in tier.loot_types
                ],
                key=lambda row: row["code"],
            ),
            "materials": sorted(
                [{"id": row.id, "code": row.code} for row in tier.augmentation_material_types],
                key=lambda row: row["code"],
            ),
        },
    }
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return canonical, sha256(canonical.encode("utf-8")).hexdigest()


def _binding(member, kind):
    rows = [row for row in member.characters if row.kind is kind]
    return [_character_identity(row) for row in sorted(rows, key=lambda row: row.id)]


def _character_identity(row):
    return {
        "id": row.id,
        "active": row.active,
        "kind": row.kind.value,
        "job_id": row.job_id,
        "name": row.name,
        "world": row.world,
    }


def _character_snapshot(character, jobs, slots, selections, gear, inventory, materials):
    selection = selections.get(character.id)
    bis = None
    if selection is not None:
        bis = {
            "id": selection.bis_set_id,
            "tier_id": selection.raid_tier_id,
            "items": [
                {
                    "slot_id": item.gear_slot_id,
                    "classification": item.classification.value,
                    "desired_item_id": item.desired_item_id,
                    "floor_id": item.raid_floor_id,
                    "loot_type_id": item.loot_type_id,
                    "base_tome_item_id": item.base_tome_item_id,
                    "material_id": item.augmentation_material_type_id,
                }
                for item in sorted(selection.bis_set.items, key=lambda row: row.gear_slot_id)
            ],
        }
    return {
        "id": character.id,
        "member_id": character.static_member_id,
        "active": character.active,
        "kind": character.kind.value,
        "job_id": character.job_id,
        "job": jobs[character.job_id].abbreviation,
        "role": jobs[character.job_id].role,
        "bis": bis,
        "gear": sorted(
            [
                {
                    "slot_id": row.gear_slot_id,
                    "classification": row.current_classification.value,
                    "manual": row.manually_complete,
                }
                for row in gear
                if row.character_id == character.id
            ],
            key=lambda row: row["slot_id"],
        ),
        "inventory": sorted(
            [
                {"item_id": row.item_id, "quantity": row.quantity}
                for row in inventory
                if row.character_id == character.id
            ],
            key=lambda row: row["item_id"],
        ),
        "materials": sorted(
            [
                {"material_id": row.augmentation_material_type_id, "quantity": row.quantity}
                for row in materials
                if row.character_id == character.id
            ],
            key=lambda row: row["material_id"],
        ),
    }
