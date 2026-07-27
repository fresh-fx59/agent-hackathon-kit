"""Order notifications: an in-memory outbox instead of a real mail gateway."""

OUTBOX = []


def reset():
    """Clear the outbox (used by tests)."""
    del OUTBOX[:]


def send(to, subject, body):
    """Queue one message; returns the stored message dict."""
    message = {"to": to, "subject": subject, "body": body}
    OUTBOX.append(message)
    return message


def send_order_confirmation(to, order):
    """Send the standard order-confirmation message."""
    subject = "Order %s confirmed" % order["order_id"]
    body = ("Thank you for your order!\n"
            "Items: %d, total to pay: %.2f"
            % (sum(order["items"].values()), order["total"]))
    return send(to, subject, body)
