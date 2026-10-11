"""Explicit live smoke check; optionally check recorded-call threshold separation."""
import argparse
import json
import math
from pathlib import Path

from backend.config import Settings
from backend.memory.precedents import validate_embedding
from backend.providers.embeddings import LiveEmbeddingProvider
from backend.scripts.replay_calls import load_recordings


def cosine_similarity(left, right):
    return sum(a * b for a, b in zip(left, right)) / (
        math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    )


def check_separation(recordings, vectors, threshold):
    denied = [r for r in recordings if r.outcome == "denied"]
    routine = [r for r in recordings if r.category == "routine" and r.outcome != "denied"]
    if not denied or not routine:
        raise ValueError("Calibration requires both routine calls and staff-denied calls")
    comparisons = [{"routine": r.id, "denied": d.id,
                    "similarity": cosine_similarity(vectors[r.id], vectors[d.id])}
                   for r in routine for d in denied]
    return {"threshold": threshold, "routine_below_denied_threshold": all(
        p["similarity"] < threshold for p in comparisons), "comparisons": comparisons}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recordings", type=Path, help="Also check routine/denied separation using recorded calls")
    args = parser.parse_args()
    settings = Settings.from_env()
    if not settings.openai_api_key:
        parser.exit(1, "BLOCKED: live embedding access missing (OPENAI_API_KEY); no fake substituted\n")
    try:
        recordings = load_recordings(args.recordings) if args.recordings else None
        provider = LiveEmbeddingProvider(settings)
        vector = provider.embed_transcript("Synthetic smoke check: one haircut tomorrow afternoon.")
        validate_embedding(vector)
        report = {"mode": "live", "model": provider.embedding_model, "dimensions": len(vector)}
        if recordings:
            vectors = {}
            for recording in recordings:
                vector = provider.embed_transcript("\n".join(recording.caller_turns))
                validate_embedding(vector)
                vectors[recording.id] = vector
            report.update(check_separation(recordings, vectors, settings.precedent_similarity_min))
        print(json.dumps(report, indent=2))
        if report.get("routine_below_denied_threshold") is False:
            parser.exit(1, "Threshold fails routine/denied separation; inspect recordings before changing it\n")
    except ValueError as exc:
        parser.exit(1, f"Live embedding validation failed: {exc}; no fake substituted\n")
    except Exception as exc:
        parser.exit(1, f"Live embedding check unavailable ({type(exc).__name__}); no fake substituted\n")


if __name__ == "__main__":
    main()
