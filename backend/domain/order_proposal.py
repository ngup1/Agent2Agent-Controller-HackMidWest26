"""SSA-031: untrusted, order-only LLM tool arguments.

Structural validation is NOT authorization. The gate checks the menu, the
order total and the keyword rules after explicit confirmation. The model
never supplies a price or a total; the server computes both from the menu.
"""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_ITEMS = 40


def _reject_control_characters(value: str) -> str:
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("Control characters are not permitted")
    return value


class OrderProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # One entry per item ordered; an item may repeat. There is no quantity field.
    items: list[str] = Field(min_length=1, max_length=MAX_ITEMS)
    customer_name: str = Field(min_length=1, max_length=80)
    callback_number: str | None = Field(default=None, max_length=32)

    @field_validator("items")
    @classmethod
    def validate_items(cls, value: list[str]) -> list[str]:
        for item in value:
            if not isinstance(item, str) or not 1 <= len(item.strip()) <= 80:
                raise ValueError("Each item must be 1-80 characters")
            _reject_control_characters(item)
        return [item.strip() for item in value]

    @field_validator("customer_name")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        return _reject_control_characters(value)

    @field_validator("callback_number")
    @classmethod
    def validate_callback_number(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        # Contact info is untrusted; no caller/staff authentication is inferred.
        if not re.fullmatch(r"\+?[0-9() .-]{7,31}", value):
            raise ValueError("Callback number has an invalid format")
        digit_count = sum(ch.isdigit() for ch in value)
        if not 7 <= digit_count <= 15:
            raise ValueError("Callback number must contain 7 to 15 digits")
        return value

    def action_payload(self) -> dict[str, Any]:
        """Canonical, JSON-serializable payload for agent_actions.payload."""
        return self.model_dump(mode="json", exclude_none=True)


def _unique_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"Duplicate tool argument: {key}")
        output[key] = value
    return output


def parse_order_arguments(raw: str | dict[str, Any]) -> OrderProposal:
    """Reject malformed JSON, unknown keys (prices, totals) and authorization fields."""
    if isinstance(raw, str):
        if len(raw) > 4096:
            raise ValueError("Tool arguments are too large")
        try:
            parsed = json.loads(raw, object_pairs_hook=_unique_json_pairs)
        except json.JSONDecodeError as exc:
            raise ValueError("Tool arguments are not valid JSON") from exc
    elif isinstance(raw, dict):
        parsed = raw
    else:
        raise ValueError("Tool arguments must be a JSON object")
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object")
    return OrderProposal.model_validate(parsed)
