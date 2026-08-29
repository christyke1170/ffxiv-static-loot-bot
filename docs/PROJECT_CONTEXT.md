# Stable Project Context

Static Loot Discord manages eight-player FFXIV statics and deterministic,
tier-free Regular or Split reclears.

## Domain model

- A Static owns active members, characters, category-only gear state, and
  Static + Job category-only BiS definitions.
- Gear uses `CRAFTED_EX`, `TOME`, `AUGMENTED_TOME`, `SAVAGE`, `GARBAGE`, and
  `NOT_APPLICABLE`. Specific equipment identities and item levels are not used
  for BiS completion.
- The fixed raid structure has floors 1–4 and fixed logical loot/resource keys.
- Main and Alt characters belong to the same member only when configured with
  identical jobs. Split planning requires eight valid pairs and automatically
  creates complementary runs with four Mains and four Alts per run, preserving
  a 2 Tank / 2 Healer / 4 DPS composition.
- Persisted hierarchy order is the planning priority source.

## Needs and resources

- Savage coffers are allocated Main-first and remain neutral unopened resources
  until application changes the intended gear category.
- `ACCESSORY_GLAZE` and `ARMOR_TWINE` are Main-only neutral material grants.
- Alt weapon Tome upgrades use independent `WEAPON_TOMESTONE` and
  `WEAPON_AUGMENT` resources. Both are required and consumed once.
- Books are informational neutral balances and never influence proposals.
- Read-only needs and planning-state reads do not write to the database.

## Weekly V2 workflow

1. Configure the Static, Static + Job BiS, hierarchy, characters, gear, and
   neutral resources.
2. `/reclear setup` creates only a DRAFT week with fixed floors 1–4.
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