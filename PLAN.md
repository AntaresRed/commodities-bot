# Indian Commodities Trading Bot — Project Plan

**Version:** Draft v0.5 · **Date:** 24 Nov 2026 · **Status:** All current decisions made (see §12). Ready to start Phase 0.

---

## 0. Ground rules (read first)

- **This is a software plan, not investment advice.** I'm not a SEBI-registered investment adviser. The strategies below are well-documented systematic methods from published research and long industry use. They are not tips, and none of them guarantees a profit. For context, SEBI's 2024 study found that about 93% of individual equity F&O traders lost money over FY22–FY24. The bot has to show an edge on data before it touches real money.
- **Who does what.** I design, code, backtest and run the signal engine. **v1 is signals-only:** the bot never sends an order to the broker. **You** decide whether to take each signal, place every order yourself, and keep your own API keys and secrets.
- **Survive first, profit second.** Every design choice favours small, diversified, hard-capped risk over big wins. A strategy that blows up once is worthless, however good its average.
- **"Tested" means tested on MCX data.** Each strategy has published evidence behind it, but we re-test every one on Indian contracts with Indian costs. Nothing goes live because a book or paper said it works.

---

## 1. Scope

| Item | v1 decision | Reason |
|---|---|---|
| Exchange | **MCX** (v1). Nifty added later (§1.3). | Almost all Indian commodity liquidity is here. NSE commodity contracts and NCDEX agri contracts are too thin. |
| Instruments | **Futures only** | Linear risk you can control. Options come later, and only with defined risk (§3.5). |
| Execution | **Signals only** | The bot sends alerts with the exact contract, lots, entry, stop-loss and target, and you place the orders. Automatic orders can be added later (§7). |
| Contract size | **Smallest contracts** (petal / micro / 100 g / mini) | With capital under ₹2 lakh, only these keep risk per trade near the target (§1.2). |
| Holding periods | Intraday + positional (days to weeks) | Two return streams that don't move together (see §3). |
| Signal frequency | Low: a few signals a day at most | Few enough to act on by hand |

### 1.1 Commodity list (v1)

Your four commodities, in the contract sizes that fit an account under ₹2 lakh. Lot sizes and margins change, so the bot reads them from the broker's instrument file every day.

| Commodity | Contract(s) for v1 | Bigger size later | Settlement | Role in the bot |
|---|---|---|---|---|
| Crude oil | **CRUDEOILM** (10 bbl) | CRUDEOIL (100 bbl) | Cash-settled (expiry ~19th) | Main intraday market (S2); trend (S1) when the stop fits |
| Natural gas | **NATGASMINI** (250 mmBtu) | NATURALGAS (1,250 mmBtu) | Cash-settled (expiry end of month) | Intraday (S2) on calmer days. Very volatile, so it gets the smallest risk share. |
| Gold | **GOLDPETAL** (1 g), **GOLDGUINEA** (8 g) or **GOLDTEN** (10 g): the most liquid one that fits the risk limit is used | GOLDM (100 g) | **Physical delivery** | Trend (S1), intraday (S2), ratio leg (S3) |
| Silver | **Silver 100** (100 g, launched Jun 2026) or **SILVERMIC** (1 kg): the most liquid one that fits is used | SILVERM (5 kg) | **Physical delivery** | Trend (S1), intraday (S2), ratio leg (S3) |

**Suggested for later, not v1**

| Market | Why later |
|---|---|
| Base metals (Zinc Mini, Aluminium Mini, then Copper) | Good diversifiers because they follow industrial demand rather than the dollar or oil supply, but even their smallest lots are too big for an account under ₹2 lakh |
| Nifty (equity index) | See §1.3 |

**Not recommended**

| Market | Why |
|---|---|
| Agri contracts (cotton, mentha oil, CPO on MCX; NCDEX) | Thin trading, wide spreads, frequent limit-move days |
| Sensex | Moves almost exactly like Nifty, so trading both doubles the same bet. Sensex futures are also thinly traded. |
| Bigger contracts of your four commodities | A normal stop on one lot would exceed the per-trade risk limit at your capital |

> ⚠️ **Delivery risk.** Gold, silver and base metals settle by physical delivery. The bot sends a **roll or exit alert well before the tender/delivery period** starts, and repeats it every day until the position is closed. Most brokers also force-close these positions before then.

**Liquidity filter.** Final instruments are chosen by data, not preference. An instrument stays in only if its average daily volume and bid-ask spread pass minimum limits. The smallest contracts, such as Gold Petal and Silver 100, can have wide spreads, so they need checking.

**Trading hours.** Non-agri MCX contracts trade from 9:00 AM to **11:55 PM IST** in winter (current schedule since 2 Nov 2026) and to **11:30 PM** while the US is on daylight saving time. Brokers square off intraday positions earlier, usually about 25–55 minutes before the close. The bot will be DST-aware and use your broker's cut-off.

### 1.2 What "under ₹2 lakh" means for the bot

- **Risk per trade is the binding limit, not margin.** At 0.5% risk, the most the bot lets you lose on one trade is about ₹1,000 on ₹2 lakh. A trade is allowed only if **one lot's** stop-loss loss fits inside that. Otherwise the bot skips the signal instead of over-risking. Margin is rarely the problem: a September 2026 snapshot put Crude Oil Mini at about ₹27k, Natural Gas Mini at about ₹10k and Gold Petal at about ₹1.4k per lot.
- **What that probably means per strategy** (Phase 2 produces the exact numbers from current prices):
  - **S1 positional trend:** uses wide stops (2.5 × daily ATR). Probably only the small gold contracts, and maybe Silver 100, fit. Crude Oil Mini and Natural Gas Mini trend trades will usually be skipped.
  - **S2 intraday breakout:** uses much tighter stops, so Crude Oil Mini should fit. Natural Gas Mini will be borderline on volatile days.
  - **S3 gold/silver ratio:** Gold Petal against Silver 100 makes value-matched pairs possible, but S3 goes ahead only if both contracts are liquid enough.
- **Your decision: risk per trade.** 0.5% means more signals get skipped and diversification is thinner. 1% lets more trades through but roughly doubles the drawdowns. The backtest will show both so you can choose (§11).
- **Fixed costs weigh more.** Zerodha's ₹500/month data plan is ₹6,000 a year, about 3% of ₹2 lakh, which the strategies must earn back before you make anything. The backtest counts it as a cost. Everything else stays free: the bot runs on your own PC.
- **Brokerage structure matters.** On tiny contracts, a flat ₹20 per order costs much more than "0.03% or ₹20, whichever is lower" (§6.1).

### 1.3 Nifty (added later)

- **Why not now.** One Nifty futures lot is 65 units (since Jan 2026), roughly ₹15–18 lakh of exposure. Its margin would use most of a ₹2 lakh account. A normal intraday stop of 40–80 points risks ₹2,600–5,200, which is 1.3–2.6% of capital on a single trade.
- **When it fits.** At 0.5% risk per trade, Nifty futures need roughly ₹5–10 lakh of capital; at 1%, roughly ₹3–5 lakh. Phase 2 will compute the exact threshold.
- **How it plugs in.** The strategy engine, risk manager and Telegram signals don't care what they trade. Adding Nifty means a new instrument file, NSE trading hours (9:15 AM–3:30 PM), the Tuesday weekly expiry, and the equity cost model (STT instead of CTT). Defined-risk Nifty option buying gets researched at the same time.
- **Sensex stays out**, for the reason in §1.1.

---

## 2. Strategy design principles

1. **Several uncorrelated strategies beat one "best" strategy.** Trend following loses money in choppy markets. Mean-reversion and relative-value strategies tend to make money there. Running both smooths the equity curve.
2. **Few parameters, robust values.** We choose parameters from a stable plateau where nearby values also work, never from the single best backtest result.
3. **Size by volatility.** Every position is sized so that a stop-out costs a fixed fraction of equity, whatever the commodity's volatility.
4. **Same code everywhere.** The exact strategy code that runs in the backtest also generates the live signals, so the backtest and live behaviour can't drift apart.

---

## 3. The strategy portfolio

| # | Strategy | Style | Holding period | Risk budget | Expected profile |
|---|---|---|---|---|---|
| **S1** | Multi-commodity trend following | Positional | Days to months | **~50%** | Low win rate (30–45%), large winners, positive skew |
| **S2** | Intraday opening-range / volatility breakout | Intraday | Minutes to hours | **~30%** | Medium win rate, about 1.5–2R winners, flat every night |
| **S3** | Gold/silver ratio mean reversion | Market-neutral, positional | Days to weeks | **~20%** | Higher win rate, smaller wins, little exposure to market direction |
| S4 | Research only: calendar spreads, short-term mean reversion, defined-risk options | — | — | 0% until it passes the gates | — |

The risk-budget split is a starting point. The final split comes from the backtest correlation matrix: strategies that diversify better get more risk. With a small account, the split will also depend on which strategies have contracts that fit (§1.2).

### 3.1 S1 — Multi-commodity trend following (core strategy)

**Evidence**
- Moskowitz, Ooi & Pedersen (2012), *Time Series Momentum*, Journal of Financial Economics: significant trend profits across 58 futures markets, commodities included.
- Hurst, Ooi & Pedersen (2017), *A Century of Evidence on Trend-Following Investing*: positive results in every decade since about 1900.
- The Turtle Trading system (Donchian breakouts with ATR-based sizing) is the classic rule set, and managed-futures (CTA) funds have run variants of it for over 40 years.

**Rules (baseline; only narrow ranges get optimised)**

| Component | Rule |
|---|---|
| Data | Daily bars on a back-adjusted continuous contract |
| Entry (long) | Close above the 55-day high, **and** 50-day EMA above 200-day EMA |
| Entry (short) | Close below the 55-day low, **and** 50-day EMA below 200-day EMA |
| Alternative (tested side by side) | 20/100 EMA crossover |
| Initial stop | 2.5 × ATR(20) from entry |
| Exit | 3 × ATR trailing stop (chandelier), or the opposite 20-day breakout, whichever comes first |
| Sizing | lots = (equity × risk%) ÷ (stop distance × contract multiplier), rounded **down**. If even 1 lot exceeds the risk budget, **no signal is sent.** |
| Signal timing | Computed after the daily close and sent in the **8:30 AM morning message**. You act in the first 30–45 minutes after the 9:00 AM open. The backtest uses this next-morning execution as its main assumption. |
| Trailing stop | The bot recalculates the trailing stop after each close and includes **"move your GTT stop to ₹X"** in the morning message (§4). |
| Rollover | Signals use the continuous series. The bot alerts you to roll to the next contract N days before the tender period (bullion) or before expiry (energy). |

**What to expect.** Long flat or slightly losing stretches in sideways markets, and most of the profit from a few big trends. You'll need the discipline to keep taking signals during a drawdown.

### 3.2 S2 — Intraday opening-range / volatility breakout

**Evidence**
- Toby Crabel (1990), *Day Trading with Short Term Price Patterns and Opening Range Breakout*, the original research on opening-range breakouts in futures.
- Larry Williams' volatility-breakout method.
- Gao, Han, Li & Zhou (2018), *Market Intraday Momentum*, Journal of Financial Economics: early-session moves predict late-session moves.

**MCX adaptation: candidate windows.** A breakout strategy needs a moment when new information or a wave of traders arrives after a quiet spell. On MCX those moments come from three places: the MCX open, London, and the US. There are six candidate windows. Times are IST in winter; in summer everything is 1 hour earlier, except W1 (fixed) and W6 (follows the broker's square-off).

| # | Window (IST, winter) | What happens then | Markets |
|---|---|---|---|
| W1 | 9:00 AM | MCX opens and prices in overnight global moves | Gold, silver (energy is thin in the morning) |
| W2 | 1:30 PM | London opens; London is the world's largest gold trading centre | Gold, silver, crude |
| W3 | 6:50–7:00 PM | US economic data (8:30 AM ET) and the COMEX metals session open | All four |
| W4 | 7:30–8:00 PM | NYMEX energy session and US stock market open: peak US liquidity | Crude, natural gas |
| W5 | 9:00 PM Wed / Thu | EIA weekly crude (Wed) and natural gas (Thu) inventory reports. Range is the first 15 minutes after the release. | Crude (Wed), natural gas (Thu) |
| W6 | ~10:15–11:00 PM | Late-session momentum: the day's early direction tends to carry into the last half-hour (Gao et al. 2018). Exits before square-off. | All four |

**Why not test every hour?** If we try 50 windows, a few will look great purely by luck and then fail with real money. This is the multiple-testing trap. So each window has a reason first, and then it gets checked:
1. **Activity scan first.** Before any profit testing, the bot measures volume and volatility in every 15-minute slot for each commodity. A window stays only if MCX activity really spikes there. If the scan finds a spike we didn't expect, it becomes a new candidate, because the reason (activity) is decided before anyone looks at profits.
2. **Higher bar for more tries.** Results are adjusted for how many windows and settings were tested (deflated Sharpe ratio, §5.3).
3. **More windows doesn't mean more trades.** The 3-trades-a-day cap stays. Windows compete, and only those that pass the gates are kept.

**Window selection rule (decided 24 Nov 2026).** The goal is the most profit per unit of risk, with as few false signals as possible, at moderate risk. Ranking by raw profit alone would just pick whichever window got luckiest in the past. So windows are ranked by **out-of-sample profit after costs per unit of drawdown**.
1. **Merge overlapping windows.** W3 and W4 become one **US-session window**: metals start at W3 (~6:50 PM), energy at W4 (~7:30 PM). That leaves 5 candidates.
2. **Activity scan.** Any candidate without a real volume and volatility spike is dropped.
3. **Stand-alone test.** Each remaining window must pass the intraday gates (§5.4) on its own, in walk-forward tests, and on more than one commodity where that applies.
4. **Keep at most 2–3 windows.** A window is added only if it improves the combined result (more profit per unit of drawdown), not just because it passes alone. Fewer, stronger windows mean fewer alerts and less noise.
5. **Noise filters on every signal:** the range-width filter, the daily-trend filter, a 5-minute close beyond the range plus a buffer, and a volume check. Each filter stays only if it improves out-of-sample results.
6. **Quarterly review.** A window whose live results fall below the expected range over at least 30 trades is paused.

**My expectation before seeing any data** (the backtest can overrule it): the US-session window and the Wednesday EIA crude window are the likeliest to survive, because that's when MCX energy and bullion trade most heavily. The 9:00 AM window is likely the weakest for energy, since crude and gas trade thinly in the Indian morning.

Because you place the orders by hand, the backtest adds extra slippage for manual entry.

**Rules (baseline)**

| Component | Rule |
|---|---|
| Instruments | CRUDEOILM first; NATGASMINI, small gold and Silver 100 contracts if they fit the risk limit and pass liquidity |
| Range | High and low of the first 15–30 minutes after the window opens (length tested) |
| Range filter | Trade only if range width is 0.3–1.2 × daily ATR(14). Skip dead days and already-exploded days. |
| Entry | A 5-minute close beyond the range plus a buffer of 0.1 × ATR |
| Volume check | The breakout bar's volume must beat that 15-minute slot's normal volume (tested on and off) |
| Signal expiry | If price has moved more than X ticks past the entry level, or N minutes have passed, the bot sends **"signal cancelled — don't chase"** |
| Trend filter (tested on and off) | Trade only in the direction of S1's daily trend |
| Stop | Opposite side of the range, capped at 1 × range width |
| Target | 2R, or trail after reaching 1R (trail alerts sent by the bot) |
| Time exit | **"Close now"** alert 15 minutes before the broker's intraday square-off |
| Frequency | At most 1 trade per instrument per day (the first valid window wins; 2 per day is also tested), and at most 3 intraday trades per day in total |
| Event filter | Outside window W5, no new entries from 15 minutes before to 15 minutes after the US releases that move crude, natural gas and bullion (table below). The backtest runs with and without this filter. |

US event times that move MCX energy and bullion:

| US release | US time | IST in winter (Nov–Mar) | IST in summer |
|---|---|---|---|
| EIA weekly petroleum status report | Wed 10:30 AM ET | 9:00 PM | 8:00 PM |
| EIA natural gas storage report | Thu 10:30 AM ET | 9:00 PM | 8:00 PM |
| US CPI and nonfarm payrolls | 8:30 AM ET | 7:00 PM | 6:00 PM |
| FOMC decision | 2:00 PM ET | after the MCX close | after the MCX close |

The FOMC decision lands after the MCX close, so it is an overnight gap risk for S1 and S3 positions.

### 3.3 S3 — Gold/silver ratio mean reversion

**Evidence**
- Gatev, Goetzmann & Rouwenhorst (2006), *Pairs Trading*, Review of Financial Studies, is the standard reference for relative-value pairs trading.
- Gold and silver share the same drivers (USD, real interest rates, risk sentiment), so their price ratio often moves in ranges.

**Known risk.** The ratio can trend for months, as in the 2020 spike above 120. The rules below include a regime check and a hard stop for that reason.

**Rules (baseline)**

| Component | Rule |
|---|---|
| Spread | ln(gold price per gram) − ln(silver price per gram), using MCX prices in the same units |
| Signal | Rolling z-score over 60–120 days (window chosen by walk-forward) |
| Regime check | Trade only while a rolling cointegration test passes and the spread's half-life is under about 30 days |
| Entry | When \|z\| > 2.0: short the expensive metal and buy the cheap one, value-matched with the closest lot combination (for a small account, GOLDPETAL against Silver 100) |
| Execution note | Sent in the 8:30 AM morning message, like S1. Two legs placed by hand; the signal gives both legs and the order to place them in. |
| Exit | When z returns to ±0.5 |
| Stop | \|z\| > 3.5, or a 30-trading-day time stop |
| Sizing | Risk is measured on the spread's own volatility, not on each leg separately |

### 3.4 S4 — Research queue

These run only in research, after S1–S3 are working, and go live only if they pass the same gates as S1–S3.
- **Calendar spreads:** near-month vs next-month gold, traded against fair cost of carry. Lower risk and lower return.
- **Short-term mean reversion:** RSI(2) and Bollinger-band reversion on daily bars with a 200-day trend filter (Connors & Alvarez, 2008). Documented on equity indices, so it needs proof on commodities.
- **Defined-risk options** on MCX crude oil and gold options: iron condors and debit spreads only.

### 3.5 What the bot will deliberately not do

| Excluded | Why |
|---|---|
| Naked option selling | Unlimited tail risk. Crude oil went negative in April 2020, and natural gas regularly spikes 10%+ in a day. |
| Martingale, averaging down, grid bots | Look great until the one trend that wipes out the account |
| Scalping or high-frequency trading | Costs and manual execution kill the edge |
| Machine-learning black boxes in v1 | Very easy to overfit. ML may be added later as a filter on top of proven rules, never as the core strategy. |
| Trading on news headlines or tips | Not systematic, can't be tested |

### 3.6 What a signal looks like (Telegram)

```
🟢 S2 BUY  CRUDEOILM DEC-26  · 1 lot
Entry : buy-stop / limit ₹5,612 (valid till 19:55)
Stop  : ₹5,571   (risk ₹410 = 0.21% of equity)
Target: ₹5,694   (2R)   · Square-off alert at 22:45
Reason: US-session range 5,571–5,606 broken; trend filter ✔
ID: S2-20261124-CRM-01   [Taken ✅] [Skipped ❌]
```

(Prices above are made up for illustration.)

- Every signal has an ID. You tap **Taken** or **Skipped**, and the bot also reads your real positions from the broker API (read-only) to confirm.
- After you're in a trade, the bot sends **management alerts**: move stop, take profit, close before square-off, roll before expiry.
- The bot keeps two records: **"model P&L"** (if every signal were taken at the signal price) and **"your P&L"** (your actual fills). The gap between them measures execution slippage and missed trades.
- **Private and locked down.** Signals go to a private chat between you and your own bot. The bot answers only your Telegram user ID and ignores everyone else. Its token lives only in your `.env` file.
- **Backup if Telegram is down.** The bot also logs every signal locally and shows a desktop notification on your PC, so a Telegram outage doesn't mean a missed stop alert.

---

## 4. Risk management

This part matters more than the entry rules. Every limit is configurable, and the values below are proposed defaults. In signals-only mode the bot can't close positions itself, so each limit works by **stopping new signals and telling you exactly what to close**.

| Layer | Rule | Default |
|---|---|---|
| **Per trade** | Maximum loss if the stop is hit | 0.5% of equity (0.25% during the first live month); 1% only if you choose it (§1.2) |
| | **You place the stop-loss order at the broker immediately after your entry fills.** Every signal gives the exact stop price. The bot also watches prices and alerts you if a stop is hit or skipped. | Always |
| | **Overnight stops via GTT.** Normal orders expire at the close, so overnight positions use a Zerodha **GTT stop** on MCX (valid up to a year). A GTT is held by Zerodha, not the exchange, and fires a limit order when triggered, so on a big price gap it may not fill. The bot alerts you if price moves past your stop. The morning message lists every GTT stop to check or move. | Every trading day |
| | **Overnight and weekend gaps.** Nothing protects a position while MCX is closed. The backtest measures historical gap losses through stops, and position sizing allows for them. | Always |
| **Per day** | Daily loss limit: no new entry signals for the day, plus a "close intraday positions" alert | −2% |
| **Per week / month** | Pause signals and send you an alert | −4% / −8% |
| **Per strategy** | If the intraday strategy (S2) falls 6% from its own peak, its trade size is halved until that drawdown is back under 3% | −6% S2 drawdown |
| **Portfolio** | **Kill switch:** no further signals and a "close everything" alert, until you restart the bot after a review | −15% from peak equity |
| | Maximum margin in use, keeping a buffer for MCX margin hikes | 40–50% of capital |
| | Correlation clusters: gold and silver count as one cluster, crude and natural gas as another | Max 2 same-direction positions per cluster |
| **Expiry** | Roll or exit alerts before tender periods and expiry, repeated daily until done | Contract calendar checked every day |
| **Events** | No new entry signals around major scheduled releases | §3.2 |
| **Operational** | Read your actual positions and funds from the broker API (read-only) | Every 60 s. If they don't match what the bot expects, alert you. |
| | Margin check against your real funds before every signal | Always |
| | Stale-data detector | No tick for 30 s during market hours: no new signals, and an alert |
| | Duplicate-signal guard: one alert per setup, each with a unique ID | Always |
| | Heartbeat and Telegram commands (`/status`, `/positions`, `/pause`, `/resume`) | Always on |
| | If the bot crashes or loses data: alert you that it's offline. Your broker stop-losses keep protecting you. | Always |

---

## 5. Backtesting and validation

### 5.1 Realistic costs (all configurable)

| Cost | Approximate rate (2026, to be verified) |
|---|---|
| Brokerage | Your broker's rate: flat ₹20 per order, or "0.03% or ₹20, whichever is lower" (§6.1) |
| CTT (commodity transaction tax) | 0.01% on the sell side (non-agri futures) |
| MCX transaction charges | about 0.002% of turnover |
| SEBI fee | ₹10 per crore |
| Stamp duty | 0.002% on the buy side |
| GST | 18% on brokerage, exchange charges and SEBI fee |
| Slippage | 1–2 ticks on small contracts, **plus an extra allowance for manual entry**; more for natural gas and around events |
| Data plan | Kite Connect ₹500/month, counted as a fixed cost against the account |

### 5.2 Data hygiene

- Continuous contracts are **back-adjusted for indicators**. P&L and stops use **actual contract prices**.
- No look-ahead: signals use only completed bars, and every fill is simulated at the next tradeable price after a realistic manual delay.
- The data handles contract-spec changes (lot sizes), MCX trading-hour changes (DST), holidays, and circuit-limit days.
- Price-level breaks are handled by working with returns, for example the gold import duty cut from 15% to 6% in July 2024.

### 5.3 Validation process

1. **In-sample design** on the older data, for example 2012–2021.
2. **Walk-forward optimisation:** train on a rolling 3 years, test on the next 1 year, and repeat.
3. **Untouched holdout** (for example 2024–2026), evaluated **once** at the very end.
4. **Parameter robustness:** results must hold when every parameter moves ±20%. We look for a plateau, not a peak.
5. **Cross-market robustness:** the same parameters must work on several commodities.
6. **Monte Carlo** (bootstrap and trade reshuffling) to estimate the 95th-percentile drawdown, which drives position sizing.
7. **Cost stress test:** results must still be profitable at 2× costs.
8. **Small-account test:** the whole portfolio is simulated at your actual capital with whole lots only, so skipped trades and rounding are counted.
9. **Multiple-testing correction:** the more windows and settings tried, the higher the bar a result has to clear (deflated Sharpe ratio, Bailey & López de Prado 2014).

### 5.4 Gates a strategy must pass before signal tracking (proposed; you can change them)

| Metric (out-of-sample, after costs) | Positional | Intraday |
|---|---|---|
| Sharpe ratio | ≥ 0.7 | ≥ 1.0 |
| Profit factor | ≥ 1.3 | ≥ 1.3 |
| Max drawdown at proposed size | ≤ 20% (Monte Carlo 95th percentile ≤ 25%) | ≤ 15% |
| Number of trades | ≥ 50 | ≥ 150 |
| Profitable at 2× costs | Yes | Yes |
| Largest single trade's share of total profit | ≤ 20% | ≤ 10% |

For context, professional trend-following funds have historically delivered Sharpe ratios of roughly 0.5–1.0 with drawdowns of 15–25%. Any backtest showing "5% a month, no drawdowns" is overfit, and we reject it.

### 5.5 Path to live trading

1. **Backtest report**, sent to you with a pass/fail result for every gate and a minimum-capital-per-instrument table. You decide whether to continue.
2. **Signal tracking** for 4–8 weeks: the bot sends live signals, you don't trade them, and the bot records model P&L. We compare it with what the backtest expected, and you get used to the alert flow.
3. **Live probation** for 1–2 months: you trade the signals with the smallest contracts at 0.25% risk per trade.
4. **Gradual scale-up**, and only while your real results (including slippage and missed trades) stay within the expected range.
5. **Optional later step:** switch to semi- or fully-automatic orders once you trust the signals (§7).

---

## 6. Data sources

| What we need | Source | Cost | Notes |
|---|---|---|---|
| **Live ticks and quotes** | Zerodha Kite Connect WebSocket | ₹500/month (Kite Connect plan) | Needed for live signals |
| **Daily history of expired futures** | Kite Connect "continuous" daily candles + MCX bhavcopy | Included / free | Enough for S1 and S3 |
| **Intraday history** (1-min) | Kite Connect historical API | Included | **Only for contracts still trading.** Expired contracts give daily candles only, so this is a few months of usable minute data, not years. |
| **Our own minute recordings** | The bot saves every 1-minute bar from the live feed, from the day Kite is connected | Free | Builds our own history going forward and cross-checks any vendor data |
| **Long daily history** (10+ years) | MCX official bhavcopy and historical data (mcxindia.com) | Free | Official settlement prices for every contract; the continuous series are built from these. The MCX site may block automated downloads (§9). |
| **Extended history and cross-check** | International benchmarks: COMEX gold and silver, NYMEX crude and natural gas (GC=F, SI=F, CL=F, NG=F) × USD/INR (RBI/FBIL reference rate) | Free | 20+ years of data to stress-test trend logic. MCX prices are roughly international price × USD/INR + duties. |
| **Multi-year intraday history** | Exchange-authorised vendors such as TrueData or Global Datafeeds | Paid (one-time history purchase or a short subscription) | **Likely needed to validate S2**, because no broker API gives years of minute data for expired MCX contracts. The price is checked and brought to you before anything is bought. |
| **Contract master** (lot sizes, expiries, tick sizes, tender periods) | Broker instrument file (daily) + MCX contract specifications and circulars | Free | Downloaded automatically every morning |
| **Event calendar** | EIA release schedule, Federal Reserve FOMC calendar, BLS CPI/NFP schedule | Free | Kept as a yearly CSV converted to IST, DST-aware |
| **Holidays and trading-hour changes** | MCX holiday list and circulars | Free | |
| **Positioning data** (later, optional) | CFTC Commitments of Traders report | Free | Possible extra filter for S1 |

**You have no existing data.** S1 and S3 can be fully backtested from free and included sources. S2 is the exception: it needs a few years of minute data, and Zerodha only gives minute data for live contracts. The Phase 1 data report will confirm exactly how much minute history we can get. If it's too short to trust the S2 backtest, you'll get a vendor quote to approve or decline. Nothing is bought without your go-ahead.

### 6.1 Broker comparison: Zerodha vs Angel One (for signals-only and under ₹2 lakh)

| | Zerodha (Kite Connect) | Angel One (SmartAPI) |
|---|---|---|
| Live + historical data API | **₹500/month** per API key. The free "Personal" API has no market data. From Oct 2026 the fee is waived in months where you pay ≥ ₹2,000 brokerage, which is unlikely at this account size. | **Free**, including MCX historical candles and WebSocket streaming |
| API quality | The most mature retail API in India: official Python client, clear docs, active developer forum with Zerodha staff | Works, but thinner documentation; users report occasional WebSocket drops and errors in volatile markets |
| Static IP | Needed only for API **orders**. Data, positions and order book work from any IP (confirmed). Not an issue for signals-only. | Rules for data access not confirmed yet; to check |
| Brokerage, commodity futures | 0.03% or ₹20 per order, **whichever is lower**, which is cheap on tiny contracts | Advertises ₹20 per order; I couldn't confirm whether a lower percentage cap applies to small MCX contracts |
| Cost bottom line | ₹6,000/year data fee, but cheaper orders on tiny contracts | ₹0 data, but possibly ₹20 even on tiny orders. At about 40 orders a month the two can end up close. |
| Minute history for expired contracts | Not available: expired contracts give daily candles only | Not checked |
| GTT (long-validity) stop orders on MCX | Yes, since Feb 2026 (Kite web first, app to follow) | Not checked |

A third option is **Fyers**, which has a free data API (with about 1–2 years of minute history) and reportedly percentage-capped brokerage. It may combine both advantages; its charges need verifying.

**Decision (24 Nov 2026): Zerodha.** Its data isn't deeper than other brokers'. Every broker gets the same exchange feed, and Zerodha's minute history for expired contracts is limited. Its real advantages are the most reliable API, the cheapest brokerage on tiny contracts, and GTT stop orders on MCX. GTT means overnight stops don't have to be re-placed every morning.

---

## 7. Regulation and compliance

**Signals-only v1:** you place orders by hand like any manual trader, so the SEBI retail-algo rules below don't apply yet. Reading market data and your positions through the API doesn't need a static IP, at least on Zerodha (confirmed). Angel One is still to be checked.

**If we later switch to automatic orders** (the SEBI retail algo framework, fully in force since 1 Apr 2026):
- **Static IP.** API orders are accepted only from a static IP registered with your broker and tied to your API key. Zerodha allows up to 2 IPs, changeable once a week.
- **Exchange algo tagging.** For self-built algos used on your own account and kept **under 10 orders per second**, the broker assigns a generic algo-ID, and full exchange registration isn't needed.
- **Daily login with 2FA (OAuth).** Needed in both modes, because API sessions (including data) expire every day.

**Tax.** Profits from commodity derivatives on a recognised exchange (where CTT applies) are generally treated as **non-speculative business income** and filed in ITR-3; a tax audit may apply above certain thresholds. Confirm with your CA.

---

## 8. Architecture and tech stack

```
 Broker WebSocket ─► Market-data handler ─► Bar builder (1m / 5m / daily)
                                                  │
                                                  ▼
                              Strategy engine (S1, S2, S3)
                    the same code runs in the backtest and in live signals
                                                  │ raw signals
                                                  ▼
       Risk manager (sizing, limits, margin check, kill switch, expiry/roll) ◄─┐
                                                  │ approved signals          │
                                                  ▼                           │
              Signal layer ─► Telegram alerts ─► YOU place the order          │
                                                  │                           │
          Broker API (read-only): your positions and funds ──────────────────┘
                                                  │
     Storage (Parquet + SQLite/DuckDB) · Logs · Model vs actual P&L · Reports
```

- **Language:** Python 3.11 (3.11.9 is already installed on your PC).
- **Libraries:** pandas, numpy, pyarrow and duckdb (storage); the broker's official SDK; APScheduler (scheduling); python-telegram-bot (signals, buttons, commands); pydantic (config validation); pytest (tests); plotly (reports). Indicators (EMA, ATR, Donchian) are written in-house: they're a few lines each, which removes dependency risk.
- **Backtester:** a custom event-driven engine that runs the same strategy classes as the live signal engine, plus a fast vectorised harness for parameter sweeps.
- **Broker adapter layer:** read-only in v1. Adding order placement later, or switching brokers, means changing one adapter file.

### 8.1 Folder layout

```
Commodities Trading/
  PLAN.md
  config/            settings.yaml, instruments.yaml, events_2026.csv, events_2027.csv
  data/              raw/, processed/ (parquet)   ← not committed to git
  src/bot/
    data/            downloaders, continuous contracts, bar builder, quality checks
    strategies/      trend.py, orb.py, gold_silver_ratio.py
    risk/            sizing.py, limits.py, killswitch.py, rollover.py
    broker/          broker_base.py, <broker>.py   (read-only: data, positions, funds)
    signals/         signal.py, tracker.py (model vs actual P&L)
    backtest/        engine.py, costs.py, metrics.py, walkforward.py, montecarlo.py
    live/            runner.py, scheduler.py
    notify/          telegram.py
  reports/           HTML backtest and signal-tracking reports
  tests/
  .env               your API keys. You create this file; it is never committed or shared.
```

### 8.2 Where the bot runs

| Option | Pros | Cons |
|---|---|---|
| **A. Your Windows PC** (recommended for v1) | Free; no static IP needed in signals-only mode | Must be on and online from 9 AM to 11:55 PM on trading days. A UPS and a phone hotspot as backup help. The bot alerts you if it goes offline. |
| B. Cloud VM (Mumbai region) | Always on, no power or internet worries | About ₹500–1,500/month, which is a big share of a small account. Worth it later, or required if we move to automatic orders (static IP). |

### 8.3 How you'll use it day to day

You'll mainly use the signals, so everything else stays under the hood. You'll only touch:
- a **start script** (double-click to run the bot),
- `config/settings.yaml` for a handful of settings (capital, risk per trade, which windows to trade),
- Telegram.

| Time (IST) | What happens |
|---|---|
| ~8:30 AM | The bot asks you to log in (2FA), then sends the **morning message**: today's S1/S3 entries and exits, GTT stops to check or move for every open position, roll reminders, and today's US event times |
| 9:00 AM – ~11:00 PM | S2 intraday signals in the trading windows, plus management alerts (move stop, take profit, signal cancelled) |
| ~10:45–11:15 PM | "Close intraday positions" alert before the broker's square-off |
| Sunday | Weekly report: model vs your P&L, open positions, drawdown, upcoming expiries |

---

## 9. What I need from you

| # | Item | Why | When |
|---|---|---|---|
| 1 | Answers to the open questions in §11 | They shape the remaining choices | **Now** |
| 2 | **Zerodha** account with the **commodity (MCX) segment activated** (needs income proof) | You trade through it | Phase 0 |
| 3 | A **Kite Connect** app (₹500/month plan) on developers.kite.trade, **for market data and read-only positions**, with the API key and secret saved **by you** in a `.env` file (I'll give you a template, and I never need to see the values) | Live data, history, position tracking | Phase 1 |
| 4 | A Telegram account and a bot created via @BotFather, with its token saved in `.env` | Signals and alerts | Phase 3 |
| 5 | Your maximum tolerable drawdown and risk-per-trade choice | Position sizing and kill-switch level | After the backtest report |
| 6 | Possibly a manual download of MCX historical files, if their site blocks scripts | Long daily history | Phase 1 |
| 7 | A 2-minute login each morning (2FA) to start the data session | Broker rules | From Phase 3 |
| 8 | Placing the stop-loss with every entry, as a GTT stop for overnight positions | Signals-only execution | From live probation |
| 9 | Tapping Taken / Skipped on each signal | Accurate model-vs-actual tracking | From Phase 3 |
| 10 | Your sign-off at each gate: backtest → signal tracking → live → scale-up | You stay in control | Each phase |
| 11 | A chat with your CA about the tax treatment | Business-income filing | Before live |

**No longer needed in signals-only mode:** a static IP, algo registration, or a cloud VM.

---

## 10. Roadmap (approximate)

| Phase | What happens | Rough timing | Output |
|---|---|---|---|
| **0. Setup** | Zerodha account with MCX, Kite Connect app, Telegram bot | Week 1 | Project skeleton, config, `.env` template |
| **1. Data** | Downloaders, continuous contracts, cost model, data-quality checks, activity scan per 15-minute slot; connect Kite and **start recording minute bars** | Weeks 1–2 | Clean Parquet dataset, a data report, and a go/no-go on buying intraday history for S2 |
| **2. Research** | Backtests of S1–S3 at your real capital, walk-forward, robustness checks, Monte Carlo, minimum capital per instrument | Weeks 2–5 | **Backtest report with gate results**, then your go/no-go |
| **3. Signal engine + tracking** | Live data feed, risk manager, Telegram signals with buttons, read-only position tracker, then 4–8 weeks of signal tracking | Weeks 5–12 | Signal-tracking report compared against the backtest |
| **4. Live probation** | You trade the signals, smallest contracts, 0.25% risk per trade | Months 3–4 | Weekly reports: model vs your P&L |
| **5. Scale and extend** | Gradual size increases, bigger contracts and base metals as capital grows, **Nifty module** (§1.3), S4 research, optional automatic orders | Month 5+ | — |

---

## 11. Open questions

**Answered:** see §12. No questions are open right now.

Coming up later:
- **After Phase 1:** whether to buy multi-year intraday history for the S2 backtest (§6).
- **After Phase 2:** risk per trade (0.5% or 1%).

Defaults I'll use unless you say otherwise:
- **Risk per trade:** the backtest shows both 0.5% and 1%; you choose after the report.
- **Kill switch:** −15% from peak equity.
- **Options:** left out of v1.
- **Your own setups:** none to test for now.

---

## 12. Decision log

| Date | Decision |
|---|---|
| 24 Nov 2026 | Draft plan v0.1 created |
| 24 Nov 2026 | Capital: **under ₹2 lakh**, so smallest contracts only, near-zero running costs, and a small-account backtest (§1.2) |
| 24 Nov 2026 | Style: **both** intraday (S2) and positional (S1, S3) |
| 24 Nov 2026 | Automation: **signals only.** The bot never places orders; you trade manually from Telegram alerts. No static IP or algo registration needed in v1. |
| 24 Nov 2026 | Signal delivery: **Telegram**, in a private chat with your own bot: entry signals with Taken/Skipped buttons, trade-management alerts, and `/status`-style commands (§3.6) |
| 24 Nov 2026 | Signal windows: **all day**, so all three S2 windows are tested |
| 24 Nov 2026 | Positional (S1, S3) signals arrive in the **8:30 AM morning message**, not late at night |
| 24 Nov 2026 | **Overnight and weekend holds are OK**, so S1 and S3 stay in the plan |
| 24 Nov 2026 | Risk per trade: **decided after the backtest**, which shows both 0.5% and 1% |
| 24 Nov 2026 | Commodities: **crude oil, natural gas, gold, silver**, in their smallest contracts (§1.1). **Nifty later**, when capital allows (§1.3). Sensex skipped. |
| 24 Nov 2026 | No existing data: v1 starts from **free sources** only |
| 24 Nov 2026 | You'll **mainly use the signals**: a start script, a small config file and Telegram (§8.3) |
| 24 Nov 2026 | Broker: **Zerodha**, with the Kite Connect ₹500/month data plan (§6.1). Overnight stops use GTT orders on MCX. |
| 24 Nov 2026 | Intraday windows widened from 3 to **6 candidates**. Each is chosen for when news or trading volume arrives, then confirmed by an activity scan before any profit testing (§3.2). |
| 24 Nov 2026 | Window selection: rank by **out-of-sample profit per unit of drawdown**, keep at most **2–3 windows**, apply noise filters on every signal, and slow S2 down after a 6% drawdown (§3.2, §4) |

---

## 13. References

- SEBI/NSE retail algo framework: [Zerodha's overview of the NSE circular](https://zerodha.com/z-connect/general/a-comprehensive-overview-of-nses-circular-on-the-new-retail-algo-trading-framework), [Fyers FAQ on the rules from 1 Apr 2026](https://support.fyers.in/portal/en/kb/articles/what-are-the-new-sebi-rules-for-retail-algo-trading-from-april-01-2026), [Angel One SmartAPI changes from 1 Apr 2026](https://www.angelone.in/news/market-updates/what-s-changing-in-angel-one-s-smartapi-access-from-april-1-2026)
- Static IP scope (Zerodha): [Zerodha support: static IP](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/static-ip)
- Kite Connect pricing: [Zerodha support: Kite API charges](https://support.zerodha.com/category/trading-and-markets/kite-api/articles/what-are-the-charges-for-kite-apis), [Free Kite Connect for active users (Oct 2026)](https://tradingqna.com/t/free-kite-connect-subscription-for-active-users/198623)
- Angel One brokerage: [Angel One commodity futures page](https://www.angelone.in/knowledge-center/futures-and-options/commodity-futures)
- MCX trading hours: [Zerodha bulletin on the 2 Nov 2026 revision](https://zerodha.com/marketintel/bulletin/459705/revision-in-commodity-market-trading-hours-from-november-02-2026)
- Lot sizes and margins: [AlgoTest MCX lot sizes 2026](https://algotest.in/blog/mcx-lot-size/), [Sahi commodity margins 2026](https://www.sahi.com/blogs/commodity-margin-lot-size-2026), [MCX Silver 100 launch](https://www.angelone.in/news/commodities/mcx-to-launch-silver-100-futures-contracts-from-june-1-2026)
- Broker APIs: [AlgoTest broker comparison 2026](https://algotest.in/blog/best-brokers-for-algo-trading-in-india/)
- Index derivatives: [Nifty lot size cut to 65](https://www.angelone.in/news/market-updates/nse-revises-lot-sizes-for-index-derivatives-from-december-2025-nifty-lot-size-to-reduce-to-65), [Fyers notice on index lot sizes](https://fyers.in/notice-board/revision-in-market-lot-sizes-for-stock-and-index-derivative-contracts-on-nse-and-bse), [Weekly expiry days](https://algotest.in/blog/all-index-expiry-day/)
- Zerodha data and orders: [Kite forum: continuous data is daily only](https://kite.trade/forum/discussion/12102/continuous-expired-futures-historical-data-not-giving-5mins-candles), [Zerodha support: expired F&O history](https://support.zerodha.com/category/trading-and-markets/charts-and-orders/charts/articles/historical-data-for-expired-f-o-contract), [Zerodha: GTT for MCX](https://zerodha.com/z-connect/business-updates/updates-for-commodity-traders)
- Research: Moskowitz, Ooi & Pedersen (2012); Hurst, Ooi & Pedersen (2017); Crabel (1990); Gao, Han, Li & Zhou (2018); Gatev, Goetzmann & Rouwenhorst (2006); Connors & Alvarez (2008); Bailey & López de Prado (2014), *The Deflated Sharpe Ratio*
