# Stable Project Context

- The app manages eight-player FFXIV statics and deterministic Regular or Split Savage reclears.
- Discord guilds/users, static membership, characters, jobs, tiers, plans, and loot resources are
  distinct concepts. Character resources and progression never belong to a Discord user directly.
- Gear is category-only: one Character, one gear slot, and one current category. BiS is category-only:
  one BiS set, one gear slot, and one desired category. Specific equipment names, FFXIV equipment item
  IDs, base-item IDs, final-item IDs, item levels, and exact equipment relationships are not tracked.
- Canonical categories are `GARBAGE`, `CRAFTED_EX`, `TOME`, `AUGMENTED_TOME`, `SAVAGE`, and
  `NOT_APPLICABLE`. Ring 1 and Ring 2 remain distinct slots.
- A BiS slot is complete only when it is `NOT_APPLICABLE`, has a manual completion override, or its
  current category exactly equals its desired category. Savage and Augmented Tome never complete one
  another. Item level never determines BiS completion.
- New Main and Alt characters immediately receive every gear slot without requiring a selected tier or
  BiS import. Applicable slots begin as `CRAFTED_EX`; Offhand begins as `CRAFTED_EX` only when the
  character's configured job has `uses_offhand = true`, otherwise it is `NOT_APPLICABLE`.
- Offhand capability is job configuration. Seeded Paladin is enabled and other current jobs are disabled;
  no service or view determines capability from a job name or abbreviation. A job change reconciles only
  Offhand and preserves all other gear.
- Unequipped gear inventory, where used, stores gear slot plus category. Unopened coffers remain
  categorized loot resources and do not complete gear until redeemed through confirmation.
- Desired Savage reports its configured coffer/drop and books alternative while current gear is not
  `SAVAGE`. Desired Augmented Tome uses current category: Crafted/EX or Garbage needs the base Tome
  item, Tome needs augmentation, Augmented Tome is complete, and Savage remains incomplete.
- Augmentation materials are allocated deterministically and one owned unit cannot satisfy two slots.
- Each static has an optional administrator-configured `crafted_item_level`. New statics require a
  positive baseline; migrated legacy statics remain unset and display a configuration warning.
- Relative slot levels for baseline `X` are: Crafted/EX `X`, Tome `X + 10`, Augmented Tome `X + 20`,
  Savage armor/accessory `X + 20`, and Savage Weapon/applicable Offhand `X + 25`. Garbage has no value
  and Not Applicable is excluded. No calculated per-piece level or average is persisted.
- Average item level has exactly eleven contributions: one weapon contribution plus Head, Body, Hands,
  Legs, Feet, Earrings, Necklace, Bracelets, Ring 1, and Ring 2. For an offhand-capable job, the weapon
  contribution is the exact decimal average of Weapon and Offhand; Offhand is never a twelfth piece.
  Precision is preserved and only the final displayed average is floored.
- Garbage in any applicable contribution makes the average unavailable and identifies every offending
  slot with a prominent replacement warning. Missing applicable state or a missing static baseline also
  makes the average unavailable. Garbage does not block weekly plan generation.
- Raid tiers remain data-driven. Floors, loot types, expected quantities, book costs, coffer resources,
  and augmentation-material types may use normal internal database IDs.
- The legacy `items` resource table remains only for named loot/material configuration. Its historical
  external-ID and item-level columns are deprecated in place for safe SQLite compatibility and are not
  read or written by equipment, BiS, inventory, planning, snapshots, confirmation, or item-level logic.
- Regular mode has one eight-Main run. Split mode has complementary groups containing four Mains and
  four Alts; each active member's Main and Alt run opposite and each character appears once.
- Planning is read-only until an authorized generated plan is persisted. Regular and Split scoring,
  deterministic priority, material fairness, paired Alt weapons, free rolls, and books exclusions remain
  unchanged. Planning operates on remaining category needs and does not mutate gear or inventory.
- Persisted gear assignments store recipient, Main/Alt designation, slot, loot/coffer type, resulting
  category, source metadata, disposition, and pairing metadata. An internal BiS-slot record reference may
  remain for integrity but never resolves an equipment item.
- Source snapshots and stale detection compare roster/scope, character and job identity, slot/current
  category, desired category, categorized inventory/resources, material balances and grants, and relevant
  tier loot configuration. They do not compare equipment identities.
- Confirmation remains atomic, source-validated, and idempotent. Savage assignments set the persisted
  slot to `SAVAGE`; paired Alt weapon upgrades set Weapon to `AUGMENTED_TOME`; Twine and Glaze remain
  grants; free roll and unassigned rows do not change gear. Books, clear credit, week advancement, audit,
  and Discord confirmation behavior remain intact.