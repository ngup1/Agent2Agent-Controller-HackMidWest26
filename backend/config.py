"""Environment configuration; business identity belongs to the server."""

import os
from dataclasses import dataclass, field
from uuid import UUID

from dotenv import load_dotenv

DEMO_BUSINESS_ID = UUID("6a33f197-7784-54dd-84db-f40dce4c8733")


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default="postgresql://demo:demo@localhost:5432/demo", repr=False
    )
    business_id: UUID = DEMO_BUSINESS_ID
    openai_base_url: str = "https://ws.akamai-workshops.com/v1"
    openai_api_key: str = field(default="", repr=False)
    chat_model: str = "hackathon-chat"
    embedding_model: str = "hackathon-embedding"
    dashboard_password: str = field(default="", repr=False)
    dashboard_preview: bool = False

    def __post_init__(self):
        if not self.database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL URL")

    @classmethod
    def from_env(cls):
        load_dotenv()
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            business_id=UUID(os.getenv("DEMO_BUSINESS_ID", str(DEMO_BUSINESS_ID))),
            openai_base_url=os.getenv("OPENAI_BASE_URL", cls.openai_base_url),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            chat_model=os.getenv("CHAT_MODEL", cls.chat_model),
            embedding_model=os.getenv("EMBEDDING_MODEL", cls.embedding_model),
            dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
            dashboard_preview=os.getenv("DASHBOARD_PREVIEW", "").lower() in {"1", "true", "yes"},
        )
