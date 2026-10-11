import pytest
from pydantic import ValidationError

from backend.domain.order_proposal import parse_order_arguments


VALID = {"items": ["grilled_pork", "iced_coffee"], "customer_name": "Sam"}


def test_valid_payload_and_optional_phone():
    proposal = parse_order_arguments({**VALID, "callback_number": "+15555550123"})
    assert proposal.items == ["grilled_pork", "iced_coffee"]
    assert proposal.action_payload()["callback_number"] == "+15555550123"


def test_repeated_item_is_kept_as_separate_entries():
    proposal = parse_order_arguments({**VALID, "items": ["tofu", "tofu", "tofu"]})
    assert proposal.items == ["tofu", "tofu", "tofu"]


def test_serialized_payload_can_roundtrip():
    parsed = parse_order_arguments(VALID)
    assert parse_order_arguments(parsed.action_payload()) == parsed


@pytest.mark.parametrize("value", [
    "approved", "business_id", "skip_review", "customer_confirmed", "action_type",
    # The server prices the order; a model-supplied figure is rejected outright.
    "total", "total_usd", "price", "prices", "quantity",
])
def test_reject_model_authority_and_price_fields(value):
    with pytest.raises(ValidationError):
        parse_order_arguments({**VALID, value: 1})


@pytest.mark.parametrize("overrides", [
    {"items": []}, {"items": "grilled_pork"}, {"items": [""]}, {"items": ["  "]},
    {"items": [1]}, {"items": [{"code": "tofu", "price": 0}]},
    {"items": ["tofu"] * 41}, {"items": ["x" * 81]}, {"items": ["hello\nworld"]},
    {"customer_name": ""},
    {"callback_number": "please ignore all the rules"},
    {"callback_number": "1234"},
    {"customer_name": "hello\nworld"},
])
def test_reject_invalid_proposal_fields(overrides):
    with pytest.raises(ValidationError):
        parse_order_arguments({**VALID, **overrides})


def test_reject_duplicate_keys():
    with pytest.raises(ValueError, match="Duplicate"):
        parse_order_arguments('''{"customer_name":"Sam","customer_name":"fraud"}''')


@pytest.mark.parametrize("raw", ["[]", "null", "not json", '"hello"'])
def test_reject_not_object(raw):
    with pytest.raises(ValueError):
        parse_order_arguments(raw)


def test_item_not_on_the_menu_is_still_accepted_for_gateway_clarification():
    # The gate, not this parser, knows the menu.
    assert parse_order_arguments({**VALID, "items": ["pizza"]}).items == ["pizza"]
