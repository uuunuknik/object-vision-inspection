import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TV_SECRET", "s3cret")
    monkeypatch.setenv("TRADE_SIZE_THB", "100000")
    import agent
    importlib.reload(agent)
    return TestClient(agent.app)


def alert(action, symbol="PTT", price=30.0, rsi=5.0, time="1", **extra):
    return {"secret": "s3cret", "action": action, "symbol": symbol, "price": price,
            "rsi": rsi, "reason": "test", "time": time, **extra}


def test_buy_then_sell_records_pnl(client):
    r = client.post("/webhook/tradingview", json=alert("BUY", symbol="SET:PTT"))
    assert r.json()["decision"] == "executed"
    pos = client.get("/positions").json()
    assert pos[0]["symbol"] == "PTT"
    assert pos[0]["qty"] == 3300  # 100,000 THB / (30 * 1.0015) rounded down to 100 shares

    r = client.post("/webhook/tradingview", json=alert("SELL", price=33.0, time="2"))
    assert r.json()["decision"] == "executed"
    assert client.get("/positions").json() == []

    pnl = client.get("/pnl").json()
    assert pnl["closed_trades"] == 1
    assert pnl["win_rate"] == 100.0
    expected = 3300 * 3 - 3300 * 30 * 0.0015 - 3300 * 33 * 0.0015
    assert pnl["net_pnl_thb"] == pytest.approx(expected, abs=0.01)


def test_wrong_secret_is_rejected_and_logged(client):
    r = client.post("/webhook/tradingview", json={**alert("BUY"), "secret": "nope"})
    assert r.status_code == 401
    sig = client.get("/signals").json()[0]
    assert sig["decision"] == "rejected"
    assert client.get("/positions").json() == []


def test_bad_payload_is_rejected_and_logged(client):
    r = client.post("/webhook/tradingview", content="not json")
    assert r.status_code == 400
    assert client.get("/signals").json()[0]["decision"] == "rejected"


def test_duplicate_alert_is_ignored(client):
    client.post("/webhook/tradingview", json=alert("BUY", id="abc"))
    r = client.post("/webhook/tradingview", json=alert("BUY", id="abc"))
    assert r.json()["decision"] == "duplicate"


def test_skip_buy_when_already_holding(client):
    client.post("/webhook/tradingview", json=alert("BUY", time="1"))
    r = client.post("/webhook/tradingview", json=alert("BUY", time="2"))
    assert r.json()["decision"] == "skipped"
    assert "already holding" in r.json()["reason"]


def test_skip_buy_when_five_positions(client):
    for i, s in enumerate(["A", "B", "C", "D", "E"]):
        assert client.post("/webhook/tradingview", json=alert("BUY", symbol=s, time=str(i))).json()["decision"] == "executed"
    r = client.post("/webhook/tradingview", json=alert("BUY", symbol="F"))
    assert r.json()["decision"] == "skipped"
    assert "max 5" in r.json()["reason"]


def test_skip_buy_when_rsi_too_high(client):
    r = client.post("/webhook/tradingview", json=alert("BUY", rsi=80))
    assert r.json()["decision"] == "skipped"


def test_skip_sell_when_not_holding(client):
    r = client.post("/webhook/tradingview", json=alert("SELL"))
    assert r.json()["decision"] == "skipped"


def test_signals_log_every_decision(client):
    client.post("/webhook/tradingview", json=alert("BUY", time="1"))
    client.post("/webhook/tradingview", json=alert("BUY", time="2"))
    decisions = [s["decision"] for s in client.get("/signals").json()]
    assert decisions == ["skipped", "executed"]
