"""Unit tests for SSA outbound callback calling via Twilio Voice."""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from backend.config import Settings
from backend.services.outbound import OutboundService, _format_time


def test_format_time():
    assert _format_time("2026-10-12T15:00:00-05:00") == "at 15:00 on 2026-10-12"
    assert _format_time("today at 3:00 PM") == "today at 3:00 PM"
    assert _format_time("") == "today"


def test_outbound_skipped_when_unconfigured():
    service = OutboundService(Settings(twilio_account_sid="", twilio_auth_token="", twilio_phone_number=""))
    assert not service.is_configured
    assert service.call_approved_customer("+15551234567") is None


def test_outbound_skipped_when_to_number_empty():
    settings = Settings(
        twilio_account_sid="AC123",
        twilio_auth_token="secret",
        twilio_phone_number="+15559876543",
    )
    service = OutboundService(settings)
    assert service.is_configured
    assert service.call_approved_customer("") is None


def test_outbound_creates_call_with_personalized_twiml():
    settings = Settings(
        twilio_account_sid="AC123",
        twilio_auth_token="secret",
        twilio_phone_number="+15559876543",
    )
    service = OutboundService(settings)

    mock_call = MagicMock()
    mock_call.sid = "CA_test_call_123"
    mock_call.status = "queued"

    mock_client_instance = MagicMock()
    mock_client_instance.calls.create.return_value = mock_call

    with patch("twilio.rest.Client", return_value=mock_client_instance) as mock_client_cls:
        call_sid = service.call_approved_customer(
            to_number="+15551234567",
            customer_name="Sam",
            service="haircut",
            start_time="2026-10-12T15:00:00-05:00",
        )

        assert call_sid == "CA_test_call_123"
        mock_client_cls.assert_called_once_with("AC123", "secret")
        mock_client_instance.calls.create.assert_called_once()
        kwargs = mock_client_instance.calls.create.call_args.kwargs
        assert kwargs["to"] == "+15551234567"
        assert kwargs["from_"] == "+15559876543"
        assert "Sam" in kwargs["twiml"]
        assert "haircut" in kwargs["twiml"]
        assert "at 15:00 on 2026-10-12" in kwargs["twiml"]


def test_outbound_handles_twilio_exception_gracefully():
    settings = Settings(
        twilio_account_sid="AC123",
        twilio_auth_token="secret",
        twilio_phone_number="+15559876543",
    )
    service = OutboundService(settings)

    mock_client_instance = MagicMock()
    mock_client_instance.calls.create.side_effect = RuntimeError("Twilio network error")

    with patch("twilio.rest.Client", return_value=mock_client_instance):
        call_sid = service.call_approved_customer("+15551234567")
        assert call_sid is None
