"""Apply the database branch's migrations, followed by the demo extension."""
from pathlib import Path

import psycopg

from backend.config import Settings

MIGRATION_DIR = Path(__file__).resolve().parents[1] / "database" / "migrations"


def apply_migrations(conn):
    # 003/004 manage their own transactions; autocommit avoids nested BEGINs.
    # PostgreSQL executes 002's multi-statement query as one transaction.
    if not conn.autocommit:
        raise ValueError("Migrations require an autocommit connection")
    for migration in sorted(MIGRATION_DIR.glob("*.sql")):
        try:
            conn.execute(migration.read_text())
        except Exception:
            # Also roll back explicit BEGINs in the inherited SQL on failure.
            conn.rollback()
            raise
        print(f"Applied {migration.name}")


def main():
    settings = Settings.from_env()
    with psycopg.connect(
        settings.database_url, autocommit=True, connect_timeout=10
    ) as conn:
        apply_migrations(conn)


if __name__ == "__main__":
    main()
