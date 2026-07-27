"""Checkout: discount calculation and order creation.

Discount model: the promo-code discount and the loyalty discount stack
additively, and the sum is capped at MAX_DISCOUNT_PERCENT.
"""

from app import notifications

# Promo codes: code -> percent off the subtotal.
PROMO_CODES = {
    "WELCOME10": 10,
    "VIP5": 5,
}

# Orders with a subtotal at or above this earn the loyalty discount.
LOYALTY_THRESHOLD = 5000.0
LOYALTY_DISCOUNT_PERCENT = 5

# Hard cap for the combined discount.
MAX_DISCOUNT_PERCENT = 15


class CheckoutError(ValueError):
    """Invalid checkout attempt (empty cart, unknown promo code, ...)."""


_order_counter = {"value": 0}


def _next_order_id():
    _order_counter["value"] += 1
    return "ORD-%d" % _order_counter["value"]


def discount_percent(subtotal, promo_code=None):
    """Return the total discount percent for an order subtotal.

    The promo-code and loyalty discounts stack additively; the sum is
    capped at MAX_DISCOUNT_PERCENT.
    """
    percent = 0
    if promo_code is not None:
        if promo_code not in PROMO_CODES:
            raise CheckoutError("unknown promo code: %s" % promo_code)
        percent += PROMO_CODES[promo_code]
    if subtotal >= LOYALTY_THRESHOLD:
        percent += LOYALTY_DISCOUNT_PERCENT
    return min(percent, MAX_DISCOUNT_PERCENT)


def checkout(cart, user, promo_code=None):
    """Turn a cart into an order and send the confirmation notification."""
    if not cart.items:
        raise CheckoutError("cannot check out an empty cart")
    subtotal = cart.subtotal()
    percent = discount_percent(subtotal, promo_code)
    discount = round(subtotal * percent / 100.0, 2)
    order = {
        "order_id": _next_order_id(),
        "user": user["login"],
        "items": dict(cart.items),
        "subtotal": subtotal,
        "discount_percent": percent,
        "discount": discount,
        "total": round(subtotal - discount, 2),
        "promo_code": promo_code,
    }
    notifications.send_order_confirmation(user["email"], order)
    return order
