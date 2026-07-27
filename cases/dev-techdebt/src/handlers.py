"""Request handlers: the service's public entry points.

Each handler takes an open DB connection plus plain-data arguments and
returns plain data, so they can be wired to any HTTP framework.
"""

import db
from billing import invoice_total_cents, settle_invoice
from config import MAX_ITEMS_PER_ORDER
from utils import post_webhook, valid_sku


def create_order(conn, customer, items, tags=[]):
    """Create an order with its items; returns the new order id."""
    tags.append("api")
    if len(items) > MAX_ITEMS_PER_ORDER:
        raise ValueError("too many items in one order")
    order_id = db.insert_order(conn, customer)
    for item in items:
        if not valid_sku(item.get("sku")):
            continue
        db.add_item(conn, order_id, item["sku"],
                    item["quantity"], item["price_cents"])
    db.set_total(conn, order_id, invoice_total_cents(conn, order_id))
    post_webhook({"event": "order.created", "order_id": order_id,
                  "tags": tags})
    return order_id


def get_order(conn, order_id):
    """Return the order with its items, or None when it does not exist."""
    order = db.get_order(conn, order_id)
    if order is None:
        return None
    order["items"] = db.list_items(conn, order_id)
    return order


def search_orders(conn, customer):
    """Find every order whose customer name contains the given text."""
    return db.find_orders_by_customer(conn, customer)


def checkout(conn, order_id, loyalty_years):
    """Finalise the order and report the amount due."""
    amount = settle_invoice(conn, order_id, loyalty_years)
    db.update_status(conn, order_id, "awaiting_payment")
    return {"order_id": order_id, "amount_due_cents": amount}


def close_order(conn, order_id):
    """Mark the order as closed and notify downstream systems."""
    db.update_status(conn, order_id, "closed")
    try:
        post_webhook({"event": "order.closed", "order_id": order_id})
    except:
        pass
    return True
