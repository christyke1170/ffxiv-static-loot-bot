# Discord Commands

This document lists the retained Discord commands and their access requirements.

## First-time setup

On a fresh database, a bot administrator must run this once:

```text
/setup seed
```

Seeding creates the global job and gear-slot reference data. It is safe to run
again and is not required once per Static.

Most commands also require the user to select a Static:

```text
/static select static_id:<id>
```

An active Static member can select their Static. Raid leaders and bot
administrators can select a Static without membership.

## Regular user commands

These commands do not require raid-leader or bot-administrator permissions.
Some require an active membership in the selected Static.

### Setup and Static selection

```text
/setup status
/static list
/static select static_id:<id>
/static show
```

### Members and characters

```text
/member list

/character add name:<name> world:<world> kind:<MAIN|ALT> job:<job>
/character edit current_name:<name> [new values]
/character deactivate current_name:<name>
/character reactivate current_name:<name>
/character list [member:<member>] [kind:<MAIN|ALT>]
```

`/character add` adds a character for the Discord user running the command. To
add a character for another member, a raid leader can use `/character add-for`.

### Needs and read-only boards

```text
/needs player character_name:<name>
/needs floor floor_number:<1-4>
/needs augment
/needs books

/gear show character_name:<name>
/gearboard
/lootboard
```

### Reclear status and pending confirmations

```text
/reclear status
/reclear resume
```

## Raid leader commands

Raid leaders can use the regular commands plus the following management
commands. Bot administrators also have raid-leader access.

### Static management

```text
/static create name:<name> crafted_item_level:<level>
/static edit new_name:<name>
/static deactivate
/static reactivate
/static item-level value:<level>
```

### Member management

```text
/member add member:<member> display_name:<name>
/member edit member:<member> display_name:<name>
/member deactivate member:<member>
/member reactivate member:<member>
```

### Character management

```text
/character add-for member:<member> name:<name> world:<world> kind:<MAIN|ALT> job:<job>
```

`/character add-for` adds a character to another existing active member of the
selected Static.

### Static + Job BiS

```text
/bis set job:<job>
/bis show job:<job>
/bis clear job:<job>
```

### Current gear

```text
/gear set display_name:<member> main_or_alt:<MAIN|ALT>
/gear clear character_name:<name> gear_slot:<slot>
/gear complete character_name:<name> gear_slot:<slot> complete:<true|false> [reason:<reason>]
/gear import attachment:<json file>
```

### Inventory, augmentation, and books

```text
/inventory set character_name:<name> gear_slot:<slot> category:<category> quantity:<number>
/augment set character_name:<name> material_code:<code> quantity:<number>
/books set character_name:<name> floor_number:<1-4> earned:<number> spent:<number> [manual_adjustment:<number>]
```

### Job hierarchy

```text
/hierarchy set jobs:<comma-separated jobs> [force:<true|false>]
/hierarchy show
```

### Reclear workflow

```text
/reclear setup mode:<Regular|Split> [notes:<notes>]
/reclear plan
/reclear complete floor_number:<1-4>
/reclear close
/reclear cancel reason:<reason>
```

### V2 loot corrections

```text
/loot correction assignment:<id> confirmation:<Receipt|Application> correct_answer:<true|false> reason:<reason>
```

## Bot administrator commands

Bot administrators are users with Discord Administrator permission or a role
listed in `BOT_ADMIN_ROLE_IDS`. These commands are restricted to bot
administrators.

### Reference seeding

```text
/setup seed
```

### Permanent deletion

All deletion commands display a confirmation prompt. Nothing is deleted until
the administrator presses **Confirm delete**. Pressing **Cancel** leaves the
database unchanged.

```text
/static delete static_id:<id>
/member delete member:<member>
/character delete member:<member> current_name:<name>
```

- `/static delete` permanently deletes the Static and its dependent data.
- `/member delete` permanently deletes the member, their characters, and
  dependent data.
- `/character delete` permanently deletes the selected character and dependent
  data.

## Retired commands

The following APIs are not part of the command surface:

```text
/tier
/bis import
```

Legacy planners, legacy confirmations, and manual Split-group selection are
also retired.