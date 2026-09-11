from __future__ import annotations

import os

from edgedash.config import Config


def get_storage_module(config: Config):
    """Return the appropriate storage module based on db_backend config.
    
    This is the single point of storage backend selection. Changing from
    SQLite to Postgres is a one-line config change.
    """
    # Hosted state takes precedence over the local config fallback. This keeps
    # the dashboard and scheduled worker on the same database when deployed.
    backend = "postgres" if os.environ.get("DATABASE_URL") else getattr(config, "db_backend", "sqlite")
    
    if backend == "postgres":
        try:
            from edgedash import storage_postgres as storage
        except ImportError as exc:
            raise ImportError(
                "Postgres storage requires psycopg2-binary. "
                "Install with: pip install psycopg2-binary"
            ) from exc
    else:
        from edgedash import storage as storage
    
    return storage


# Re-export common functions for backward compatibility
def init_db(config: Config) -> None:
    """Initialize database using configured backend."""
    storage = get_storage_module(config)
    use_postgres = bool(os.environ.get("DATABASE_URL")) or config.db_backend == "postgres"
    
    if use_postgres:
        # For Postgres, use DATABASE_URL or connection params
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            storage.init_db(db_url)
        else:
            # Fall back to trying connection params if URL not available
            storage.init_db()
    else:
        # SQLite uses file path
        storage.init_db(config.db_path)
