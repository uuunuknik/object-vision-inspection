"""Backtest the RSI(2) Pullback strategy with the same rules as strategy.pine.

Signals are taken at the bar close and filled at the next bar's open.
Indicators follow Pine's ta.ema / ta.rsi / ta.atr exactly (SMA-seeded).

  python backtest.py PTT.BK AOT.BK CPALL.BK --years 10
  python backtest.py --csv "export from TradingView.csv"
  python backtest.py PTT.BK --optimize
"""

import argparse
import itertools
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Params:
    rsi_len: int = 2
    rsi_entry: float = 10
    trend_len: int = 200
    exit_len: int = 5
    max_bars: int = 10
    atr_len: int = 14
    atr_mult: float = 3.0
    commission: float = 0.0015  # per side


# ---------- Indicators (Pine-compatible) ----------

def _seeded(values, length, alpha):
    """Pine-style moving average: SMA of the first `length` values, then exponential."""
    x = np.asarray(values, dtype=float)
    out = np.full(len(x), np.nan)
    start = np.argmax(~np.isnan(x)) if (~np.isnan(x)).any() else len(x)
    if len(x) - start < length:
        return out
    out[start + length - 1] = x[start:start + length].mean()
    for i in range(start + length, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def ema(x, length):
    return _seeded(x, length, 2 / (length + 1))


def rma(x, length):
    return _seeded(x, length, 1 / length)


def rsi(close, length):
    change = np.diff(np.asarray(close, dtype=float), prepend=np.nan)
    up = rma(np.where(np.isnan(change), np.nan, np.maximum(change, 0)), length)
    down = rma(np.where(np.isnan(change), np.nan, -np.minimum(change, 0)), length)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + up / down)
    out = np.where(down == 0, 100.0, out)
    out = np.where((up == 0) & (down != 0), 0.0, out)
    return np.where(np.isnan(up) | np.isnan(down), np.nan, out)


def atr(high, low, close, length):
    prev_close = np.roll(close, 1)
    tr = np.maximum(high - low, np.maximum(abs(high - prev_close), abs(low - prev_close)))
    tr[0] = high[0] - low[0]
    return rma(tr, length)


def add_indicators(df, p: Params):
    df = df.copy()
    c = df["close"].to_numpy(float)
    df["rsi"] = rsi(c, p.rsi_len)
    df["ema_trend"] = ema(c, p.trend_len)
    df["ema_exit"] = ema(c, p.exit_len)
    df["atr"] = atr(df["high"].to_numpy(float), df["low"].to_numpy(float), c, p.atr_len)
    return df


# ---------- Simulation ----------

def simulate(df, p: Params, start=0, end=None):
    """Run the strategy on bars [start, end). Indicators must already be on df.

    Returns (trades DataFrame, equity Series). Equity starts at 1.0 and puts
    100% into each trade, like the Pine strategy.
    """
    end = len(df) if end is None else end
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    r, et, ex, a = (df[k].to_numpy(float) for k in ("rsi", "ema_trend", "ema_exit", "atr"))
    dates = df.index
    fee = p.commission

    trades, equity = [], []
    cash = 1.0
    shares = 0.0
    pending_buy = None  # (signal close, atr at signal)
    pending_sell = None  # reason
    pos = None  # dict while in a trade

    def close_trade(i, price, reason):
        nonlocal cash, shares, pos
        cash = shares * price * (1 - fee)
        ret = price * (1 - fee) / (pos["entry"] * (1 + fee)) - 1
        trades.append({
            "entry_date": dates[pos["bar"]], "entry_price": pos["entry"],
            "exit_date": dates[i], "exit_price": price,
            "bars": i - pos["bar"], "return_pct": ret * 100, "exit_reason": reason,
        })
        shares, pos = 0.0, None

    for i in range(start, end):
        # 1) Orders from the previous close fill at this open.
        if pending_sell and pos:
            close_trade(i, o[i], pending_sell)
        pending_sell = None
        if pending_buy and not pos:
            sig_close, sig_atr = pending_buy
            shares = cash / (o[i] * (1 + fee))
            cash = 0.0
            pos = {"bar": i, "entry": o[i], "atr": sig_atr,
                   "stop": sig_close - sig_atr * p.atr_mult}  # estimate until the fill is known
        pending_buy = None

        # 2) Stop loss inside the bar (gap below the stop fills at the open).
        if pos and l[i] <= pos["stop"]:
            close_trade(i, min(o[i], pos["stop"]), "stop_loss")

        # 3) Decisions at the close.
        if pos:
            pos["stop"] = pos["entry"] - pos["atr"] * p.atr_mult
            if c[i] > ex[i]:
                pending_sell = "target"
            elif i - pos["bar"] >= p.max_bars:
                pending_sell = "time_exit"
        elif not (np.isnan(et[i]) or np.isnan(r[i]) or np.isnan(a[i])):
            if c[i] > et[i] and r[i] < p.rsi_entry:
                pending_buy = (c[i], a[i])

        equity.append(cash + shares * c[i])

    if pos:  # close anything still open at the last close
        close_trade(end - 1, c[end - 1], "end_of_data")
        equity[-1] = cash

    columns = ["entry_date", "entry_price", "exit_date", "exit_price", "bars", "return_pct", "exit_reason"]
    return pd.DataFrame(trades, columns=columns), pd.Series(equity, index=dates[start:end])


def max_drawdown(equity):
    if len(equity) == 0:
        return 0.0
    peak = equity.cummax()
    return float(((equity / peak) - 1).min() * 100)


def stats(df, trades, equity, p: Params, start=0, end=None):
    end = len(df) if end is None else end
    n = len(trades)
    rets = trades["return_pct"] if n else pd.Series(dtype=float)
    gains = rets[rets > 0].sum()
    losses = -rets[rets <= 0].sum()
    first_open = df["open"].iloc[start]
    closes = df["close"].iloc[start:end]
    bh_equity = closes / (first_open * (1 + p.commission))
    years = max((df.index[end - 1] - df.index[start]).days / 365.25, 1e-9)
    total = (equity.iloc[-1] - 1) * 100 if len(equity) else 0.0
    return {
        "from": df.index[start].date(), "to": df.index[end - 1].date(),
        "trades": n,
        "win_rate": round((rets > 0).mean() * 100, 2) if n else None,
        "expectancy": round(rets.mean(), 3) if n else None,
        "profit_factor": round(gains / losses, 3) if losses > 0 else (math.inf if n else None),
        "total_return": round(total, 2),
        "cagr": round(((1 + total / 100) ** (1 / years) - 1) * 100, 2),
        "max_dd": round(max_drawdown(equity), 2),
        "buy_hold": round((bh_equity.iloc[-1] * (1 - p.commission) - 1) * 100, 2),
        "buy_hold_max_dd": round(max_drawdown(bh_equity), 2),
        "avg_bars": round(trades["bars"].mean(), 1) if n else None,
    }


# ---------- Data ----------

def load_yahoo(symbol, years):
    import yfinance as yf

    df = yf.Ticker(symbol).history(period=f"{years}y", auto_adjust=True)
    if df.empty:
        raise SystemExit(f"no data from Yahoo for {symbol}")
    df = df.rename(columns=str.lower)[["open", "high", "low", "close"]]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df.dropna()


def load_csv(path):
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next((c for c in ("time", "date", "datetime") if c in df.columns), None)
    if tcol is None:
        raise SystemExit(f"{path}: need a time/date column")
    t = df[tcol]
    if pd.api.types.is_numeric_dtype(t):
        unit = "ms" if t.abs().max() > 1e11 else "s"  # TradingView exports unix seconds
        df.index = pd.to_datetime(t, unit=unit)
    else:
        df.index = pd.to_datetime(t)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    missing = {"open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise SystemExit(f"{path}: missing columns {sorted(missing)}")
    return df[["open", "high", "low", "close"]].astype(float).dropna().sort_index()


# ---------- Optimize ----------

GRID = {
    "rsi_entry": [5, 10, 15, 20],
    "max_bars": [5, 10, 15],
    "atr_mult": [2.0, 3.0, 4.0],
}
MIN_TRAIN_TRADES = 10


def optimize(df_raw, base: Params, train_frac=0.7):
    """Pick parameters on the first 70% of bars, then test them on the last 30%."""
    df = add_indicators(df_raw, base)  # grid does not change indicator lengths
    warm = base.trend_len
    split = warm + int((len(df) - warm) * train_frac)
    if split - warm < 100 or len(df) - split < 50:
        raise SystemExit("not enough data to optimize (need more years)")

    rows = []
    for values in itertools.product(*GRID.values()):
        p = replace(base, **dict(zip(GRID, values)))
        tr, eq = simulate(df, p, warm, split)
        s = stats(df, tr, eq, p, warm, split)
        rows.append({**dict(zip(GRID, values)), **s})
    table = pd.DataFrame(rows)
    ok = table[table["trades"] >= MIN_TRAIN_TRADES]
    if ok.empty:
        raise SystemExit(f"no setting made {MIN_TRAIN_TRADES}+ trades in the training period")
    ok = ok.sort_values(["expectancy", "profit_factor"], ascending=False)

    best_row = ok.iloc[0]
    best = replace(base, **{k: type(getattr(base, k))(best_row[k]) for k in GRID})
    test = {}
    for label, p in (("default", base), ("best", best)):
        tr, eq = simulate(df, p, split, len(df))
        test[label] = stats(df, tr, eq, p, split, len(df))
    return ok, best, test


# ---------- CLI ----------

def run_one(name, df_raw, p: Params, out_dir: Path):
    df = add_indicators(df_raw, p)
    start = p.trend_len
    if len(df) <= start + 1:
        print(f"{name}: only {len(df)} bars, need more than {start}")
        return None
    trades, equity = simulate(df, p, start)
    s = stats(df, trades, equity, p, start)
    out = out_dir / f"trades_{name}.csv"
    trades.to_csv(out, index=False)
    return {"symbol": name, **s, "file": out.name}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbols", nargs="*", help="Yahoo symbols, e.g. PTT.BK")
    ap.add_argument("--csv", action="append", default=[], help="CSV exported from TradingView")
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--optimize", action="store_true")
    ap.add_argument("--rsi-entry", type=float, default=Params.rsi_entry)
    ap.add_argument("--max-bars", type=int, default=Params.max_bars)
    ap.add_argument("--atr-mult", type=float, default=Params.atr_mult)
    ap.add_argument("--commission", type=float, default=Params.commission * 100, help="percent per side")
    ap.add_argument("--out", default=".", help="folder for trades_<name>.csv")
    args = ap.parse_args(argv)

    if not args.symbols and not args.csv:
        ap.error("give at least one symbol or --csv file")

    p = Params(rsi_entry=args.rsi_entry, max_bars=args.max_bars,
               atr_mult=args.atr_mult, commission=args.commission / 100)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    def safe(name):
        return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")

    data = [(safe(s), lambda s=s: load_yahoo(s, args.years)) for s in args.symbols]
    data += [(safe(Path(f).stem), lambda f=f: load_csv(f)) for f in args.csv]

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    results = []
    for name, loader in data:
        df_raw = loader()
        if args.optimize:
            table, best, test = optimize(df_raw, p)
            print(f"\n=== {name}: top 5 settings on the first 70% ===")
            print(table.head(5)[list(GRID) + ["trades", "win_rate", "expectancy", "profit_factor", "max_dd"]]
                  .to_string(index=False))
            print(f"\nBest: rsi_entry={best.rsi_entry} max_bars={best.max_bars} atr_mult={best.atr_mult}")
            print("Out-of-sample (last 30%, never seen while choosing):")
            print(pd.DataFrame(test).T.to_string())
        else:
            r = run_one(name, df_raw, p, out_dir)
            if r:
                results.append(r)

    if results:
        print(pd.DataFrame(results).to_string(index=False))
        print("\nUnits: win_rate/expectancy/returns/max_dd in %. expectancy is the average net return per trade.")


if __name__ == "__main__":
    main()
