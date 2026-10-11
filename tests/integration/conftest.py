import os
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql
import pytest

from backend.config import DEMO_BUSINESS_ID
from backend.database.repository import TranscriptRepository
from backend.scripts.init_db import apply_migrations
from backend.scripts.seed import seed

CLOCK = datetime.fromisoformat("2026-10-10T08:00:00-05:00")


@pytest.fixture
def database():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL unset; real PostgreSQL validation skipped")
    schema = "ssa001_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            with psycopg.connect(dsn, autocommit=True, options=f"-c search_path={schema},public") as conn:
                apply_migrations(conn)
                seed(conn, DEMO_BUSINESS_ID, CLOCK)

                def connect():
                    connection = psycopg.connect(dsn, options=f"-c search_path={schema},public")
                    register_vector(connection)
                    return connection

                yield SimpleNamespace(conn=conn, connect=connect, dsn=dsn, repo=TranscriptRepository(connect))
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
