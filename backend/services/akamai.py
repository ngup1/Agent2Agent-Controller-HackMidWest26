"""Compatibility imports for the reusable database branch's adapter."""

from backend.providers.akamai import (
    EMBEDDING_DIM,
    EXTRACTION_PROMPT,
    AkamaiService,
    RequestDetails,
    parse_extraction,
)

__all__ = [
    "EMBEDDING_DIM", "EXTRACTION_PROMPT", "AkamaiService",
    "RequestDetails", "parse_extraction",
]
