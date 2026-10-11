"""Offline contract tests for SSA-008 -> SSA-010 integration (no live API calls)."""
from uuid import uuid4
from unittest.mock import Mock

import pytest

from backend.config import Settings
from backend.memory.precedents import PrecedentService, RetrievalUnavailable, validate_embedding
from backend.providers.embeddings import FakeEmbeddingProvider, LiveEmbeddingProvider
from backend.services.gateway import evaluate
from tests.test_gateway import FakeStore, proposal


@pytest.mark.parametrize("retrieval_mode", ["fake", "live"])
@pytest.mark.parametrize("embedding_mode", ["fake", "live"])
def test_gateway_uses_persisted_action_and_retrieval_mode(monkeypatch, retrieval_mode, embedding_mode):
    action_id = uuid4()
    seen = []

    def fake_persisted_precedents(self, received_id, k=3):
        seen.append((received_id, k, self.business_id, type(self.provider)))
        return [{"interaction_id": "earlier", "summary": "Owner approval claim",
                 "outcome": "denied", "similarity": 0.95}]

    monkeypatch.setattr(PrecedentService, "retrieve_for_action", fake_persisted_precedents)
    store = FakeStore(proposal())
    settings = Settings(retrieval_mode=retrieval_mode, embedding_mode=embedding_mode, jev_mode="fake")
    result = evaluate(action_id, store=store, settings=settings)
    assert result.decision == "escalate"
    assert result.risk_level == "high"
    assert "SIMILAR_TO_DENIED" in [r["code"] for r in result.reasons]
    provider_type = LiveEmbeddingProvider if retrieval_mode == "live" else FakeEmbeddingProvider
    assert seen == [(action_id, 3, settings.business_id, provider_type)]
    assert result.policy_snapshot["providers"]["retrieval"] == retrieval_mode
    assert result.policy_snapshot["providers"]["embeddings"] == retrieval_mode
    assert store.applied == [result]


def test_live_gateway_failure_never_allows_an_order(monkeypatch):
    def unavailable(self, action_id, k=3):
        raise RetrievalUnavailable("live", "embedding")

    monkeypatch.setattr(PrecedentService, "retrieve_for_action", unavailable)
    settings = Settings(retrieval_mode="live", embedding_mode="live", jev_mode="fake")
    result = evaluate(uuid4(), store=FakeStore(proposal()), settings=settings)
    assert result.decision == "escalate"
    assert result.risk_level == "unknown"
    assert "RETRIEVAL_UNAVAILABLE" in [r["code"] for r in result.reasons]


def test_live_retrieval_rejects_a_missing_action_context():
    from backend.services.gateway import live_retrieve, ProviderUnavailable
    with pytest.raises(ProviderUnavailable, match="persisted action"):
        live_retrieve(uuid4(), "text", uuid4())


def test_cached_vector_does_not_make_a_second_embedding_request():
    provider = FakeEmbeddingProvider()
    v = provider.embed_transcript("Haircut tomorrow please")
    repo = Mock()
    repo.get_proposal_transcript.return_value = {
        "interaction_id": uuid4(), "segment_id": 7, "text": "Haircut tomorrow please",
    }
    repo.get_embedding.return_value = {"model": provider.embedding_model, "vector": v}
    repo.search_precedents.return_value = []
    service = PrecedentService(repo, Settings(embedding_mode="fake"), provider)
    assert service.retrieve_for_action(uuid4()) == []
    repo.attach_embedding.assert_not_called()
    assert repo.search_precedents.call_args.args[3] == provider.embedding_model


def test_model_mismatch_is_unavailable_and_never_reembeds():
    repo = Mock()
    repo.get_proposal_transcript.return_value = {
        "interaction_id": uuid4(), "segment_id": 7, "text": "Haircut please",
    }
    repo.get_embedding.return_value = {"model": "different-model", "vector": [1.0] * 1024}
    service = PrecedentService(repo, Settings(embedding_mode="fake"))
    with pytest.raises(RetrievalUnavailable) as error:
        service.retrieve_for_action(uuid4())
    assert error.value.stage == "embedding"
    repo.attach_embedding.assert_not_called()


def test_vectors_and_mode_are_validated_and_explicit():
    fake = FakeEmbeddingProvider()
    vector = fake.embed_transcript("Haircut Tomorrow")
    validate_embedding(vector)
    assert len(vector) == 1024
    assert vector == FakeEmbeddingProvider().embed_transcript("haircut tomorrow")
    with pytest.raises(ValueError):
        validate_embedding([0.0] * 1024)
    with pytest.raises(ValueError):
        validate_embedding([float("nan")] * 1024)
    assert isinstance(PrecedentService(Mock(), Settings()).provider, LiveEmbeddingProvider)


def test_invalid_embedding_mode_or_threshold_is_rejected():
    with pytest.raises(ValueError):
        Settings(embedding_mode="unknown")
    with pytest.raises(ValueError):
        Settings(precedent_similarity_min=float("nan"))


def test_dashboard_does_not_label_fake_embeddings_as_live():
    from backend.services.dashboard_display import case_view
    case = {"decision": "escalate", "policy_snapshot": {"providers": {
        "retrieval": "live", "embeddings": "fake", "jev": "fake"}}}
    view = case_view(case)
    assert view["providers"]["retrieval"] == "Fake embeddings (pgvector)"
