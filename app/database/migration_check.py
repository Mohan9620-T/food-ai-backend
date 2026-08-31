import logging
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

logger = logging.getLogger(__name__)


def warn_if_migrations_pending(engine: Engine) -> None:
    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    try:
        config = Config(str(config_path))
        script = ScriptDirectory.from_config(config)
        expected_heads = set(script.get_heads())
        with engine.connect() as connection:
            current_heads = set(MigrationContext.configure(connection).get_current_heads())

        if current_heads != expected_heads:
            logger.warning(
                "database_migrations_pending",
                extra={
                    "current_revisions": sorted(current_heads),
                    "expected_revisions": sorted(expected_heads),
                },
            )
    except Exception:
        logger.warning("database_migration_check_failed", exc_info=True)
