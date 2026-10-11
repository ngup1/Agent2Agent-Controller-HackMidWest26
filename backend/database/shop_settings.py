"""Shop name, menu and order rules for the dashboard settings page.

Callers validate first (backend.services.shop_settings). Menu items left out
of a save are deactivated, never deleted, so past orders keep their records.
"""

import psycopg
from psycopg.types.json import Jsonb

from backend.config import Settings
from backend.scripts.seed import seed


class ShopSettingsStore:
    def __init__(self, settings: Settings, connection_factory=None):
        self.business_id = settings.business_id
        self.connect = connection_factory or (lambda: psycopg.connect(
            settings.database_url, connect_timeout=3,
            options="-c statement_timeout=5000",
        ))

    def load(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT b.name, p.booking_rules FROM businesses b
                   LEFT JOIN business_policies p ON p.business_id=b.id WHERE b.id=%s""",
                (self.business_id,),
            ).fetchone()
            if row is None:
                return None
            items = conn.execute(
                "SELECT code, name, price_usd FROM services WHERE business_id=%s AND active ORDER BY code",
                (self.business_id,),
            ).fetchall()
        return {"name": row[0], "rules": row[1] or {},
                "menu": {code: {"name": name, "price": price} for code, name, price in items}}

    def save(self, name: str, menu: dict, rules: dict) -> bool:
        """Replace the name, rules and active menu in one transaction. False if unseeded."""
        with self.connect() as conn, conn.transaction():
            if conn.execute("UPDATE businesses SET name=%s WHERE id=%s",
                            (name, self.business_id)).rowcount == 0:
                return False
            conn.execute(
                """INSERT INTO business_policies (business_id, booking_rules) VALUES (%s, %s)
                   ON CONFLICT (business_id) DO UPDATE SET booking_rules=EXCLUDED.booking_rules""",
                (self.business_id, Jsonb(rules)),
            )
            conn.execute(
                "UPDATE services SET active=FALSE WHERE business_id=%s AND NOT (code = ANY(%s))",
                (self.business_id, list(menu)),
            )
            for code, item in menu.items():
                conn.execute(
                    """INSERT INTO services (business_id, code, name, price_usd) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (business_id, code) DO UPDATE
                       SET name=EXCLUDED.name, price_usd=EXCLUDED.price_usd, active=TRUE""",
                    (self.business_id, code, item["name"], item["price"]),
                )
        return True

    def restore_example(self) -> None:
        """Put back the preloaded banh mi name, menu and rules. Calls and orders are kept."""
        with self.connect() as conn:
            seed(conn, self.business_id, reset=True)
