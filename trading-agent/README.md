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
| `notify.py` | ส่งข้อความ LINE ตอนซื้อ/ขาย, เตือน win rate และสรุปรายวัน | อยู่ใน agent |
| `Dockerfile`, `docker-compose.home.yml` | รันตลอดเวลาโดยไม่ต้องมี VPS (ผ่าน ngrok) | คอมที่เปิดทิ้งไว้ |
| `docker-compose.yml`, `Caddyfile` | ขึ้น VPS พร้อม HTTPS ด้วยโดเมนของเราเอง | VPS |
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
| `TRADE_SIZE_THB` | `10000` | เงินต่อการซื้อ 1 ครั้ง (ปัดลงเป็น lot ละ 100 หุ้น) หุ้นราคาเกิน ~99.85 บาทจะซื้อไม่ได้แม้ 1 lot และถูกข้าม |
| `LOT_SIZE` | `100` | board lot ของ SET |
| `COMMISSION` | `0.0015` | ค่าธรรมเนียมต่อข้าง |
| `DB_PATH` | `agent.db` | ไฟล์ SQLite |
| `VIEW_KEY` | (ว่าง) | ถ้าตั้งไว้ ต้องใส่ `?key=...` ถึงจะดู `/positions` `/signals` `/pnl` ได้ (ควรตั้งเมื่อขึ้นเซิร์ฟเวอร์) |
| `LINE_CHANNEL_ACCESS_TOKEN` | (ว่าง) | token ของ LINE bot (ใช้ตัวเดียวกับโปรเจกต์ LINE bot เดิมได้) |
| `LINE_USER_ID` | (ว่าง) | LINE user ID ของคนที่จะรับข้อความ |
| `WINRATE_ALERT` | `50` | แจ้งใน LINE เมื่อ win rate ขึ้นไปเกินค่านี้ |
| `WINRATE_MIN_TRADES` | `5` | เริ่มแจ้งหลังปิดไม้ครบเท่านี้ (น้อยกว่านี้ยังสรุปอะไรไม่ได้) |
| `DAILY_SUMMARY_AT` | `17:30` | เวลาส่งสรุปรายวัน (เวลาไทย, จันทร์–ศุกร์) ใส่ว่างเพื่อปิด |

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
- `GET /health` เช็คว่า agent ทำงานอยู่และต่อ LINE แล้วหรือยัง

## แจ้งเตือนทาง LINE

agent ส่งข้อความเข้า LINE ให้:
- **ทุกครั้งที่ซื้อ/ขาย** — ตอนขายจะแนบสรุป win rate, expectancy, profit factor, กำไรรวม
- **สรุปรายวัน** จันทร์–ศุกร์ 17:30 (หลังตลาดปิด) พร้อมหุ้นที่ถืออยู่
- **🎉 แจ้งเมื่อ win rate เกิน 50%** — ส่งครั้งเดียวตอนที่ไม้ล่าสุดทำให้ win rate ขึ้นไปเกิน 50% (ต้องปิดครบ 5 ไม้ก่อน)
  ถ้าภายหลังตกลงมาไม่เกิน 50% แล้วขึ้นไปใหม่ จะแจ้งอีกครั้ง ระหว่างที่ยังเกินอยู่ทุกสรุปจะมีบรรทัด ✅
- **⚠️ เตือน** ถ้า expectancy ไม่เป็นบวก เพราะ win rate เกิน 50% แต่ยังขาดทุนได้ (ดูหัวข้อ "อ่านผลยังไง")

ตัวอย่างข้อความตอนขายไม้ที่ทำให้ win rate เกิน 50%:
```
🎉 win rate เกิน 50% แล้ว (54.5% จาก 11 ไม้)

🔴 ขาย PTT [target] (paper)
sold 300 @ 34.5 via paper, net 343.51 THB

ปิดแล้ว 11 ไม้ | win rate 54.5%
expectancy +0.41% ต่อไม้ | profit factor 1.35
กำไรสุทธิรวม +452.10 บาท
✅ win rate เกิน 50%
```

ตั้งค่า (ใช้ LINE Messaging API เพราะ LINE Notify ปิดบริการไปแล้ว):
1. ใช้ channel ของ LINE bot เดิม หรือสร้างใหม่ที่ [LINE Developers](https://developers.line.biz/console/)
2. แท็บ **Messaging API** → ออก **Channel access token (long-lived)** → ใส่ `LINE_CHANNEL_ACCESS_TOKEN`
3. แท็บ **Basic settings** → คัดลอก **Your user ID** (ขึ้นต้นด้วย `U`) → ใส่ `LINE_USER_ID`
4. แอด bot เป็นเพื่อนใน LINE (สแกน QR ในแท็บ Messaging API)
5. ทดสอบ: `python agent.py test-line` ควรได้ข้อความ "✅ ทดสอบจาก trading agent"

push message มีโควต้าฟรีต่อเดือนจำกัด ดูได้ใน LINE Official Account Manager
ระบบนี้ส่งราวๆ 20–60 ข้อความต่อเดือน (สรุปรายวัน ~22 + ตามจำนวนไม้) ถ้า LINE ส่งไม่ได้ agent ยังเทรดต่อตามปกติ

## รันให้ทำงานตลอดเวลา

TradingView ส่ง webhook ได้เฉพาะ URL https พอร์ต 443 (หรือ 80) และต้องมีเครื่องที่รัน agent อยู่ตลอดเวลาตลาดเปิด
เลือกได้ 2 แบบ ทั้งสองแบบใช้ Docker และไฟล์ `.env` เดียวกัน

| | แบบ A: ไม่มี VPS (ngrok) | แบบ B: VPS + โดเมน |
|---|---|---|
| เครื่อง | คอม/โน้ตบุ๊ก/มินิพีซีที่เปิดทิ้งไว้ | VPS (DigitalOcean, Vultr, Lightsail, Oracle Cloud ฯลฯ) |
| ค่าใช้จ่าย | ฟรี (ค่าไฟ) | ฟรีถึงราวๆ 150–250 บาท/เดือน แล้วแต่เจ้า |
| URL | โดเมน ngrok ฟรี 1 ชื่อ (ไม่เปลี่ยน) | โดเมนของเรา (DuckDNS ฟรีได้) |
| ข้อเสีย | เครื่องดับ/เน็ตหลุด/sleep = พลาด alert | ต้องดูแลเซิร์ฟเวอร์เอง |

เริ่มจากแบบ A ระหว่าง paper trade ได้ แล้วย้ายไปแบบ B ก่อนใช้เงินจริง (ย้ายแค่โฟลเดอร์ `data/`)
เงื่อนไขฟรีของ ngrok / Oracle Cloud เปลี่ยนได้ ตรวจสอบหน้าเว็บของผู้ให้บริการอีกครั้ง

### แบบ A: ไม่มี VPS (ngrok)

1. ลง [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/Mac) หรือ Docker (Linux)
2. สมัคร [ngrok](https://dashboard.ngrok.com/) ฟรี → คัดลอก **Authtoken** และไปที่ **Domains** รับโดเมนฟรี 1 ชื่อ (เช่น `xxx.ngrok-free.app`)
3. ตั้งค่าและรัน:
   ```bash
   git clone <repo> && cd <repo>/trading-agent
   cp .env.example .env    # ใส่ NGROK_AUTHTOKEN, NGROK_DOMAIN, TV_SECRET, VIEW_KEY, LINE_*
   docker compose -f docker-compose.home.yml up -d --build
   docker compose -f docker-compose.home.yml exec agent python agent.py test-line
   ```
4. Webhook URL ใน TradingView: `https://<NGROK_DOMAIN>/webhook/tradingview`
5. ดูผล: `https://<NGROK_DOMAIN>/pnl?key=<VIEW_KEY>`
6. ตั้งเครื่องไม่ให้ sleep และให้ Docker เปิดเองตอนบูต (Docker Desktop → Settings → "Start Docker Desktop when you sign in")

### แบบ B: VPS + โดเมน (Caddy ทำ HTTPS ให้)

สิ่งที่ต้องมี: VPS ที่ลง Docker แล้ว และโดเมนที่ชี้มาที่ IP ของ VPS
(บริการฟรีที่ "หลับ" เมื่อไม่มีคนเรียก เช่น Render free ไม่เหมาะ เพราะ alert อาจหาย สรุปรายวันไม่ส่ง และไฟล์ SQLite หาย)

```bash
git clone <repo> && cd <repo>/trading-agent
cp .env.example .env        # แก้ DOMAIN, TV_SECRET, VIEW_KEY, LINE_* ให้ครบ
docker compose up -d --build
docker compose exec agent python agent.py test-line   # ทดสอบ LINE
curl https://<DOMAIN>/health
```

- Webhook URL ใน TradingView: `https://<DOMAIN>/webhook/tradingview`
- ดูผล: `https://<DOMAIN>/pnl?key=<VIEW_KEY>`

### ทั้งสองแบบ

- ข้อมูลอยู่ใน `./data/agent.db` บนเซิร์ฟเวอร์ (restart/อัปเดตแล้วไม่หาย) ควร backup เป็นระยะ
- ดู log: `docker compose logs -f agent` (แบบ A เติม `-f docker-compose.home.yml` หลัง `compose`)
- อัปเดตโค้ด: `git pull && docker compose up -d --build`
- รันแค่ 1 worker (ตั้งไว้แล้วใน Dockerfile) เพราะการเช็คความเสี่ยงและสรุปรายวันต้องอยู่ใน process เดียว

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
5. รัน agent ตามหัวข้อ "รันให้ทำงานตลอดเวลา" (ไม่มี VPS ใช้แบบ A)
6. สร้าง Alert ใน TradingView:
   - Condition: เลือก strategy นี้ → "Order fills and alert() function calls"
   - Message: `{{strategy.order.alert_message}}`
   - Webhook URL: `https://<ngrok-url หรือ DOMAIN>/webhook/tradingview`
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
