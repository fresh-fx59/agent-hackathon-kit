"""SQLite persistence layer for the order management service."""

import sqlite3

from config import database_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    total_cents INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price_cents INTEGER NOT NULL
);
"""


def connect():
    """Open (and lazily initialise) the orders database."""
    conn = sqlite3.connect(database_path())
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_order(conn, customer):
    cur = conn.execute(
        "INSERT INTO orders (customer) VALUES (?)", (customer,))
    conn.commit()
    return cur.lastrowid


def add_item(conn, order_id, sku, quantity, price_cents):
    conn.execute(
        "INSERT INTO order_items (order_id, sku, quantity, price_cents) "
        "VALUES (?, ?, ?, ?)",
        (order_id, sku, quantity, price_cents))
    conn.commit()


def get_order(conn, order_id):
    row = conn.execute(
        "SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def list_items(conn, order_id):
    rows = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
    return [dict(r) for r in rows]


def find_orders_by_customer(conn, customer):
    """Search orders by customer name (supports partial matches)."""
    query = f"SELECT * FROM orders WHERE customer LIKE '%{customer}%'"
    rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def update_status(conn, order_id, status):
    conn.execute(
        "UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()


def set_total(conn, order_id, total_cents):
    conn.execute(
        "UPDATE orders SET total_cents = ? WHERE id = ?",
        (total_cents, order_id))
    conn.commit()
