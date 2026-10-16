import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notify  # noqa: E402


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TV_SECRET", "s3cret")
    monkeypatch.setenv("VIEW_KEY", "viewme")
    import agent
    importlib.reload(agent)
    sent = []
    monkeypatch.setattr(agent.notify, "push_async", sent.append)
    return TestClient(agent.app), sent


def alert(action, symbol="PTT", price=30.0, time="1"):
    return {"secret": "s3cret", "action": action, "symbol": symbol, "price": price,
            "rsi": 5, "reason": "target", "time": time}


def test_line_message_on_buy_and_sell(setup):
    client, sent = setup
    client.post("/webhook/tradingview", json=alert("BUY"))
    client.post("/webhook/tradingview", json=alert("SELL", price=33, time="2"))
    assert len(sent) == 2
    assert sent[0].startswith("🟢 ซื้อ PTT") and "1/5" in sent[0]
    assert sent[1].startswith("🔴 ขาย PTT") and "win rate 100.0%" in sent[1]


def test_no_line_message_when_skipped(setup):
    client, sent = setup
    client.post("/webhook/tradingview", json=alert("SELL"))
    assert sent == []


def test_view_key_required(setup):
    client, _ = setup
    assert client.get("/pnl").status_code == 401
    assert client.get("/positions?key=wrong").status_code == 401
    assert client.get("/pnl?key=viewme").status_code == 200
    assert client.get("/health").status_code == 200


def stats(n, win_rate, expectancy):
    return {"closed_trades": n, "win_rate": win_rate, "expectancy_pct": expectancy,
            "profit_factor": 1.1, "net_pnl_thb": 100.0}


def test_warns_when_win_rate_below_50():
    assert "⚠️ win rate ต่ำกว่า 50%" in notify.fmt_stats(stats(10, 40.0, 0.3))
    assert "⚠️" not in notify.fmt_stats(stats(10, 60.0, 0.3))
    assert "⚠️" not in notify.fmt_stats(stats(2, 0.0, -1.0))  # too few trades to judge


def test_warns_when_expectancy_not_positive():
    assert "expectancy ไม่เป็นบวก" in notify.fmt_stats(stats(10, 65.0, -0.2))


def test_daily_message_lists_positions():
    msg = notify.daily_message(stats(3, 66.67, 0.5), [{"symbol": "PTT"}, {"symbol": "AOT"}])
    assert "ถืออยู่ 2 ตัว: PTT, AOT" in msg


def test_push_without_config_does_not_raise(monkeypatch):
    monkeypatch.setattr(notify, "LINE_TOKEN", "")
    assert notify.push("hello") is False
