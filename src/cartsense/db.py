"""SQLite storage: schema, connection helper and the queries the sense engine reads."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, persona TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
  price REAL NOT NULL, unit TEXT NOT NULL, emoji TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  ts TEXT NOT NULL,
  eta_minutes INTEGER NOT NULL DEFAULT 0,
  total REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS order_items (
  order_id INTEGER NOT NULL REFERENCES orders(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  qty INTEGER NOT NULL,
  PRIMARY KEY (order_id, product_id)
);
CREATE TABLE IF NOT EXISTS cart (
  user_id INTEGER NOT NULL REFERENCES users(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  qty INTEGER NOT NULL,
  PRIMARY KEY (user_id, product_id)
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id);
"""


def db_path() -> Path:
    return Path(os.environ.get("CARTSENSE_DB", "cartsense.db"))


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con


def user_purchases(con: sqlite3.Connection, user_id: int) -> dict[int, list[datetime]]:
    """Purchase timestamps per product for one user, oldest first."""
    rows = con.execute(
        "SELECT i.product_id, o.ts FROM orders o JOIN order_items i ON i.order_id = o.id "
        "WHERE o.user_id = ? ORDER BY o.ts",
        (user_id,),
    )
    out: dict[int, list[datetime]] = {}
    for r in rows:
        out.setdefault(r["product_id"], []).append(datetime.fromisoformat(r["ts"]))
    return out


def user_category_events(con: sqlite3.Connection, user_id: int) -> list[tuple[datetime, str]]:
    """(timestamp, category) for every item the user ever ordered."""
    rows = con.execute(
        "SELECT o.ts, p.category FROM orders o "
        "JOIN order_items i ON i.order_id = o.id JOIN products p ON p.id = i.product_id "
        "WHERE o.user_id = ? ORDER BY o.ts",
        (user_id,),
    )
    return [(datetime.fromisoformat(r["ts"]), r["category"]) for r in rows]


def all_baskets(con: sqlite3.Connection) -> list[set[int]]:
    """Every order in the store as a set of product ids (all users), for co-purchase mining."""
    rows = con.execute("SELECT order_id, product_id FROM order_items ORDER BY order_id")
    baskets: dict[int, set[int]] = {}
    for r in rows:
        baskets.setdefault(r["order_id"], set()).add(r["product_id"])
    return list(baskets.values())
