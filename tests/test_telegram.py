import pytest
import requests

from bot.notify.telegram import (HttpTransport, MockTransport, TelegramError, TelegramNotifier, demo_signal,
                                 find_chat_ids, parse_callback, signal_keyboard)
from bot.signals.tracker import SignalJournal

ME = 555000111


@pytest.fixture
def setup(tmp_path):
    t = MockTransport()
    j = SignalJournal(tmp_path)
    return t, j, TelegramNotifier(t, ME, journal=j)


def _tap(update_id, action, sid, user=ME, message_id=101):
    return {"update_id": update_id, "callback_query": {
        "id": f"cb{update_id}", "from": {"id": user}, "data": f"{action}|{sid}",
        "message": {"message_id": message_id, "chat": {"id": ME}, "text": "signal text"}}}


def test_signal_sent_with_taken_skipped_buttons(setup):
    t, j, n = setup
    sig = demo_signal()
    assert n.send_signal(sig)
    (payload,) = t.sent()
    assert payload["chat_id"] == ME
    assert "TEST SIGNAL" in payload["text"]
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["Taken ✅", "Skipped ❌"]
    assert [b["callback_data"] for b in buttons] == [f"taken|{sig.signal_id}", f"skipped|{sig.signal_id}"]
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)   # Telegram limit
    assert j.exists(sig.signal_id)


def test_duplicate_signal_not_resent(setup):
    t, j, n = setup
    sig = demo_signal()
    assert n.send_signal(sig)
    assert not n.send_signal(sig)
    assert len(t.sent()) == 1


def test_signal_logged_locally_even_if_telegram_down(setup):
    t, j, n = setup
    t.fail_methods.add("sendMessage")
    sig = demo_signal()
    assert not n.send_signal(sig)
    assert j.exists(sig.signal_id)
    assert sig.signal_id in j.jsonl_path.read_text(encoding="utf-8")


def test_taken_tap_is_recorded_and_message_updated(setup):
    t, j, n = setup
    sig = demo_signal()
    n.send_signal(sig)
    t.pending_updates.append(_tap(1, "taken", sig.signal_id))
    assert n.poll_once(timeout=0) == 1
    assert j.latest_response(sig.signal_id) == "taken"
    assert t.sent("answerCallbackQuery")[0]["text"] == "Recorded: Taken ✅"
    edit = t.sent("editMessageText")[0]
    assert "Taken ✅ at" in edit["text"] and "reply_markup" not in edit   # buttons removed
    assert n.offset == 2


def test_skipped_then_changed_mind(setup):
    t, j, n = setup
    sig = demo_signal()
    n.send_signal(sig)
    n.handle_update(_tap(1, "skipped", sig.signal_id))
    assert j.latest_response(sig.signal_id) == "skipped"
    n.handle_update(_tap(2, "taken", sig.signal_id))
    assert j.latest_response(sig.signal_id) == "taken"


def test_strangers_are_ignored(setup):
    t, j, n = setup
    sig = demo_signal()
    n.send_signal(sig)
    assert n.handle_update(_tap(1, "taken", sig.signal_id, user=999)) is None
    assert j.latest_response(sig.signal_id) is None
    stranger_cmd = {"update_id": 2, "message": {"text": "/status", "from": {"id": 999}, "chat": {"id": 999}}}
    assert n.handle_update(stranger_cmd) is None
    assert len(t.sent()) == 1          # only the signal itself; no reply to the stranger


def test_status_command(setup):
    t, j, n = setup
    n.status_provider = lambda: "all good"
    n.handle_update({"update_id": 1, "message": {"text": "/status", "from": {"id": ME}, "chat": {"id": ME}}})
    assert t.sent()[-1]["text"] == "all good"


def test_bad_callback_data_ignored(setup):
    t, j, n = setup
    assert parse_callback("bought|X") is None and parse_callback("taken|") is None
    assert parse_callback("taken|S2-1") == ("taken", "S2-1")
    assert n.handle_update({"update_id": 1, "callback_query": {"id": "c", "from": {"id": ME}, "data": "junk"}}) is None


def test_keyboard_shape():
    kb = signal_keyboard("S1-20261124-GDT-01")
    assert len(kb["inline_keyboard"]) == 1 and len(kb["inline_keyboard"][0]) == 2


def test_find_chat_ids():
    t = MockTransport(pending_updates=[
        {"update_id": 1, "message": {"text": "hi", "chat": {"id": 42, "type": "private", "first_name": "A"}}},
        {"update_id": 2, "message": {"text": "x", "chat": {"id": -100, "type": "group"}}}])
    assert find_chat_ids(t) == [(42, "A")]


class _BoomSession:
    def post(self, url, **kw):
        raise requests.ConnectionError(f"failed to reach {url}")


def test_http_transport_never_leaks_token():
    token = "987654:SECRET-test-token"
    tr = HttpTransport(token, session=_BoomSession())
    assert token not in repr(tr)
    with pytest.raises(TelegramError) as e:
        tr.call("sendMessage", {})
    assert token not in str(e.value)


def test_http_transport_needs_token():
    with pytest.raises(TelegramError):
        HttpTransport("")
