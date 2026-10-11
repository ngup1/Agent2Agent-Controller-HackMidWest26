"""Deterministic order rules. No database, clock or model access here.

Every limit comes from the stored rules record (business_policies.booking_rules),
so changing that record changes the next decision without a code change.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from backend.domain.gate import reason


@dataclass(frozen=True)
class RuleOutcome:
    result: str  # pass | escalate | clarify
    checks: list[dict]
    reasons: list[dict]
    total: Decimal | None = None  # None when an item is not on the menu


def money(value: Decimal) -> str:
    return f"${value:.2f}"


def rules_used(order_rules: dict) -> dict:
    """The limits the gate compares against, JSON-ready; fails loudly if unseeded."""
    try:
        limit = Decimal(str(order_rules["max_order_total_auto"]))
        keyword_rules = order_rules["keyword_rules"]
    except KeyError as exc:
        raise ValueError(f"order rules are missing {exc.args[0]}; run the seed") from exc
    except InvalidOperation as exc:
        raise ValueError("max_order_total_auto must be a number") from exc
    if not limit.is_finite() or limit < 0:
        raise ValueError("max_order_total_auto must be zero or more")
    if not isinstance(keyword_rules, list):
        raise ValueError("keyword_rules must be a list")
    cleaned = []
    for rule in keyword_rules:
        label = rule.get("label") if isinstance(rule, dict) else None
        keywords = rule.get("keywords") if isinstance(rule, dict) else None
        if (not isinstance(label, str) or not label.strip() or not isinstance(keywords, list)
                or not all(isinstance(word, str) and word.strip() for word in keywords)):
            raise ValueError("Each keyword rule needs a label and a list of words")
        cleaned.append({"label": label.strip(), "keywords": [word.strip().casefold() for word in keywords]})
    return {"max_order_total_auto": f"{limit:.2f}", "keyword_rules": cleaned}


def matched_keywords(keywords: list[str], caller_text: str) -> list[str]:
    """Whole-word (or whole-phrase) matches in what the caller said."""
    text = " ".join(caller_text.casefold().split())
    return [word for word in keywords
            if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text)]


def check_rules(proposal: dict, order_rules: dict, menu: dict, caller_text: str) -> RuleOutcome:
    """Compare the structured order with the rules; one check per rule.

    `menu` maps an active item code to {"name": str, "price": Decimal}.
    """
    rules = rules_used(order_rules)
    limit = Decimal(rules["max_order_total_auto"])
    checks, reasons = [], []

    def check(rule, limit_text, actual, result, code=None, evidence=None):
        checks.append({"rule": rule, "limit": limit_text, "actual": actual, "result": result})
        if code:
            reasons.append(reason(code, "rule", result, evidence))

    items = proposal.get("items")
    valid_list = isinstance(items, list) and all(isinstance(item, str) for item in items)
    unknown = [] if not valid_list else sorted({item for item in items if item not in menu})
    if not valid_list or not items:
        check("menu_items", "at least one item on the menu", None, "clarify", "EMPTY_ORDER")
    elif unknown:
        check("menu_items", "every item is on the menu", ", ".join(items), "clarify",
              "UNKNOWN_ITEM", {"items": unknown})
    else:
        check("menu_items", "every item is on the menu", ", ".join(items), "pass")

    total = None
    limit_text = f"{money(limit)} automatic"
    if not valid_list or not items or unknown:
        check("order_total", limit_text, None, "not_checked")
    else:
        total = sum((menu[item]["price"] for item in items), Decimal("0"))
        if total > limit:
            check("order_total", limit_text, money(total), "escalate",
                  "ORDER_OVER_LIMIT", {"limit": f"{limit:.2f}", "actual": f"{total:.2f}"})
        else:
            check("order_total", limit_text, money(total), "pass")

    for rule in rules["keyword_rules"]:
        matched = matched_keywords(rule["keywords"], caller_text)
        name = f"keyword: {rule['label']}"
        if matched:
            check(name, "none of the listed words", ", ".join(matched), "escalate",
                  "KEYWORD_RULE", {"label": rule["label"], "matched": matched})
        else:
            check(name, "none of the listed words", "none", "pass")

    results = {item["result"] for item in checks}
    result = "clarify" if "clarify" in results else "escalate" if "escalate" in results else "pass"
    return RuleOutcome(result, checks, reasons, total)
