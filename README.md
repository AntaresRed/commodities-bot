# MCX signal bot

A **signals-only** trading assistant for MCX commodities: crude oil, natural gas, gold and silver, in their smallest contracts. It sends you trade signals on Telegram, and **you** decide whether to take each one and place every order yourself in Zerodha. **The bot never places, changes or cancels an order.** The full design is in [PLAN.md](PLAN.md).

> Not investment advice. The strategies have to prove themselves on MCX data in the backtest (Phase 2) before any signal is worth trading.

**Where the project is now:** Phase 0 (setup) and the part of Phase 1 (data) that needs no keys are done. Live signals come in Phase 3. Right now, double-clicking `start_bot.bat`:

1. checks your settings,
2. downloads free international price history,
3. imports any MCX files you have downloaded,
4. writes a data-quality report,
5. logs in to Kite and sends Telegram a "bot started" message, once you have added those keys.

---

## Setup, step by step

Plan on about an hour, spread over a few days while Zerodha activates the commodity segment.

### Step 1: Zerodha account with the MCX (commodity) segment

1. If you don't have a Zerodha account, open one at zerodha.com.
2. Log in to **console.zerodha.com**, open **Account → Segments** (sometimes called "Segment activation") and turn on **Commodity (MCX)**.
3. Upload income proof when asked: usually a 6-month bank statement, latest ITR or salary slip. Then e-sign.
4. Activation usually takes 1–3 working days. You'll get an email when it's done.

(Menu names on Zerodha's site change from time to time. If you can't find it, search "activate commodity segment" on support.zerodha.com.)

### Step 2: Decide on the Kite Connect data plan (₹500/month)

The bot needs Zerodha's **Kite Connect** API for live prices and history. It costs **₹500 per month**, which you pay to Zerodha on developers.kite.trade. You can finish every other step first and come back to this one. The bot only **reads** data, positions and funds. It does not need order permissions, and you don't need a static IP.

### Step 3: Create your Kite Connect app

1. Go to **https://developers.kite.trade**, sign up and log in.
2. Click **Create new app** and fill it in:
   - **Type:** **Connect** (the paid one). The free "Personal" type has **no market data** and won't work.
   - **App name:** anything, for example `My MCX signals`
   - **Zerodha Client ID:** your Zerodha user ID, for example `AB1234`
   - **Redirect URL:** exactly `http://127.0.0.1:5678/kite/callback`
   - **Postback URL:** leave empty
   - **Description:** anything
3. Create the app and add credits for the subscription.
4. Open the app page. You'll see the **API key** and **API secret**. Leave the page open for Step 5, and don't paste them anywhere else.

**Why that redirect URL:** each morning the bot opens the Kite login page in your browser. After you log in (password + 2FA), Kite sends your browser back to `127.0.0.1`, which is your own PC. A small helper in the bot picks up the login there. Nothing is exposed to the internet.

### Step 4: Create your Telegram bot

1. Install Telegram on your phone and/or PC.
2. In Telegram, search for **@BotFather** (it has a blue tick) and press **Start**.
3. Send `/newbot`. Give it a display name, then a username that ends in `bot`, for example `my_mcx_signals_bot`.
4. BotFather replies with a **token** that looks like `1234567890:AAH...`. Keep that message for Step 5.
5. Recommended: send BotFather `/setjoingroups`, pick your bot, and choose **Disable**, so nobody can add your bot to a group.
6. Open your new bot (BotFather gives you a `t.me/...` link), press **Start** and send it `hi`.

You'll find your **chat ID** after Step 5. The bot only ever answers that ID and ignores everyone else.

### Step 5: Put your keys in `.env`

1. In this folder, copy `.env.example` and rename the copy to `.env`. In Notepad, use *Save as → Save as type: All files* so it doesn't become `.env.txt`.
2. Fill it in, with no quotes and no spaces:
   ```
   KITE_API_KEY=your api key
   KITE_API_SECRET=your api secret
   TELEGRAM_BOT_TOKEN=the token from BotFather
   TELEGRAM_CHAT_ID=
   ```
3. Double-click **`tools\telegram_find_chat_id.bat`**. It shows your chat ID (a number). Put it after `TELEGRAM_CHAT_ID=` and save.
4. Double-click **`tools\telegram_test.bat`**. You should get a **TEST** signal with **Taken ✅ / Skipped ❌** buttons. Tap one, and the window confirms it was received.

`.env` stays on your PC. It's excluded from git, the bot never prints or logs its values, and you should never send it to anyone (including me).

### Step 6: MCX price history (nothing to do by hand)

The long daily history comes from **Kite** once your Kite Connect app works: Kite gives daily "continuous" candles for expired futures, and the bot will download them itself (Phase 1b).

Downloading years of history from mcxindia.com by hand is not practical. Checked on 25 Nov 2026:
- *Market Data → Historical Data* only has yearly volume and turnover totals, with no prices.
- *Market Data → Bhav Copy → Date Wise* has daily prices back to 2003, but **one day per file** (thousands of files for 10 years).
- *Bhav Copy → Commodity Wise* takes a date range, but **one contract per download**, and only contracts expiring from Apr 2025 onwards are listed.

The importer still reads Bhav Copy files (CSV or Excel) if you ever want to add a few: save them into `data
aw\mcx\` and double-click `tools\import_mcx_files.bat`. Useful later for spot-checking Kite's data, not needed now.

### Step 7: Start the bot

Double-click **`start_bot.bat`**. The first run takes a few minutes to set up Python packages. After that, each morning:

- your browser opens the Kite login page; log in with your Zerodha ID, password and 2FA,
- the window shows the settings check, data update, Kite status and Telegram status,
- the bot then waits for `/status` and button taps on Telegram until you close the window.

---

## Settings you can change

Open **`config\settings.yaml`** in Notepad. The top section holds the few settings you'd normally touch:

| Setting | Meaning |
|---|---|
| `capital_inr` | Your trading capital, in rupees |
| `risk_per_trade_pct` | Most you can lose on one trade if its stop is hit, as % of capital. You'll choose 0.5 or 1.0 after the backtest report. |
| `intraday_windows.enabled` | Which intraday windows send signals (stays empty until Phase 3) |
| `limits` | Daily/weekly/monthly loss limits and the −15% kill switch |

If you make a mistake, `start_bot.bat` tells you which line is wrong. Changing `mode` from `signals_only` is refused.

`config\instruments.yaml` lists the contracts. Lot sizes and tick sizes marked `verified: false` will be checked against Zerodha's instrument file once Kite is connected.

---

## Getting real MCX history from Kite

Once your Kite Connect app and `.env` keys are set up:

1. Double-click **`tools\kite_download.bat`**. If you haven't logged in today, your browser opens the Kite login first. The download is **read-only market data** and takes a few minutes. It saves:
   - years of **daily** history for each contract (Kite's "continuous" series of expired futures),
   - about a year of **1-minute** bars for the contracts still trading (Kite has no minute data for expired contracts),
   - today's contract list, plus a check of Zerodha's real lot and tick sizes against `config\instruments.yaml`.

   A summary is saved to `reports\kite_download.txt`.
2. Double-click **`tools\run_mcx_backtest.bat`**. It runs the S1/S3 backtest on **real MCX prices** and opens `reports\mcx_backtest.md`.

## What happens next

- **Phase 1b (rest):** the **activity scan** on the minute data checks which intraday windows have real volume spikes. Then you get a data report and a go/no-go on whether intraday history must be bought for S2 (nothing is bought without your OK).
- **Phase 2:** backtests of S1–S3 at your real capital with full costs, then the backtest report with pass/fail gates.
- **Phase 3:** live signals, the risk manager, the morning message, and model-vs-your P&L tracking.

---

## For developers

```
.venv\Scripts\python -m pytest                          # all tests (network tests skipped)
set RUN_NETWORK_TESTS=1 && .venv\Scripts\python -m pytest -m network
.venv\Scripts\python -m bot.live.runner --no-listen     # what start_bot.bat runs
.venv\Scripts\python -m bot.data.events                 # regenerate config\events_YYYY.csv
.venv\Scripts\python -m bot.backtest.proxy              # proxy backtest of S1/S3 -> reports\proxy_backtest.md
.venv\Scripts\python -m bot.data.kite_history           # download MCX history from Kite (needs login)
.venv\Scripts\python -m bot.backtest.proxy --source kite # S1/S3 on real MCX prices -> reports\mcx_backtest.md
```

| Path | What |
|---|---|
| `config/` | `settings.yaml`, `instruments.yaml`, `events_2026.csv`, `events_2027.csv` |
| `src/bot/config.py` | pydantic validation of both YAML files |
| `src/bot/broker/` | read-only interface (`broker_base.py`), Zerodha adapter, daily Kite login (`kite_auth.py`) |
| `src/bot/notify/telegram.py` | signals with Taken/Skipped buttons, `/status`, a mock transport for tests |
| `src/bot/signals/` | signal format + journal (SQLite + JSON-lines backup) |
| `src/bot/data/` | MCX importer, Yahoo benchmarks, continuous contracts, quality checks, event calendar; `kite_history.py` downloads Kite history (live recorder and activity scan still to come) |
| `src/bot/backtest/costs.py` | cost model (PLAN §5.1), every rate set in `settings.yaml` |
| `data/`, `logs/`, `reports/`, `.env` | git-ignored |

**Event calendar (`config/events_*.csv`).** All times are converted to IST with US daylight-saving time handled. The `verified` column means:
- `yes`: the date is listed on the official EIA / Fed / BLS page.
- `rule`: a weekly EIA release generated from the standard schedule.
- `check`: a weekly EIA release in a US-holiday week where the official page lists no change. Confirm these.
- `tentative`: 2027 FOMC dates, which the Fed confirms one meeting ahead.
