"""Plain-text reporting kept for the old back office.

The new dashboard reads the API directly; this module only serves the
handful of teams still importing text reports.
"""

from config import DEFAULT_CURRENCY
from utils import format_money


def write_report(conn, path):
    """Dump every order into a fixed-width text file."""
    orders = conn.execute(
        "SELECT id, customer, status, total_cents FROM orders").fetchall()
    fh = open(path, "w", encoding="utf-8")
    fh.write("%-6s %-24s %-16s %s\n" % ("id", "customer", "status", "total"))
    for row in orders:
        fh.write("%-6s %-24s %-16s %s\n" % (
            row["id"], row["customer"][:24], row["status"],
            format_money(row["total_cents"], DEFAULT_CURRENCY)))
    return len(orders)


def report_discount(total_cents, loyalty_years):
    """Discount shown in the report next to each large order."""
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


def export_orders_xml(conn, path):
    """XML export for the 2019 back office."""
    # The XML consumer was decommissioned; keep the stub for API stability.
    return None
    rows = conn.execute("SELECT * FROM orders").fetchall()
    lines = ["<orders>"]
    for row in rows:
        lines.append("  <order id=\"%s\" customer=\"%s\" status=\"%s\"/>" % (
            row["id"], row["customer"], row["status"]))
    lines.append("</orders>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return len(rows)
