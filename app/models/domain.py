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
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.database.base import Base
from app.models.enums import (
    CharacterKind,
    ClearMode,
    DistributionErrorType,
    GearClassification,
    GearSlotCode,
    LootAssignmentState,
    LootCategory,
    LootConfirmationType,
    LootPlanState,
    PlannedLootDisposition,
    ReclearWorkflowState,
    WeeklyLootPlanStatus,
    job_uses_offhand,
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
    active_raid_tier_id: Mapped[int | None] = mapped_column(ForeignKey("raid_tiers.id"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    guild: Mapped[DiscordGuild] = relationship(back_populates="statics")
    active_raid_tier: Mapped["RaidTier | None"] = relationship(foreign_keys=[active_raid_tier_id])
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
    inventory_items: Mapped[list["InventoryItem"]] = relationship(back_populates="character")
    bis_selections: Mapped[list["CharacterBisSelection"]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )


class RaidTier(Base):
    __tablename__ = "raid_tiers"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    starts_on: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    floors: Mapped[list["RaidFloor"]] = relationship(
        back_populates="raid_tier", cascade="all, delete-orphan"
    )
    loot_types: Mapped[list["LootType"]] = relationship(
        back_populates="raid_tier", cascade="all, delete-orphan"
    )
    augmentation_material_types: Mapped[list["AugmentationMaterialType"]] = relationship(
        back_populates="raid_tier", cascade="all, delete-orphan"
    )
    bis_sets: Mapped[list["BisSet"]] = relationship(back_populates="raid_tier")


class RaidFloor(Base):
    __tablename__ = "raid_floors"
    __table_args__ = (
        UniqueConstraint("raid_tier_id", "floor_number"),
        UniqueConstraint("id", "raid_tier_id"),
        CheckConstraint("floor_number > 0", name="positive_floor_number"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    raid_tier_id: Mapped[int] = mapped_column(ForeignKey("raid_tiers.id"), nullable=False)
    floor_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    raid_tier: Mapped[RaidTier] = relationship(back_populates="floors")
    loot_rules: Mapped[list["FloorLootRule"]] = relationship(
        back_populates="raid_floor", cascade="all, delete-orphan"
    )


class GearSlot(Base):
    __tablename__ = "gear_slots"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[GearSlotCode] = mapped_column(Enum(GearSlotCode), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)


class Item(Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_item_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    item_level: Mapped[int | None] = mapped_column(Integer)


class LootType(Base):
    __tablename__ = "loot_types"
    __table_args__ = (UniqueConstraint("raid_tier_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    raid_tier_id: Mapped[int] = mapped_column(ForeignKey("raid_tiers.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[LootCategory] = mapped_column(Enum(LootCategory), nullable=False)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    raid_tier: Mapped[RaidTier] = relationship(back_populates="loot_types")
    item: Mapped[Item | None] = relationship()


class AugmentationMaterialType(Base):
    __tablename__ = "augmentation_material_types"
    __table_args__ = (UniqueConstraint("raid_tier_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    raid_tier_id: Mapped[int] = mapped_column(ForeignKey("raid_tiers.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    raid_tier: Mapped[RaidTier] = relationship(back_populates="augmentation_material_types")
    item: Mapped[Item | None] = relationship()


class FloorLootRule(Base):
    __tablename__ = "floor_loot_rules"
    __table_args__ = (
        UniqueConstraint("raid_floor_id", "loot_type_id"),
        CheckConstraint("expected_quantity >= 0", name="nonnegative_expected_quantity"),
        CheckConstraint("book_cost IS NULL OR book_cost >= 0", name="nonnegative_book_cost"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    raid_floor_id: Mapped[int] = mapped_column(ForeignKey("raid_floors.id"), nullable=False)
    loot_type_id: Mapped[int] = mapped_column(ForeignKey("loot_types.id"), nullable=False)
    expected_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    book_cost: Mapped[int | None] = mapped_column(Integer)
    augmentation_material_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("augmentation_material_types.id")
    )
    raid_floor: Mapped[RaidFloor] = relationship(back_populates="loot_rules")
    loot_type: Mapped[LootType] = relationship()
    augmentation_material_type: Mapped[AugmentationMaterialType | None] = relationship()


class BisSet(Base):
    __tablename__ = "bis_sets"
    __table_args__ = (UniqueConstraint("job_id", "raid_tier_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    raid_tier_id: Mapped[int] = mapped_column(ForeignKey("raid_tiers.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    gcd_label: Mapped[str | None] = mapped_column(String(30))
    gear_set_url: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    job: Mapped[Job] = relationship(back_populates="bis_sets")
    raid_tier: Mapped[RaidTier] = relationship(back_populates="bis_sets")
    items: Mapped[list["BisSetItem"]] = relationship(
        back_populates="bis_set", cascade="all, delete-orphan"
    )


class BisSetItem(Base):
    __tablename__ = "bis_set_items"
    __table_args__ = (
        UniqueConstraint("bis_set_id", "gear_slot_id"),
        CheckConstraint("tome_cost IS NULL OR tome_cost >= 0", name="nonnegative_tome_cost"),
        CheckConstraint("book_cost IS NULL OR book_cost >= 0", name="nonnegative_book_cost"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    bis_set_id: Mapped[int] = mapped_column(ForeignKey("bis_sets.id"), nullable=False)
    gear_slot_id: Mapped[int] = mapped_column(ForeignKey("gear_slots.id"), nullable=False)
    classification: Mapped[GearClassification] = mapped_column(
        Enum(GearClassification), nullable=False
    )
    desired_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    raid_floor_id: Mapped[int | None] = mapped_column(ForeignKey("raid_floors.id"))
    loot_type_id: Mapped[int | None] = mapped_column(ForeignKey("loot_types.id"))
    base_tome_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    tome_cost: Mapped[int | None] = mapped_column(Integer)
    augmentation_material_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("augmentation_material_types.id")
    )
    book_cost: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    bis_set: Mapped[BisSet] = relationship(back_populates="items")
    gear_slot: Mapped[GearSlot] = relationship()
    desired_item: Mapped[Item | None] = relationship(foreign_keys=[desired_item_id])
    raid_floor: Mapped[RaidFloor | None] = relationship()
    loot_type: Mapped[LootType | None] = relationship()
    base_tome_item: Mapped[Item | None] = relationship(foreign_keys=[base_tome_item_id])
    augmentation_material_type: Mapped[AugmentationMaterialType | None] = relationship()


class CharacterBisSelection(Base):
    __tablename__ = "character_bis_selections"
    __table_args__ = (
        UniqueConstraint(
            "character_id", "raid_tier_id", name="uq_character_bis_selections_character_tier"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    raid_tier_id: Mapped[int] = mapped_column(ForeignKey("raid_tiers.id"), nullable=False)
    bis_set_id: Mapped[int] = mapped_column(ForeignKey("bis_sets.id"), nullable=False)
    character: Mapped[Character] = relationship(back_populates="bis_selections")
    raid_tier: Mapped[RaidTier] = relationship()
    bis_set: Mapped[BisSet] = relationship()


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


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        UniqueConstraint("character_id", "item_id"),
        CheckConstraint("quantity >= 0", name="nonnegative_quantity"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    character: Mapped[Character] = relationship(back_populates="inventory_items")
    item: Mapped[Item] = relationship()


class CharacterAugmentationInventory(Base):
    __tablename__ = "character_augmentation_inventory"
    __table_args__ = (
        UniqueConstraint("character_id", "augmentation_material_type_id"),
        CheckConstraint("quantity >= 0", name="nonnegative_quantity"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    augmentation_material_type_id: Mapped[int] = mapped_column(
        ForeignKey("augmentation_material_types.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    character: Mapped[Character] = relationship()
    augmentation_material_type: Mapped[AugmentationMaterialType] = relationship()


class CharacterFloorBookBalance(Base):
    __tablename__ = "character_floor_book_balances"
    __table_args__ = (
        UniqueConstraint("character_id", "raid_floor_id"),
        CheckConstraint("earned >= 0", name="nonnegative_earned"),
        CheckConstraint("spent >= 0", name="nonnegative_spent"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    raid_floor_id: Mapped[int] = mapped_column(ForeignKey("raid_floors.id"), nullable=False)
    earned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    spent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    manual_adjustment: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    character: Mapped[Character] = relationship()
    raid_floor: Mapped[RaidFloor] = relationship()

    @property
    def available(self) -> int:
        return self.earned - self.spent + self.manual_adjustment


class WeeklyLockout(Base):
    __tablename__ = "weekly_lockouts"
    __table_args__ = (UniqueConstraint("character_id", "raid_floor_id", "week_start"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    raid_floor_id: Mapped[int] = mapped_column(ForeignKey("raid_floors.id"), nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    cleared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    loot_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    character: Mapped[Character] = relationship()
    raid_floor: Mapped[RaidFloor] = relationship()


class ReclearFloorCompletion(Base):
    __tablename__ = "reclear_floor_completions"
    __table_args__ = (UniqueConstraint("reclear_week_id", "reclear_group_id", "raid_floor_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    reclear_week_id: Mapped[int] = mapped_column(ForeignKey("split_weeks.id"), nullable=False)
    reclear_group_id: Mapped[int] = mapped_column(ForeignKey("split_groups.id"), nullable=False)
    raid_floor_id: Mapped[int] = mapped_column(ForeignKey("raid_floors.id"), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actor_discord_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reclear_week: Mapped["ReclearWeek"] = relationship()
    reclear_group: Mapped["ReclearGroup"] = relationship()
    raid_floor: Mapped["RaidFloor"] = relationship()


class JobHierarchy(Base):
    __tablename__ = "job_hierarchies"
    __table_args__ = (
        UniqueConstraint("static_id", "version", name="uq_job_hierarchies_static_version"),
        UniqueConstraint("static_id", "active_marker", name="uq_job_hierarchies_static_active"),
        CheckConstraint("version > 0", name="positive_version"),
        CheckConstraint("active_marker IS NULL OR active_marker = 1", name="valid_active_marker"),
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
    __table_args__ = (UniqueConstraint("static_id", "week_start"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    static_id: Mapped[int] = mapped_column(ForeignKey("statics.id"), nullable=False)
    raid_tier_id: Mapped[int] = mapped_column(ForeignKey("raid_tiers.id"), nullable=False)
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
    raid_tier: Mapped[RaidTier] = relationship()
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


class LootPlan(Base):
    __tablename__ = "loot_plans"
    __table_args__ = (
        UniqueConstraint("split_week_id", "name"),
        UniqueConstraint("id", "split_week_id", name="uq_loot_plans_id_reclear_week"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    reclear_week_id: Mapped[int] = mapped_column(
        "split_week_id", ForeignKey("split_weeks.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[LootPlanState] = mapped_column(
        Enum(LootPlanState), default=LootPlanState.DRAFT, nullable=False
    )
    mode: Mapped[ClearMode] = mapped_column(
        Enum(ClearMode), default=ClearMode.REGULAR, nullable=False, index=True
    )
    status: Mapped[WeeklyLootPlanStatus] = mapped_column(
        Enum(WeeklyLootPlanStatus),
        default=WeeklyLootPlanStatus.DRAFT,
        nullable=False,
        index=True,
    )
    created_by_discord_user_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_snapshot_version: Mapped[int | None] = mapped_column(Integer)
    source_snapshot: Mapped[str | None] = mapped_column(Text)
    source_state_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    reclear_week: Mapped[ReclearWeek] = relationship()
    runs: Mapped[list["LootPlanRun"]] = relationship(
        back_populates="loot_plan", cascade="all, delete-orphan", order_by="LootPlanRun.run_number"
    )
    assignments: Mapped[list["LootAssignment"]] = relationship(
        back_populates="loot_plan", cascade="all, delete-orphan"
    )


class LootPlanRun(Base):
    __tablename__ = "loot_plan_runs"
    __table_args__ = (
        UniqueConstraint("loot_plan_id", "run_number"),
        UniqueConstraint("loot_plan_id", "name"),
        UniqueConstraint("id", "loot_plan_id", name="uq_loot_plan_runs_id_plan"),
        CheckConstraint("run_number > 0", name="positive_run_number"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    loot_plan_id: Mapped[int] = mapped_column(ForeignKey("loot_plans.id"), nullable=False)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    loot_plan: Mapped[LootPlan] = relationship(back_populates="runs")
    participants: Mapped[list["LootPlanParticipant"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["LootAssignment"]] = relationship(
        back_populates="plan_run", overlaps="assignments,loot_plan"
    )


class LootPlanParticipant(Base):
    __tablename__ = "loot_plan_participants"
    __table_args__ = (UniqueConstraint("plan_run_id", "character_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_run_id: Mapped[int] = mapped_column(ForeignKey("loot_plan_runs.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    designation: Mapped[CharacterKind] = mapped_column(Enum(CharacterKind), nullable=False)
    run: Mapped[LootPlanRun] = relationship(back_populates="participants")
    character: Mapped[Character] = relationship()


class LootAssignment(Base):
    __tablename__ = "loot_assignments"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("expected_drop_instance > 0", name="positive_drop_instance"),
        UniqueConstraint(
            "loot_plan_id",
            "reclear_group_id",
            "raid_floor_id",
            "loot_type_id",
            "expected_drop_instance",
            name="uq_loot_assignment_expected_drop",
        ),
        ForeignKeyConstraint(
            ["plan_run_id", "loot_plan_id"],
            ["loot_plan_runs.id", "loot_plan_runs.loot_plan_id"],
            name="fk_loot_assignments_plan_run_plan",
        ),
        UniqueConstraint(
            "plan_run_id",
            "raid_floor_id",
            "loot_type_id",
            "expected_drop_instance",
            name="uq_loot_assignment_run_expected_drop",
        ),
        CheckConstraint(
            "paired_assignment_id IS NULL OR paired_assignment_id != id",
            name="paired_assignment_not_self",
        ),
        CheckConstraint(
            "disposition != 'ASSIGNED' OR intended_character_id IS NOT NULL",
            name="assigned_has_recipient",
        ),
        CheckConstraint(
            "intended_character_id IS NOT NULL OR recipient_designation IS NULL",
            name="designation_requires_recipient",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    loot_plan_id: Mapped[int] = mapped_column(ForeignKey("loot_plans.id"), nullable=False)
    plan_run_id: Mapped[int | None] = mapped_column(Integer, index=True)
    reclear_group_id: Mapped[int | None] = mapped_column(ForeignKey("split_groups.id"))
    raid_floor_id: Mapped[int] = mapped_column(ForeignKey("raid_floors.id"), nullable=False)
    loot_type_id: Mapped[int] = mapped_column(ForeignKey("loot_types.id"), nullable=False)
    intended_character_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"))
    intended_bis_set_item_id: Mapped[int | None] = mapped_column(ForeignKey("bis_set_items.id"))
    intended_final_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    suggested_recipient_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"))
    final_recipient_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"))
    backup_recipient_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"))
    expected_drop_instance: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    planning_reason: Mapped[str | None] = mapped_column(Text)
    recipient_owns_base_tome_item: Mapped[bool | None] = mapped_column(Boolean)
    hierarchy_position: Mapped[int | None] = mapped_column(Integer)
    recipient_designation: Mapped[CharacterKind | None] = mapped_column(Enum(CharacterKind))
    disposition: Mapped[PlannedLootDisposition] = mapped_column(
        Enum(PlannedLootDisposition), default=PlannedLootDisposition.UNASSIGNED, nullable=False
    )
    paired_assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("loot_assignments.id"), unique=True
    )
    state: Mapped[LootAssignmentState] = mapped_column(
        Enum(LootAssignmentState), default=LootAssignmentState.PROPOSED, nullable=False
    )
    manually_overridden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    loot_plan: Mapped[LootPlan] = relationship(back_populates="assignments", overlaps="assignments")
    plan_run: Mapped[LootPlanRun | None] = relationship(
        back_populates="assignments", overlaps="assignments,loot_plan"
    )
    reclear_group: Mapped[ReclearGroup | None] = relationship()
    raid_floor: Mapped[RaidFloor] = relationship()
    loot_type: Mapped[LootType] = relationship()
    intended_character: Mapped[Character | None] = relationship(
        foreign_keys=[intended_character_id]
    )
    intended_bis_set_item: Mapped[BisSetItem | None] = relationship()
    intended_final_item: Mapped[Item | None] = relationship()
    suggested_recipient: Mapped[Character | None] = relationship(
        foreign_keys=[suggested_recipient_id]
    )
    final_recipient: Mapped[Character | None] = relationship(foreign_keys=[final_recipient_id])
    backup_recipient: Mapped[Character | None] = relationship(foreign_keys=[backup_recipient_id])
    paired_assignment: Mapped["LootAssignment | None"] = relationship(
        remote_side=[id], foreign_keys=[paired_assignment_id], post_update=True
    )
    material_grant: Mapped["ConfirmedReclearMaterialGrant | None"] = relationship(
        back_populates="assignment", cascade="all, delete-orphan", uselist=False
    )
    confirmations: Mapped[list["LootConfirmation"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )
    receipt: Mapped["LootReceipt | None"] = relationship(
        back_populates="assignment", cascade="all, delete-orphan", uselist=False
    )
    completion_items: Mapped[list["LootAssignmentCompletionItem"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )


class ConfirmedReclearMaterialGrant(Base):
    """Bot-confirmed reclear grant history; manual week-one materials do not belong here."""

    __tablename__ = "confirmed_reclear_material_grants"
    __table_args__ = (
        UniqueConstraint("loot_assignment_id"),
        CheckConstraint("quantity > 0", name="positive_quantity"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    loot_assignment_id: Mapped[int] = mapped_column(
        ForeignKey("loot_assignments.id"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id"), nullable=False, index=True
    )
    augmentation_material_type_id: Mapped[int] = mapped_column(
        ForeignKey("augmentation_material_types.id"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    confirmed_by_discord_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    assignment: Mapped[LootAssignment] = relationship(back_populates="material_grant")
    character: Mapped[Character] = relationship()
    augmentation_material_type: Mapped[AugmentationMaterialType] = relationship()


class LootAssignmentCompletionItem(Base):
    """One gear slot completed by a loot assignment.

    Most assignments have one target. A PLD weapon coffer has sword and shield
    targets while remaining one physical drop and one receipt/redemption workflow.
    """

    __tablename__ = "loot_assignment_completion_items"
    __table_args__ = (
        UniqueConstraint(
            "loot_assignment_id",
            "bis_set_item_id",
            name="uq_loot_assignment_completion_item",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    loot_assignment_id: Mapped[int] = mapped_column(
        ForeignKey("loot_assignments.id"), nullable=False
    )
    bis_set_item_id: Mapped[int] = mapped_column(ForeignKey("bis_set_items.id"), nullable=False)
    intended_final_item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    previous_gear_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    previous_gear_classification: Mapped[GearClassification | None] = mapped_column(
        Enum(GearClassification)
    )
    assignment: Mapped[LootAssignment] = relationship(back_populates="completion_items")
    bis_set_item: Mapped[BisSetItem] = relationship()
    intended_final_item: Mapped[Item] = relationship(foreign_keys=[intended_final_item_id])
    previous_gear_item: Mapped[Item | None] = relationship(foreign_keys=[previous_gear_item_id])


class LootConfirmation(Base):
    __tablename__ = "loot_confirmations"
    id: Mapped[int] = mapped_column(primary_key=True)
    loot_assignment_id: Mapped[int] = mapped_column(
        ForeignKey("loot_assignments.id"), nullable=False
    )
    confirmation_type: Mapped[LootConfirmationType] = mapped_column(
        Enum(LootConfirmationType), nullable=False
    )
    result: Mapped[bool] = mapped_column(Boolean, nullable=False)
    answered_by_discord_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("loot_confirmations.id"))
    previous_gear_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    previous_gear_classification: Mapped[GearClassification | None] = mapped_column(
        Enum(GearClassification)
    )
    assignment: Mapped[LootAssignment] = relationship(back_populates="confirmations")
    supersedes: Mapped["LootConfirmation | None"] = relationship(
        remote_side="LootConfirmation.id", uselist=False
    )


class DistributionError(Base):
    __tablename__ = "distribution_errors"
    id: Mapped[int] = mapped_column(primary_key=True)
    reclear_week_id: Mapped[int] = mapped_column(ForeignKey("split_weeks.id"), nullable=False)
    loot_assignment_id: Mapped[int] = mapped_column(
        ForeignKey("loot_assignments.id"), nullable=False
    )
    intended_recipient_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"))
    actual_recipient_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"))
    error_type: Mapped[DistributionErrorType] = mapped_column(
        Enum(DistributionErrorType), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reported_by_discord_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    reclear_week: Mapped[ReclearWeek] = relationship()
    loot_assignment: Mapped[LootAssignment] = relationship()
    intended_recipient: Mapped[Character | None] = relationship(
        foreign_keys=[intended_recipient_id]
    )
    actual_recipient: Mapped[Character | None] = relationship(foreign_keys=[actual_recipient_id])


class LootReceipt(Base):
    __tablename__ = "loot_receipts"
    __table_args__ = (CheckConstraint("quantity > 0", name="positive_quantity"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    loot_assignment_id: Mapped[int] = mapped_column(
        ForeignKey("loot_assignments.id"), unique=True, nullable=False
    )
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    assignment: Mapped[LootAssignment] = relationship(back_populates="receipt")
    item: Mapped[Item | None] = relationship()


class PriorityRule(Base):
    __tablename__ = "priority_rules"
    __table_args__ = (
        UniqueConstraint("static_id", "name"),
        CheckConstraint("priority >= 0", name="nonnegative_priority"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    static_id: Mapped[int] = mapped_column(ForeignKey("statics.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    character_kind: Mapped[CharacterKind | None] = mapped_column(Enum(CharacterKind))
    gear_classification: Mapped[GearClassification | None] = mapped_column(Enum(GearClassification))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    static: Mapped[Static] = relationship()


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


def _tier_id(value: object) -> int | None:
    return getattr(value, "raid_tier_id", None) or getattr(
        getattr(value, "raid_tier", None), "id", None
    )


@event.listens_for(Session, "before_flush")
def validate_cross_tier_models(session: Session, *_: object) -> None:
    for obj in session.new.union(session.dirty):
        if isinstance(obj, CharacterBisSelection):
            selection_tier = obj.raid_tier_id or getattr(obj.raid_tier, "id", None)
            set_tier = _tier_id(obj.bis_set)
            wrong_transient_tier = (
                obj.raid_tier is not None
                and obj.bis_set is not None
                and obj.bis_set.raid_tier is not None
                and obj.raid_tier is not obj.bis_set.raid_tier
            )
            if wrong_transient_tier or (
                selection_tier is not None and set_tier is not None and selection_tier != set_tier
            ):
                raise ValueError("selected BiS set belongs to another raid tier")
        elif isinstance(obj, BisSetItem):
            _validate_bis_requirement(obj)


def _validate_bis_requirement(item: BisSetItem) -> None:
    if item.tome_cost is not None and item.tome_cost < 0:
        raise ValueError("tome_cost must be nonnegative")
    if item.book_cost is not None and item.book_cost < 0:
        raise ValueError("book_cost must be nonnegative")
    if item.classification == GearClassification.NOT_APPLICABLE:
        fields = (
            item.desired_item,
            item.desired_item_id,
            item.raid_floor,
            item.raid_floor_id,
            item.loot_type,
            item.loot_type_id,
            item.base_tome_item,
            item.base_tome_item_id,
            item.tome_cost,
            item.augmentation_material_type,
            item.augmentation_material_type_id,
            item.book_cost,
        )
        if any(value is not None for value in fields):
            raise ValueError("NOT_APPLICABLE cannot define an item requirement")
    slot_code = getattr(item.gear_slot, "code", None)
    job_abbreviation = getattr(getattr(item.bis_set, "job", None), "abbreviation", None)
    if slot_code is GearSlotCode.OFFHAND and job_abbreviation:
        uses_offhand = job_uses_offhand(job_abbreviation)
        if uses_offhand and item.classification is GearClassification.NOT_APPLICABLE:
            raise ValueError("PLD OFFHAND must define an applicable item requirement")
        if not uses_offhand and item.classification is not GearClassification.NOT_APPLICABLE:
            raise ValueError(f"{job_abbreviation} OFFHAND must be NOT_APPLICABLE")
    if item.classification == GearClassification.AUGMENTED_TOME and (
        (item.base_tome_item is None and item.base_tome_item_id is None)
        or (item.augmentation_material_type is None and item.augmentation_material_type_id is None)
    ):
        raise ValueError("AUGMENTED_TOME requires base_tome_item and augmentation_material_type")
    set_tier = _tier_id(item.bis_set)
    for reference, field in (
        (item.raid_floor, "raid_floor"),
        (item.loot_type, "loot_type"),
        (item.augmentation_material_type, "augmentation_material_type"),
    ):
        reference_tier = _tier_id(reference)
        if set_tier is not None and reference_tier is not None and set_tier != reference_tier:
            raise ValueError(f"{field} belongs to another raid tier")


SplitWeek = ReclearWeek
SplitGroup = ReclearGroup
SplitParticipant = ReclearParticipant
