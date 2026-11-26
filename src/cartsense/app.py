"""HTTP API and the single-page storefront."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from importlib import resources
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import db, sense
from .seed import seed_demo

app = FastAPI(title="cartsense", version="0.1.0")


def _con() -> sqlite3.Connection:
    con = db.connect()
    if con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        con.close()
        seed_demo()
        con = db.connect()
    return con


def _product_map(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    return {r["id"]: dict(r) for r in con.execute("SELECT * FROM products")}


def _check_user(con: sqlite3.Connection, user_id: int) -> None:
    if con.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise HTTPException(404, f"no user {user_id}")


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (resources.files("cartsense") / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/users")
def users() -> list[dict[str, Any]]:
    con = _con()
    try:
        return [dict(r) for r in con.execute("SELECT * FROM users ORDER BY id")]
    finally:
        con.close()


@app.get("/api/products")
def products(category: str | None = None, q: str | None = None) -> list[dict[str, Any]]:
    con = _con()
    try:
        sql, args = "SELECT * FROM products", []
        where = []
        if category:
            where.append("category = ?")
            args.append(category)
        if q:
            where.append("LOWER(name) LIKE ?")
            args.append(f"%{q.lower()}%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [dict(r) for r in con.execute(sql + " ORDER BY category, name", args)]
    finally:
        con.close()


@app.get("/api/categories")
def categories() -> list[str]:
    con = _con()
    try:
        return [r["category"] for r in con.execute("SELECT DISTINCT category FROM products ORDER BY category")]
    finally:
        con.close()


@app.get("/api/foryou")
def foryou(user_id: int, limit: int = 8) -> dict[str, Any]:
    """The sense rail: what this user probably needs right now, each with its reasons."""
    con = _con()
    try:
        _check_user(con, user_id)
        now = datetime.now()
        prods = _product_map(con)
        cats = {pid: p["category"] for pid, p in prods.items()}
        ranked = sense.for_you(db.user_purchases(con, user_id), db.user_category_events(con, user_id), cats, now, limit)
        return {
            "bucket": sense.bucket_of(now.hour),
            "items": [{"product": prods[s.product_id], "score": s.score, "reasons": s.reasons} for s in ranked],
        }
    finally:
        con.close()


class CartChange(BaseModel):
    user_id: int
    product_id: int
    qty: int = Field(default=1, ge=1, le=20)


@app.get("/api/cart")
def cart(user_id: int) -> dict[str, Any]:
    con = _con()
    try:
        _check_user(con, user_id)
        rows = con.execute(
            "SELECT p.*, c.qty FROM cart c JOIN products p ON p.id = c.product_id WHERE c.user_id = ?",
            (user_id,),
        )
        items = [dict(r) for r in rows]
        return {"items": items, "total": round(sum(i["price"] * i["qty"] for i in items), 2)}
    finally:
        con.close()


@app.post("/api/cart/add")
def cart_add(change: CartChange) -> dict[str, Any]:
    con = _con()
    try:
        _check_user(con, change.user_id)
        if con.execute("SELECT 1 FROM products WHERE id = ?", (change.product_id,)).fetchone() is None:
            raise HTTPException(404, f"no product {change.product_id}")
        con.execute(
            "INSERT INTO cart (user_id, product_id, qty) VALUES (?,?,?) "
            "ON CONFLICT(user_id, product_id) DO UPDATE SET qty = qty + excluded.qty",
            (change.user_id, change.product_id, change.qty),
        )
        con.commit()
        return cart(change.user_id)
    finally:
        con.close()


@app.post("/api/cart/remove")
def cart_remove(change: CartChange) -> dict[str, Any]:
    con = _con()
    try:
        con.execute("DELETE FROM cart WHERE user_id = ? AND product_id = ?", (change.user_id, change.product_id))
        con.commit()
        return cart(change.user_id)
    finally:
        con.close()


@app.get("/api/cart/suggest")
def cart_suggest(user_id: int, limit: int = 4) -> list[dict[str, Any]]:
    """Basket sense: what usually rides along with the items in this cart."""
    con = _con()
    try:
        _check_user(con, user_id)
        in_cart = {r["product_id"] for r in con.execute("SELECT product_id FROM cart WHERE user_id = ?", (user_id,))}
        prods = _product_map(con)
        out = []
        names = ", ".join(sorted(prods[c]["name"] for c in in_cart)[:2])
        for pid, lift in sense.complements(db.all_baskets(con), in_cart)[:limit]:
            reason = f"Shoppers who buy {names} pick this up ×{lift:g} more often"
            out.append({"product": prods[pid], "lift": lift, "reason": reason})
        return out
    finally:
        con.close()


class Checkout(BaseModel):
    user_id: int


@app.post("/api/checkout")
def checkout(req: Checkout) -> dict[str, Any]:
    """Turn the cart into an order with a simulated quick-commerce ETA (no payments, demo only)."""
    con = _con()
    try:
        _check_user(con, req.user_id)
        rows = con.execute(
            "SELECT c.product_id, c.qty, p.price FROM cart c JOIN products p ON p.id = c.product_id "
            "WHERE c.user_id = ?",
            (req.user_id,),
        ).fetchall()
        if not rows:
            raise HTTPException(400, "cart is empty")
        total = round(sum(r["price"] * r["qty"] for r in rows), 2)
        eta = 8 + min(10, len(rows))  # simulated: picking time grows with basket size
        now = datetime.now().isoformat(timespec="seconds")
        cur = con.execute(
            "INSERT INTO orders (user_id, ts, eta_minutes, total) VALUES (?,?,?,?)", (req.user_id, now, eta, total)
        )
        oid = cur.lastrowid
        con.executemany(
            "INSERT INTO order_items (order_id, product_id, qty) VALUES (?,?,?)",
            [(oid, r["product_id"], r["qty"]) for r in rows],
        )
        con.execute("DELETE FROM cart WHERE user_id = ?", (req.user_id,))
        con.commit()
        return {"order_id": oid, "eta_minutes": eta, "total": total, "items": len(rows)}
    finally:
        con.close()


@app.get("/api/orders")
def orders(user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    con = _con()
    try:
        _check_user(con, user_id)
        rows = con.execute(
            "SELECT o.id, o.ts, o.eta_minutes, o.total, COUNT(i.product_id) AS items FROM orders o "
            "LEFT JOIN order_items i ON i.order_id = o.id WHERE o.user_id = ? "
            "GROUP BY o.id ORDER BY o.ts DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(r) for r in rows]
    finally:
        con.close()
