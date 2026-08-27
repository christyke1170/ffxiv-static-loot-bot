"""Executable entry point; importing this module never connects to Discord."""

import logging
import subprocess
from logging.handlers import RotatingFileHandler

from app.config import get_settings
from app.database import create_database_engine
from app.security import RedactingFilter
from bot.client import StaticLootClient
from bot.services.migrations import verify_migration_head


def configure_logging(settings) -> None:
    handler = (
        RotatingFileHandler(settings.log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        if settings.log_file
        else logging.StreamHandler()
    )
    handler.addFilter(RedactingFilter())
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler],
        force=True,
    )


def main() -> None:
    settings = get_settings().validate()
    configure_logging(settings)
    if settings.auto_migrate:
        subprocess.run(["alembic", "upgrade", "head"], check=True)
    else:
        engine = create_database_engine(settings.database_url)
        try:
            verify_migration_head(engine)
        finally:
            engine.dispose()
    client = StaticLootClient(settings)
    client.run(settings.discord_token)


if __name__ == "__main__":
    main()
