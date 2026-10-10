"""End-to-end wiring test using fakes (not a live API/database test)."""
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from backend.services.akamai import AkamaiService


def test_demo_wiring_with_mock_akamai_and_postgres():
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=(
            '{"intent":"place_order","action":"place_order","amount_usd":"40.00",'
            '"summary":"Lunch pickup","missing_information":[]}'
        )))]
    )
    client.embeddings.create.return_value = SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * 1024)])
    akamai = AkamaiService(client=client)
    transcript = "Two lunch combos for pickup, forty dollars."
    proposal = akamai.analyze_transcript(transcript)
    embedding = akamai.embed_transcript(transcript)

    repo = MagicMock()
    business_id, interaction_id = uuid4(), uuid4()
    repo.create_demo_business.return_value = business_id
    repo.create_interaction.return_value = interaction_id
    repo.save_segment.return_value = 12
    repo.get_segment.return_value = {"segment_id": 12, "transcript": transcript}
    repo.search_similar.return_value = [{"segment_id": 12, "transcript": transcript, "distance": 0.0}]

    segment_id = repo.save_segment(business_id, interaction_id, transcript, embedding)
    fetched = repo.get_segment(business_id, segment_id)
    matches = repo.search_similar(business_id, embedding, limit=3)
    assert proposal.amount_usd == "40.00"
    assert fetched["transcript"] == transcript
    assert matches[0]["segment_id"] == segment_id
    assert client.chat.completions.create.call_args.kwargs["model"] == "hackathon-chat"
    assert client.embeddings.create.call_args.kwargs["model"] == "hackathon-embedding"
