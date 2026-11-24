"""Telegram notifier: signals with Taken/Skipped buttons, plain alerts, and taps.

Talks to the Telegram Bot HTTP API through a small "transport" so tests can use
MockTransport (no token, no network). The bot answers ONLY the user ID in .env
(TELEGRAM_CHAT_ID); everyone else is ignored (PLAN §3.6).

Every signal is written to the local journal BEFORE sending, so a Telegram
outage never loses a signal.

Command line (needs TELEGRAM_BOT_TOKEN in .env):
  python -m bot.notify.telegram --find-chat-id   # after you send your bot any message
  python -m bot.notify.telegram --test           # sends a TEST signal with buttons
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

import requests

from bot.signals.signal import Signal, make_signal_id
from bot.signals.tracker import SignalJournal
from bot.timeutil import now_ist

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
CALLBACK_SEP = "|"
ACTIONS = {"taken": "Taken ✅", "skipped": "Skipped ❌"}


class TelegramError(Exception):
    """Telegram call failed. Messages never include the bot token."""


class Transport(Protocol):
    def call(self, method: str, payload: dict[str, Any], timeout: float = 30) -> Any: ...


class HttpTransport:
    """Real Telegram Bot API over HTTPS. The token is kept private to this object."""

    def __init__(self, token: str, session: requests.Session | None = None):
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is missing from .env")
        self.__token = token
        self._session = session or requests.Session()

    def __repr__(self) -> str:  # never show the token
        return "HttpTransport(token=***)"

    def call(self, method: str, payload: dict[str, Any], timeout: float = 30) -> Any:
        url = f"{API_BASE}/bot{self.__token}/{method}"
        try:
            r = self._session.post(url, json=payload, timeout=timeout)
        except requests.RequestException as e:
            # requests' error text can contain the URL, which contains the token.
            raise TelegramError(f"{method}: network error ({type(e).__name__})") from None
        try:
            body = r.json()
        except ValueError:
            raise TelegramError(f"{method}: HTTP {r.status_code}, non-JSON reply") from None
        if not body.get("ok"):
            raise TelegramError(f"{method}: {body.get('error_code')} {body.get('description')}")
        return body.get("result")


@dataclass
class MockTransport:
    """In-memory stand-in for Telegram, for tests and dry runs."""

    calls: list[tuple[str, dict]] = field(default_factory=list)
    pending_updates: list[dict] = field(default_factory=list)
    fail_methods: set[str] = field(default_factory=set)
    _next_message_id: int = 100

    def call(self, method: str, payload: dict[str, Any], timeout: float = 30) -> Any:
        self.calls.append((method, payload))
        if method in self.fail_methods:
            raise TelegramError(f"{method}: simulated failure")
        if method == "sendMessage":
            self._next_message_id += 1
            return {"message_id": self._next_message_id, "chat": {"id": payload["chat_id"]},
                    "text": payload["text"]}
        if method == "getUpdates":
            offset = payload.get("offset") or 0
            out = [u for u in self.pending_updates if u["update_id"] >= offset]
            return out
        if method == "getMe":
            return {"id": 1, "is_bot": True, "username": "mock_bot"}
        return True

    def sent(self, method: str = "sendMessage") -> list[dict]:
        return [p for m, p in self.calls if m == method]


def signal_keyboard(signal_id: str) -> dict:
    return {"inline_keyboard": [[
        {"text": ACTIONS["taken"], "callback_data": f"taken{CALLBACK_SEP}{signal_id}"},
        {"text": ACTIONS["skipped"], "callback_data": f"skipped{CALLBACK_SEP}{signal_id}"},
    ]]}


def parse_callback(data: str) -> tuple[str, str] | None:
    action, sep, signal_id = (data or "").partition(CALLBACK_SEP)
    if not sep or action not in ACTIONS or not signal_id:
        return None
    return action, signal_id


class TelegramNotifier:
    def __init__(self, transport: Transport, chat_id: int | str,
                 journal: SignalJournal | None = None,
                 status_provider: Callable[[], str] | None = None):
        self.t = transport
        self.chat_id = int(chat_id)
        # In a private chat the chat ID equals your user ID; only you are answered.
        self.allowed_user_id = int(chat_id)
        self.journal = journal
        self.status_provider = status_provider or (lambda: "Bot is running (Phase 0: no live signals yet).")
        self.offset: int | None = None

    # ---- sending -------------------------------------------------------
    def send_text(self, text: str) -> bool:
        try:
            self.t.call("sendMessage", {"chat_id": self.chat_id, "text": text,
                                        "disable_web_page_preview": True})
            return True
        except TelegramError as e:
            log.error("Telegram send failed: %s (message kept in log): %s", e, text)
            return False

    def send_signal(self, signal: Signal) -> bool:
        """Log locally first (duplicate guard), then send with Taken/Skipped buttons."""
        if self.journal is not None and not self.journal.log_signal(signal):
            log.warning("Duplicate signal %s not sent again", signal.signal_id)
            return False
        log.info("SIGNAL %s\n%s", signal.signal_id, signal.format())
        try:
            self.t.call("sendMessage", {"chat_id": self.chat_id, "text": signal.format(),
                                        "reply_markup": signal_keyboard(signal.signal_id)})
            ok = True
        except TelegramError as e:
            log.error("Telegram send failed for %s: %s. Signal is in the local journal.", signal.signal_id, e)
            ok = False
        if self.journal is not None:
            self.journal.mark_sent(signal.signal_id, ok)
        return ok

    # ---- receiving -----------------------------------------------------
    def _authorised(self, user: dict | None) -> bool:
        return bool(user) and int(user.get("id", -1)) == self.allowed_user_id

    def handle_update(self, update: dict) -> str | None:
        """Process one update. Returns 'taken'/'skipped'/'command' or None if ignored."""
        cq = update.get("callback_query")
        if cq:
            if not self._authorised(cq.get("from")):
                log.warning("Ignored button tap from unknown user id %s", (cq.get("from") or {}).get("id"))
                return None
            parsed = parse_callback(cq.get("data", ""))
            if not parsed:
                return None
            action, signal_id = parsed
            if self.journal is not None:
                self.journal.record_response(signal_id, action, user_id=self.allowed_user_id)
            log.info("You tapped %s on %s", action.upper(), signal_id)
            stamp = now_ist().strftime("%H:%M")
            self._safe("answerCallbackQuery", {"callback_query_id": cq["id"],
                                               "text": f"Recorded: {ACTIONS[action]}"})
            msg = cq.get("message") or {}
            if msg.get("message_id"):
                self._safe("editMessageText", {
                    "chat_id": self.chat_id, "message_id": msg["message_id"],
                    "text": f"{msg.get('text', '')}\n→ {ACTIONS[action]} at {stamp}"})
            return action

        msg = update.get("message")
        if msg and msg.get("text", "").startswith("/"):
            if not self._authorised(msg.get("from")):
                log.warning("Ignored command from unknown user id %s", (msg.get("from") or {}).get("id"))
                return None
            cmd = msg["text"].split()[0].split("@")[0].lower()
            if cmd in ("/status", "/start"):
                self.send_text(self.status_provider())
            elif cmd in ("/positions", "/pause", "/resume"):
                self.send_text(f"{cmd} arrives with the live signal engine (Phase 3).")
            else:
                self.send_text("Commands: /status, /positions, /pause, /resume")
            return "command"
        return None

    def poll_once(self, timeout: int = 25) -> int:
        """Long-poll Telegram once and handle any updates. Returns how many were handled."""
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if self.offset is not None:
            payload["offset"] = self.offset
        updates = self.t.call("getUpdates", payload, timeout=timeout + 10) or []
        for u in updates:
            self.offset = u["update_id"] + 1
            try:
                self.handle_update(u)
            except Exception:  # one bad update must not stop the bot
                log.exception("Failed to handle Telegram update %s", u.get("update_id"))
        return len(updates)

    def _safe(self, method: str, payload: dict) -> None:
        try:
            self.t.call(method, payload)
        except TelegramError as e:
            log.warning("Telegram %s failed: %s", method, e)


def demo_signal(seq: int = 1) -> Signal:
    """The PLAN §3.6 example, clearly marked as a test."""
    today = now_ist().date()
    return Signal(
        signal_id=make_signal_id("S2", today, "CRUDEOILM", seq) + "-TEST",
        strategy="S2", side="BUY", symbol="CRUDEOILM", contract="DEC-26", lots=1,
        entry_type="buy-stop / limit", entry=5612, valid_till="19:55",
        stop=5571, risk_inr=410, equity_inr=200000,
        target=5694, target_label="2R", squareoff_alert="22:45",
        reason="US-session range 5,571–5,606 broken; trend filter ✔", test=True)


def find_chat_ids(transport: Transport) -> list[tuple[int, str]]:
    """Private chats that have messaged the bot recently: (chat_id, first name)."""
    out: dict[int, str] = {}
    for u in transport.call("getUpdates", {"timeout": 0}) or []:
        chat = (u.get("message") or {}).get("chat") or {}
        if chat.get("type") == "private":
            out[int(chat["id"])] = chat.get("first_name", "")
    return sorted(out.items())


def _cli(argv: list[str] | None = None) -> int:
    from bot import env as envmod
    from bot.logging_setup import setup_logging

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--find-chat-id", action="store_true", help="show the chat ID of whoever messaged your bot")
    g.add_argument("--test", action="store_true", help="send a TEST signal and wait 2 minutes for your tap")
    args = ap.parse_args(argv)

    env = envmod.load_env()
    setup_logging(secrets=[env.get(k, "") for k in envmod.SECRET_KEYS])
    if not env.get("TELEGRAM_BOT_TOKEN"):
        print("TELEGRAM_BOT_TOKEN is missing in .env (see README step 4).")
        return 2
    transport = HttpTransport(env["TELEGRAM_BOT_TOKEN"])
    try:
        me = transport.call("getMe", {})
    except TelegramError as e:
        print(f"Telegram rejected the token: {e}")
        return 2
    print(f"Connected to your bot @{me.get('username')}")

    if args.find_chat_id:
        chats = find_chat_ids(transport)
        if not chats:
            print("No messages found. Open your bot in Telegram, send it 'hi', then run this again.")
            return 1
        for cid, name in chats:
            print(f"Chat ID for {name or 'you'}: {cid}   <- put this in .env as TELEGRAM_CHAT_ID")
        return 0

    if not env.get("TELEGRAM_CHAT_ID"):
        print("TELEGRAM_CHAT_ID is missing in .env. Run with --find-chat-id first.")
        return 2
    notifier = TelegramNotifier(transport, env["TELEGRAM_CHAT_ID"], journal=SignalJournal())
    sig = demo_signal(seq=int(time.time()) % 90 + 10)
    if not notifier.send_signal(sig):
        print("Sending failed; see logs/bot.log")
        return 1
    print("Test signal sent. Tap Taken or Skipped in Telegram within 2 minutes...")
    deadline = time.time() + 120
    while time.time() < deadline:
        notifier.poll_once(timeout=10)
        resp = notifier.journal.latest_response(sig.signal_id)
        if resp:
            print(f"Received your tap: {resp.upper()}. Telegram is working.")
            return 0
    print("No tap received in 2 minutes (the signal is still logged locally).")
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
