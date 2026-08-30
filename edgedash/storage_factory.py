from __future__ import annotations

from edgedash.config import Config


def get_storage_module(config: Config):
    """Return the appropriate storage module based on db_backend config.
    
    This is the single point of storage backend selection. Changing from
    SQLite to Postgres is a one-line config change.
    """
    backend = getattr(config, "db_backend", "sqlite")
    
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
    
    if config.db_backend == "postgres":
        # For Postgres, use DATABASE_URL or connection params
        import os
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            storage.init_db(db_url)
        else:
            # Fall back to trying connection params if URL not available
            storage.init_db()
    else:
        # SQLite uses file path
        storage.init_db(config.db_path)