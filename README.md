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
- Floors are fixed at 1–4 with fixed logical loot/resource keys.
- Regular mode creates one eight-Main run.
- Split mode automatically evaluates 35 canonical partitions. Each Main/Alt pair
  uses identical jobs, appears in opposite runs, and each run contains 2 Tanks,
  2 Healers, and 4 DPS.
- Persisted hierarchy order is the planning priority.
- Savage coffers are Main-first. Glaze and Twine are Main-only grants.
- Paired Alt weapon Tome upgrades require and consume both
  `WEAPON_TOMESTONE` and `WEAPON_AUGMENT`.
- Books are informational only and never affect proposals.
- `/reclear setup` creates only a DRAFT week; an administrator runs
  `/reclear plan` to generate the V2 plan.

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