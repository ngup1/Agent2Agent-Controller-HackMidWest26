"""Explicit live/fake embedding providers. A live failure never selects a fake."""
from __future__ import annotations

import hashlib
import math
import re

from backend.config import Settings
from backend.providers.akamai import AkamaiService


class FakeEmbeddingProvider:
    """Deterministic token hashing for offline plumbing tests, not semantic claims."""

    mode = "fake"
    embedding_model = "fake-token-hash-v1-1024"

    def embed_transcript(self, text: str) -> list[float]:
        if not text.strip() or len(text) > 20000:
            raise ValueError("Embedding input must contain 1-20000 characters")
        vector = [0.0] * 1024
        for token in re.findall(r"\w+", text.casefold()) or [text]:
            digest = hashlib.sha256(token.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % 1024] += 1.0
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector]


class LiveEmbeddingProvider:
    mode = "live"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.embedding_model = settings.embedding_model
        self._service = None

    def embed_transcript(self, text: str) -> list[float]:
        if self._service is None:
            if not self.settings.openai_api_key or "paste_your_team_key_here" in self.settings.openai_api_key:
                raise RuntimeError("Akamai team key is unavailable")
            from openai import OpenAI
            client = OpenAI(api_key=self.settings.openai_api_key,
                            base_url=self.settings.openai_base_url,
                            timeout=15.0, max_retries=0)
            self._service = AkamaiService(client=client, embedding_model=self.embedding_model)
        return self._service.embed_transcript(text)


def embedding_provider(settings: Settings):
    if settings.embedding_mode == "fake":
        return FakeEmbeddingProvider()
    if settings.embedding_mode == "live":
        return LiveEmbeddingProvider(settings)
    raise ValueError("EMBEDDING_MODE must be live or fake")
