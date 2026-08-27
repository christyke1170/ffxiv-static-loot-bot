# Stable Project Context

- The app manages eight-player FFXIV statics and regular or split Savage reclears.
- Discord guilds/users, static membership, and FFXIV characters are distinct concepts.
- A static member may own multiple characters; each character is explicitly a main or alt.
- Gear, books, augmentation materials, inventory, weekly lockouts, split participation,
  assignments, and receipts belong to characters rather than Discord users.
- Jobs and characters are separate. A job may have multiple named BiS sets in one raid tier,
  including different GCD variants.
- A BiS slot stores a broad gear classification separately from its exact desired item.
- BiS slots are Weapon, Offhand, Head, Body, Hands, Legs, Feet, Earrings, Necklace,
  Bracelets, Ring 1, and Ring 2. Ring 1 and Ring 2 are always distinct. Paladin is the only
  supported FFXIV combat job with an applicable Offhand; Offhand is N/A for every other job.
- Desired classifications remain SAVAGE, AUGMENTED_TOME, TOME, CRAFTED, EX_WEAPON, CATCHUP, RELIC,
  NORMAL_RAID, EITHER, OTHER, and NOT_APPLICABLE. Current equipped classifications are strictly
  CRAFTED, EX_WEAPON, SAVAGE, TOME, AUGMENTED_TOME, or GARBAGE.
- Current equipped gear and carried inventory are separate normalized records. Current gear stores
  only a slot classification; inventory retains exact Item identity and quantity.
- Current equipped gear states are source-based and tier-independent; gear-board status never
  infers source from item names or uses item level. Starting a new tier uses a reset/new working
  static state rather than comparing historical current gear by item level.
- `Item` catalog identity, external IDs, and item levels remain shared metadata for desired BiS,
  inventory, loot, and materials. Current equipped gear commands, imports, persistence, board DTOs,
  and status logic do not read, require, store, or display them. Current gear also has no note or
  raid-tier field.
- Raid tiers are data-driven. Floors, loot types, expected quantities, book costs, and
  augmentation-material types must not be hard-coded for one tier.
- Weekly reset periods start Tuesday; reset-boundary calculation is centralized and configurable.
- A static has at most one reclear record per reset period. Regular mode has one group containing
  each active member's main. Split mode has two groups of four mains and four alts; each active
  member uses their main in one group and alt in the opposite group, and a character appears once.
- Each static may select an active tier and one active versioned job hierarchy. Weekly plans copy
  normalized immutable hierarchy snapshot rows so later hierarchy edits affect only future plans.
- Characters select at most one same-tier BiS set per tier. Current gear stores only confirmed or
  manually entered state; calculated completion and remaining needs are not persisted.
- Planned loot preserves intended, suggested, final, and backup recipients. Receipt, coffer
  redemption, and augmentation application answers are append-only confirmation records.
- Failed receipt/redemption does not complete gear. Distribution errors preserve the assignment,
  reporter, timestamp, optional explanation, actual recipient when known, and resolution state.
- Gear-board Summary is a read-only aggregate view: static completion, current working week,
  active Main progress, remaining Savage drops by configured floor/loot type, additional augmentation
  materials, and per-character effective books. It never implies books are pooled and does not show
  the gear-state legend used by overview/detail views.
- The first working reclear is Week 2. New reclear setup idempotently seeds one earned book per
  configured floor for explicitly participating characters only, preserving existing book accounting.
  `mark_reclear_floors_complete` is the future clear-state service boundary: its unique week/group/floor
  completion grants only that floor's book to that group's participants; no Discord clear command is
  implemented yet.
- Keep BiS sets, gear boards, and inventories normalized; do not serialize them as JSON blobs.
- Remaining-BiS needs are calculated read-only from the selected same-tier BiS set, same-slot current
  classification, exact inventory item IDs, manual completion, materials, effective books, and tier
  configuration. Matching CRAFTED, EX_WEAPON, SAVAGE, TOME, or AUGMENTED_TOME classifications
  complete a slot; exact desired inventory ownership or manual completion may also complete it.
  Item level and unopened coffers never complete a slot.
- `/gear set display_name main_or_alt` is an ephemeral admin-only classification editor. It resolves
  the display name only within the invoking admin's selected static and selects Main or Alt using
  `Character.kind`. It uses the shared
  authoritative raid-leader/admin policy before loading data and in every callback, binds the view
  to its opener, edits one message in place, and enforces EX as Weapon-only and non-PLD Offhand as
  visible but non-editable N/A.
- Owned augmentation materials, matching unopened coffers, and effective books are simulated once
  in gear-slot sort order. The calculation never persists reservations or calculated statuses.
- Effective books are earned minus spent plus manual adjustment. Book purchasing is an alternative
  to the primary Savage loot need and does not replace that need in calculated results.
- Weekly planning preserves every configured expected drop per group and floor, including
  unassigned leftovers. Split inventories and recipient pools remain separate, and alts are never
  automatic planned recipients.
- Eligible mains are ordered first by the week's immutable job-hierarchy snapshot, using the job
  from their selected same-tier BiS set. Plan balancing and confirmed tier receipts are secondary
  tiebreakers; missing snapshot jobs sort last with a warning.
- Planning simulates coffer and augmentation consumption without changing gear or inventory.
- Read-only Regular planning uses this fixed base-job priority, highest first: SAM, VPR, BLM, RPR,
  MNK, DRG, NIN, PCT, SMN, RDM, MCH, DNC, BRD, WHM, SGE, AST, SCH, DRK, GNB, PLD, WAR.
  Same-job ties use stable static-roster order; unsupported jobs warn and sort after supported jobs.
- A Regular proposal contains all eight active members' Mains and exactly one run. It tracks Earring,
  Necklace, Bracelet, and Ring coffers on Floor 1; Head, Gloves, and Boots coffers plus Glaze on
  Floor 2; Chest and Pants coffers plus Twine on Floor 3; and one Weapon Coffer on Floor 4.
  Weapon Tomestone and Weapon Augment are free-roll and untracked in Regular planning, as are random
  Savage weapons and cosmetic drops.
- Regular Savage coffers use unlimited funneling: each configured drop goes to the highest-priority
  participating Main who retains that Savage need in the authoritative needs calculation, and one
  Main may receive multiple drops. Drops with no eligible Main are free roll.
- Regular Twine and Glaze are ranked independently by fewest confirmed bot-managed reclear grants of
  that exact material, most current remaining material need, base-job priority, then roster order.
  Manual/imported/current material ownership can reduce remaining need but is never fairness history;
  base Tome item ownership is not required for material eligibility.
- Regular planning ignores Savage books completely: book balances and purchasing alternatives do not
  affect eligibility, ranking, assignment, or material priority.
- Read-only Split roster generation deterministically considers all 35 unique complementary four-Main
  partitions before composition filtering. Run A always includes the first stable-roster member's Main,
  which removes mirrored Run A/Run B duplicates.
- Every Split candidate has two complementary eight-character runs with four Mains and four Alts each.
  Every member appears in both runs, plays Main once and Alt once, and has their Main and Alt in
  opposite runs. Each accepted run strictly requires 2 Tanks, 2 Healers, and 4 DPS; melee, physical
  ranged, and magical ranged jobs all count as DPS. Candidate generation is deterministic, read-only,
  and does not score candidates or assign loot.
- Split Savage planning assigns each guaranteed coffer to an eligible Main first; an Alt is considered
  only when no Main in that physical run needs the coffer, and otherwise the coffer is free roll.
  Unlimited funneling is allowed and no one-item-per-player or Savage fairness rotation is applied.
- Split Savage candidates are compared first by a lexicographic 21-position Main assignment vector in
  base job-hierarchy order. Carry balance is considered only after that vector ties: completed Main DPS
  carries are compared in priority order, preferring carries separated between the two runs. Tanks,
  Healers, and incomplete DPS are not carries.
- After Main assignments and carry balance tie, candidates maximize useful Alt Savage assignments and
  then compare the Alt assignment vector in the same hierarchy order. Remaining ties use canonical
  candidate order, so winner selection is deterministic and read-only.
- Split Twine and Glaze assignments are Main-only and use independent confirmed reclear-grant histories.
  Eligible Mains are ordered by fewest exact-material grants, greatest remaining need, base job priority,
  then stable roster order; the two physical copies are simulated sequentially without writing history.
- Each Split run may receive one paired Alt Weapon Tomestone and Weapon Augment proposal. Both components
  target the same eligible Alt, ranked by base job priority and roster order; Savage and Augmented Tome
  weapons are ineligible. Planning does not change gear or create inventory records.
- The complete Split comparison order is Main Savage vector, Twine score, Glaze score, completed-DPS
  carry balance, useful Alt Savage total, Alt Savage vector, useful paired Alt weapon upgrades, then
  canonical candidate order. Books remain excluded from Split planning.
- Split Twine and Glaze assignments are Main-only and use independent confirmed reclear-grant histories.
  Eligible Mains are ordered by fewest exact-material grants, greatest remaining need, base job priority,
  then stable roster order; material copies are simulated sequentially per physical run. No material
  history or inventory rows are written by planning.
- Each Split run may also receive one paired Alt Weapon Tomestone and Weapon Augment proposal. Both
  components name the same eligible Alt, selected by base job priority and roster order; Savage and
  Augmented Tome weapons are ineligible. The proposal does not change current gear or create inventory.
- Valid generated Regular and Split plans can be persisted as READY snapshots. Their run structures,
  participants, Main/Alt identity, assignments, materials, and paired Alt weapon components are stored
  for later loading. Materials and paired weapon components remain proposals until confirmation.
- Only one active generated plan is allowed for a static, raid tier, and target week. Persisting a plan
  does not apply loot or advance the static's week.
- New generated plans store a versioned authoritative source-state snapshot and deterministic hash.
  Relevant planning-state changes make READY plans stale; book changes do not affect staleness.
  Historical active plans without a supported snapshot are unverifiable.
- Active plans can be loaded by static, tier, and target week. DRAFT and READY plans may be cancelled;
  applied plans cannot be cancelled. Cancellation preserves the historical plan contents and applies
  no loot.
- Authorized users can generate Regular or Split plans through Discord. Active plans can be viewed
  without recalculation; displays include rosters, assignments, materials, free rolls, and Alt weapon
  proposals. Stale and unverifiable plans show warnings. DRAFT and READY plans can be cancelled through
  a confirmation view. Discord planning commands do not apply loot or advance the week.
- The complete Split comparison order is Main Savage vector, Twine score, Glaze score, completed-DPS
  carry balance, useful Alt Savage total, Alt Savage vector, useful paired Alt weapon upgrades, then
  canonical candidate order. Books remain excluded from all Split planning scores and eligibility.
- Reclear floor completion is append-only and unique per week, group, and floor; it awards one
  book and one weekly lockout to each participating character exactly once, then opens confirmation.
- Confirmation questions are ordered by floor, group, loot-rule order, and drop instance. Receipt,
  redemption, and augmentation answers are append-only; idempotent repeats are harmless and
  contradictory repeats require correction. Failed distribution preserves the assignment and need.