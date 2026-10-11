"""SSA Outbound Calling: Automated staff approval callback via Twilio Voice.

Allows the gateway/dashboard to dial back a caller upon staff approval,
or to test live voice connectivity from the CLI.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("uvicorn.error")


def _format_time(raw_time: Any) -> str:
    """Helper to convert ISO timestamp or string to human-friendly speech."""
    if not raw_time:
        return "today"
    time_str = str(raw_time)
    # If standard ISO like 2026-10-12T15:00:00-05:00, extract clean time
    if "T" in time_str:
        try:
            parts = time_str.split("T")
            date_part = parts[0]
            time_part = parts[1].split("-")[0].split("+")[0][:5]
            return f"at {time_part} on {date_part}"
        except Exception:
            return time_str
    return time_str


class OutboundService:
    def __init__(self, settings: Any = None):
        if settings is not None:
            self.account_sid = getattr(settings, "twilio_account_sid", "")
            self.auth_token = getattr(settings, "twilio_auth_token", "")
            self.from_number = getattr(settings, "twilio_phone_number", "")
            self.public_host = getattr(settings, "public_host", "")
            self.twiml_url = getattr(settings, "twilio_twiml_url", "")
            self.api_key_sid = ""
            self.api_key_secret = ""
        else:
            self.account_sid = (
                os.getenv("TWILIO_ACCOUNT_SID", "")
                or os.getenv("TWILIO_SID", "")
            )
            self.auth_token = (
                os.getenv("TWILIO_AUTH_TOKEN", "")
                or os.getenv("TWILIO_CLIENT_SECRET", "")
                or os.getenv("TWILIO_API_SECRET", "")
            )
            self.from_number = (
                os.getenv("TWILIO_PHONE_NUMBER", "")
                or os.getenv("TWILIO_FROM_NUMBER", "")
            )
            self.public_host = os.getenv("PUBLIC_HOST", "").strip()
            self.twiml_url = os.getenv("TWILIO_TWIML_URL", "").strip()
            self.api_key_sid = os.getenv("TWILIO_API_KEY_SID", "")
            self.api_key_secret = os.getenv("TWILIO_API_KEY_SECRET", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.from_number)

    def get_client(self):
        from twilio.rest import Client
        if self.api_key_sid and self.api_key_secret and self.account_sid:
            return Client(self.api_key_sid, self.api_key_secret, account_sid=self.account_sid)
        return Client(self.account_sid, self.auth_token)

    def call_approved_customer(
        self,
        to_number: str,
        customer_name: str = "Valued Customer",
        service: str = "appointment",
        start_time: Any = "today",
        direct: bool = False,
    ) -> str | None:
        """Places an outbound voice call to the customer confirming approved booking."""
        if not to_number:
            logger.info("Outbound call skipped: no callback number provided.")
            return None

        if not self.is_configured:
            logger.info(
                "Twilio outbound call skipped: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, or TWILIO_PHONE_NUMBER not configured."
            )
            return None

        try:
            from twilio.rest import Client
        except ImportError:
            logger.warning("Twilio package not installed; run 'pip install twilio' to enable live outbound calls.")
            return None

        time_display = _format_time(start_time)
        clean_name = customer_name.strip() if customer_name else "Valued Customer"
        clean_service = service.strip() if service else "appointment"

        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Pause length="1"/>
    <Say>
        Hello {clean_name}! This is the automated assistant from Demo Banh Mi.
        Our staff has reviewed and approved your order for {clean_service}.
        Your order is now confirmed and being prepared. Thank you for ordering with us!
    </Say>
</Response>"""

        try:
            client = self.get_client()
            logger.info("Dialing outbound callback to %s from %s...", to_number, self.from_number)
            if self.twiml_url:
                call = client.calls.create(
                    to=to_number,
                    from_=self.from_number,
                    url=self.twiml_url,
                )
            elif self.public_host and not direct:
                callback_url = f"https://{self.public_host}/voice/approval"
                call = client.calls.create(
                    to=to_number,
                    from_=self.from_number,
                    url=callback_url,
                )
            else:
                call = client.calls.create(
                    to=to_number,
                    from_=self.from_number,
                    twiml=twiml,
                )
            logger.info("Outbound call placed successfully (SID: %s, status: %s)", call.sid, call.status)
            return call.sid
        except Exception as error:
            logger.warning("Twilio outbound call to %s failed: %s", to_number, error)
            return None


def main():
    parser = argparse.ArgumentParser(description="Make an outbound approval call via Twilio Voice.")
    parser.add_argument("to", help="Target phone number in E.164 format (e.g. +15551234567)")
    parser.add_argument("--name", default="Sam", help="Customer name to announce (default: Sam)")
    parser.add_argument("--service", default="haircut", help="Service name (default: haircut)")
    parser.add_argument("--time", default="today at 3:00 PM", help="Appointment time (default: today at 3:00 PM)")
    parser.add_argument("--url", default="", help="TwiML URL to fetch instructions from (bypasses trial account restriction)")
    parser.add_argument("--direct", action="store_true", help="Send inline TwiML directly without ngrok/tunnel (recommended for paid Twilio accounts)")

    args = parser.parse_args()

    service = OutboundService()
    if args.url:
        service.twiml_url = args.url

    missing = []
    if not service.account_sid:
        missing.append("TWILIO_ACCOUNT_SID")
    if not service.auth_token:
        missing.append("TWILIO_AUTH_TOKEN")
    if not service.from_number:
        missing.append("TWILIO_PHONE_NUMBER")

    if missing:
        print("❌ Error: Missing Twilio credentials in environment or .env:")
        for var in missing:
            print(f"  - {var}")
        sys.exit(1)

    if service.account_sid.startswith("SK"):
        print("⚠️  Notice: Your TWILIO_ACCOUNT_SID starts with 'SK' (e.g. SKc81449...).")
        print("   In Twilio, 'SK...' is an API Key, NOT your Account SID.")
        print("   Your Account SID always starts with 'AC...' (found on your Twilio Console home page under 'Account Info').")
        print("   To fix:")
        print("   1. Open .env")
        print("   2. Set TWILIO_ACCOUNT_SID=AC... (the Account SID from Twilio Console home page)")
        print("   3. Set TWILIO_AUTH_TOKEN=your_primary_auth_token (from the same page)")
        print()

    if args.to == service.from_number:
        print(f"❌ Error: You are trying to dial your own Twilio number ({args.to})!")
        print("   Twilio does not permit calling a number from itself.")
        print("   Please pass your personal mobile phone number instead (which must be verified on Twilio Console).")
        sys.exit(1)


    print(f"📞 Dialing {args.to} from {service.from_number}...")
    call_sid = service.call_approved_customer(
        to_number=args.to,
        customer_name=args.name,
        service=args.service,
        start_time=args.time,
        direct=args.direct,
    )

    if call_sid:
        print(f"✅ Call initiated successfully! Twilio Call SID: {call_sid}")
    else:
        print("❌ Call could not be placed. Check the log output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
