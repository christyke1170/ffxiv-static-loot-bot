"""Normalized SQLAlchemy models for static, gear, and weekly loot state."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.enums import (
    CharacterKind,
    ClearMode,
    GearClassification,
    GearSlotCode,
    ReclearWorkflowState,
)


class DiscordGuild(Base):
    __tablename__ = "discord_guilds"
    id: Mapped[int] = mapped_column(primary_key=True)
    discord_guild_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    statics: Mapped[list["Static"]] = relationship(back_populates="guild")


class Static(Base):
    __tablename__ = "statics"
    __table_args__ = (UniqueConstraint("guild_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(ForeignKey("discord_guilds.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    crafted_item_level: Mapped[int | None] = mapped_column(Integer)
    guild: Mapped[DiscordGuild] = relationship(back_populates="statics")
    members: Mapped[list["StaticMember"]] = relationship(back_populates="static")
    reclear_weeks: Mapped[list["ReclearWeek"]] = relationship(back_populates="static")
    job_hierarchies: Mapped[list["JobHierarchy"]] = relationship(back_populates="static")

    @property
    def split_weeks(self) -> list["ReclearWeek"]:
        return self.reclear_weeks


class StaticMember(Base):
    __tablename__ = "static_members"
    __table_args__ = (UniqueConstraint("static_id", "discord_user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    static_id: Mapped[int] = mapped_column(ForeignKey("statics.id"), nullable=False)
    discord_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    static: Mapped[Static] = relationship(back_populates="members")
    characters: Mapped[list["Character"]] = relationship(back_populates="static_member")


class UserStaticPreference(Base):
    """Persist the static selected by a Discord user in a guild."""

    __tablename__ = "user_static_preferences"
    __table_args__ = (UniqueConstraint("guild_id", "discord_user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(ForeignKey("discord_guilds.id"), nullable=False)
    discord_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    static_id: Mapped[int] = mapped_column(ForeignKey("statics.id"), nullable=False)
    static: Mapped[Static] = relationship()


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    abbreviation: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(30), default="Unknown", nullable=False)
    uses_offhand: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    characters: Mapped[list["Character"]] = relationship(back_populates="job")
    bis_sets: Mapped[list["BisSet"]] = relationship(back_populates="job")


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (UniqueConstraint("name", "world"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    static_member_id: Mapped[int] = mapped_column(ForeignKey("static_members.id"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    world: Mapped[str] = mapped_column(String(50), nullable=False)
    kind: Mapped[CharacterKind] = mapped_column(Enum(CharacterKind), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    static_member: Mapped[StaticMember] = relationship(back_populates="characters")
    job: Mapped[Job] = relationship(back_populates="characters")
    gear_slots: Mapped[list["CharacterGearSlot"]] = relationship(back_populates="character")


class GearSlot(Base):
    __tablename__ = "gear_slots"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[GearSlotCode] = mapped_column(Enum(GearSlotCode), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)


class Item(Base):
    """Named loot/material resource metadata; never an equipment identity."""

    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
    # Legacy columns are retained only because this resource table is heavily FK-referenced.
    # Corrected application paths never read or write them.
    legacy_external_item_id: Mapped[int | None] = mapped_column(
        "external_item_id", Integer, unique=True
    )
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    legacy_item_level: Mapped[int | None] = mapped_column("item_level", Integer)


class BisSet(Base):
    __tablename__ = "bis_sets"
    __table_args__ = (UniqueConstraint("static_id", "job_id", name="uq_bis_sets_static_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    static_id: Mapped[int | None] = mapped_column(ForeignKey("statics.id"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    gcd_label: Mapped[str | None] = mapped_column(String(30))
    gear_set_url: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    static: Mapped[Static | None] = relationship()
    job: Mapped[Job] = relationship(back_populates="bis_sets")
    items: Mapped[list["BisSetItem"]] = relationship(
        back_populates="bis_set", cascade="all, delete-orphan"
    )


class BisSetItem(Base):
    __tablename__ = "bis_set_items"
    __table_args__ = (
        UniqueConstraint("bis_set_id", "gear_slot_id", name="uq_bis_set_items_neutral"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    bis_set_id: Mapped[int] = mapped_column(ForeignKey("bis_sets.id"), nullable=False)
    gear_slot_id: Mapped[int] = mapped_column(ForeignKey("gear_slots.id"), nullable=False)
    classification: Mapped[GearClassification] = mapped_column(
        Enum(GearClassification), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)
    bis_set: Mapped[BisSet] = relationship(back_populates="items")
    gear_slot: Mapped[GearSlot] = relationship()


class CharacterGearSlot(Base):
    __tablename__ = "character_gear_slots"
    __table_args__ = (UniqueConstraint("character_id", "gear_slot_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    gear_slot_id: Mapped[int] = mapped_column(ForeignKey("gear_slots.id"), nullable=False)
    current_classification: Mapped[GearClassification] = mapped_column(
        Enum(GearClassification), nullable=False
    )
    manually_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    character: Mapped[Character] = relationship(back_populates="gear_slots")
    gear_slot: Mapped[GearSlot] = relationship()


class WeeklyLockout(Base):
    __tablename__ = "weekly_lockouts"
    __table_args__ = (
        UniqueConstraint(
            "character_id", "floor_number", "week_start", name="uq_weekly_lockouts_neutral"
        ),
        Index(
            "uq_weekly_lockout_neutral_floor",
            "character_id",
            "floor_number",
            "week_start",
            unique=True,
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    floor_number: Mapped[int] = mapped_column(Integer, nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    cleared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    loot_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ReclearWeekFloor(Base):
    """Logical fixed floor state for a neutral weekly reclear."""

    __tablename__ = "reclear_week_floors"
    __table_args__ = (
        UniqueConstraint("reclear_week_id", "floor_number"),
        CheckConstraint("floor_number BETWEEN 1 AND 4", name="valid_reclear_week_floor_number"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    reclear_week_id: Mapped[int] = mapped_column(ForeignKey("split_weeks.id"), nullable=False)
    floor_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reclear_week: Mapped["ReclearWeek"] = relationship(back_populates="neutral_floors")


class JobHierarchy(Base):
    __tablename__ = "job_hierarchies"
    __table_args__ = (
        UniqueConstraint("static_id", "version", name="uq_job_hierarchies_static_version"),
        UniqueConstraint("static_id", "active_marker", name="uq_job_hierarchies_static_active"),
        CheckConstraint("version > 0", name="positive_version"),
        CheckConstraint(
            "active_marker IS NULL OR active_marker IS TRUE", name="valid_active_marker"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    static_id: Mapped[int] = mapped_column(ForeignKey("statics.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String(100))
    active_marker: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    static: Mapped[Static] = relationship(back_populates="job_hierarchies")
    entries: Mapped[list["JobHierarchyEntry"]] = relationship(
        back_populates="hierarchy",
        cascade="all, delete-orphan",
        order_by="JobHierarchyEntry.position",
    )

    @property
    def active(self) -> bool:
        return self.active_marker is True


class JobHierarchyEntry(Base):
    __tablename__ = "job_hierarchy_entries"
    __table_args__ = (
        UniqueConstraint("hierarchy_id", "job_id", name="uq_job_hierarchy_entries_hierarchy_job"),
        UniqueConstraint(
            "hierarchy_id", "position", name="uq_job_hierarchy_entries_hierarchy_position"
        ),
        CheckConstraint("position > 0", name="positive_position"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    hierarchy_id: Mapped[int] = mapped_column(ForeignKey("job_hierarchies.id"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    hierarchy: Mapped[JobHierarchy] = relationship(back_populates="entries")
    job: Mapped[Job] = relationship()


class ReclearWeek(Base):
    __tablename__ = "split_weeks"
    __table_args__ = (UniqueConstraint("static_id", "week_start", name="uq_split_weeks_static_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    static_id: Mapped[int] = mapped_column(ForeignKey("statics.id"), nullable=False)
    hierarchy_id: Mapped[int | None] = mapped_column(ForeignKey("job_hierarchies.id"))
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    clear_mode: Mapped[ClearMode] = mapped_column(Enum(ClearMode), nullable=False)
    workflow_state: Mapped[ReclearWorkflowState] = mapped_column(
        Enum(ReclearWorkflowState), default=ReclearWorkflowState.DRAFT, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    static: Mapped[Static] = relationship(back_populates="reclear_weeks")
    source_hierarchy: Mapped[JobHierarchy | None] = relationship()
    groups: Mapped[list["ReclearGroup"]] = relationship(
        back_populates="reclear_week", cascade="all, delete-orphan"
    )
    participants: Mapped[list["ReclearParticipant"]] = relationship(
        back_populates="reclear_week", cascade="all, delete-orphan", overlaps="participants,group"
    )
    hierarchy_snapshot: Mapped[list["WeeklyHierarchySnapshotEntry"]] = relationship(
        back_populates="reclear_week",
        cascade="all, delete-orphan",
        order_by="WeeklyHierarchySnapshotEntry.position",
    )
    neutral_floors: Mapped[list["ReclearWeekFloor"]] = relationship(
        back_populates="reclear_week",
        cascade="all, delete-orphan",
        order_by="ReclearWeekFloor.floor_number",
    )


class ReclearGroup(Base):
    __tablename__ = "split_groups"
    __table_args__ = (
        UniqueConstraint("split_week_id", "group_number"),
        UniqueConstraint("id", "split_week_id"),
        CheckConstraint("group_number IN (1, 2)", name="group_number_one_or_two"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    reclear_week_id: Mapped[int] = mapped_column(
        "split_week_id", ForeignKey("split_weeks.id"), nullable=False
    )
    group_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reclear_week: Mapped[ReclearWeek] = relationship(back_populates="groups")
    participants: Mapped[list["ReclearParticipant"]] = relationship(
        back_populates="group", overlaps="participants,reclear_week"
    )

    def __init__(self, **kwargs: object) -> None:
        if "split_week" in kwargs:
            kwargs["reclear_week"] = kwargs.pop("split_week")
        super().__init__(**kwargs)


class ReclearParticipant(Base):
    __tablename__ = "split_participants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["split_group_id", "split_week_id"], ["split_groups.id", "split_groups.split_week_id"]
        ),
        UniqueConstraint("split_week_id", "character_id"),
        UniqueConstraint("split_group_id", "character_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    reclear_week_id: Mapped[int] = mapped_column(
        "split_week_id", ForeignKey("split_weeks.id"), nullable=False
    )
    group_id: Mapped[int] = mapped_column("split_group_id", Integer, nullable=False)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    reclear_week: Mapped[ReclearWeek] = relationship(
        back_populates="participants", overlaps="participants,group"
    )
    group: Mapped[ReclearGroup] = relationship(
        back_populates="participants", overlaps="participants,reclear_week"
    )
    character: Mapped[Character] = relationship()

    def __init__(self, **kwargs: object) -> None:
        for old, new in {"split_week": "reclear_week", "split_group": "group"}.items():
            if old in kwargs:
                kwargs[new] = kwargs.pop(old)
        super().__init__(**kwargs)


class WeeklyHierarchySnapshotEntry(Base):
    __tablename__ = "weekly_hierarchy_snapshot_entries"
    __table_args__ = (
        UniqueConstraint("reclear_week_id", "job_id", name="uq_weekly_snapshot_week_job"),
        UniqueConstraint("reclear_week_id", "position", name="uq_weekly_snapshot_week_position"),
        CheckConstraint("position > 0", name="positive_position"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    reclear_week_id: Mapped[int] = mapped_column(ForeignKey("split_weeks.id"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    job_abbreviation: Mapped[str] = mapped_column(String(10), nullable=False)
    reclear_week: Mapped[ReclearWeek] = relationship(back_populates="hierarchy_snapshot")
    job: Mapped[Job] = relationship()


class V2Plan(Base):
    __tablename__ = "v2_plans"
    __table_args__ = (UniqueConstraint("reclear_week_id", name="uq_v2_plans_week"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    static_id: Mapped[int] = mapped_column(ForeignKey("statics.id"), nullable=False)
    reclear_week_id: Mapped[int] = mapped_column(ForeignKey("split_weeks.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    week_number: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    state_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    score_json: Mapped[str | None] = mapped_column(Text)
    partitions_evaluated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    static: Mapped[Static] = relationship()
    reclear_week: Mapped[ReclearWeek] = relationship()
    runs: Mapped[list["V2PlanRun"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["V2PlanAssignment"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    unassigned: Mapped[list["V2PlanUnassigned"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class V2PlanRun(Base):
    __tablename__ = "v2_plan_runs"
    __table_args__ = (UniqueConstraint("plan_id", "run_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("v2_plans.id"), nullable=False)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_group_id: Mapped[int | None] = mapped_column(Integer)
    plan: Mapped[V2Plan] = relationship(back_populates="runs")
    participants: Mapped[list["V2PlanParticipant"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["V2PlanAssignment"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class V2PlanParticipant(Base):
    __tablename__ = "v2_plan_participants"
    __table_args__ = (UniqueConstraint("run_id", "character_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("v2_plan_runs.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    designation: Mapped[str] = mapped_column(String(10), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    run: Mapped[V2PlanRun] = relationship(back_populates="participants")
    character: Mapped[Character] = relationship()


class V2PlanAssignment(Base):
    __tablename__ = "v2_plan_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("v2_plans.id"), nullable=False)
    run_id: Mapped[int] = mapped_column(ForeignKey("v2_plan_runs.id"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    floor_number: Mapped[int] = mapped_column(Integer, nullable=False)
    loot_key: Mapped[str] = mapped_column(String(100), nullable=False)
    primary_slot: Mapped[str | None] = mapped_column(String(30))
    material_key: Mapped[str | None] = mapped_column(String(100))
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"))
    recipient_job: Mapped[str | None] = mapped_column(String(10))
    recipient_kind: Mapped[str | None] = mapped_column(String(10))
    owned_alt_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"))
    hierarchy_position: Mapped[int | None] = mapped_column(Integer)
    disposition: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    fairness_count: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    score_json: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[V2Plan] = relationship(back_populates="assignments")
    run: Mapped[V2PlanRun] = relationship(back_populates="assignments")
    effects: Mapped[list["V2PlanEffect"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )


class V2PlanEffect(Base):
    __tablename__ = "v2_plan_effects"
    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("v2_plan_assignments.id"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_key: Mapped[str] = mapped_column(String(30), nullable=False)
    resulting_category: Mapped[str] = mapped_column(String(30), nullable=False)
    assignment: Mapped[V2PlanAssignment] = relationship(back_populates="effects")


class V2PlanUnassigned(Base):
    __tablename__ = "v2_plan_unassigned"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("v2_plans.id"), nullable=False)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    group_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    floor_number: Mapped[int] = mapped_column(Integer, nullable=False)
    loot_key: Mapped[str] = mapped_column(String(100), nullable=False)
    primary_slot: Mapped[str | None] = mapped_column(String(30))
    material_key: Mapped[str | None] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[V2Plan] = relationship(back_populates="unassigned")


class V2Confirmation(Base):
    __tablename__ = "v2_confirmations"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "resource_key", "action", name="uq_v2_confirmation_action"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("v2_plan_assignments.id"), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    note: Mapped[str | None] = mapped_column(Text)
    assignment: Mapped[V2PlanAssignment] = relationship()
    recipient: Mapped[Character | None] = relationship()
    effects: Mapped[list["V2EffectLedger"]] = relationship(
        back_populates="confirmation", cascade="all, delete-orphan"
    )


class V2EffectLedger(Base):
    __tablename__ = "v2_effect_ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    confirmation_id: Mapped[int] = mapped_column(ForeignKey("v2_confirmations.id"), nullable=False)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    slot_key: Mapped[str] = mapped_column(String(30), nullable=False)
    resulting_category: Mapped[str] = mapped_column(String(30), nullable=False)
    before_category: Mapped[str | None] = mapped_column(String(30))
    after_category: Mapped[str | None] = mapped_column(String(30))
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmation: Mapped[V2Confirmation] = relationship(back_populates="effects")
    recipient: Mapped[Character] = relationship()


class V2ResourceBalance(Base):
    __tablename__ = "v2_resource_balances"
    __table_args__ = (
        UniqueConstraint("plan_id", "recipient_id", "resource_key", name="uq_v2_resource_balance"),
        CheckConstraint(
            "(plan_id IS NOT NULL AND static_id IS NULL) OR "
            "(plan_id IS NULL AND static_id IS NOT NULL)",
            name="v2_resource_balance_scope",
        ),
        CheckConstraint(
            "resource_key IN ("
            "'BOOK_FLOOR_1','BOOK_FLOOR_2','BOOK_FLOOR_3','BOOK_FLOOR_4',"
            "'ACCESSORY_GLAZE','ARMOR_TWINE','ACCESSORY_COFFER','HEAD_COFFER',"
            "'GLOVES_COFFER','BOOTS_COFFER','CHEST_COFFER','PANTS_COFFER',"
            "'WEAPON_COFFER','WEAPON_TOMESTONE','WEAPON_AUGMENT')",
            name="supported_v2_resource_key",
        ),
        Index(
            "uq_v2_current_resource_balance",
            "static_id",
            "recipient_id",
            "resource_key",
            unique=True,
        ),
        CheckConstraint("quantity >= 0", name="nonnegative_v2_resource_quantity"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("v2_plans.id"))
    static_id: Mapped[int | None] = mapped_column(ForeignKey("statics.id"))
    recipient_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    plan: Mapped[V2Plan] = relationship()
    static: Mapped[Static | None] = relationship()
    recipient: Mapped[Character] = relationship()


class NeutralResourceMigrationIssue(Base):
    __tablename__ = "neutral_resource_migration_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    character: Mapped[Character] = relationship()


class V2Correction(Base):
    __tablename__ = "v2_corrections"
    id: Mapped[int] = mapped_column(primary_key=True)
    confirmation_id: Mapped[int] = mapped_column(ForeignKey("v2_confirmations.id"), nullable=False)
    correction_type: Mapped[str] = mapped_column(String(30), nullable=False)
    corrected_success: Mapped[bool | None] = mapped_column(Boolean)
    actor_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmation: Mapped[V2Confirmation] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    static_id: Mapped[int | None] = mapped_column(ForeignKey("statics.id"))
    actor_discord_user_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    static: Mapped[Static | None] = relationship()
