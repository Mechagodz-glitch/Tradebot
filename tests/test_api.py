from fastapi.testclient import TestClient

from tradebot.api import app as app_module


def test_api_roundtrip(engine, monkeypatch):
    monkeypatch.setattr(app_module, "_engine", engine)
    c = TestClient(app_module.app)
    assert c.get("/health").json()["ok"] is True
    q = c.get("/quote/BTC-USD").json()
    assert q["symbol"] == "BTC-USD" and q["last"] == 50_000
    r = c.post("/orders", json={"symbol": "eth-usd", "side": "buy", "qty": 0.5, "reason": "api test"})
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["status"] == "filled"
    assert c.get(f"/orders/{o['id']}").json()["id"] == o["id"]
    assert len(c.get("/positions?venue=paper").json()) == 1
    r = c.post("/orders", json={"symbol": "BTC-USD", "side": "buy", "qty": 5})
    assert r.status_code == 422 and r.json()["error"] == "risk_rejected"
    assert c.post("/sync").json()["paper"]["equity"]["crypto"] > 0
    assert len(c.get("/equity?venue=paper&market=crypto").json()) == 1
    r = c.post("/close", json={"symbol": "ETH-USD"})
    assert r.json()["status"] == "filled"
    assert c.get("/account?venue=paper&market=crypto").json()[0]["open_positions"] == 0
    assert c.get("/journal").status_code == 200
    assert c.get("/").status_code == 200 and "Tradebot" in c.get("/").text


def test_api_token(engine, monkeypatch):
    engine.settings.api_token = "secret"
    monkeypatch.setattr(app_module, "_engine", engine)
    c = TestClient(app_module.app)
    assert c.get("/positions").status_code == 401
    assert c.get("/positions", headers={"Authorization": "Bearer secret"}).status_code == 200
