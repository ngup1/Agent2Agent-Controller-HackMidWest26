"""Embed proposals once and retrieve evidence for the persisted gateway."""
from __future__ import annotations

import math
from typing import Protocol, TypedDict
from uuid import UUID

from backend.config import Settings
from backend.database.repository import TranscriptRepository
from backend.providers.embeddings import embedding_provider


class Precedent(TypedDict):
    interaction_id: str
    summary: str
    outcome: str
    similarity: float


class EmbeddingProvider(Protocol):
    mode: str
    embedding_model: str

    def embed_transcript(self, text: str) -> list[float]: ...


class RetrievalUnavailable(RuntimeError):
    """Gateway must escalate with unknown risk; never treat this as no matches."""

    reason_code = "RETRIEVAL_UNAVAILABLE"

    def __init__(self, mode: str, stage: str):
        self.mode = mode
        self.stage = stage
        # Do not expose provider errors, credentials, or transcripts in evidence.
        super().__init__(f"Precedent retrieval unavailable ({stage}; {mode})")


def validate_embedding(vector):
    if len(vector) != 1024:
        raise ValueError(f"Expected 1024 embedding dimensions, got {len(vector)}")
    if not all(math.isfinite(v) for v in vector) or not any(vector):
        raise ValueError("Embedding must be finite and nonzero")


class PrecedentService:
    def __init__(self, repository: TranscriptRepository, settings: Settings,
                 provider: EmbeddingProvider | None = None):
        self.repository = repository
        self.business_id = settings.business_id
        self.similarity_min = settings.precedent_similarity_min
        self.provider = provider if provider is not None else embedding_provider(settings)
        self.mode = self.provider.mode

    def retrieve_for_action(self, action_id: UUID, k: int = 3) -> list[Precedent]:
        """Preferred gate entrypoint: text and current call come from persistence."""
        self._validate_limit(k)
        context = self._context(action_id)
        return self._retrieve(context, k)

    def retrieve_precedents(self, business_id: UUID, text: str, k: int = 3,
                            *, action_id: UUID) -> list[Precedent]:
        """Spec interface with mandatory action context to exclude the current call.

        Adapters must use the configured business, never one supplied by a caller.
        Text must be the persisted caller-only text at proposal creation.
        """
        self._validate_limit(k)
        if business_id != self.business_id:
            raise ValueError("Business must match server configuration")
        context = self._context(action_id)
        if text != context["text"]:
            raise ValueError("Text must match the persisted proposal transcript")
        return self._retrieve(context, k)

    @staticmethod
    def _validate_limit(k):
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 20:
            raise ValueError("k must be an integer from 1 to 20")

    def _context(self, action_id):
        try:
            context = self.repository.get_proposal_transcript(self.business_id, action_id)
            if context is None or not context["text"].strip():
                raise ValueError("Proposal has no caller transcript")
            return context
        except Exception as exc:
            raise RetrievalUnavailable(self.mode, "transcript") from exc

    def _retrieve(self, context, k):
        stage = "embedding"
        try:
            # Retrying evaluation reuses the stored vector, including across restarts.
            stored = self.repository.get_embedding(self.business_id, context["segment_id"])
            if stored is not None:
                if stored["model"] != self.provider.embedding_model:
                    raise ValueError("Stored embedding belongs to another model/mode")
                vector = stored["vector"]
            else:
                vector = self.provider.embed_transcript(context["text"])
                validate_embedding(vector)
                self.repository.attach_embedding(
                    self.business_id, context["segment_id"], vector,
                    self.provider.embedding_model,
                )
                # Read the winning insert if another evaluation stored it first.
                stored = self.repository.get_embedding(self.business_id, context["segment_id"])
                if stored is None or stored["model"] != self.provider.embedding_model:
                    raise ValueError("Embedding was not persisted with the expected model")
                vector = stored["vector"]
            validate_embedding(vector)
            stage = "search"
            matches = self.repository.search_precedents(
                self.business_id, vector, context["interaction_id"],
                self.provider.embedding_model, k,
            )
            if any(not math.isfinite(match["similarity"]) for match in matches):
                raise ValueError("Retrieved similarity must be finite")
            return matches
        except Exception as exc:
            raise RetrievalUnavailable(self.mode, stage) from exc
