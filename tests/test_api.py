"""API tests over the real app with a seeded temporary database."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module):
    db = tmp_path_factory.mktemp("db") / "cartsense-test.db"
    monkeypatch_module.setenv("CARTSENSE_DB", str(db))
    from cartsense.app import app
    from cartsense.seed import seed_demo

    stats = seed_demo()
    assert stats["orders"] > 100
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.text == "ok"


def test_index_serves_storefront(client):
    r = client.get("/")
    assert r.status_code == 200 and "cartsense" in r.text


def test_users_and_products(client):
    assert len(client.get("/api/users").json()) == 3
    products = client.get("/api/products").json()
    assert len(products) == 48
    dairy = client.get("/api/products", params={"category": "Dairy & Breakfast"}).json()
    assert dairy and all(p["category"] == "Dairy & Breakfast" for p in dairy)
    milk = client.get("/api/products", params={"q": "milk"}).json()
    assert any("Milk" in p["name"] for p in milk)


def test_categories(client):
    cats = client.get("/api/categories").json()
    assert "Staples" in cats and len(cats) >= 6


def test_foryou_has_scored_reasons(client):
    d = client.get("/api/foryou", params={"user_id": 1}).json()
    assert d["bucket"] in {"morning", "afternoon", "evening", "night"}
    assert d["items"], "90 days of history must produce suggestions"
    first = d["items"][0]
    assert first["score"] > 0 and first["reasons"]
    assert "product" in first and "name" in first["product"]


def test_foryou_unknown_user(client):
    assert client.get("/api/foryou", params={"user_id": 99}).status_code == 404


def test_cart_flow_and_suggestions(client):
    add = {"user_id": 3, "product_id": 17, "qty": 2}  # Meera + chips
    c = client.post("/api/cart/add", json=add).json()
    assert c["items"][0]["qty"] == 2 and c["total"] == 60.0
    sug = client.get("/api/cart/suggest", params={"user_id": 3}).json()
    assert sug, "chips should have co-purchase complements (cola)"
    assert any(s["product"]["name"] == "Cola" for s in sug)
    assert all("reason" in s for s in sug)
    c = client.post("/api/cart/remove", json={"user_id": 3, "product_id": 17}).json()
    assert c["items"] == [] and c["total"] == 0


def test_cart_add_unknown_product(client):
    assert client.post("/api/cart/add", json={"user_id": 1, "product_id": 999}).status_code == 404


def test_checkout_creates_order_and_clears_cart(client):
    client.post("/api/cart/add", json={"user_id": 2, "product_id": 30, "qty": 1})
    client.post("/api/cart/add", json={"user_id": 2, "product_id": 33, "qty": 2})
    before = len(client.get("/api/orders", params={"user_id": 2}).json())
    o = client.post("/api/checkout", json={"user_id": 2}).json()
    assert o["total"] == 320.0 + 2 * 155.0
    assert 8 <= o["eta_minutes"] <= 18
    assert client.get("/api/cart", params={"user_id": 2}).json()["items"] == []
    orders = client.get("/api/orders", params={"user_id": 2}).json()
    assert len(orders) == min(before + 1, 10)
    assert orders[0]["id"] == o["order_id"] and orders[0]["items"] == 2


def test_checkout_empty_cart_rejected(client):
    assert client.post("/api/checkout", json={"user_id": 1}).status_code == 400


def test_cli_seed(tmp_path, monkeypatch, capsys):
    from cartsense.__main__ import main

    monkeypatch.setenv("CARTSENSE_DB", str(tmp_path / "cli.db"))
    assert main(["seed-demo"]) == 0
    assert "seeded 3 users, 48 products" in capsys.readouterr().out
