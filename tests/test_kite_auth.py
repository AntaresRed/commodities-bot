import threading
import urllib.request
from datetime import datetime

import pytest

from bot.broker import kite_auth as ka
from bot.timeutil import IST


def test_login_url():
    url = ka.login_url("myappkey")
    assert url == "https://kite.zerodha.com/connect/login?v=3&api_key=myappkey"
    with pytest.raises(ka.KiteLoginError):
        ka.login_url("")


def test_redirect_url_constant():
    assert ka.REDIRECT_URL == "http://127.0.0.1:5678/kite/callback"


def test_parse_redirect():
    assert ka.parse_redirect("/kite/callback?request_token=abc&action=login&status=success") == "abc"
    with pytest.raises(ka.KiteLoginError, match="not successful"):
        ka.parse_redirect("/kite/callback?status=cancelled")
    with pytest.raises(ka.KiteLoginError, match="no request_token"):
        ka.parse_redirect("/kite/callback?status=success")


@pytest.mark.parametrize("created,expected", [
    (datetime(2026, 11, 24, 8, 30, tzinfo=IST), datetime(2026, 11, 25, 6, 0, tzinfo=IST)),
    (datetime(2026, 11, 24, 5, 0, tzinfo=IST), datetime(2026, 11, 24, 6, 0, tzinfo=IST)),
    (datetime(2026, 11, 24, 23, 59, tzinfo=IST), datetime(2026, 11, 25, 6, 0, tzinfo=IST)),
])
def test_token_expiry_is_next_6am_ist(created, expected):
    assert ka.token_expiry(created) == expected


def test_token_store_roundtrip_and_expiry(tmp_path):
    store = ka.TokenStore(tmp_path / "s.json")
    created = datetime(2026, 11, 24, 8, 30, tzinfo=IST)
    s = ka.KiteSession("tok-123456", "AB1234", created, ka.token_expiry(created))
    store.save(s)
    fresh = ka.TokenStore(tmp_path / "s.json")
    assert fresh.load(at=datetime(2026, 11, 24, 20, 0, tzinfo=IST)).access_token == "tok-123456"
    assert fresh.load(at=datetime(2026, 11, 25, 6, 1, tzinfo=IST)) is None
    assert "tok-123456" not in repr(s)


def test_memory_only_store():
    store = ka.TokenStore(path=None)
    created = datetime.now(IST)
    store.save(ka.KiteSession("t", "u", created, ka.token_expiry(created)))
    assert store.load().access_token == "t"


def test_redirect_catcher_receives_token():
    with ka.RedirectCatcher(port=0) as c:   # port 0 = any free port
        url = f"http://127.0.0.1:{c.port}/kite/callback?request_token=rt123&action=login&status=success"
        body = urllib.request.urlopen(url, timeout=5).read().decode()
        assert "login received" in body
        assert c.wait(timeout=5) == "rt123"


def test_redirect_catcher_reports_failure():
    with ka.RedirectCatcher(port=0) as c:
        url = f"http://127.0.0.1:{c.port}/kite/callback?status=error"
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(url, timeout=5)
        with pytest.raises(ka.KiteLoginError):
            c.wait(timeout=5)


def test_redirect_catcher_times_out():
    with ka.RedirectCatcher(port=0) as c:
        with pytest.raises(ka.KiteLoginError, match="No login"):
            c.wait(timeout=0.1)


class FakeKite:
    instances = []

    def __init__(self, api_key):
        self.api_key = api_key
        FakeKite.instances.append(self)

    def generate_session(self, request_token, api_secret):
        assert (request_token, api_secret) == ("rt123", "sec")
        return {"access_token": "access-xyz", "user_id": "AB1234"}


def test_daily_login_end_to_end_with_fakes(tmp_path):
    store = ka.TokenStore(tmp_path / "s.json")
    opened = []

    def fake_browser(url):
        opened.append(url)
        # simulate Kite redirecting the browser after a successful login
        def hit():
            urllib.request.urlopen(f"http://127.0.0.1:{port}/kite/callback?request_token=rt123&status=success",
                                   timeout=5).read()
        threading.Thread(target=hit, daemon=True).start()

    # grab a free port first
    with ka.RedirectCatcher(port=0) as probe:
        port = probe.port
    s = ka.daily_login("key", "sec", store=store, open_browser=fake_browser, timeout=5,
                       kite_factory=FakeKite, port=port)
    assert s.access_token == "access-xyz" and s.user_id == "AB1234"
    assert opened == [ka.login_url("key")]
    # second call reuses the saved session without opening the browser
    s2 = ka.daily_login("key", "sec", store=store, open_browser=lambda u: pytest.fail("should reuse"),
                        kite_factory=FakeKite, port=port)
    assert s2.access_token == "access-xyz"


def test_exchange_needs_secret():
    with pytest.raises(ka.KiteLoginError, match="SECRET"):
        ka.exchange_token("key", "", "rt", kite_factory=FakeKite)
