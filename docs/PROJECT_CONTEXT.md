# Stable Project Context

Static Loot Discord manages eight-player FFXIV statics and deterministic,
tier-free Regular or Split reclears.

## Domain model

- A Static owns active members, characters, category-only gear state, and
  Static + Job category-only BiS definitions.
- Gear uses `CRAFTED_EX`, `TOME`, `AUGMENTED_TOME`, `SAVAGE`, `GARBAGE`, and
  `NOT_APPLICABLE`. Specific equipment identities and item levels are not used
  for BiS completion.
- The fixed raid structure has floors 1-4 and fixed logical loot/resource keys.
- Main and Alt characters belong to the same member only when configured with
  identical jobs. Split planning requires eight valid pairs and automatically
  creates complementary runs with four Mains and four Alts per run, preserving
  a 2 Tank / 2 Healer / 4 DPS composition.
- Persisted hierarchy order is the planning priority source.
- The combat-job catalog is code-managed in `app/domain/jobs.py`; Discord cannot
  add jobs. Seeding and the default hierarchy derive from that catalog. Existing
  saved hierarchies are preserved when new jobs are seeded.
- BiS setup covers every supported job, regardless of roster usage. `/bis setup`
  provides batch slot edits, reviewed copies, and configured/missing progress.
- `/character setup` registers a same-job Main/Alt pair through one form and
  explicit confirmation, preserving existing matching characters and gear.

## Needs and resources

- Savage coffers are allocated Main-first and remain neutral unopened resources
  until application changes the intended gear category.
- Savage allocation is greedy in the saved job hierarchy. Historical Savage
  receipts and the number of earlier assignments do not reduce priority.
- Split partitions are compared by Main Savage counts in hierarchy order, then
  Main material quality, then useful Alt Savage allocations and Alt Tome weapon
  opportunities. A lower-priority recipient's extra upgrades cannot offset a
  lost allocation for a higher-priority Main. Finished Mains do not retain an
  artificial group-leader preference.
- Four Mains / four Alts and opposite runs for owned pairs are mandatory. A
  2 DPS / 1 Healer / 1 Tank Main mix is a final preference only; useful allocation
  takes precedence, including layouts with three DPS Mains in one run.
- `ACCESSORY_GLAZE` and `ARMOR_TWINE` are Main-only neutral material grants,
  without an Alt or unrestricted free-for-all fallback. Regular and Split use
  combined prior Glaze/Twine receipts (including earlier proposed grants), then
  remaining need, then hierarchy, then receipts of the specific material.
- Alt weapon Tome upgrades use independent `WEAPON_TOMESTONE` and
  `WEAPON_AUGMENT` resources for the same Alt. Both are required and consumed
  once by a single application. These never go to Mains, and do not target an
  Alt already wearing or proposed to receive a Savage Weapon.
- Savage coffers without participating Main needs may go to an Alt for its job
  BiS. If no participating Main or Alt needs the piece for BiS, it is free-for-all.
  Gearing additional jobs is a future feature, not part of this allocator.
- Floor 4's Weapon Coffer follows normal greedy allocation. Its random weapon
  is excluded from planning and is recorded manually as current gear. Each new
  weekly plan uses current gear; previously persisted plans are not regenerated.
- Books are informational neutral balances and never influence proposals.
- Read-only needs and planning-state reads do not write to the database.

## Weekly V2 workflow

1. Configure the Static, Static + Job BiS, hierarchy, characters, gear, and
   neutral resources.
2. `/reclear setup` creates only a DRAFT week with fixed floors 1-4.
3. An administrator runs `/reclear plan`. Regular planning creates one V2 plan;
   Split planning automatically evaluates 35 canonical partitions and persists
   two generated runs.
4. Confirmation views record receipt outcomes. Successful receipts create
   neutral balances; failed receipts do not.
5. Application consumes the matching resource and applies persisted ordered
   effects. Retries are idempotent.
6. `/reclear resume` returns the first unresolved resource. `/reclear close`
   succeeds only after all required questions are terminal.

The retained `/reclear` surface is exactly `setup`, `status`, `plan`, `complete`,
`resume`, `close`, and `cancel`.

## Corrections and safety

- V2 receipts and applications are source-validated, transactional, and
  append-only.
- Administrator corrections require actor identity and a non-empty reason.
- Application reversal restores recorded resources and gear categories only when
  current gear still matches the recorded applied state.
- Cancellation is allowed only for an untouched week. Closed weeks reject
  normal confirmations.
- Foreign keys, unique constraints, nonnegative balances, and exact resource
  scope are enforced by the database.

## Deployment contract

- PostgreSQL is the production multi-instance database. SQLite is supported for
  local development and one-process deployments only.
- PostgreSQL backups use `pg_dump` and PostgreSQL restore tooling. The bundled
  backup command supports SQLite only.
- Migration `z7r5n1p9v3x6` destructively removes historical tier and legacy
  planning/confirmation data. Downgrade does not restore deleted data.
