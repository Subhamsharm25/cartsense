"""Synthetic but realistic demo data: three personas with distinct shopping rhythms.

Everything here is generated — no real customer data. The patterns are chosen so each
sense signal has something to find: steady replenishment cycles, time-of-day habits,
and co-purchase pairs that genuinely co-occur.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from .catalog import PRODUCTS
from .db import connect

DAYS = 90

USERS = [
    (1, "Asha", "morning-routine"),
    (2, "Rohan", "weekly-bulk"),
    (3, "Meera", "evening-snacker"),
]

# persona: list of (product_ids bought together, every_n_days, jitter_days, hour, hour_jitter)
HABITS: dict[str, list[tuple[list[int], float, float, int, int]]] = {
    "morning-routine": [
        ([1], 2.0, 0.5, 7, 2),  # milk
        ([2, 4], 3.0, 0.7, 7, 2),  # bread + butter
        ([3], 5.0, 1.0, 8, 2),  # eggs
        ([9, 10], 3.0, 1.0, 8, 3),  # fruit
        ([47], 4.0, 1.0, 7, 2),  # curd
        ([37, 41], 14.0, 3.0, 10, 4),  # household top-up
    ],
    "weekly-bulk": [
        ([30, 31, 32, 33, 34], 7.0, 1.0, 11, 3),  # staples basket
        ([11, 12, 13, 14, 15], 3.5, 1.0, 18, 2),  # veg top-up
        ([38, 39, 40], 14.0, 2.0, 11, 3),  # cleaning
        ([42, 44], 21.0, 4.0, 11, 3),  # personal care
        ([1, 3], 3.0, 0.8, 9, 2),  # dairy top-up
    ],
    "evening-snacker": [
        ([17, 18], 2.5, 0.8, 19, 2),  # chips + cola
        ([20, 21], 5.0, 1.5, 20, 2),  # peanuts + chocolate
        ([25, 27], 4.0, 1.0, 20, 2),  # noodles + ketchup
        ([23, 22], 7.0, 1.5, 17, 2),  # tea + biscuits
        ([46], 6.0, 2.0, 21, 2),  # ice cream
        ([1, 2], 3.0, 0.8, 9, 2),  # breakfast basics too
    ],
}

PRICE = {p[0]: p[3] for p in PRODUCTS}


def seed_demo(now: datetime | None = None) -> dict[str, int]:
    """(Re)build the database with the catalog, personas and DAYS days of order history."""
    now = now or datetime.now()
    rng = random.Random(42)
    con = connect()
    try:
        con.executescript("DELETE FROM cart; DELETE FROM order_items; DELETE FROM orders; DELETE FROM users;")
        con.executemany(
            "INSERT OR REPLACE INTO products (id, name, category, price, unit, emoji) VALUES (?,?,?,?,?,?)",
            PRODUCTS,
        )
        con.executemany("INSERT INTO users (id, name, persona) VALUES (?,?,?)", USERS)
        n_orders = 0
        start = now - timedelta(days=DAYS)
        for user_id, _name, persona in USERS:
            for items, every, jitter, hour, hj in HABITS[persona]:
                t = start + timedelta(days=rng.uniform(0, every))
                while t < now - timedelta(hours=12):
                    ts = t.replace(hour=max(0, min(23, hour + rng.randint(-hj, hj))), minute=rng.randint(0, 59))
                    total = round(sum(PRICE[i] for i in items), 2)
                    cur = con.execute(
                        "INSERT INTO orders (user_id, ts, eta_minutes, total) VALUES (?,?,?,?)",
                        (user_id, ts.isoformat(timespec="seconds"), rng.randint(9, 18), total),
                    )
                    oid = cur.lastrowid
                    for pid in items:
                        if rng.random() < 0.92:  # occasionally an item is skipped, like real life
                            con.execute(
                                "INSERT OR IGNORE INTO order_items (order_id, product_id, qty) VALUES (?,?,?)",
                                (oid, pid, 1),
                            )
                    n_orders += 1
                    t += timedelta(days=max(0.5, every + rng.uniform(-jitter, jitter)))
        con.commit()
        return {"users": len(USERS), "products": len(PRODUCTS), "orders": n_orders}
    finally:
        con.close()
