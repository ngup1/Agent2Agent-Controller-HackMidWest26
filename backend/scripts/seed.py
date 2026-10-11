"""Repeatable synthetic banh mi shop seed. No calls, decisions or orders are seeded.

By default existing rules and menu items are left alone. --reset restores the
preloaded example: it overwrites the rules, updates the listed menu items and
deactivates every other item. It never deletes calls, reviews or orders.
"""

import argparse

import psycopg
from psycopg.types.json import Jsonb

from backend.config import Settings

BUSINESS_NAME = "Demo Banh Mi"
ORDER_RULES = {
    "timezone": "America/Chicago",
    # Orders above this server-computed total go to staff.
    "max_order_total_auto": 100.00,
    # If the caller says any listed word, the order goes to staff under that label.
    "keyword_rules": [
        {
            "label": "Allergy mention",
            "keywords": [
                "allergy", "allergies", "allergic", "allergen", "allergens",
                "peanut", "peanuts", "gluten", "celiac", "shellfish",
                "anaphylactic", "anaphylaxis", "epipen", "intolerant", "intolerance",
            ],
        },
    ],
}
# code: (display name, price in USD)
MENU = {
    "grilled_pork": ("Grilled pork banh mi", "8.50"),
    "lemongrass_chicken": ("Lemongrass chicken banh mi", "8.50"),
    "tofu": ("Tofu banh mi", "8.00"),
    "cold_cut_special": ("Cold cut special banh mi", "9.00"),
    "iced_coffee": ("Vietnamese iced coffee", "4.50"),
    "party_tray": ("Party tray", "60.00"),
}


def seed(conn, business_id, reset: bool = False):
    with conn.transaction():
        conn.execute(
            """INSERT INTO businesses (id, name, industry) VALUES (%s, %s, 'restaurant')
               ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, industry=EXCLUDED.industry
               WHERE %s""",
            (business_id, BUSINESS_NAME, reset),
        )
        conn.execute(
            """INSERT INTO business_policies (business_id, booking_rules) VALUES (%s, %s)
               ON CONFLICT (business_id) DO UPDATE SET booking_rules=EXCLUDED.booking_rules
               WHERE business_policies.booking_rules='{}'::jsonb OR %s""",
            (business_id, Jsonb(ORDER_RULES), reset),
        )
        if reset:
            conn.execute(
                "UPDATE services SET active=FALSE WHERE business_id=%s AND NOT (code = ANY(%s))",
                (business_id, list(MENU)),
            )
        for code, (name, price) in MENU.items():
            conn.execute(
                """INSERT INTO services (business_id, code, name, price_usd)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (business_id, code) DO UPDATE
                   SET name=EXCLUDED.name, price_usd=EXCLUDED.price_usd, active=TRUE
                   WHERE %s""",
                (business_id, code, name, price, reset),
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true",
                        help="Restore the preloaded rules and menu, replacing what is stored")
    args = parser.parse_args()
    settings = Settings.from_env()
    with psycopg.connect(settings.database_url, connect_timeout=10) as conn:
        seed(conn, settings.business_id, args.reset)
    print(f"Seeded {BUSINESS_NAME}: {len(MENU)} menu items"
          + (" (rules and menu reset to the preloaded example)" if args.reset else ""))


if __name__ == "__main__":
    main()
