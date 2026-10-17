"""Code-managed combat jobs, ordered by the default loot priority.

Add future jobs here, then run reference seeding. Discord cannot edit this catalog.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JobDefinition:
    abbreviation: str
    name: str
    role: str
    uses_offhand: bool = False


JOB_CATALOG = (
    JobDefinition("SAM", "Samurai", "Melee DPS"),
    JobDefinition("VPR", "Viper", "Melee DPS"),
    JobDefinition("BLM", "Black Mage", "Magical Ranged DPS"),
    JobDefinition("RPR", "Reaper", "Melee DPS"),
    JobDefinition("MNK", "Monk", "Melee DPS"),
    JobDefinition("DRG", "Dragoon", "Melee DPS"),
    JobDefinition("NIN", "Ninja", "Melee DPS"),
    JobDefinition("PCT", "Pictomancer", "Magical Ranged DPS"),
    JobDefinition("SMN", "Summoner", "Magical Ranged DPS"),
    JobDefinition("RDM", "Red Mage", "Magical Ranged DPS"),
    JobDefinition("MCH", "Machinist", "Physical Ranged DPS"),
    JobDefinition("DNC", "Dancer", "Physical Ranged DPS"),
    JobDefinition("BRD", "Bard", "Physical Ranged DPS"),
    JobDefinition("WHM", "White Mage", "Healer"),
    JobDefinition("SGE", "Sage", "Healer"),
    JobDefinition("AST", "Astrologian", "Healer"),
    JobDefinition("SCH", "Scholar", "Healer"),
    JobDefinition("DRK", "Dark Knight", "Tank"),
    JobDefinition("GNB", "Gunbreaker", "Tank"),
    JobDefinition("PLD", "Paladin", "Tank", True),
    JobDefinition("WAR", "Warrior", "Tank"),
)
