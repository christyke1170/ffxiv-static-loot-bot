"""Environment-backed application configuration."""

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = "sqlite:///static_loot.db"
    discord_token: str | None = None
    dev_guild_id: int | None = None
    bot_admin_role_ids: tuple[int, ...] = ()
    raid_leader_role_ids: tuple[int, ...] = ()
    auto_migrate: bool = False
    log_level: str = "INFO"
    log_file: str | None = None

    def validate(self, *, require_token: bool = True) -> "Settings":
        missing = []
        if require_token and not self.discord_token:
            missing.append("DISCORD_TOKEN")
        if not self.database_url:
            missing.append("DATABASE_URL")
        if missing:
            raise ValueError("Missing required configuration: " + ", ".join(missing))
        return self


def parse_id_list(value: str | None, *, variable: str) -> tuple[int, ...]:
    """Parse comma-separated Discord IDs without silently accepting bad values."""
    if not value or not value.strip():
        return ()
    result: list[int] = []
    for raw in value.split(","):
        item = raw.strip()
        if not item.isdigit() or int(item) <= 0:
            raise ValueError(f"{variable} must contain positive numeric Discord IDs")
        if int(item) not in result:
            result.append(int(item))
    return tuple(result)


def parse_bool(value: str | None, *, variable: str, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{variable} must be true or false")
    return normalized in {"true", "1", "yes"}


@lru_cache
def get_settings() -> Settings:
    """Return process settings, loading values from the environment once."""
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///static_loot.db"),
        discord_token=os.getenv("DISCORD_TOKEN"),
        dev_guild_id=(int(os.environ["DEV_GUILD_ID"]) if os.getenv("DEV_GUILD_ID") else None),
        bot_admin_role_ids=parse_id_list(
            os.getenv("BOT_ADMIN_ROLE_IDS"), variable="BOT_ADMIN_ROLE_IDS"
        ),
        raid_leader_role_ids=parse_id_list(
            os.getenv("RAID_LEADER_ROLE_IDS"), variable="RAID_LEADER_ROLE_IDS"
        ),
        auto_migrate=parse_bool(os.getenv("AUTO_MIGRATE"), variable="AUTO_MIGRATE"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        log_file=os.getenv("LOG_FILE") or None,
    )
