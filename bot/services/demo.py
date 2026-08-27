"""Create isolated, fictional data for manually exercising the complete loot workflow."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    BisSet,
    BisSetItem,
    Character,
    CharacterGearSlot,
    CharacterKind,
    DiscordGuild,
    DistributionError,
    GearClassification,
    GearSlot,
    Item,
    Job,
    LootAssignment,
    LootPlan,
    RaidTier,
    ReclearWeek,
    ReclearWorkflowState,
    Static,
)
from app.services.gear import import_current_state
from app.services.imports import import_bis_sets, import_raid_tier
from app.services.reclear import initialize_participant_books
from app.services.seed import seed_reference_data
from bot.services.admin import (
    add_character,
    add_member,
    create_static,
    guild,
    select_bis,
    select_static,
    select_tier,
    set_hierarchy,
)

DEMO_STATIC_NAME = "Loot Demo"
LEGACY_DEMO_ADMIN_NAME = "Demo Administrator (Fictional Static)"
MAIN_JOBS = ("PLD", "WHM", "DRG", "BRD", "BLM", "WAR", "SGE", "NIN")
ALT_JOBS = ("GNB", "SCH", "MNK", "DNC", "RDM", "DRK", "AST", "PCT")
SLOTS = (
    "WEAPON",
    "OFFHAND",
    "HEAD",
    "BODY",
    "HANDS",
    "LEGS",
    "FEET",
    "EARRINGS",
    "NECKLACE",
    "BRACELETS",
    "RING_1",
    "RING_2",
)


@dataclass(frozen=True, slots=True)
class DemoCreationResult:
    static_id: int
    static_name: str
    tier_code: str
    member_count: int
    character_count: int
    bis_set_count: int
    hierarchy_version: int


@dataclass(frozen=True, slots=True)
class DemoRefreshResult:
    static_id: int
    static_name: str
    created: int
    updated: int
    unchanged: int
    rejected: int


def synthetic_demo_user_ids(discord_guild_id: int) -> tuple[int, ...]:
    """Return stable IDs outside Discord's non-negative snowflake namespace."""
    if discord_guild_id <= 0:
        raise ValueError("Discord guild ID must be positive.")
    # Keep values inside signed BIGINT even for production-sized snowflakes. The
    # database scopes members by static, while negativity makes collisions with
    # every real Discord snowflake impossible.
    namespace = discord_guild_id % 900_000_000_000_000_000
    return tuple(-(namespace * 10 + index) for index in range(1, 8))


def create_demo_static(
    session: Session,
    discord_guild_id: int,
    guild_name: str,
    invoking_user_id: int,
) -> DemoCreationResult:
    """Build a complete demo static in the caller-managed transaction.

    Existing records using either reserved demo identity are rejected rather than
    reused, so an ambiguous or partially hand-created data set is never modified.
    """
    guild_row = session.scalar(
        select(DiscordGuild).where(DiscordGuild.discord_guild_id == discord_guild_id)
    )
    tier_code = _tier_code(discord_guild_id)
    if guild_row is not None and session.scalar(
        select(Static).where(
            Static.guild_id == guild_row.id,
            Static.name == DEMO_STATIC_NAME,
        )
    ):
        raise ValueError(
            "A `Loot Demo` static already exists in this guild. Select it with `/static select`; "
            "demo creation will not modify or duplicate it."
        )
    if session.scalar(select(RaidTier).where(RaidTier.code == tier_code)):
        raise ValueError(
            "The reserved fictional demo tier already exists, so its identity is ambiguous. "
            "No demo data was changed."
        )

    dummy_ids = synthetic_demo_user_ids(discord_guild_id)
    if invoking_user_id in dummy_ids:
        raise ValueError("The invoking user conflicts with a reserved synthetic demo identity.")

    seed_reference_data(session)
    guild_row = guild(session, discord_guild_id, guild_name)
    static = create_static(session, guild_row.id, DEMO_STATIC_NAME)
    tier = import_raid_tier(session, _tier_data(discord_guild_id))
    assert tier is not None
    select_tier(static, tier)

    jobs = {row.abbreviation for row in session.scalars(select(Job))}
    if not set(MAIN_JOBS + ALT_JOBS) <= jobs:
        raise ValueError("Reference job seeding did not create every required demo job.")
    members = []
    for index in range(8):
        user_id = invoking_user_id if index == 0 else dummy_ids[index - 1]
        display_name = f"Player {index + 1}"
        member = add_member(session, static, user_id, display_name)
        members.append(member)
        add_character(
            session,
            member,
            f"Player {index + 1} Main",
            _demo_world(discord_guild_id),
            CharacterKind.MAIN,
            MAIN_JOBS[index],
        )
        add_character(
            session,
            member,
            f"Player {index + 1} Alt",
            _demo_world(discord_guild_id),
            CharacterKind.ALT,
            ALT_JOBS[index],
        )

    imported_sets = import_bis_sets(session, _bis_data(discord_guild_id))
    sets_by_job = {row.job.abbreviation: row for row in imported_sets}
    characters = [character for member in members for character in member.characters]
    for character in characters:
        select_bis(session, character, tier, sets_by_job[character.job.abbreviation])

    import_current_state(
        session,
        static,
        _current_state(discord_guild_id, characters),
        invoking_user_id,
    )
    initialize_participant_books(
        session,
        static,
        tuple(character for character in characters if character.kind is CharacterKind.MAIN),
    )
    _ensure_demo_item_levels(session, tier)
    hierarchy = set_hierarchy(session, static, ",".join(MAIN_JOBS))
    hierarchy.name = "Fictional Demo Main Job Priority"
    select_static(session, guild_row.id, invoking_user_id, static)
    session.flush()
    return DemoCreationResult(
        static.id,
        static.name,
        tier.code,
        len(members),
        len(characters),
        len(imported_sets),
        hierarchy.version,
    )


def refresh_demo_static(
    session: Session,
    discord_guild_id: int,
    invoking_user_id: int,
    static: Static,
) -> DemoRefreshResult:
    """Verify and reconcile a selected generated demo without replacing its static row."""
    _verify_demo_identity(session, discord_guild_id, static)
    _normalize_demo_identity(session, discord_guild_id, static)
    _require_demo_workflows_closed(session, static.id)
    counts = {"created": 0, "updated": 0, "unchanged": 0, "rejected": 0}
    tier = static.active_raid_tier
    expected_sets = {row["job"]: row for row in _bis_data(discord_guild_id)["sets"]}
    slots = {row.code.name: row for row in session.scalars(select(GearSlot))}
    jobs = {row.abbreviation: row for row in session.scalars(select(Job))}
    sets_by_job: dict[str, BisSet] = {}
    for job_code, data in expected_sets.items():
        bis_set = session.scalar(
            select(BisSet).where(
                BisSet.raid_tier_id == tier.id,
                BisSet.job_id == jobs[job_code].id,
                BisSet.name == data["name"],
            )
        )
        created = bis_set is None
        if bis_set is None:
            bis_set = BisSet(raid_tier=tier, job=jobs[job_code], name=data["name"])
            session.add(bis_set)
            session.flush()
        changed = _reconcile_bis_set(session, bis_set, data, slots)
        counts["created" if created else "updated" if changed else "unchanged"] += 1
        sets_by_job[job_code] = bis_set

    characters = _demo_characters(static)
    initialize_participant_books(
        session,
        static,
        tuple(character for character in characters if character.kind is CharacterKind.MAIN),
    )
    for character in characters:
        change = select_bis(session, character, tier, sets_by_job[character.job.abbreviation])
        counts["updated" if change.changed else "unchanged"] += 1
    if _ensure_demo_item_levels(session, tier):
        counts["updated"] += 1

    expected_state = _current_state(discord_guild_id, characters)
    expected_gear = {
        (row["name"], gear["slot"]): gear
        for row in expected_state["characters"]
        for gear in row["gear_slots"]
    }
    for character in characters:
        for slot_code in SLOTS:
            expected = expected_gear.get((character.name, slot_code))
            if expected is None:
                existing = session.scalar(
                    select(CharacterGearSlot).where(
                        CharacterGearSlot.character_id == character.id,
                        CharacterGearSlot.gear_slot_id == slots[slot_code].id,
                    )
                )
                if slot_code == "OFFHAND" and character.job.abbreviation != "PLD" and existing:
                    session.delete(existing)
                    counts["updated"] += 1
                else:
                    counts["unchanged"] += 1
                continue
            changed, created = _reconcile_current_gear(
                session, character, slots[slot_code], expected
            )
            key = "created" if created else "updated" if changed else "unchanged"
            counts[key] += 1

    # The command operates on the caller's selected static; ensure any demo
    # characters owned by that caller also select the current generated BiS.
    for character in characters:
        if character.static_member.discord_user_id == invoking_user_id:
            change = select_bis(session, character, tier, sets_by_job[character.job.abbreviation])
            if change.changed:
                counts["updated"] += 1
    session.flush()
    return DemoRefreshResult(static.id, static.name, **counts)


def _tier_code(guild_id: int) -> str:
    return f"DEMO_{guild_id}"


def _demo_world(guild_id: int) -> str:
    """Keep concise demo character names unique across isolated guild demos."""
    return f"Fictional Demo World {guild_id}"


def _prefix(guild_id: int) -> str:
    return f"Fictional Demo G{guild_id}"


def _demo_characters(static: Static) -> list[Character]:
    return sorted(
        (character for member in static.members for character in member.characters),
        key=lambda row: (row.static_member_id, row.id),
    )


def _verify_demo_identity(session: Session, guild_id: int, static: Static) -> None:
    """Reject real and ambiguous statics before any repair is attempted."""
    tier = static.active_raid_tier
    if (
        static.guild.discord_guild_id != guild_id
        or static.name != DEMO_STATIC_NAME
        or tier is None
        or tier.code != _tier_code(guild_id)
        or tier.name != _tier_data(guild_id)["name"]
    ):
        raise ValueError(
            "The selected static is not the verified fictional Loot Demo; no data was changed."
        )
    members = sorted(static.members, key=lambda row: row.discord_user_id)
    characters = _demo_characters(static)
    dummy_ids = set(synthetic_demo_user_ids(guild_id))
    real_members = [row for row in members if row.discord_user_id not in dummy_ids]
    expected_character_rows = {
        (
            f"Player {index + 1} Main",
            CharacterKind.MAIN,
            MAIN_JOBS[index],
        )
        for index in range(8)
    } | {
        (
            f"Player {index + 1} Alt",
            CharacterKind.ALT,
            ALT_JOBS[index],
        )
        for index in range(8)
    }
    actual_character_rows = {
        (row.name, row.kind, row.job.abbreviation) for row in characters if row.active
    }
    expected_member_names = {f"Player {index}" for index in range(1, 9)}
    legacy_member_names = {
        LEGACY_DEMO_ADMIN_NAME,
        *(f"Fictional Demo Player {index}" for index in range(2, 9)),
    }
    actual_member_names = {row.display_name for row in members if row.active}
    legacy_character_rows = {
        (
            f"Fictional Demo Main {index} G{guild_id}",
            CharacterKind.MAIN,
            MAIN_JOBS[index - 1],
        )
        for index in range(1, 9)
    } | {
        (
            f"Fictional Demo Alt {index} G{guild_id}",
            CharacterKind.ALT,
            ALT_JOBS[index - 1],
        )
        for index in range(1, 9)
    }
    valid = (
        static.active
        and len(members) == len([row for row in members if row.active]) == 8
        and {row.discord_user_id for row in members if row.discord_user_id in dummy_ids}
        == dummy_ids
        and len(real_members) == 1
        and real_members[0].discord_user_id > 0
        and actual_member_names in (expected_member_names, legacy_member_names)
        and len(characters) == 16
        and actual_character_rows in (expected_character_rows, legacy_character_rows)
    )
    duplicate_tier = session.scalar(
        select(func.count()).select_from(RaidTier).where(RaidTier.code == _tier_code(guild_id))
    )
    if not valid or duplicate_tier != 1:
        raise ValueError(
            "The selected Loot Demo has a real or ambiguous identity/roster; no data was changed."
        )


def _normalize_demo_identity(session: Session, guild_id: int, static: Static) -> None:
    """Repair legacy demo labels without touching real statics."""
    ordered_ids = synthetic_demo_user_ids(guild_id)
    index_by_user_id = {user_id: index for index, user_id in enumerate(ordered_ids, 2)}
    for member in static.members:
        index = index_by_user_id.get(member.discord_user_id, 1)
        member.display_name = f"Player {index}"
        for character in member.characters:
            character.name = f"Player {index} {character.kind.value.title()}"
            character.world = _demo_world(guild_id)


def _require_demo_workflows_closed(session: Session, static_id: int) -> None:
    open_states = tuple(
        state
        for state in ReclearWorkflowState
        if state not in {ReclearWorkflowState.CLOSED, ReclearWorkflowState.CANCELLED}
    )
    open_week = session.scalar(
        select(ReclearWeek.id)
        .where(ReclearWeek.static_id == static_id, ReclearWeek.workflow_state.in_(open_states))
        .limit(1)
    )
    unresolved = session.scalar(
        select(DistributionError.id)
        .join(ReclearWeek, DistributionError.reclear_week_id == ReclearWeek.id)
        .where(ReclearWeek.static_id == static_id, DistributionError.resolved.is_(False))
        .limit(1)
    )
    open_plan = session.scalar(
        select(LootAssignment.id)
        .join(LootPlan)
        .join(ReclearWeek)
        .where(ReclearWeek.static_id == static_id, ReclearWeek.workflow_state.in_(open_states))
        .limit(1)
    )
    if open_week is not None or open_plan is not None or unresolved is not None:
        raise ValueError(
            "Close or cancel the demo reclear and resolve every loot workflow before refresh."
        )


def _reconcile_bis_set(
    session: Session, bis_set: BisSet, data: dict, slots: dict[str, GearSlot]
) -> bool:
    changed = False
    metadata = {
        "gcd_label": data.get("gcd_label"),
        "gear_set_url": data.get("gear_set_url"),
        "description": data.get("description"),
        "active": data.get("active", True),
    }
    for field, value in metadata.items():
        if getattr(bis_set, field) != value:
            setattr(bis_set, field, value)
            changed = True
    existing = {row.gear_slot.code.name: row for row in bis_set.items}
    tier = bis_set.raid_tier
    for item_data in data["items"]:
        row = existing.get(item_data["slot"])
        if row is None:
            row = BisSetItem(bis_set=bis_set, gear_slot=slots[item_data["slot"]])
            session.add(row)
            changed = True
        desired = _demo_item(session, item_data.get("desired_item"))
        base = _demo_item(session, item_data.get("base_tome_item"))
        values = {
            "classification": GearClassification[item_data["classification"]],
            "desired_item": desired,
            "raid_floor": next(
                (floor for floor in tier.floors if floor.floor_number == item_data.get("floor")),
                None,
            ),
            "loot_type": next(
                (loot for loot in tier.loot_types if loot.code == item_data.get("loot_type")),
                None,
            ),
            "base_tome_item": base,
            "tome_cost": item_data.get("tome_cost"),
            "augmentation_material_type": next(
                (
                    material
                    for material in tier.augmentation_material_types
                    if material.code == item_data.get("augmentation_material")
                ),
                None,
            ),
            "book_cost": item_data.get("book_cost"),
            "notes": item_data.get("notes"),
        }
        for field, value in values.items():
            current = getattr(row, field)
            current_id = getattr(current, "id", current)
            value_id = getattr(value, "id", value)
            if current_id != value_id:
                setattr(row, field, value)
                changed = True
    return changed


def _ensure_demo_item_levels(session: Session, tier: RaidTier) -> bool:
    """Give fictional desired/base items explicit levels for board presentation."""
    changed = False
    for bis_set in tier.bis_sets:
        for requirement in bis_set.items:
            if requirement.desired_item is not None and requirement.desired_item.item_level != 730:
                requirement.desired_item.item_level = 730
                changed = True
            if (
                requirement.base_tome_item is not None
                and requirement.base_tome_item.item_level != 710
            ):
                requirement.base_tome_item.item_level = 710
                changed = True
    return changed


def _demo_item(
    session: Session,
    name: str | None,
    *,
    item_level: int | None = None,
    external_item_id: int | None = None,
) -> Item | None:
    if name is None:
        return None
    row = session.scalar(select(Item).where(Item.name == name))
    if row is None:
        row = Item(name=name)
        session.add(row)
        session.flush()
    if item_level is not None and row.item_level != item_level:
        row.item_level = item_level
    if external_item_id is not None and row.external_item_id != external_item_id:
        existing = session.scalar(
            select(Item).where(
                Item.external_item_id == external_item_id,
                Item.id != row.id,
            )
        )
        if existing is not None:
            raise ValueError("Fictional demo external item ID is ambiguous.")
        row.external_item_id = external_item_id
    return row


def _reconcile_current_gear(
    session: Session, character: Character, slot: GearSlot, expected: dict
) -> tuple[bool, bool]:
    row = session.scalar(
        select(CharacterGearSlot).where(
            CharacterGearSlot.character_id == character.id,
            CharacterGearSlot.gear_slot_id == slot.id,
        )
    )
    values = {
        "current_classification": GearClassification[expected["current_classification"]],
        "manually_complete": expected.get("manually_complete", False),
    }
    if row is None:
        session.add(CharacterGearSlot(character=character, gear_slot=slot, **values))
        return True, True
    changed = False
    for field, value in values.items():
        current = getattr(row, field)
        if getattr(current, "id", current) != getattr(value, "id", value):
            setattr(row, field, value)
            changed = True
    return changed, False


def _tier_data(guild_id: int) -> dict:
    prefix = _prefix(guild_id)
    return {
        "code": _tier_code(guild_id),
        "name": f"{prefix} Four-Floor Tier",
        "active": True,
        "floors": [
            {"number": number, "name": f"{prefix} Floor {number}"} for number in range(1, 5)
        ],
        "loot_types": [
            {
                "code": "ACCESSORY_COFFER",
                "name": f"{prefix} Accessory Coffer",
                "category": "COFFER",
                "item": f"{prefix} Accessory Coffer",
            },
            {
                "code": "HEAD_COFFER",
                "name": f"{prefix} Head Coffer",
                "category": "COFFER",
                "item": f"{prefix} Head Coffer",
            },
            {
                "code": "ARMOR_TWINE",
                "name": f"{prefix} Armor Twine",
                "category": "AUGMENTATION_MATERIAL",
                "item": f"{prefix} Armor Twine",
            },
            {
                "code": "WEAPON_COFFER",
                "name": f"{prefix} Weapon Coffer",
                "category": "COFFER",
                "item": f"{prefix} Weapon Coffer",
            },
        ],
        "augmentation_material_types": [
            {
                "code": "ACCESSORY_GLAZE",
                "name": f"{prefix} Accessory Glaze",
                "item": f"{prefix} Accessory Glaze",
            },
            {
                "code": "ARMOR_TWINE",
                "name": f"{prefix} Armor Twine",
                "item": f"{prefix} Armor Twine",
            },
        ],
        "floor_loot_rules": [
            {
                "floor": 1,
                "loot_type": "ACCESSORY_COFFER",
                "expected_quantity": 2,
                "book_cost": 4,
            },
            {
                "floor": 2,
                "loot_type": "HEAD_COFFER",
                "expected_quantity": 2,
                "book_cost": 4,
            },
            {
                "floor": 3,
                "loot_type": "ARMOR_TWINE",
                "expected_quantity": 1,
                "augmentation_material": "ARMOR_TWINE",
            },
            {
                "floor": 4,
                "loot_type": "WEAPON_COFFER",
                "expected_quantity": 1,
                "book_cost": 8,
            },
        ],
    }


def _bis_data(guild_id: int) -> dict:
    prefix = _prefix(guild_id)
    sets = []
    for job in MAIN_JOBS + ALT_JOBS:
        item_prefix = f"{prefix} {job}"
        items = []
        for slot in SLOTS:
            if slot == "OFFHAND" and job != "PLD":
                items.append({"slot": slot, "classification": "NOT_APPLICABLE"})
            elif slot == "OFFHAND":
                items.append(
                    {
                        "slot": slot,
                        "desired_item": f"{item_prefix} Savage Shield",
                        "classification": "SAVAGE",
                        "floor": 4,
                        "loot_type": "WEAPON_COFFER",
                        "book_cost": 8,
                    }
                )
            elif slot == "WEAPON":
                items.append(
                    {
                        "slot": slot,
                        "desired_item": f"{item_prefix} Savage Weapon",
                        "classification": "SAVAGE",
                        "floor": 4,
                        "loot_type": "WEAPON_COFFER",
                        "book_cost": 8,
                    }
                )
            elif slot == "HEAD":
                items.append(
                    {
                        "slot": slot,
                        "desired_item": f"{item_prefix} Savage Head",
                        "classification": "SAVAGE",
                        "floor": 2,
                        "loot_type": "HEAD_COFFER",
                        "book_cost": 4,
                    }
                )
            elif slot == "BODY":
                items.append(
                    {
                        "slot": slot,
                        "desired_item": f"{item_prefix} Augmented Body",
                        "classification": "AUGMENTED_TOME",
                        "base_tome_item": f"{item_prefix} Base Tome Body",
                        "tome_cost": 825,
                        "augmentation_material": "ARMOR_TWINE",
                    }
                )
            elif slot == "EARRINGS":
                items.append(
                    {
                        "slot": slot,
                        "desired_item": f"{item_prefix} Savage Earrings",
                        "classification": "SAVAGE",
                        "floor": 1,
                        "loot_type": "ACCESSORY_COFFER",
                        "book_cost": 4,
                    }
                )
            elif slot == "RING_1":
                items.append(
                    {
                        "slot": slot,
                        "desired_item": f"{item_prefix} Augmented Ring",
                        "classification": "AUGMENTED_TOME",
                        "base_tome_item": f"{item_prefix} Base Tome Ring",
                        "tome_cost": 375,
                        "augmentation_material": "ACCESSORY_GLAZE",
                    }
                )
            elif slot == "HANDS":
                items.append(
                    {
                        "slot": slot,
                        "desired_item": f"{item_prefix} Tome Hands",
                        "classification": "TOME",
                        "tome_cost": 495,
                    }
                )
            else:
                items.append(
                    {
                        "slot": slot,
                        "desired_item": f"{item_prefix} Fictional Crafted {slot.title()}",
                        "classification": "CRAFTED",
                    }
                )
        sets.append(
            {
                "tier_code": _tier_code(guild_id),
                "job": job,
                "name": f"{item_prefix} Complete BiS",
                "gcd_label": "fictional demo",
                "gear_set_url": "https://example.invalid/fictional-demo-bis",
                "description": "Fictional demo data only; not FFXIV gearing advice.",
                "items": items,
            }
        )
    return {"sets": sets}


def _current_state(guild_id: int, characters: list) -> dict:
    prefix = _prefix(guild_id)
    rows = []
    for index, character in enumerate(characters):
        gear = []
        for slot in SLOTS:
            if slot == "OFFHAND" and character.job.abbreviation != "PLD":
                continue
            gear.append(
                {
                    "slot": slot,
                    "current_classification": "CRAFTED",
                }
            )

        # Base tome gear is intentionally equipped for some augmented goals. The
        # remaining rows retain different current sources so the demo shows both
        # READY_TO_AUGMENT and NEEDS_AUGMENTATION.
        if index % 4 == 0:
            body = next(row for row in gear if row["slot"] == "BODY")
            body["current_classification"] = "TOME"

        item_prefix = f"{prefix} {character.job.abbreviation}"
        inventory = [{"item": f"{item_prefix} Base Tome Body", "quantity": 1, "item_level": 710}]
        materials = []
        if index % 4 == 0:
            materials.append({"material": "ARMOR_TWINE", "quantity": 1})
        elif index % 4 == 2:
            inventory.append({"item": f"{prefix} Head Coffer", "quantity": 1, "item_level": 0})
        else:
            inventory.append(
                {"item": f"{item_prefix} Savage Earrings", "quantity": 1, "item_level": 730}
            )
        if index % 5 == 0:
            inventory.append(
                {"item": f"{item_prefix} Base Tome Ring", "quantity": 1, "item_level": 710}
            )
            materials.append({"material": "ACCESSORY_GLAZE", "quantity": 1})

        rows.append(
            {
                "name": character.name,
                "world": character.world,
                "gear_slots": gear,
                "inventory_items": inventory,
                "augmentation_materials": materials,
            }
        )
    return {"characters": rows}


def _demo_external_item_id(
    guild_id: int, character_index: int, slot_index: int, *, exact: bool = False, base: bool = False
) -> int:
    """Return stable positive fictional external IDs without name-based inference."""
    namespace = guild_id % 1_000_000
    variant = 2 if exact else 1 if base else 0
    return 1_000_000_000 + namespace * 10_000 + character_index * 100 + slot_index * 3 + variant
