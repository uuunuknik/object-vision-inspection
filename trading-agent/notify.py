"""LINE push messages: a message on every buy/sell and a daily summary.

Uses the LINE Messaging API push endpoint. Needs:
  LINE_CHANNEL_ACCESS_TOKEN  channel access token (long-lived) of your LINE bot
  LINE_USER_ID               who receives the messages (LINE Developers -> Basic settings -> Your user ID)
If either is missing, messages are only printed to the log.
"""

import json
import os
import threading
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.getenv("LINE_USER_ID", "")
WINRATE_ALERT = float(os.getenv("WINRATE_ALERT", "50"))  # alert when win rate goes above this
WINRATE_MIN_TRADES = int(os.getenv("WINRATE_MIN_TRADES", "5"))  # ...with at least this many closed trades
DAILY_SUMMARY_AT = os.getenv("DAILY_SUMMARY_AT", "17:30")  # Bangkok time, after SET closes; "" = off
BANGKOK = ZoneInfo("Asia/Bangkok")


def enabled():
    return bool(LINE_TOKEN and LINE_USER_ID)


def push(text: str) -> bool:
    """Send one text message. Never raises: a LINE outage must not stop trading."""
    if not enabled():
        print("[line disabled]", text)
        return False
    body = json.dumps({"to": LINE_USER_ID, "messages": [{"type": "text", "text": text[:5000]}]}).encode()
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print("LINE push failed:", e)
        return False


def push_async(text: str):
    threading.Thread(target=push, args=(text,), daemon=True).start()


def fmt_stats(s: dict) -> str:
    if not s["closed_trades"]:
        return "ยังไม่มีไม้ที่ปิดแล้ว"
    pf = s["profit_factor"]
    lines = [
        f"ปิดแล้ว {s['closed_trades']} ไม้ | win rate {s['win_rate']:.1f}%",
        f"expectancy {s['expectancy_pct']:+.2f}% ต่อไม้ | profit factor {pf if pf is not None else '-'}",
        f"กำไรสุทธิรวม {s['net_pnl_thb']:+,.2f} บาท",
    ]
    if winrate_above(s):
        lines.append(f"✅ win rate เกิน {WINRATE_ALERT:g}%")
    if s["expectancy_pct"] <= 0 and s["closed_trades"] >= WINRATE_MIN_TRADES:
        lines.append("⚠️ expectancy ไม่เป็นบวก = ยังไม่ได้กำไรจริง")
    return "\n".join(lines)


def winrate_above(s: dict) -> bool:
    return s["closed_trades"] >= WINRATE_MIN_TRADES and s["win_rate"] > WINRATE_ALERT


def winrate_alert(before: dict, after: dict) -> str:
    """Headline for the trade that pushed win rate above WINRATE_ALERT, else ""."""
    if winrate_above(after) and not winrate_above(before):
        return f"🎉 win rate เกิน {WINRATE_ALERT:g}% แล้ว ({after['win_rate']:.1f}% จาก {after['closed_trades']} ไม้)\n\n"
    return ""


def daily_message(stats: dict, positions: list) -> str:
    today = datetime.now(BANGKOK).strftime("%d/%m/%Y")
    held = ", ".join(p["symbol"] for p in positions) or "-"
    return f"📊 สรุปประจำวัน {today} (paper)\n{fmt_stats(stats)}\nถืออยู่ {len(positions)} ตัว: {held}"


def start_daily_summary(get_stats, get_positions):
    """Background thread that pushes the summary Mon-Fri at DAILY_SUMMARY_AT (Bangkok)."""
    if not DAILY_SUMMARY_AT or not enabled():
        return None
    hh, mm = (int(x) for x in DAILY_SUMMARY_AT.split(":"))
    stop = threading.Event()

    def loop():
        while True:
            now = datetime.now(BANGKOK)
            nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            while nxt.weekday() >= 5:  # SET is closed Sat/Sun
                nxt += timedelta(days=1)
            if stop.wait((nxt - now).total_seconds()):
                return
            try:
                push(daily_message(get_stats(), get_positions()))
            except Exception as e:
                print("daily summary failed:", e)

    threading.Thread(target=loop, daemon=True).start()
    return stop
