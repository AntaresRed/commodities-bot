"""What start_bot.bat runs.

Phase 0/1 behaviour (no live signals yet):
  1. Check config/settings.yaml and config/instruments.yaml
  2. Check which .env keys are set (names only, never values)
  3. Update free data: benchmarks, MCX files you downloaded, continuous series,
     data-quality report
  4. Kite daily login (only if your Kite keys are in .env)
  5. Telegram: "bot started" message, then answer /status and button taps until
     you close the window (only if your Telegram keys are in .env)

SIGNALS ONLY: nothing here (or anywhere in the bot) places orders.
"""

from __future__ import annotations

import argparse
import logging
import sys

from bot import __version__, paths
from bot import env as envmod
from bot.config import ConfigError, load_instruments, load_settings
from bot.logging_setup import setup_logging
from bot.timeutil import mcx_session, now_ist

log = logging.getLogger("bot.runner")


def _section(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MCX signal bot (signals only)")
    ap.add_argument("--no-download", action="store_true", help="skip internet data downloads")
    ap.add_argument("--no-kite", action="store_true", help="skip the Kite login")
    ap.add_argument("--no-listen", action="store_true", help="exit instead of listening on Telegram")
    args = ap.parse_args(argv)

    paths.ensure_dirs()
    env = envmod.load_env()
    setup_logging(secrets=[env.get(k, "") for k in envmod.SECRET_KEYS])
    now = now_ist()
    sess = mcx_session(now.date())
    print(f"MCX signal bot v{__version__}  -  SIGNALS ONLY: it never places orders.")
    print(f"{now:%a %d %b %Y %H:%M} IST.  MCX today {sess.open:%H:%M}-{sess.close:%H:%M} "
          f"({'US summer' if sess.us_dst else 'US winter'} hours; holidays not checked yet)")

    _section("1. Settings")
    try:
        settings = load_settings()
        instruments = load_instruments()
    except ConfigError as e:
        print(e)
        print("\nFix the line(s) above in the config folder, save, and run start_bot.bat again.")
        return 2
    print(f"Capital Rs {settings.capital_inr:,.0f}; risk per trade {settings.risk_per_trade_pct}% "
          f"= Rs {settings.risk_per_trade_inr:,.0f}; kill switch at -{settings.limits.kill_switch_drawdown_pct}%")
    print(f"Contracts: {', '.join(i.symbol for i in instruments.tradeable())}")
    unverified = [i.symbol for i in instruments.tradeable() if not i.verified]
    if unverified:
        print(f"Note: lot/tick specs not yet confirmed from Kite for {', '.join(unverified)}")

    _section("2. Your .env keys")
    print("\n".join(envmod.status_lines(env)))
    if not paths.ENV_FILE.exists():
        print("No .env file yet: copy .env.example to .env and fill it in (README step 5).")

    _section("3. Free data update")
    from bot.data.pipeline import update_all
    try:
        for line in update_all(instruments, download=not args.no_download):
            print(line)
    except Exception as e:
        log.exception("Data update failed")
        print(f"Data update failed ({type(e).__name__}); details in logs/bot.log")

    _section("4. Zerodha Kite (read-only)")
    if args.no_kite:
        print("Skipped (--no-kite).")
    elif envmod.missing(envmod.KITE_KEYS, env):
        print("Skipped: add KITE_API_KEY and KITE_API_SECRET to .env (README step 3).")
    else:
        from bot.broker.kite_auth import KiteLoginError, daily_login
        try:
            s = daily_login(env["KITE_API_KEY"], env["KITE_API_SECRET"])
            print(f"Kite session OK for {s.user_id}, valid until {s.expires_ist:%d %b %H:%M} IST.")
            from bot.broker.zerodha import ZerodhaReadOnly
            funds = ZerodhaReadOnly(env["KITE_API_KEY"], s.access_token).funds()
            avail = (funds or {}).get("available", {}).get("live_balance")
            if avail is not None:
                print(f"Commodity segment balance: Rs {float(avail):,.0f}")
        except KiteLoginError as e:
            print(f"Kite login failed: {e}")
        except Exception as e:
            log.exception("Kite check failed")
            print(f"Kite connected but the funds check failed ({type(e).__name__}); see logs/bot.log")

    _section("5. Telegram")
    if envmod.missing(envmod.TELEGRAM_KEYS, env):
        print("Skipped: add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env (README step 4).")
        return 0
    from bot.notify.telegram import HttpTransport, TelegramError, TelegramNotifier
    from bot.signals.tracker import SignalJournal

    def status() -> str:
        t = now_ist()
        return (f"✅ Bot running ({t:%d %b %H:%M} IST). Phase 0/1: data + setup only, "
                f"no live signals yet. Risk per trade {settings.risk_per_trade_pct}%.")

    try:
        notifier = TelegramNotifier(HttpTransport(env["TELEGRAM_BOT_TOKEN"]), env["TELEGRAM_CHAT_ID"],
                                    journal=SignalJournal(), status_provider=status)
    except (TelegramError, ValueError) as e:
        print(f"Telegram not set up correctly: {e}")
        return 1
    if notifier.send_text(status()):
        print("Sent 'bot started' to your Telegram.")
    else:
        print("Could not reach Telegram; see logs/bot.log")
    if args.no_listen:
        return 0
    print("Listening for /status and button taps. Close this window (or Ctrl+C) to stop.")
    try:
        while True:
            try:
                notifier.poll_once(timeout=25)
            except TelegramError as e:
                log.warning("Telegram poll failed: %s; retrying", e)
                import time
                time.sleep(10)
    except KeyboardInterrupt:
        print("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
