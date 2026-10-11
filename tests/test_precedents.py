from unittest.mock import Mock
from uuid import uuid4

import pytest

from backend.config import Settings
from backend.memory.precedents import PrecedentService, RetrievalUnavailable
from backend.providers.embeddings import FakeEmbeddingProvider, LiveEmbeddingProvider


def setup_service():
    settings = Settings(embedding_mode="fake")
    repo = Mock()
    repo.get_proposal_transcript.return_value = {
        "interaction_id": uuid4(), "segment_id": 7, "text": "Book a haircut\nYes",
    }
    provider = Mock(wraps=FakeEmbeddingProvider())
    provider.mode = "fake"
    provider.embedding_model = FakeEmbeddingProvider.embedding_model
    vector = FakeEmbeddingProvider().embed_transcript("Book a haircut\nYes")
    repo.get_embedding.side_effect = [None, {"model": provider.embedding_model, "vector": vector}]
    repo.search_precedents.return_value = []
    return PrecedentService(repo, settings, provider), repo, provider


def test_embed_once_and_empty_search_is_success():
    service, repo, provider = setup_service()
    assert service.retrieve_for_action(uuid4()) == []
    provider.embed_transcript.assert_called_once_with("Book a haircut\nYes")
    assert repo.attach_embedding.call_args.args[1] == 7
    assert repo.search_precedents.call_args.args[2] == repo.get_proposal_transcript.return_value["interaction_id"]


def test_stored_vector_reused_without_provider_call():
    service, repo, provider = setup_service()
    repo.get_embedding.side_effect = None
    repo.get_embedding.return_value = {
        "model": provider.embedding_model, "vector": [1.0] * 1024,
    }
    service.retrieve_for_action(uuid4())
    provider.embed_transcript.assert_not_called()
    repo.attach_embedding.assert_not_called()


@pytest.mark.parametrize("failure_stage", ["transcript", "embedding", "search"])
def test_failures_are_explicit_and_safe(failure_stage):
    service, repo, provider = setup_service()
    target = {"transcript": repo.get_proposal_transcript,
              "embedding": provider.embed_transcript,
              "search": repo.search_precedents}[failure_stage]
    target.side_effect = RuntimeError("sensitive provider response")
    with pytest.raises(RetrievalUnavailable) as caught:
        service.retrieve_for_action(uuid4())
    assert caught.value.reason_code == "RETRIEVAL_UNAVAILABLE"
    assert caught.value.stage == failure_stage
    assert caught.value.mode == "fake"
    assert "sensitive" not in str(caught.value)


@pytest.mark.parametrize("vector", [[1.0], [0.0] * 1024, [float("nan")] * 1024, [float("inf")] * 1024])
def test_bad_embeddings_fail_before_storage(vector):
    service, repo, provider = setup_service()
    provider.embed_transcript.return_value = vector
    with pytest.raises(RetrievalUnavailable):
        service.retrieve_for_action(uuid4())
    repo.attach_embedding.assert_not_called()
    repo.search_precedents.assert_not_called()


def test_model_mismatch_never_reuses_or_replaces_vector():
    service, repo, provider = setup_service()
    repo.get_embedding.side_effect = None
    repo.get_embedding.return_value = {"model": "different", "vector": [1.0] * 1024}
    with pytest.raises(RetrievalUnavailable):
        service.retrieve_for_action(uuid4())
    provider.embed_transcript.assert_not_called()
    repo.attach_embedding.assert_not_called()


def test_spec_interface_requires_matching_server_business_and_persisted_text():
    service, repo, _ = setup_service()
    with pytest.raises(ValueError, match="configuration"):
        service.retrieve_precedents(uuid4(), "text", action_id=uuid4())
    repo.get_proposal_transcript.assert_not_called()
    with pytest.raises(ValueError, match="persisted"):
        service.retrieve_precedents(service.business_id, "made up", action_id=uuid4())


def test_fake_is_deterministic_and_distinct_from_live():
    fake = FakeEmbeddingProvider()
    assert fake.embed_transcript("A haircut") == FakeEmbeddingProvider().embed_transcript("a HAIRCUT")
    assert len(fake.embed_transcript("A haircut")) == 1024
    assert fake.embedding_model != Settings().embedding_model


def test_missing_live_access_does_not_fall_back_to_fake():
    service = PrecedentService(Mock(), Settings(openai_api_key=""))
    assert isinstance(service.provider, LiveEmbeddingProvider)
    service.repository.get_proposal_transcript.return_value = {
        "interaction_id": uuid4(), "segment_id": 1, "text": "Synthetic call",
    }
    service.repository.get_embedding.return_value = None
    with pytest.raises(RetrievalUnavailable) as caught:
        service.retrieve_for_action(uuid4())
    assert caught.value.mode == "live"


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan")])
def test_invalid_threshold_rejected(threshold):
    with pytest.raises(ValueError, match="PRECEDENT_SIMILARITY_MIN"):
        Settings(precedent_similarity_min=threshold)
