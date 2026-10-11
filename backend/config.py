"""Environment configuration; business identity belongs to the server."""

import math
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
    embedding_mode: str = "live"
    dashboard_password: str = field(default="", repr=False)
    dashboard_preview: bool = False
    # Gate providers: "fake", "live", or "" (unset, which the gate escalates).
    retrieval_mode: str = ""
    jev_mode: str = ""
    precedent_similarity_min: float = 0.80
    typesafe_api_key: str = field(default="", repr=False)
    typesafe_model: str = ""  # empty uses the SDK default
    chat_session_secret: str = field(default="", repr=False)

    def __post_init__(self):
        if not self.database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL URL")
        if {self.retrieval_mode, self.jev_mode} - {"", "fake", "live"}:
            raise ValueError("RETRIEVAL_MODE and JEV_MODE must be fake, live or unset")

        if self.embedding_mode not in {"fake", "live"}:
            raise ValueError("EMBEDDING_MODE must be fake or live")
        if not math.isfinite(self.precedent_similarity_min) or not 0 <= self.precedent_similarity_min <= 1:
            raise ValueError("PRECEDENT_SIMILARITY_MIN must be between 0 and 1")

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
            embedding_mode=os.getenv("EMBEDDING_MODE", cls.embedding_mode).strip().lower(),
            dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
            dashboard_preview=os.getenv("DASHBOARD_PREVIEW", "").lower() in {"1", "true", "yes"},
            retrieval_mode=os.getenv("RETRIEVAL_MODE", "").strip().lower(),
            jev_mode=os.getenv("JEV_MODE", "").strip().lower(),
            typesafe_api_key=os.getenv("TYPESAFE_API_KEY", "").strip(),
            typesafe_model=os.getenv("TYPESAFE_MODEL", "").strip(),
            precedent_similarity_min=float(
                os.getenv("PRECEDENT_SIMILARITY_MIN") or cls.precedent_similarity_min
            ),
            chat_session_secret=os.getenv("CHAT_SESSION_SECRET", ""),
        )
