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
- Reclear floor completion is append-only and unique per week, group, and floor; it awards one
  book and one weekly lockout to each participating character exactly once, then opens confirmation.
- Confirmation questions are ordered by floor, group, loot-rule order, and drop instance. Receipt,
  redemption, and augmentation answers are append-only; idempotent repeats are harmless and
  contradictory repeats require correction. Failed distribution preserves the assignment and need.