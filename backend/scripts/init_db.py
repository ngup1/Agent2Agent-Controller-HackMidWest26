"""Apply gateway schema after provisioning PostgreSQL/pgvector."""
import os
from pathlib import Path
from dotenv import load_dotenv


def main():
    import psycopg
    load_dotenv()
    dsn = os.environ["DATABASE_URL"]
    migration = Path(__file__).resolve().parents[1] / "database" / "migrations" / "002_governance.sql"
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        conn.execute(migration.read_text())
    print("Governance schema initialized")


if __name__ == "__main__":
    main()
