"""Discord views for database-backed generated loot plans."""

from __future__ import annotations

import discord

from app.models import CharacterKind, WeeklyLootPlanStatus
from app.services.loot_plan_lifecycle import load_active_loot_plan
from bot.checks import is_raid_leader
from bot.services.commands import selected


def _status(status: WeeklyLootPlanStatus) -> str:
    labels = {
        "DRAFT": "Draft",
        "READY": "Ready",
        "APPLIED": "Applied",
        "CANCELLED": "Cancelled",
    }
    return labels[status.value]


def _state(result) -> str:
    if result.staleness.value == "CURRENT":
        return "Plan State: Current"
    if result.staleness.value == "STALE":
        return (
            "⚠ Plan State: Stale\n"
            "This plan can no longer be safely confirmed. Cancel it and generate a new plan."
        )
    return (
        "⚠ Plan State: Unverifiable\n"
        "This older plan has no compatible source snapshot and must be regenerated."
    )


def _recipient(row) -> str:
    if row.recipient_id is None:
        return "Free Roll"
    return f"{row.recipient_name} ({row.recipient_job})"


def plan_pages(result) -> list[str]:
    overview = [
        f"**{result.static_name} — Loot Plan**",
        f"Tier: {result.tier_name}",
        f"Target week: {result.target_week}",
        f"Mode: {result.mode.value.title()}",
        f"Status: {_status(result.status)}",
        f"Created: {result.created_at:%Y-%m-%d %H:%M UTC}",
        (
            "Creator: Recorded Discord actor"
            if result.creator_discord_user_id
            else "Creator: Recorded actor"
        ),
        _state(result),
    ]
    pages = ["\n".join(overview)]
    for run in result.runs:
        roster_title = f"**{run.name} Participants**"
        groups = []
        for designation in (CharacterKind.MAIN, CharacterKind.ALT):
            rows = [p for p in run.participants if p.designation is designation]
            if rows:
                groups.append(
                    designation.value.title()
                    + "\n"
                    + "\n".join(f"{p.character_name} — {p.job}" for p in rows)
                )
        pages.append(roster_title + "\n" + "\n\n".join(groups))
        floors: dict[int, list[str]] = {}
        consumed_pairs: set[int] = set()
        for assignment in run.assignments:
            if assignment.assignment_id in consumed_pairs:
                continue
            if assignment.loot_label in {"Weapon Tomestone", "Weapon Augment"}:
                paired = next(
                    (
                        row
                        for row in run.assignments
                        if row.assignment_id == assignment.paired_assignment_id
                    ),
                    None,
                )
                if paired is not None:
                    consumed_pairs.update({assignment.assignment_id, paired.assignment_id})
                    label = "Weapon Tomestone + Weapon Augment"
                    floors.setdefault(assignment.floor_number, []).append(
                        f"{label}: {_recipient(assignment)}"
                    )
                    continue
                continue
            floors.setdefault(assignment.floor_number, []).append(
                f"{assignment.loot_label}: {_recipient(assignment)}"
            )
        assignment_lines = [f"**{run.name} Assignments**"]
        for floor_number in sorted(floors):
            assignment_lines.append(f"\nFloor {floor_number}\n" + "\n".join(floors[floor_number]))
        pages.append("\n".join(assignment_lines))
    warnings = list(result.validation_warnings)
    warnings.extend(reason.message for reason in result.stale_reasons)
    if warnings:
        pages.append("**Warnings**\n" + "\n".join(f"- {warning}" for warning in warnings))
    return [page[:1990] for page in pages] or ["No plan details are available."]


class LootPlanView(discord.ui.LayoutView):
    def __init__(self, bot, result, owner_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.result = result
        self.owner_id = owner_id
        self.pages = plan_pages(result)
        self.page = 0
        self._build()

    def _build(self):
        self.clear_items()
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(self.pages[self.page]),
                discord.ui.TextDisplay(f"Page {self.page + 1}/{len(self.pages)}"),
            )
        )
        previous = discord.ui.Button(
            label="Previous", custom_id="loot-plan:previous", disabled=self.page == 0
        )
        next_button = discord.ui.Button(
            label="Next", custom_id="loot-plan:next", disabled=self.page == len(self.pages) - 1
        )
        previous.callback = self.previous
        next_button.callback = self.next
        self.add_item(discord.ui.ActionRow(previous, next_button))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the user who opened this plan may control it.", ephemeral=True
            )
            return False
        if interaction.guild is None:
            await interaction.response.send_message(
                "This plan view is not available in this guild.", ephemeral=True
            )
            return False
        with self.bot.session_factory() as session:
            current = selected(session, interaction)
            if current.id != self.result.static_id:
                await interaction.response.send_message(
                    "This plan view is for another selected static.", ephemeral=True
                )
                return False
            if current.guild.discord_guild_id != interaction.guild.id or not current.active:
                await interaction.response.send_message(
                    "This plan is not available for the selected guild/static.", ephemeral=True
                )
                return False
        return True

    async def previous(self, interaction):
        self.page = max(0, self.page - 1)
        self._build()
        await interaction.response.edit_message(view=self)

    async def next(self, interaction):
        self.page = min(len(self.pages) - 1, self.page + 1)
        self._build()
        await interaction.response.edit_message(view=self)

    async def on_timeout(self):
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True


class LootPlanCancelView(discord.ui.LayoutView):
    def __init__(self, bot, plan_id: int, static_id: int, owner_id: int, text: str):
        super().__init__(timeout=300)
        self.bot = bot
        self.plan_id = plan_id
        self.static_id = static_id
        self.owner_id = owner_id
        self._build(text)

    def _build(self, text):
        self.clear_items()
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(text[:1900])))
        cancel = discord.ui.Button(
            label="Cancel Plan",
            style=discord.ButtonStyle.danger,
            custom_id=f"loot-plan-cancel:{self.plan_id}:ok",
        )
        keep = discord.ui.Button(
            label="Keep Plan", custom_id=f"loot-plan-cancel:{self.plan_id}:keep"
        )
        cancel.callback = self.confirm
        keep.callback = self.keep
        self.add_item(discord.ui.ActionRow(cancel, keep))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or not is_raid_leader(interaction, None):
            await interaction.response.send_message(
                "Only the invoking raid leader or administrator may confirm this action.",
                ephemeral=True,
            )
            return False
        if interaction.guild is None:
            await interaction.response.send_message(
                "This action can only be used in a Discord guild.", ephemeral=True
            )
            return False
        return True

    async def confirm(self, interaction):
        from app.services import cancel_loot_plan

        with self.bot.session_factory() as session:
            static = selected(session, interaction)
            if static.id != self.static_id:
                raise ValueError("This cancellation preview is stale for the selected static.")
            active = load_active_loot_plan(session, static.id)
            if active.plan_id != self.plan_id:
                raise ValueError("This cancellation preview is stale; the active plan changed.")
            result = cancel_loot_plan(session, self.plan_id, interaction.user.id)
        self._disable()
        await interaction.response.edit_message(
            content=f"Plan cancelled for {result.static_name}, week {result.target_week}.",
            view=self,
        )
        self.stop()

    async def keep(self, interaction):
        self._disable()
        await interaction.response.edit_message(
            content="Plan kept; no database changes were made.", view=self
        )
        self.stop()

    def _disable(self):
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True

    async def on_timeout(self):
        self._disable()
