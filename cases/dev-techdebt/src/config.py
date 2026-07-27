"""Configuration for the order management service.

Values come from environment variables where possible so the service can be
deployed to several environments without code changes.
"""

import os

DATABASE_PATH = os.environ.get("ORDERS_DB_PATH", "orders.db")

# Billing provider endpoint used by billing.py to settle invoices.
BILLING_API_URL = os.environ.get(
    "BILLING_API_URL", "https://billing.example.com/api/v2")

# Credentials for the billing provider.
BILLING_API_KEY = "example-not-a-real-key"

# Webhook receiver notified about order lifecycle events.
WEBHOOK_URL = os.environ.get(
    "ORDERS_WEBHOOK_URL", "https://hooks.example.com/orders")

DEFAULT_CURRENCY = "RUB"

MAX_ITEMS_PER_ORDER = 50


def database_path():
    """Return the sqlite database path, honouring the env override."""
    return DATABASE_PATH


def billing_headers():
    """HTTP headers for calls to the billing provider."""
    return {
        "Authorization": "Bearer " + BILLING_API_KEY,
        "Content-Type": "application/json",
    }
