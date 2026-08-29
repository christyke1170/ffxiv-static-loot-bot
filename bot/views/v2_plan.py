"""Unregistered Discord presentation for immutable neutral V2 plans."""

from __future__ import annotations

import discord

from app.models import ClearMode
from bot.checks import is_raid_leader
from bot.services.commands import selected


def _recipient(assignment, names: dict[int, str]) -> str:
    if assignment.recipient_id is None:
        return "Free-for-all (recipient required)"
    return names.get(assignment.recipient_id, "Assigned recipient")


def _assignment_line(assignment, names: dict[int, str]) -> str:
    effects = ", ".join(
        f"{effect.slot_key.title()} -> {effect.resulting_category.replace('_', ' ').title()}"
        for effect in assignment.gear_effects
    )
    resource = (
        getattr(assignment, "material_type", None)
        or getattr(assignment, "material_key", None)
        or getattr(assignment, "loot_type", None)
        or getattr(assignment, "loot_key", "resource")
    )
    if effects:
        resource += f" [{effects}]"
    return f"{resource}: {_recipient(assignment, names)}"


def v2_plan_pages(result, names: dict[int, str] | None = None) -> list[str]:
    """Render only neutral persisted-plan fields, split into Discord-sized pages."""
    names = names or {}
    proposal = result.proposal
    lines = [
        f"**V2 Weekly Plan - Static {proposal.static_id}**",
        f"Week: {proposal.week_id} (week {proposal.week_number})",
        f"Mode: {proposal.mode.value.title()}",
        "",
    ]
    pages = ["\n".join(lines)]
    if proposal.mode is ClearMode.REGULAR:
        assignments = proposal.assignments
        roster = "\n".join(
            f"- {names.get(row.recipient_id, str(row.recipient_id))} "
            f"({row.recipient_job or 'Job unavailable'})"
            for row in assignments
            if row.recipient_id is not None
        )
        pages.append("**Regular roster**\n" + (roster or "No assigned recipients."))
        unassigned = proposal.unassigned
    else:
        for group in proposal.groups:
            pages.append(
                f"**Run {'A' if group.group_number == 1 else 'B'} roster**\n"
                + "\n".join(
                    f"- {names.get(identifier, str(identifier))}"
                    for identifier in group.participant_ids
                )
            )
        unassigned = proposal.unassigned
        for group in proposal.groups:
            floors: dict[int, list[str]] = {}
            for assignment in group.assignments:
                floors.setdefault(assignment.floor_number, []).append(
                    _assignment_line(assignment, names)
                )
            pages.append(
                f"**Run {'A' if group.group_number == 1 else 'B'} assignments**\n"
                + "\n".join(
                    f"Floor {floor}\n" + "\n".join(rows) for floor, rows in sorted(floors.items())
                )
            )
    if proposal.mode is ClearMode.REGULAR:
        floors: dict[int, list[str]] = {}
        for assignment in assignments:
            floors.setdefault(assignment.floor_number, []).append(
                _assignment_line(assignment, names)
            )
        pages.append(
            "**Assignments**\n"
            + "\n".join(
                f"Floor {floor}\n" + "\n".join(rows) for floor, rows in sorted(floors.items())
            )
        )
    if unassigned:
        pages.append(
            "**Free-for-all / unassigned resources**\n"
            + "\n".join(
                f"- Floor {row.floor_number}: "
                f"{getattr(row, 'loot_type', None) or getattr(row, 'loot_key', 'resource')} - "
                f"{row.reason}"
                for row in unassigned
            )
        )
    if proposal.warnings:
        pages.append(
            "**Configuration warnings**\n"
            + "\n".join(f"- {warning}" for warning in proposal.warnings)
        )
    return [page[:1990] for page in pages] or ["No V2 plan details are available."]


class V2PlanView(discord.ui.LayoutView):
    """Owner/raid-leader restricted, unregistered V2 plan pager."""

    def __init__(self, bot, result, owner_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.result = result
        self.owner_id = owner_id
        self.page = 0
        self.pages = v2_plan_pages(result)
        self._build()

    def _build(self):
        self.clear_items()
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(self.pages[self.page]),
                discord.ui.TextDisplay(f"Page {self.page + 1}/{len(self.pages)}"),
            )
        )
        previous = discord.ui.Button(label="Previous", disabled=self.page == 0)
        following = discord.ui.Button(label="Next", disabled=self.page == len(self.pages) - 1)
        previous.callback = self.previous
        following.callback = self.next
        self.add_item(discord.ui.ActionRow(previous, following))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or not is_raid_leader(interaction, None):
            await interaction.response.send_message(
                "Only the invoking raid leader may control this view.", ephemeral=True
            )
            return False
        if interaction.guild is None:
            await interaction.response.send_message(
                "This view requires a Discord guild.", ephemeral=True
            )
            return False
        try:
            with self.bot.session_factory() as session:
                static = selected(session, interaction)
                if (
                    static.id != self.result.proposal.static_id
                    or static.guild.discord_guild_id != interaction.guild.id
                ):
                    await interaction.response.send_message(
                        "This V2 plan is not for the selected static.", ephemeral=True
                    )
                    return False
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
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
