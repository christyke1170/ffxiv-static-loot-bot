"""Focused application services for administrative bot operations."""

import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    BisSet,
    Character,
    CharacterBisSelection,
    CharacterKind,
    DiscordGuild,
    Job,
    JobHierarchy,
    JobHierarchyEntry,
    LootAssignment,
    LootPlan,
    RaidTier,
    ReclearParticipant,
    ReclearWeek,
    ReclearWorkflowState,
    Static,
    StaticMember,
    UserStaticPreference,
)
from app.services.character_gear import initialize_character_gear, reconcile_character_offhand

OPEN_WORKFLOW_STATES = tuple(
    state
    for state in ReclearWorkflowState
    if state not in {ReclearWorkflowState.CLOSED, ReclearWorkflowState.CANCELLED}
)


@dataclass(frozen=True, slots=True)
class SelectionChange:
    old: object | None
    new: object | None
    changed: bool


def guild(session: Session, discord_id: int, name: str) -> DiscordGuild:
    row = session.scalar(select(DiscordGuild).where(DiscordGuild.discord_guild_id == discord_id))
    if row is None:
        row = DiscordGuild(discord_guild_id=discord_id, name=name)
        session.add(row)
        session.flush()
    return row


def create_static(session: Session, guild_id: int, name: str, crafted_item_level: int) -> Static:
    name = _required_text(name, "Static name", 100)
    if session.scalar(select(Static).where(Static.guild_id == guild_id, Static.name == name)):
        raise ValueError("A static with that name already exists in this guild.")
    _positive_item_level(crafted_item_level)
    row = Static(guild_id=guild_id, name=name, crafted_item_level=crafted_item_level)
    session.add(row)
    session.flush()
    return row


def edit_static(session: Session, static: Static, name: str, actor_id: int) -> Static:
    name = _required_text(name, "Static name", 100)
    duplicate = session.scalar(
        select(Static).where(
            Static.guild_id == static.guild_id,
            func.lower(Static.name) == name.casefold(),
            Static.id != static.id,
        )
    )
    if duplicate:
        raise ValueError("A static with that name already exists in this guild.")
    old = static.name
    if old == name:
        raise ValueError("The static already has that name.")
    static.name = name
    _audit(
        session,
        static.id,
        actor_id,
        "STATIC_RENAMED",
        "Static",
        static.id,
        {"old": old, "new": name},
    )
    return static


def deactivate_static(session: Session, static: Static, actor_id: int) -> Static:
    if not static.active:
        return static
    if _open_week(session, static.id) is not None:
        raise ValueError(
            "The static has an active unfinished reclear week; cancel or close it before "
            "deactivation."
        )
    static.active = False
    _audit(session, static.id, actor_id, "STATIC_DEACTIVATED", "Static", static.id)
    return static


def reactivate_static(session: Session, static: Static, actor_id: int) -> Static:
    if static.active:
        return static
    static.active = True
    _audit(session, static.id, actor_id, "STATIC_REACTIVATED", "Static", static.id)
    return static


def select_static(session: Session, guild_id: int, user_id: int, static: Static) -> None:
    if not static.active:
        raise ValueError("A deactivated static cannot be selected for new work.")
    pref = session.scalar(
        select(UserStaticPreference).where(
            UserStaticPreference.guild_id == guild_id,
            UserStaticPreference.discord_user_id == user_id,
        )
    )
    if pref is None:
        session.add(UserStaticPreference(guild_id=guild_id, discord_user_id=user_id, static=static))
    else:
        pref.static_id = static.id


def selected_static(session: Session, guild_id: int, user_id: int) -> Static:
    row = session.scalar(
        select(UserStaticPreference)
        .join(DiscordGuild, UserStaticPreference.guild_id == DiscordGuild.id)
        .where(
            DiscordGuild.discord_guild_id == guild_id,
            UserStaticPreference.discord_user_id == user_id,
        )
    )
    if row is None or row.static.guild.discord_guild_id != guild_id:
        raise ValueError("Select a static first with `/static select`.")
    return row.static


def list_statics(session: Session, guild_id: int) -> list[Static]:
    return list(
        session.scalars(
            select(Static)
            .join(DiscordGuild)
            .where(DiscordGuild.discord_guild_id == guild_id)
            .order_by(Static.name)
        )
    )


def resolve_static(session: Session, guild_id: int, static_id: int) -> Static:
    row = session.scalar(
        select(Static)
        .join(DiscordGuild)
        .where(Static.id == static_id, DiscordGuild.discord_guild_id == guild_id)
    )
    if row is None:
        raise ValueError("That static does not belong to this Discord guild.")
    return row


def add_member(session: Session, static: Static, user_id: int, display_name: str) -> StaticMember:
    if not static.active:
        raise ValueError("A deactivated static cannot accept new members.")
    display_name = _required_text(display_name, "Display name", 100)
    if session.scalar(
        select(StaticMember).where(
            StaticMember.static_id == static.id, StaticMember.discord_user_id == user_id
        )
    ):
        raise ValueError("That Discord member is already in this static.")
    row = StaticMember(static=static, discord_user_id=user_id, display_name=display_name)
    session.add(row)
    session.flush()
    return row


def deactivate_member(
    session: Session, static: Static, user_id: int, actor_id: int | None = None
) -> StaticMember:
    row = session.scalar(
        select(StaticMember).where(
            StaticMember.static_id == static.id, StaticMember.discord_user_id == user_id
        )
    )
    if row is None:
        raise ValueError("That Discord member is not in the selected static.")
    if not row.active:
        return row
    row.active = False
    if actor_id is not None:
        _audit(session, static.id, actor_id, "MEMBER_DEACTIVATED", "StaticMember", row.id)
    return row


def edit_member(
    session: Session, static: Static, user_id: int, display_name: str, actor_id: int
) -> StaticMember:
    row = _member(session, static, user_id)
    display_name = _required_text(display_name, "Display name", 100)
    old = row.display_name
    if old == display_name:
        raise ValueError("The member already has that display name.")
    row.display_name = display_name
    _audit(
        session,
        static.id,
        actor_id,
        "MEMBER_RENAMED",
        "StaticMember",
        row.id,
        {"old": old, "new": display_name},
    )
    return row


def reactivate_member(
    session: Session, static: Static, user_id: int, actor_id: int
) -> StaticMember:
    if not static.active:
        raise ValueError("Reactivate the static before reactivating a member.")
    row = _member(session, static, user_id)
    if row.active:
        return row
    row.active = True
    _audit(session, static.id, actor_id, "MEMBER_REACTIVATED", "StaticMember", row.id)
    return row


def add_character(
    session: Session,
    member: StaticMember,
    name: str,
    world: str,
    kind: CharacterKind,
    job_abbreviation: str,
) -> Character:
    if not member.active or not member.static.active:
        raise ValueError("Characters require an active member in an active static.")
    name = _required_text(name, "Character name", 100)
    world = _required_text(world, "World", 50)
    job = session.scalar(
        select(Job).where(func.upper(Job.abbreviation) == job_abbreviation.upper())
    )
    if job is None:
        raise ValueError(f"Unknown job abbreviation: {job_abbreviation}.")
    if session.scalar(select(Character).where(Character.name == name, Character.world == world)):
        raise ValueError("That character name/world already exists.")
    row = Character(static_member=member, job=job, name=name, world=world, kind=kind)
    session.add(row)
    session.flush()
    initialize_character_gear(session, row)
    return row


def resolve_member_character(
    session: Session, static: Static, member_user_id: int, current_name: str
) -> tuple[StaticMember, Character]:
    member = _member(session, static, member_user_id)
    rows = list(
        session.scalars(
            select(Character).where(
                Character.static_member_id == member.id,
                func.lower(Character.name) == current_name.strip().lower(),
            )
        )
    )
    if not rows:
        raise ValueError("That character is not owned by the selected member in this static.")
    if len(rows) > 1:
        raise ValueError("Character name is ambiguous; an administrator must correct it manually.")
    return member, rows[0]


def edit_character(
    session: Session,
    static: Static,
    character: Character,
    actor_id: int,
    *,
    new_name: str | None = None,
    new_world: str | None = None,
    new_kind: CharacterKind | None = None,
    new_job: str | None = None,
    clear_incompatible_bis: bool = False,
) -> tuple[Character, int]:
    _require_character_in_static(static, character)
    if not static.active or not character.static_member.active:
        raise ValueError("Character corrections require an active static membership.")
    values = {
        "name": _required_text(new_name, "Character name", 100)
        if new_name is not None
        else character.name,
        "world": _required_text(new_world, "World", 50)
        if new_world is not None
        else character.world,
        "kind": new_kind if new_kind is not None else character.kind,
    }
    job = character.job
    if new_job is not None:
        job = session.scalar(
            select(Job).where(func.upper(Job.abbreviation) == new_job.strip().upper())
        )
        if job is None:
            raise ValueError(f"Unknown job abbreviation: {new_job}.")
    changed = (
        values["name"] != character.name
        or values["world"] != character.world
        or values["kind"] is not character.kind
        or job.id != character.job_id
    )
    if not changed:
        raise ValueError("Provide at least one value that changes the character.")
    duplicate = session.scalar(
        select(Character).where(
            Character.name == values["name"],
            Character.world == values["world"],
            Character.id != character.id,
        )
    )
    if duplicate:
        raise ValueError("That character name/world already exists.")
    incompatible = [s for s in character.bis_selections if s.bis_set.job_id != job.id]
    if incompatible and not clear_incompatible_bis:
        raise ValueError(
            "The new job is incompatible with selected BiS set(s); retry with "
            "clear_incompatible_bis=true to clear them explicitly."
        )
    before = {
        "name": character.name,
        "world": character.world,
        "kind": character.kind.value,
        "job": character.job.abbreviation,
    }
    for selection in incompatible:
        session.delete(selection)
    character.name = values["name"]
    character.world = values["world"]
    character.kind = values["kind"]
    job_changed = job.id != character.job_id
    character.job = job
    if job_changed:
        reconcile_character_offhand(session, character)
    _audit(
        session,
        static.id,
        actor_id,
        "CHARACTER_EDITED",
        "Character",
        character.id,
        {"before": before, "cleared_bis": len(incompatible)},
    )
    return character, len(incompatible)


def set_crafted_item_level(
    session: Session, static: Static, value: int, actor_id: int
) -> tuple[int | None, int]:
    _positive_item_level(value)
    previous = static.crafted_item_level
    if previous == value:
        raise ValueError("The static already uses that crafted item level.")
    static.crafted_item_level = value
    _audit(
        session,
        static.id,
        actor_id,
        "STATIC_CRAFTED_ITEM_LEVEL_CHANGED",
        "Static",
        static.id,
        {"previous": previous, "new": value},
    )
    return previous, value


def _positive_item_level(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 10_000:
        raise ValueError("Crafted item level must be a positive integer no greater than 10,000.")


def set_character_active(
    session: Session, static: Static, character: Character, active: bool, actor_id: int
) -> Character:
    _require_character_in_static(static, character)
    if not static.active or not character.static_member.active:
        raise ValueError("Character lifecycle changes require an active static membership.")
    if character.active == active:
        return character
    if not active and _character_in_open_workflow(session, character.id):
        raise ValueError(
            "The character is included in an open reclear or unresolved loot workflow and cannot "
            "be deactivated. Cancel or close the workflow first."
        )
    character.active = active
    _audit(
        session,
        static.id,
        actor_id,
        "CHARACTER_REACTIVATED" if active else "CHARACTER_DEACTIVATED",
        "Character",
        character.id,
    )
    return character


def select_bis(
    session: Session, character: Character, tier: RaidTier, bis_set: BisSet
) -> SelectionChange:
    if bis_set.raid_tier_id != tier.id or bis_set.job_id != character.job_id:
        raise ValueError("The BiS set must match the character job and raid tier.")
    row = session.scalar(
        select(CharacterBisSelection).where(
            CharacterBisSelection.character_id == character.id,
            CharacterBisSelection.raid_tier_id == tier.id,
        )
    )
    if row is None:
        session.add(CharacterBisSelection(character=character, raid_tier=tier, bis_set=bis_set))
        return SelectionChange(None, bis_set, True)
    else:
        old = row.bis_set
        if row.bis_set_id == bis_set.id:
            return SelectionChange(old, bis_set, False)
        row.bis_set_id = bis_set.id
        return SelectionChange(old, bis_set, True)


def clear_bis(
    session: Session, static: Static, character: Character, tier: RaidTier
) -> SelectionChange:
    _require_character_in_static(static, character)
    row = session.scalar(
        select(CharacterBisSelection).where(
            CharacterBisSelection.character_id == character.id,
            CharacterBisSelection.raid_tier_id == tier.id,
        )
    )
    if row is None:
        return SelectionChange(None, None, False)
    if _character_in_open_workflow(session, character.id):
        raise ValueError("An active unfinished reclear depends on this BiS selection.")
    old = row.bis_set
    session.delete(row)
    return SelectionChange(old, None, True)


def select_tier(static: Static, tier: RaidTier) -> SelectionChange:
    if not static.active:
        raise ValueError("A deactivated static cannot be used for new planning.")
    old = static.active_raid_tier
    if old is tier or (old is not None and old.id == tier.id):
        return SelectionChange(old, tier, False)
    static.active_raid_tier = tier
    return SelectionChange(old, tier, True)


def clear_tier(session: Session, static: Static) -> SelectionChange:
    old = static.active_raid_tier
    if old is None:
        return SelectionChange(None, None, False)
    if _open_week(session, static.id) is not None:
        raise ValueError("An active unfinished reclear depends on the tier selection.")
    static.active_raid_tier = None
    return SelectionChange(old, None, True)


def set_hierarchy(
    session: Session, static: Static, abbreviations: str, force: bool = False
) -> JobHierarchy:
    jobs = [part.strip().upper() for part in abbreviations.split(",") if part.strip()]
    if not jobs or len(jobs) != len(set(jobs)):
        raise ValueError("Hierarchy jobs must be non-empty and unique.")
    rows = {job.abbreviation.upper(): job for job in session.scalars(select(Job))}
    unknown = [job for job in jobs if job not in rows]
    if unknown:
        raise ValueError("Unknown jobs: " + ", ".join(unknown))
    if not force:
        required = {
            selection.bis_set.job.abbreviation.upper()
            for member in static.members
            if member.active
            for character in member.characters
            if character.active and character.kind is CharacterKind.MAIN
            for selection in character.bis_selections
            if static.active_raid_tier_id == selection.raid_tier_id
        }
        missing = sorted(required - set(jobs))
        if missing:
            raise ValueError("Hierarchy is missing active main jobs: " + ", ".join(missing))
    for old in session.scalars(select(JobHierarchy).where(JobHierarchy.static_id == static.id)):
        old.active_marker = None
    version = (
        session.scalar(
            select(func.max(JobHierarchy.version)).where(JobHierarchy.static_id == static.id)
        )
        or 0
    ) + 1
    hierarchy = JobHierarchy(static=static, version=version, active_marker=True)
    hierarchy.entries = [
        JobHierarchyEntry(job=rows[abbr], position=index) for index, abbr in enumerate(jobs, 1)
    ]
    session.add(hierarchy)
    session.flush()
    return hierarchy


def _member(session: Session, static: Static, user_id: int) -> StaticMember:
    row = session.scalar(
        select(StaticMember).where(
            StaticMember.static_id == static.id, StaticMember.discord_user_id == user_id
        )
    )
    if row is None:
        raise ValueError("That Discord member is not in the selected static.")
    return row


def _open_week(session: Session, static_id: int) -> ReclearWeek | None:
    return session.scalar(
        select(ReclearWeek).where(
            ReclearWeek.static_id == static_id,
            ReclearWeek.workflow_state.in_(OPEN_WORKFLOW_STATES),
        )
    )


def _character_in_open_workflow(session: Session, character_id: int) -> bool:
    participant = session.scalar(
        select(ReclearParticipant.id)
        .join(ReclearWeek)
        .where(
            ReclearParticipant.character_id == character_id,
            ReclearWeek.workflow_state.in_(OPEN_WORKFLOW_STATES),
        )
        .limit(1)
    )
    if participant is not None:
        return True
    assignment = session.scalar(
        select(LootAssignment.id)
        .join(LootPlan)
        .join(ReclearWeek)
        .where(
            ReclearWeek.workflow_state.in_(OPEN_WORKFLOW_STATES),
            (
                (LootAssignment.intended_character_id == character_id)
                | (LootAssignment.suggested_recipient_id == character_id)
                | (LootAssignment.final_recipient_id == character_id)
                | (LootAssignment.backup_recipient_id == character_id)
            ),
        )
        .limit(1)
    )
    return assignment is not None


def _require_character_in_static(static: Static, character: Character) -> None:
    if character.static_member.static_id != static.id:
        raise ValueError("Character is not in the selected static.")


def _required_text(value: str, label: str, maximum: int) -> str:
    result = value.strip()
    if not result:
        raise ValueError(f"{label} is required.")
    if len(result) > maximum:
        raise ValueError(f"{label} must be {maximum} characters or fewer.")
    return result


def _audit(
    session: Session,
    static_id: int,
    actor_id: int,
    action: str,
    entity_type: str,
    entity_id: int,
    details: object | None = None,
) -> None:
    session.add(
        AuditLog(
            static_id=static_id,
            actor_discord_user_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            details=json.dumps(details) if details is not None else None,
        )
    )
