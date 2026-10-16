# ระบบเทรดหุ้นอัตโนมัติ: TradingView → Agent → โบรกไทย

TradingView คิด signal (RSI(2) Pullback) แล้วส่ง alert ผ่าน webhook มาที่ agent ของเรา
agent เช็คความเสี่ยงก่อนส่งคำสั่งไปโบรกเกอร์ **ตอนนี้เป็น paper trade (จำลอง) ยังไม่ส่งคำสั่งจริง**

```
strategy.pine ──alert JSON──▶ agent.py ──ผ่าน──▶ PaperBroker (ต่อไป: Settrade Open API)
                                  └──ไม่ผ่าน──▶ ข้าม + บันทึกเหตุผล
```

ทุก signal ถูกบันทึกใน SQLite ทั้งที่ส่งคำสั่งและที่ข้าม ดูย้อนหลังได้ที่ `GET /signals`

## ไฟล์

| ไฟล์ | ทำอะไร | รันที่ไหน |
|---|---|---|
| `strategy.pine` | กลยุทธ์ RSI(2) Pullback ส่ง alert เป็น JSON ตอน BUY/SELL | Pine Editor ใน TradingView |
| `agent.py` | FastAPI รับ webhook, เช็ค secret, กัน alert ซ้ำ, จำกัดจำนวนหุ้นที่ถือ, บันทึก SQLite, ส่งคำสั่ง (paper) | เครื่องเรา/เซิร์ฟเวอร์ |
| `backtest.py` | ทดสอบกลยุทธ์เดียวกันกับราคาย้อนหลัง (Yahoo หรือ CSV จาก TradingView) | เครื่องเรา |
| `tests/` | เทสต์ของ agent และ backtest (`pytest`) | เครื่องเรา |

## กลยุทธ์ RSI(2) Pullback

| ส่วน | เงื่อนไข | ค่าเริ่มต้น |
|---|---|---|
| เข้าซื้อ | Close > EMA200 และ RSI(2) ต่ำกว่าเกณฑ์ | RSI(2) < 10 |
| ขาย (เป้า) | Close > EMA5 | — |
| ขาย (หมดเวลา) | ถือครบจำนวนแท่ง | 10 แท่ง |
| Stop loss | ราคาเข้า − ATR(14) × ตัวคูณ | 3 × ATR |
| ค่าธรรมเนียม | ต่อข้าง (ซื้อ/ขาย) | 0.15% |

ส่งคำสั่งตอนปิดแท่ง และ fill ที่ราคาเปิดแท่งถัดไป ทั้งใน Pine และ backtest.py
Indicator ใน backtest.py คำนวณแบบเดียวกับ `ta.ema` / `ta.rsi` / `ta.atr` ของ Pine

## Agent

ตั้งค่าผ่าน environment variable:

| ตัวแปร | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `TV_SECRET` | `change-me` | ต้องตรงกับช่อง "Webhook secret" ใน Pine |
| `MAX_POSITIONS` | `5` | ถือได้สูงสุดกี่ตัวพร้อมกัน |
| `MAX_ENTRY_RSI` | `75` | ข้ามสัญญาณซื้อถ้า RSI ที่ส่งมาสูงกว่านี้ |
| `TRADE_SIZE_THB` | `100000` | เงินต่อการซื้อ 1 ครั้ง (ปัดลงเป็น lot ละ 100 หุ้น) |
| `LOT_SIZE` | `100` | board lot ของ SET |
| `COMMISSION` | `0.0015` | ค่าธรรมเนียมต่อข้าง |
| `DB_PATH` | `agent.db` | ไฟล์ SQLite |

กฎที่ agent ใช้:
- secret ผิด → ตอบ 401 และบันทึกเป็น `rejected`
- alert ซ้ำ (id เดิม) → `duplicate` ไม่ทำซ้ำ
- BUY: ข้ามถ้าถือหุ้นตัวนั้นอยู่แล้ว, ถือครบ 5 ตัว, RSI > 75 หรือเงินไม่พอ 1 lot
- SELL: ข้ามถ้าไม่ได้ถือหุ้นตัวนั้น

Endpoint:
- `POST /webhook/tradingview` รับ signal
- `GET /positions` หุ้นที่ถืออยู่
- `GET /signals?limit=100` ประวัติ signal และเหตุผลที่ทำ/ข้าม
- `GET /pnl` กำไรที่ปิดแล้ว, win rate, expectancy, profit factor

## Backtest

```bash
python backtest.py PTT.BK AOT.BK CPALL.BK --years 10
python backtest.py --csv "ไฟล์ export จาก TradingView.csv"
python backtest.py PTT.BK --optimize
```

- ได้สถิติต่อหุ้นและไฟล์ `trades_<ชื่อ>.csv` รายการทุกไม้
- `--optimize` ลองหลายค่า (RSI เข้า, จำนวนแท่ง, ตัวคูณ ATR) บนข้อมูล 70% แรก แล้วทดสอบค่าที่ดีที่สุดกับ 30% หลังที่ไม่เคยเห็น เพื่อกัน overfit
- ปรับค่าเองได้: `--rsi-entry 5 --max-bars 7 --atr-mult 2.5 --commission 0.157`
- ข้อมูล Yahoo เป็นราคาปรับปันผลแล้ว (adjusted) ส่วน TradingView ปกติไม่ปรับ ผลจึงต่างกันเล็กน้อยได้

### อ่านผลยังไง (win rate เกิน 50% ไม่ได้แปลว่ากำไร)

| ตัวเลข | ความหมาย | ควรเป็น |
|---|---|---|
| `win_rate` | % ไม้ที่กำไร | ดูประกอบ ไม่ใช่เป้าหมายหลัก |
| `expectancy` | กำไรเฉลี่ยต่อไม้ (%) หลังหักค่าธรรมเนียม | มากกว่า 0 |
| `profit_factor` | กำไรรวม ÷ ขาดทุนรวม | มากกว่า 1.2 |
| `max_dd` | พอร์ตตกลงจากจุดสูงสุดมากที่สุด | รับได้ทั้งเงินและใจ |
| `buy_hold` | ผลถ้าซื้อแล้วถือเฉยๆ | กลยุทธ์ควรชนะ หรือเสี่ยงน้อยกว่าชัดเจน |

ตัวอย่างจากการทดสอบกับราคาสุ่ม (random walk): win rate 60% แต่ expectancy −0.75% ต่อไม้ คือขาดทุน

## ขั้นตอนเริ่มต้น

1. `pip install -r requirements.txt`
2. รัน `python backtest.py` กับหุ้นที่สนใจ 3–5 ตัว ดู expectancy, profit factor, buy_hold
3. วาง `strategy.pine` ใน TradingView (กราฟ Daily) แล้วเทียบผลใน Strategy Tester
4. ตั้ง secret ให้ตรงกัน: ใส่ในช่อง "Webhook secret" ของ Pine และ `export TV_SECRET=...`
5. รัน agent: `uvicorn agent:app --port 8000` แล้วเปิดให้เข้าถึงจากภายนอกด้วย `ngrok http 8000`
6. สร้าง Alert ใน TradingView:
   - Condition: เลือก strategy นี้ → "Order fills and alert() function calls"
   - Message: `{{strategy.order.alert_message}}`
   - Webhook URL: `https://<ngrok-url>/webhook/tradingview`
   - ต้องสร้าง alert แยกต่อหุ้น 1 ตัว (หรือใช้ watchlist alert ถ้าแพ็กเกจรองรับ)
7. ดูผลที่ `/signals`, `/positions`, `/pnl`
8. Paper trade อย่างน้อย 1–2 เดือน
9. สมัคร Settrade Open API ทดสอบ Sandbox แล้วค่อยต่อเงินจริงด้วยจำนวนน้อย

## ต่อโบรกเกอร์จริง (Settrade Open API)

เขียนคลาสใหม่ที่มีเมธอด `place_order(symbol, side, qty, price) -> Fill` แบบเดียวกับ `PaperBroker`
ให้เรียก SDK `settrade-v2` (ดูชื่อเมธอดจากเอกสารทางการของ Settrade) แล้วเปลี่ยน `BROKER = PaperBroker()` ใน `agent.py`
ทดสอบใน Sandbox ก่อนเสมอ ค่าธรรมเนียมและเงื่อนไขต่างกันแต่ละโบรก ให้ถามโบรกโดยตรง

> ผลในอดีตไม่รับประกันอนาคต ควรเห็นผลดีในหลายหุ้นและในช่วง out-of-sample ก่อนใช้เงินจริง

## เทสต์

```bash
python -m pytest -q
```
