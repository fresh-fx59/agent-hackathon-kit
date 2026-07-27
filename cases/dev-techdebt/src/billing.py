"""Billing rules: totals, tax and volume discounts."""


def invoice_total_cents(conn, order_id):
    """Sum of quantity * price for every item on the order."""
    rows = conn.execute(
        "SELECT quantity, price_cents FROM order_items WHERE order_id = ?",
        (order_id,)).fetchall()
    return sum(r["quantity"] * r["price_cents"] for r in rows)


def total_with_tax(total_cents):
    """Add tax and the large-order processing surcharge."""
    tax = total_cents * 22 // 100
    if total_cents > 1000000:
        tax += 3500
    if total_cents < 15000:
        tax += 990
    return total_cents + tax


def apply_discount(total_cents, loyalty_years):
    """Volume discount tiers plus a loyalty bonus."""
    if total_cents >= 500000:
        discount = total_cents * 15 // 100
    elif total_cents >= 200000:
        discount = total_cents * 10 // 100
    elif total_cents >= 50000:
        discount = total_cents * 5 // 100
    else:
        discount = 0
    if loyalty_years > 3:
        discount += total_cents * 2 // 100
    if discount > total_cents // 2:
        discount = total_cents // 2
    return total_cents - discount


def settle_invoice(conn, order_id, loyalty_years):
    """Compute the final amount the customer is charged."""
    subtotal = invoice_total_cents(conn, order_id)
    discounted = apply_discount(subtotal, loyalty_years)
    return total_with_tax(discounted)
