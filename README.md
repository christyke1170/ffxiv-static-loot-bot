# Static Loot Discord

Python 3.12+ Discord bot for eight-player FFXIV static administration,
category-only gear and BiS tracking, fixed-floor loot planning, Regular and
automatic Split reclears, neutral resources, and append-only V2 confirmation
history.

## Installation

Windows PowerShell:

```powershell
.\scripts\setup.ps1
```

Linux/macOS:

```sh
chmod +x scripts/setup.sh
./scripts/setup.sh
```

The scripts create `.venv`, install `.[dev]`, copy `.env.example` only when
`.env` does not exist, and run migrations. They never overwrite an existing
environment file or database.

On a fresh database, an administrator must run `/setup seed` once after the
migrations. This creates the global job and gear-slot reference data and
initializes missing static hierarchies. The command is safe to repeat and is
not required again for each newly created Static.

Manual setup:

```sh
python -m venv .venv
python -m pip install -e ".[dev]"
static-loot-db-upgrade
static-loot-validate
static-loot-bot
```

## Configuration

Copy `.env.example` to `.env` and set `DISCORD_TOKEN`, `DATABASE_URL`, optional
`DEV_GUILD_ID`, `BOT_ADMIN_ROLE_IDS`, `RAID_LEADER_ROLE_IDS`, `AUTO_MIGRATE`,
`LOG_LEVEL`, and optional `LOG_FILE`. `POSTGRES_PASSWORD` is required by Docker
Compose. Never commit or print secrets. Invalid configuration, migration failure,
or stale migration state stops startup before the Discord client is created.

## Docker deployment

Set `POSTGRES_PASSWORD` and Discord settings in `.env`, then run:

```sh
docker compose build
docker compose up -d
```

Compose waits for PostgreSQL, upgrades migrations, and starts the non-root bot.
PostgreSQL is required for multiple bot instances. SQLite enables foreign keys,
WAL, and a busy timeout but is a one-process deployment only.

## Current domain and planning rules

- BiS is configured once per Static and Job and stores desired slot categories.
- Floors are fixed at 1-4 with fixed logical loot/resource keys.
- Regular mode creates one eight-Main run.
- Split mode automatically evaluates 35 canonical partitions. Each Main/Alt pair
  uses identical jobs, appears in opposite runs, and each run contains 2 Tanks,
  2 Healers, and 4 DPS.
- Persisted hierarchy order is the planning priority.
- Savage coffers are Main-first. Glaze and Twine are Main-only grants.
- Savage coffers follow greedy hierarchy without receipt-history or per-week
  assignment penalties. Split group selection maximizes Main allocations in
  hierarchy order before material fairness and any Alt benefits.
- A two-DPS/one-Healer/one-Tank Main mix is a preference, never a restriction
  ahead of useful allocations. Every Split run still has four Mains/four Alts,
  and each owned pair is in opposite runs.
- In both modes, Glaze/Twine use combined material receipt fairness, then
  remaining need, then hierarchy. Earlier proposed material grants count toward
  fairness. Materials without an eligible Main remain unassigned.
- Paired Alt weapon Tome upgrades require and consume both
  `WEAPON_TOMESTONE` and `WEAPON_AUGMENT`.
- Tome weapon resources never go to Mains. Alts already wearing or proposed to
  receive a Savage Weapon are excluded from this weapon upgrade opportunity.
- Floor 4's random weapon is updated manually in current gear and never enters
  the planned drop list. A newly generated plan reads those updated categories.
- Books are informational only and never affect proposals.
- `/reclear setup` creates only a DRAFT week; an administrator runs
  `/reclear plan` to generate the V2 plan.
- Reference-backed commands require the one-time fresh-database `/setup seed`.

## Discord command surface

The retained `/reclear` commands are exactly:

```text
/reclear setup
/reclear status
/reclear plan
/reclear complete
/reclear resume
/reclear close
/reclear cancel
```

Other retained groups manage Statics, members, characters, category-only BiS,
hierarchy, current gear, neutral resources, needs, gearboard, and V2 loot
corrections. There is no `/tier`, `/bis import`, legacy planner, legacy plan,
legacy confirmation, or manual Split-group selection command.

Use `/bis setup` for initial setup across every supported job, including jobs
outside the current roster. The private overview shows configured/missing sets.
Select a job, select multiple slots and a category, then apply that category to
the draft. Review the slot list and Save. Individual slot editing remains
available through `/bis set` or the batch editor's Individual slots button.
Dropdown selections stay in the draft without refreshing the full message.

The overview's Copy saved BiS flow copies one saved set to selected jobs only
after Review and Save copy. Existing destination sets are replaced. Offhand
becomes N/A for jobs without one; an N/A source Offhand uses the source Weapon
category when copying to PLD. Review the result for each job's actual BiS.

After a leader adds members, each member can run `/character setup` to select a
shared job and enter Main/Alt names and worlds in one form. Save pair confirms
both characters together. Leaders can specify `member` to register another
member's pair. Existing matching characters and gear are preserved; use
`/character edit` for corrections. Cancel writes nothing.

Combat jobs are maintained only in `app/domain/jobs.py`, in default priority
order. Add future jobs there and run `/setup seed`; there is no Discord job
catalog editor. Seeding preserves saved BiS and hierarchy configurations.
Existing Statics can place newly added jobs using `/hierarchy set`. Job setup
menus paginate as the catalog grows.

## V2 confirmation workflow

Successful receipt callbacks create one neutral current balance; failed receipts
create none. Receipt retries are idempotent and contradictory outcomes are
rejected. Application consumes the matching coffer or both paired Tome resources,
then applies persisted ordered gear effects. Material receipts remain grants and
never fabricate gear changes.

Administrators can append receipt/application corrections with an actor and
non-empty reason. Successful applications can be reversed when recorded gear
state still matches; reversal restores resources and recorded before-categories.

## Database lifecycle and backup

```sh
static-loot-db-upgrade
static-loot-db-check
static-loot-db-backup [optional-sqlite-destination]
```

The built-in backup supports SQLite only and never overwrites an existing backup.
Use `pg_dump` and PostgreSQL restore tooling for production. Migration
`z7r5n1p9v3x6` destructively removes historical tier and legacy
planning/confirmation data; downgrade does not recreate deleted data.

## Validation

```sh
pytest
ruff check .
ruff format --check .
alembic upgrade head
alembic check
python -m compileall -q app bot tests
python -c "import app.models; import bot.main"
```
