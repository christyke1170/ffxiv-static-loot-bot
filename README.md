# Static Loot Discord

Python 3.12+ Discord bot for eight-player FFXIV static administration, current
gear and remaining-BiS tracking, regular and split weekly reclears, deterministic
loot planning, and append-only loot confirmation history.

## Discord application setup

1. Open the [Discord Developer Portal](https://discord.com/developers/applications),
   create an application, and open **Bot**.
2. Create the bot user. Under **Token**, use **Reset Token**, copy it once, and put
   it only in the local `.env` file as `DISCORD_TOKEN`. Never paste a token into
   Discord, an issue, source control, or chat. Reset it immediately if exposed.
3. Under **Privileged Gateway Intents**, enable only **Server Members Intent**.
   Message Content and Presence intents are not required. The ordinary Guilds
   intent is enabled by the bot automatically.
4. Open **OAuth2 > URL Generator**. Select scopes `bot` and
   `applications.commands`. Select bot permissions **View Channels**, **Send
   Messages**, **Embed Links**, and **Read Message History**. Generate the URL and
   invite the bot to the target server.
5. Enable Discord **Developer Mode** under User Settings > Advanced. Right-click
   the server and roles to copy their numeric IDs. Set `DEV_GUILD_ID` for rapid
   development sync, `BOT_ADMIN_ROLE_IDS` for bot setup administrators, and
   `RAID_LEADER_ROLE_IDS` for weekly write access. Comma-separate multiple roles.

Commands sync to `DEV_GUILD_ID` during development. Without it they sync globally;
global application-command changes may take up to an hour to become visible.

## Local installation

Windows PowerShell:

```powershell
.\scripts\setup.ps1
```

Linux/macOS:

```sh
chmod +x scripts/setup.sh
./scripts/setup.sh
```

The scripts create `.venv`, install `.[dev]`, copy `.env.example` only when `.env`
does not exist, and run migrations. They never overwrite an existing `.env` or
database. After editing `.env`, start with `static-loot-bot` from the virtual
environment. Verify Discord and database setup with `/setup status`, then run
`/setup seed` as a configured bot administrator.

Manual setup is equivalent to:

```sh
python -m venv .venv
python -m pip install -e ".[dev]"
static-loot-db-upgrade
static-loot-validate
static-loot-bot
```

`static-loot-validate` checks configuration, token presence without printing it,
Discord ID parsing, migration head, connectivity, seed rows, SQLite directory
writability, and active-static readiness. It never connects to Discord.

## Configuration

Copy `.env.example` to `.env` and configure:

- `DISCORD_TOKEN`: secret bot token.
- `DATABASE_URL`: defaults to `sqlite:///static_loot.db`; PostgreSQL uses, for
  example, `postgresql+psycopg://user:password@host/database`.
- `DEV_GUILD_ID`: optional development server ID.
- `BOT_ADMIN_ROLE_IDS`: roles allowed to run `/setup seed`, `/setup demo`, and
  `/setup demo-refresh`.
- `RAID_LEADER_ROLE_IDS`: roles allowed to modify static/weekly state.
- `AUTO_MIGRATE`: run `alembic upgrade head` before Discord startup.
- `LOG_LEVEL`: standard Python level such as `INFO`.
- `LOG_FILE`: optional rotating file (5 MiB, five backups). If unset, structured
  timestamped logs go to stdout/stderr, the recommended container strategy.
  Known tokens, password assignments, and URL credentials are redacted.

Invalid configuration or migration failure occurs before the Discord client is
created. Extension failures abort startup and unload already-loaded extensions.
Persistent confirmation views register before command sync and normal handling.
Unhandled asynchronous task exceptions are logged. `discord.py` handles SIGINT
and SIGTERM through its runner where the platform supports signals; shutdown
closes Discord and disposes database connection pools.

## Deployment

Build and run the production image with `Dockerfile`, or use `docker-compose.yml`
for the bot plus PostgreSQL:

```sh
docker compose build
docker compose up -d
```

Set `POSTGRES_PASSWORD` and Discord settings in `.env`. Compose waits for
PostgreSQL, upgrades migrations, then starts the bot. The image runs as non-root.

SQLite enables foreign keys, a 30-second busy timeout, and WAL for file-backed
databases. **Run only one bot process/instance against a SQLite database.**
In-process entity locks serialize receipt, confirmation, correction, override,
and floor-completion writes; SQLite is not a multi-instance coordination system.
Use PostgreSQL for multiple bot instances. PostgreSQL write paths use row locking,
and uniqueness constraints/idempotent state checks reject conflicting retries.

## Database lifecycle and backup

```sh
static-loot-db-upgrade
static-loot-db-check
static-loot-db-backup [optional-destination]
```

The backup command supports SQLite only and uses SQLite's online backup API. Its
default destination is timestamped beside the database; it never overwrites and
never deletes old backups. It copies only the database—not `.env`, tokens, or
other files. For PostgreSQL use `pg_dump` and PostgreSQL restore tooling.

To restore SQLite:

1. Stop every bot process using the database.
2. Back up or move the current database file, including any `-wal`/`-shm` files.
3. Copy the selected backup to the path in `DATABASE_URL`.
4. Run `static-loot-db-upgrade`, `static-loot-db-check`, and
   `static-loot-validate`.
5. Start one bot instance.

## First-time user workflow

Run the actual workflow in this order:

1. Start the bot.
2. `/setup seed`.
3. A raid leader or bot administrator runs `/static create`.
4. They add eight Discord users with `/member add`; each active member then runs
   `/static select` for that static.
5. Each active member adds only their own main/alt characters with `/character add`.
6. `/tier import` with the tier JSON.
7. `/tier select`.
8. `/bis import` with BiS JSON.
9. `/bis select` for every main.
10. `/gear import` with current gear/resources JSON.
11. `/hierarchy set` with priority job abbreviations.
12. Verify `/gearboard`.
13. Tuesday: `/reclear setup`.
14. `/reclear plan`.
15. Follow `/lootboard` during distribution.
16. `/reclear complete` for cleared floor/group combinations.
17. Answer the confirmation wizard.
18. Use `/reclear resume` after interruption.
19. `/reclear close` after every question is terminal.

Regular example: choose **Regular** in `/reclear setup`; the preview contains the
eight active mains in one group. Split example: choose **Split**, select exactly
four members whose mains go to Split A, review Split A/B, and Confirm. Each player
then appears once on main and once on alt across the two clears. Previewing or
reselecting does not persist participants.

### Demo workflow

A configured bot administrator can run `/setup demo` to create and select a new
`Loot Demo` static for the current guild. It contains entirely fictional, clearly
labelled data: the invoking user, seven synthetic non-Discord members, sixteen
main/alt characters, a complete four-floor tier and BiS sets, plus varied gear,
coffers, books, and augmentation materials. It starts without a reclear week and
supports the regular and split weekly workflows above without seven real users.

The demo is isolated from existing statics and refuses to run if `Loot Demo` or its
reserved guild-specific tier already exists. Synthetic IDs are negative and are
never looked up, messaged, or mentioned. `/setup demo-refresh` repairs only the
currently selected demo after strictly verifying its reserved fictional tier,
deterministic synthetic IDs, names, and eight-member/sixteen-character structure.
It refuses real or ambiguous statics and blocks while reclear or loot work is open.
Refresh is transactional and idempotent and never deletes or recreates the static.

## Weekly and loot behavior

`/reclear status` shows reset, mode, workflow state, tier, hierarchy snapshot,
rosters, completions, plan state, confirmation progress, errors, and closure
readiness. `/reclear plan` reports all roster/lockout blockers together and reuses
an existing plan rather than duplicating assignments. The Components V2
`/lootboard` uses configured floor/drop names and offers floor/group navigation,
refresh, and assignment details.

Raid-leader tools are `/loot override`, `/loot leftover`, and `/loot correction`.
Overrides preserve suggested/original recipients; leftover/free-roll never
updates gear; corrections preserve append-only history and report unsafe manual
intervention. Floor completion awards one book and lockout per participant once.

The persistent wizard asks receipt, coffer redemption, and augmentation questions
as required. **No** accepts an empty explanation/actual-recipient modal. **Skip**
writes nothing, and **Stop** ends only the current UI. Every callback reloads the
database and rechecks guild, selected static, current week, assignment state, and
raid-leader permission. Closed/cancelled/resolved actions reject safely.

Read-only `/gearboard` and `/lootboard` are available to active static members.
Writes and confirmation answers require raid-leader permission except for explicit
self-service operations: active members may select a static they belong to and add,
edit, deactivate, or reactivate characters in only their own active membership.
Raid leaders and bot administrators may correct another member's character only by
explicitly selecting that Discord member. Multi-week projections are intentionally
not implemented.

### Gear-board status language

Every `/gearboard` overview slot and selected-player detail uses exactly the same legend:
`🟩 BiS`, `🟦 Alternate`, `🟧 Tome needs augment`, `🟨 Crafted / EX`, `⬛ N/A`, and
`🟥 Needs replacement`. Current equipped gear is classification-only and stores exactly one of
`CRAFTED`, `EX_WEAPON`, `SAVAGE`, `TOME`, `AUGMENTED_TOME`, or `GARBAGE`; it never stores an item
name, external item ID, item level, note, or current raid tier. A same-slot desired/current
classification match is BiS for the first five states. Manual completion and an exact desired item
owned in inventory also complete a slot. Otherwise Savage and augmented Tome are Alternate, Tome
needs augment, Crafted or EX is Crafted / EX, and Garbage, missing, unknown, or invalid gear needs
replacement. Non-PLD Offhand is N/A. Starting a new raid tier uses a reset/new working static state
rather than comparing historical current gear by item level.

`/gear set display_name main_or_alt` is an admin-only ephemeral editor. It resolves the member in the
invoking admin's selected static, selects that member's Main or Alt character, and displays all slots
in authoritative order with their current classifications, then lets an authorized static administrator choose a slot
and save one of the six classifications immediately without opening another message. The editor
supports repeated changes, reset, and Close; every callback revalidates the shared raid-leader/admin
policy and is restricted to the administrator who opened it. EX is Weapon-only, non-PLD Offhand is
visible as N/A and cannot be edited, and PLD Offhand is editable but still rejects EX.

FFXIV's separate-offhand rule is validated in BiS definitions: PLD must define an
applicable Offhand item, while every other supported combat job must set Offhand to
`NOT_APPLICABLE`.

### Gear-board Summary and weekly books

The `/gearboard` Summary view shows static completion, the current working week, active Main
progress, remaining Savage drops grouped by configured floor and loot type, additional augmentation
materials needed, and book balances. Books are always individual character balances; the compact
floor summary is used only when every displayed Main has the same available count, otherwise readable
per-player exceptions are shown. Summary does not display the gear-state legend; overview and
selected-player detail retain it.

The first working reclear is Week 2. Creating a new reclear initializes one earned book from every
configured floor for each explicitly participating character, idempotently, without overwriting
existing earned, spent, or manual-adjustment values. Regular reclears participate with active Mains;
Alts receive books only when explicitly included by the selected roster mode. The existing
`mark_reclear_floors_complete` service is the future clear-state boundary: it records a unique
week/group/floor completion, grants that floor's book only to that group's participants, and is
separate from advancing or creating the weekly record. No Discord clear command exists yet.

## Correction command reference

- `/static edit`, `/static deactivate`, `/static reactivate`: rename or change the
  selected static's availability without removing members or history. Deactivation
  is blocked while an unfinished reclear is open.
- `/member edit`, `/member deactivate`, `/member reactivate`: correct a selected
  Discord member while retaining characters and history.
- `/character edit`, `/character deactivate`, `/character reactivate`: correct the
  same character row and all attached gear/resources/history. A job change that
  conflicts with selected BiS requires `clear_incompatible_bis: true`; deactivation
  is blocked while an open reclear or loot workflow references the character.
- `/tier select` and `/bis select` replace prior selections idempotently and report
  old → new. `/tier clear` and `/bis clear` remove current selections only when no
  unfinished workflow depends on them. Existing weekly tier/hierarchy snapshots do
  not change.
- `/gear set`, `/gear clear`, `/gear complete`, `/inventory set`, `/augment set`,
  `/books set`, and `/gear import` correct current state without duplicate rows;
  quantity zero clears inventory and negative quantities are rejected.
- `/tier import` and `/bis import` dry-run validation first and report inserted,
  updated, unchanged, and rejected definitions. Referenced incompatible definitions
  are retained and rejected rather than rewriting history or exact-item links.
- `/hierarchy set` always creates a new active version. `/reclear cancel`,
  `/loot override`, `/loot leftover`, and `/loot correction` are the safe correction
  paths for weekly state. Completed floors, snapshots, receipts, confirmations, and
  audits are not directly edited; confirmation corrections append superseding rows,
  and unsafe reversals request manual intervention.

## JSON data and development

Tier definitions are data-driven: floors, loot types, expected quantities, book
costs, and accessory/armor augmentation materials are imported rather than
hard-coded. `/gear import` accepts strict UTF-8 JSON up to 1 MiB and validates the
whole document before writing. Each current gear row accepts only Character (`name` and `world`),
`slot`, and `current_classification`; obsolete current-item identity, item-level, tier, and note
fields are rejected. Inventory rows retain their separate exact-item fields. Fictional examples
are under `sample_data/`; full
four-floor integration fixtures are under `tests/fixtures/`.

Validation commands:

```sh
pytest
ruff format --check .
ruff check .
alembic upgrade head
alembic check
python -m build
python -c "import bot.main"
```