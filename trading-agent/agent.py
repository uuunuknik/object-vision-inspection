"""Trading agent: receives TradingView alerts, checks risk, sends orders to a broker.

Every signal is stored in SQLite with the decision (executed / skipped / rejected)
and the reason, so you can always look back at why the agent did what it did.

Run:  uvicorn agent:app --port 8000
Test the LINE connection:  python agent.py test-line
"""

import hashlib
import hmac
import json
import math
import os
import sqlite3
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

import notify

# Settings. Override with environment variables; TV_SECRET must match the Pine input.
TV_SECRET = os.getenv("TV_SECRET", "change-me")
DB_PATH = os.getenv("DB_PATH", "agent.db")
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "5"))
MAX_ENTRY_RSI = float(os.getenv("MAX_ENTRY_RSI", "75"))
TRADE_SIZE_THB = float(os.getenv("TRADE_SIZE_THB", "10000"))  # money per new position
LOT_SIZE = int(os.getenv("LOT_SIZE", "100"))  # SET board lot
COMMISSION = float(os.getenv("COMMISSION", "0.0015"))  # per side, 0.15%
VIEW_KEY = os.getenv("VIEW_KEY", "")  # if set, GET endpoints need ?key=VIEW_KEY


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- Broker ----------

@dataclass
class Fill:
    price: float
    qty: int


class PaperBroker:
    """Simulated broker: fills the whole order at the alert price.

    To trade for real, write a class with the same place_order() signature that
    calls the Settrade Open API SDK (settrade-v2), and set BROKER below.
    Test it in the Settrade Sandbox first.
    """

    name = "paper"

    def place_order(self, symbol: str, side: str, qty: int, price: float) -> Fill:
        return Fill(price=price, qty=qty)


BROKER = PaperBroker()


# ---------- Storage ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT,
    received_at TEXT NOT NULL,
    symbol TEXT,
    action TEXT,
    price REAL,
    rsi REAL,
    reason TEXT,
    decision TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_signal_id ON signals(signal_id);
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    qty INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    entry_fee REAL NOT NULL,
    opened_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    qty INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    fees REAL NOT NULL,
    net_pnl REAL NOT NULL,
    return_pct REAL NOT NULL,
    exit_reason TEXT
);
"""

_lock = threading.Lock()  # one signal at a time, so two alerts can't both pass the checks


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript(SCHEMA)


# ---------- Signal ----------

class Signal(BaseModel):
    secret: str
    action: str
    symbol: str
    price: float = Field(gt=0)
    rsi: Optional[float] = None
    reason: Optional[str] = None
    time: Optional[str] = None
    id: Optional[str] = None

    @field_validator("action")
    @classmethod
    def check_action(cls, v):
        v = v.strip().upper()
        if v not in ("BUY", "SELL"):
            raise ValueError("action must be BUY or SELL")
        return v

    @field_validator("symbol")
    @classmethod
    def clean_symbol(cls, v):
        # "SET:PTT" -> "PTT"
        return v.strip().upper().split(":")[-1]

    def signal_id(self) -> str:
        if self.id:
            return self.id
        key = f"{self.symbol}|{self.action}|{self.time}|{self.price}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


def record(conn, decision, decision_reason, sig: Optional[Signal] = None, raw=None):
    conn.execute(
        "INSERT INTO signals (signal_id, received_at, symbol, action, price, rsi, reason,"
        " decision, decision_reason, raw) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            sig.signal_id() if sig else None,
            now_iso(),
            sig.symbol if sig else None,
            sig.action if sig else None,
            sig.price if sig else None,
            sig.rsi if sig else None,
            sig.reason if sig else None,
            decision,
            decision_reason,
            raw,
        ),
    )


def check_buy(conn, sig: Signal) -> Optional[str]:
    """Return why the buy must be skipped, or None if it may go ahead."""
    if conn.execute("SELECT 1 FROM positions WHERE symbol=?", (sig.symbol,)).fetchone():
        return "already holding this stock"
    open_count = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    if open_count >= MAX_POSITIONS:
        return f"already holding {open_count} stocks (max {MAX_POSITIONS})"
    if sig.rsi is not None and sig.rsi > MAX_ENTRY_RSI:
        return f"RSI {sig.rsi} above {MAX_ENTRY_RSI}"
    if buy_qty(sig.price) == 0:
        return f"price {sig.price} too high for one lot of {LOT_SIZE} with {TRADE_SIZE_THB} THB"
    return None


def buy_qty(price: float) -> int:
    lots = math.floor(TRADE_SIZE_THB / (price * (1 + COMMISSION) * LOT_SIZE))
    return max(lots, 0) * LOT_SIZE


def execute_buy(conn, sig: Signal) -> str:
    fill = BROKER.place_order(sig.symbol, "BUY", buy_qty(sig.price), sig.price)
    fee = fill.qty * fill.price * COMMISSION
    conn.execute(
        "INSERT INTO positions (symbol, qty, entry_price, entry_fee, opened_at) VALUES (?,?,?,?,?)",
        (sig.symbol, fill.qty, fill.price, fee, now_iso()),
    )
    return f"bought {fill.qty} @ {fill.price} via {BROKER.name}"


def execute_sell(conn, sig: Signal, pos) -> str:
    fill = BROKER.place_order(sig.symbol, "SELL", pos["qty"], sig.price)
    exit_fee = fill.qty * fill.price * COMMISSION
    fees = pos["entry_fee"] + exit_fee
    cost = pos["qty"] * pos["entry_price"]
    net = (fill.price - pos["entry_price"]) * fill.qty - fees
    conn.execute(
        "INSERT INTO trades (symbol, qty, entry_price, exit_price, opened_at, closed_at, fees,"
        " net_pnl, return_pct, exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (sig.symbol, fill.qty, pos["entry_price"], fill.price, pos["opened_at"], now_iso(),
         round(fees, 2), round(net, 2), round(net / cost * 100, 4), sig.reason),
    )
    conn.execute("DELETE FROM positions WHERE symbol=?", (sig.symbol,))
    return f"sold {fill.qty} @ {fill.price} via {BROKER.name}, net {net:.2f} THB"


def handle(conn, sig: Signal):
    if conn.execute(
        "SELECT 1 FROM signals WHERE signal_id=? AND decision IN ('executed','skipped')",
        (sig.signal_id(),),
    ).fetchone():
        return "duplicate", "same alert already handled"

    if sig.action == "BUY":
        why_not = check_buy(conn, sig)
        if why_not:
            return "skipped", why_not
        return "executed", execute_buy(conn, sig)

    pos = conn.execute("SELECT * FROM positions WHERE symbol=?", (sig.symbol,)).fetchone()
    if not pos:
        return "skipped", "not holding this stock"
    return "executed", execute_sell(conn, sig, pos)


# ---------- API ----------

def compute_stats(conn):
    trades = [dict(r) for r in conn.execute("SELECT * FROM trades ORDER BY id").fetchall()]
    return stats_of(trades), trades


def stats_of(trades):
    wins = [t for t in trades if t["net_pnl"] > 0]
    gross_win = sum(t["net_pnl"] for t in wins)
    gross_loss = -sum(t["net_pnl"] for t in trades if t["net_pnl"] <= 0)
    n = len(trades)
    return {
        "closed_trades": n,
        "win_rate": round(len(wins) / n * 100, 2) if n else None,
        "net_pnl_thb": round(sum(t["net_pnl"] for t in trades), 2),
        "expectancy_pct": round(sum(t["return_pct"] for t in trades) / n, 4) if n else None,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
    }


def current_stats():
    with db() as conn:
        return compute_stats(conn)[0]


def current_positions():
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM positions ORDER BY opened_at").fetchall()]


def trade_message(sig: Signal, reason: str) -> str:
    if sig.action == "BUY":
        return f"🟢 ซื้อ {sig.symbol} (paper)\n{reason}\nถืออยู่ {len(current_positions())}/{MAX_POSITIONS} ตัว"
    with db() as conn:
        after, trades = compute_stats(conn)
    headline = notify.winrate_alert(stats_of(trades[:-1]), after)
    return f"{headline}🔴 ขาย {sig.symbol} [{sig.reason or '-'}] (paper)\n{reason}\n\n{notify.fmt_stats(after)}"


def check_key(key: Optional[str]):
    if VIEW_KEY and not hmac.compare_digest((key or "").encode(), VIEW_KEY.encode()):
        raise HTTPException(401, "missing or wrong key")


@asynccontextmanager
async def lifespan(app):
    stop = notify.start_daily_summary(current_stats, current_positions)
    yield
    if stop:
        stop.set()


app = FastAPI(title="TradingView trading agent", lifespan=lifespan)
init_db()


@app.post("/webhook/tradingview")
async def webhook(request: Request):
    raw = (await request.body()).decode("utf-8", errors="replace")
    try:
        sig = Signal.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as e:
        with _lock, db() as conn:
            record(conn, "rejected", f"bad payload: {str(e)[:200]}", raw=raw[:1000])
        raise HTTPException(400, "bad payload")

    if not hmac.compare_digest(sig.secret.encode(), TV_SECRET.encode()):
        with _lock, db() as conn:
            record(conn, "rejected", "wrong secret", sig)
        raise HTTPException(401, "wrong secret")

    with _lock:
        with db() as conn:
            decision, reason = handle(conn, sig)
            record(conn, decision, reason, sig)
        # build the message under the lock so "before/after" win rate belongs to this trade
        message = trade_message(sig, reason) if decision == "executed" else None
    if message:
        notify.push_async(message)
    return {"decision": decision, "reason": reason, "symbol": sig.symbol, "action": sig.action}


@app.get("/health")
def health():
    return {"ok": True, "line": notify.enabled()}


@app.get("/positions")
def positions(key: Optional[str] = None):
    check_key(key)
    return current_positions()


@app.get("/signals")
def signals(limit: int = 100, key: Optional[str] = None):
    check_key(key)
    with db() as conn:
        rows = conn.execute(
            "SELECT id, signal_id, received_at, symbol, action, price, rsi, reason,"
            " decision, decision_reason FROM signals ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/pnl")
def pnl(key: Optional[str] = None):
    check_key(key)
    with db() as conn:
        stats, trades = compute_stats(conn)
    return JSONResponse({**stats, "trades": trades})


if __name__ == "__main__":
    if sys.argv[1:] == ["test-line"]:
        ok = notify.push("✅ ทดสอบจาก trading agent\n" + notify.fmt_stats(current_stats()))
        print("sent" if ok else "not sent (check LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID)")
    else:
        print("usage: python agent.py test-line")
