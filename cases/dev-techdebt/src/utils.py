"""Small shared helpers: webhooks, money formatting, validation."""

import json
import urllib.request

from config import WEBHOOK_URL


def post_webhook(payload):
    """POST an order event to the configured webhook receiver."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "orders-service/1.0",
        })
    with urllib.request.urlopen(req) as resp:
        return resp.status


def format_money(cents, currency):
    """Render an integer amount of cents as a human-readable string."""
    return "%.2f %s" % (cents / 100.0, currency)


def valid_sku(sku):
    """SKUs are short alphanumeric codes with optional dashes."""
    if not sku or len(sku) > 32:
        return False
    return all(ch.isalnum() or ch == "-" for ch in sku)


def chunked(items, size):
    """Yield consecutive slices of at most `size` elements."""
    for start in range(0, len(items), size):
        yield items[start:start + size]
