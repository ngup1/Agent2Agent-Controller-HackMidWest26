"""Runs one real Akamai call, creates a vector, and round-trips it to PostgreSQL.

Only use a test transcript; this creates a demo business each run.
"""
import json
import os
from dotenv import load_dotenv
from backend.services.akamai import AkamaiService
from backend.database.repository import TranscriptRepository

SAMPLE = "Hello, I need to place an order for two lunch combos totaling 40 dollars for pickup."


def main():
    load_dotenv()
    print("Akamai chat: extracting request details...")
    service = AkamaiService()
    details = service.analyze_transcript(SAMPLE)
    print(json.dumps(details.as_dict(), indent=2))
    print("Akamai embeddings: encoding transcript...")
    embedding = service.embed_transcript(SAMPLE)
    print(f"Embedding length: {len(embedding)}")
    print("PostgreSQL: creating demo business + interaction and saving transcript...")
    repo = TranscriptRepository()
    business_id = repo.create_demo_business()
    interaction_id = repo.create_interaction(business_id)
    segment_id = repo.save_segment(business_id, interaction_id, SAMPLE, embedding)
    retrieved = repo.get_segment(business_id, segment_id)
    similar = repo.search_similar(business_id, embedding, limit=3)
    print(json.dumps({"business_id": str(business_id), "interaction_id": str(interaction_id),
                      "segment_id": segment_id, "retrieved": retrieved, "similar": similar}, indent=2))
    assert retrieved["transcript"] == SAMPLE
    assert similar and similar[0]["segment_id"] == segment_id
    print("SUCCESS: model call, embedding, PostgreSQL save/get/vector search")


if __name__ == "__main__":
    main()
