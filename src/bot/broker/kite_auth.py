"""Daily Kite Connect login (PLAN §7: API sessions expire every day).

Flow:
  1. Open the Kite login page for your app in your browser.
  2. You log in with your Zerodha ID, password and 2FA (the bot never sees these).
  3. Kite redirects your browser to http://127.0.0.1:5678/kite/callback?request_token=...
     A tiny local web server (only reachable from this PC) catches that.
  4. The request_token + your API secret are exchanged for an access token.
  5. The access token is saved to data/session/kite_session.json (git-ignored)
     and reused until it expires at 06:00 IST the next morning.

Set this EXACT redirect URL in your Kite Connect app (README step 3):
  http://127.0.0.1:5678/kite/callback

Command line:  python -m bot.broker.kite_auth
"""

from __future__ import annotations

import http.server
import json
import logging
import sys
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from bot import paths
from bot.timeutil import IST, now_ist

log = logging.getLogger(__name__)

LOGIN_BASE = "https://kite.zerodha.com/connect/login"
REDIRECT_HOST = "127.0.0.1"
REDIRECT_PORT = 5678
REDIRECT_PATH = "/kite/callback"
REDIRECT_URL = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}{REDIRECT_PATH}"
TOKEN_EXPIRY_TIME = time(6, 0)  # Kite access tokens expire at 6 AM IST the next day


class KiteLoginError(Exception):
    pass


def login_url(api_key: str) -> str:
    if not api_key:
        raise KiteLoginError("KITE_API_KEY is missing from .env")
    return f"{LOGIN_BASE}?{urlencode({'v': 3, 'api_key': api_key})}"


def parse_redirect(path_or_url: str) -> str:
    """Extract request_token from the redirect; raise if the login failed."""
    q = parse_qs(urlparse(path_or_url).query)
    status = (q.get("status") or [""])[0]
    token = (q.get("request_token") or [""])[0]
    if status and status != "success":
        raise KiteLoginError(f"Kite login was not successful (status={status})")
    if not token:
        raise KiteLoginError("Redirect had no request_token")
    return token


def token_expiry(created: datetime) -> datetime:
    """Next 06:00 IST after `created`."""
    created = created.astimezone(IST)
    expiry = datetime.combine(created.date(), TOKEN_EXPIRY_TIME, tzinfo=IST)
    if created >= expiry:
        expiry += timedelta(days=1)
    return expiry


@dataclass
class KiteSession:
    access_token: str
    user_id: str
    created_ist: datetime
    expires_ist: datetime

    def __repr__(self) -> str:
        return f"KiteSession(user_id={self.user_id!r}, access_token=***, expires_ist={self.expires_ist})"

    def valid(self, at: datetime | None = None) -> bool:
        return (at or now_ist()) < self.expires_ist


class TokenStore:
    """Keeps today's access token in a git-ignored JSON file (or memory only if path=None)."""

    def __init__(self, path: Path | None = paths.SESSION_DIR / "kite_session.json"):
        self.path = path
        self._mem: KiteSession | None = None

    def save(self, s: KiteSession) -> None:
        self._mem = s
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "access_token": s.access_token, "user_id": s.user_id,
            "created_ist": s.created_ist.isoformat(), "expires_ist": s.expires_ist.isoformat()}),
            encoding="utf-8")

    def load(self, at: datetime | None = None) -> KiteSession | None:
        s = self._mem
        if s is None and self.path is not None and self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                s = KiteSession(d["access_token"], d.get("user_id", ""),
                                datetime.fromisoformat(d["created_ist"]),
                                datetime.fromisoformat(d["expires_ist"]))
            except (ValueError, KeyError):
                log.warning("Ignoring unreadable Kite session file")
                return None
        if s is not None and s.valid(at):
            return s
        return None

    def clear(self) -> None:
        self._mem = None
        if self.path is not None and self.path.exists():
            self.path.unlink()


class RedirectCatcher:
    """One-shot local HTTP server on 127.0.0.1 that captures Kite's redirect."""

    def __init__(self, host: str = REDIRECT_HOST, port: int = REDIRECT_PORT, path: str = REDIRECT_PATH):
        self.expected_path = path
        self._event = threading.Event()
        self.request_token: str | None = None
        self.error: str | None = None
        catcher = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if urlparse(self.path).path != catcher.expected_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                try:
                    catcher.request_token = parse_redirect(self.path)
                    body, code = "Kite login received. You can close this tab and go back to the bot.", 200
                except KiteLoginError as e:
                    catcher.error = str(e)
                    body, code = f"Kite login failed: {e}. Close this tab and try again.", 400
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))
                catcher._event.set()

            def log_message(self, *args):  # keep the request_token out of the console
                pass

        try:
            self._server = http.server.HTTPServer((host, port), Handler)
        except OSError as e:
            raise KiteLoginError(f"Port {port} is busy; close the other bot window and retry ({e})") from None
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "RedirectCatcher":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    def wait(self, timeout: float) -> str:
        if not self._event.wait(timeout):
            raise KiteLoginError(f"No login within {int(timeout)} s. Run the login again.")
        if self.error:
            raise KiteLoginError(self.error)
        assert self.request_token
        return self.request_token


def exchange_token(api_key: str, api_secret: str, request_token: str,
                   kite_factory: Callable[..., Any] | None = None, now: datetime | None = None) -> KiteSession:
    if not api_secret:
        raise KiteLoginError("KITE_API_SECRET is missing from .env")
    if kite_factory is None:
        from kiteconnect import KiteConnect
        kite_factory = KiteConnect
    kite = kite_factory(api_key=api_key)
    try:
        data = kite.generate_session(request_token, api_secret=api_secret)
    except Exception as e:  # kiteconnect raises its own exception types
        raise KiteLoginError(f"Kite rejected the login ({type(e).__name__}: {e})") from None
    created = now or now_ist()
    return KiteSession(access_token=data["access_token"], user_id=str(data.get("user_id", "")),
                       created_ist=created, expires_ist=token_expiry(created))


def daily_login(api_key: str, api_secret: str, store: TokenStore | None = None,
                open_browser: Callable[[str], Any] = webbrowser.open, timeout: float = 300,
                kite_factory: Callable[..., Any] | None = None,
                port: int = REDIRECT_PORT) -> KiteSession:
    """Return today's session, logging in through the browser only if needed."""
    store = store or TokenStore()
    existing = store.load()
    if existing:
        log.info("Using saved Kite session for %s (valid until %s)", existing.user_id,
                 existing.expires_ist.strftime("%d %b %H:%M IST"))
        return existing
    url = login_url(api_key)
    with RedirectCatcher(port=port) as catcher:
        log.info("Opening the Kite login page in your browser. Log in with your Zerodha ID + 2FA.")
        print(f"\nIf the browser does not open, paste this into it:\n  {url}\n")
        open_browser(url)
        request_token = catcher.wait(timeout)
    session = exchange_token(api_key, api_secret, request_token, kite_factory=kite_factory)
    store.save(session)
    log.info("Kite login OK for %s; session saved until %s", session.user_id,
             session.expires_ist.strftime("%d %b %H:%M IST"))
    return session


def main() -> int:
    from bot import env as envmod
    from bot.logging_setup import setup_logging

    env = envmod.load_env()
    setup_logging(secrets=[env.get(k, "") for k in envmod.SECRET_KEYS])
    miss = envmod.missing(envmod.KITE_KEYS, env)
    if miss:
        print(f"Missing in .env: {', '.join(miss)} (see README step 3)")
        return 2
    try:
        s = daily_login(env["KITE_API_KEY"], env["KITE_API_SECRET"])
    except KiteLoginError as e:
        print(f"Kite login failed: {e}")
        return 1
    print(f"Logged in to Kite as {s.user_id}. Session valid until {s.expires_ist:%d %b %H:%M} IST.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
