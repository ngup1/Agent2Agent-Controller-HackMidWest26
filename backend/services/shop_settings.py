"""Validation for the dashboard settings page. No database access here.

The settings JSON has two parts: `menu` (item code to name and price) and
`rules` (the record the gate reads on every decision). Nothing is saved unless
the whole document is valid.
"""

import json
import re
from decimal import Decimal, InvalidOperation

from backend.policy.order_rules import rules_used
from backend.scripts.seed import BUSINESS_NAME, MENU, ORDER_RULES

MAX_JSON_CHARACTERS = 20000
MAX_MENU_ITEMS = 50
MAX_PRICE = Decimal("10000")
CODE = re.compile(r"[a-z0-9_]{1,40}")


def clean_name(value) -> str:
    name = " ".join(value.split()) if isinstance(value, str) else ""
    if not name or len(name) > 80 or not name.isprintable():
        raise ValueError("The shop name must be 1 to 80 printable characters.")
    return name


def clean_menu(menu) -> dict:
    if not isinstance(menu, dict) or not menu:
        raise ValueError('"menu" must be an object with at least one item.')
    if len(menu) > MAX_MENU_ITEMS:
        raise ValueError(f"The menu can hold at most {MAX_MENU_ITEMS} items.")
    cleaned = {}
    for code, item in menu.items():
        if not CODE.fullmatch(code):
            raise ValueError(f'Menu code "{code[:40]}" must use only lowercase letters, digits and underscores.')
        if not isinstance(item, dict) or set(item) != {"name", "price"}:
            raise ValueError(f'Menu item "{code}" needs exactly a "name" and a "price".')
        name, price = item["name"], item["price"]
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or not name.isprintable():
            raise ValueError(f'Menu item "{code}" needs a name of 1 to 80 printable characters.')
        try:
            if isinstance(price, bool) or not isinstance(price, (int, float, str)):
                raise InvalidOperation
            amount = Decimal(str(price))
        except InvalidOperation:
            raise ValueError(f'The price of "{code}" must be a number.') from None
        if not amount.is_finite() or amount < 0 or amount > MAX_PRICE:
            raise ValueError(f'The price of "{code}" must be between 0 and {MAX_PRICE}.')
        cleaned[code] = {"name": name.strip(), "price": amount.quantize(Decimal("0.01"))}
    return cleaned


def clean_rules(rules) -> dict:
    if not isinstance(rules, dict):
        raise ValueError('"rules" must be an object.')
    used = rules_used(rules)  # the gate's own validation, so a saved record always loads
    if Decimal(used["max_order_total_auto"]) > MAX_PRICE * MAX_MENU_ITEMS:
        raise ValueError("max_order_total_auto is too large.")
    if len(used["keyword_rules"]) > 20 or any(len(rule["keywords"]) > 100 for rule in used["keyword_rules"]):
        raise ValueError("At most 20 keyword rules with 100 words each are allowed.")
    return {**rules, "max_order_total_auto": float(used["max_order_total_auto"])}


def parse_config(text: str) -> tuple[dict, dict]:
    """Return (menu, rules) from the settings JSON, or raise ValueError with a staff-readable message."""
    if len(text) > MAX_JSON_CHARACTERS:
        raise ValueError("The settings JSON is too long.")
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        where = f" (line {exc.lineno}, column {exc.colno})" if isinstance(exc, json.JSONDecodeError) else ""
        raise ValueError(f"That is not valid JSON{where}.") from None
    if not isinstance(document, dict) or set(document) != {"menu", "rules"}:
        raise ValueError('The JSON must be an object with exactly two keys: "menu" and "rules".')
    return clean_menu(document["menu"]), clean_rules(document["rules"])


def config_text(menu: dict, rules: dict) -> str:
    """The stored menu and rules as the JSON shown in the settings box."""
    return json.dumps({
        "menu": {code: {"name": item["name"], "price": float(item["price"])} for code, item in menu.items()},
        "rules": rules,
    }, indent=2)


def example() -> dict:
    """The preloaded banh mi example, in the shape the settings store returns."""
    return {"name": BUSINESS_NAME, "rules": dict(ORDER_RULES),
            "menu": {code: {"name": name, "price": Decimal(price)} for code, (name, price) in MENU.items()}}
