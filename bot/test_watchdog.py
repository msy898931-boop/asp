"""Tests for the watchdog: the alert decision, and each check. No network."""

from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import watchdog as wd  # noqa: E402

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def healthy():
    return {"healthy": True, "problems": []}


def down(*keys):
    return {"healthy": False,
            "problems": [{"key": k, "ar": "خلل " + k, "detail": "d"} for k in keys]}


class DecideTests(unittest.TestCase):
    def test_first_trouble_alerts(self):
        msg = wd.decide(down("bot"), {"healthy": True}, NOW)
        self.assertIn("🚨", msg)
        self.assertIn("خلل bot", msg)

    def test_still_down_does_not_repeat_immediately(self):
        state = {"healthy": False,
                 "last_alert_at": int((NOW - timedelta(hours=1)).timestamp())}
        self.assertIsNone(wd.decide(down("bot"), state, NOW))

    def test_still_down_realerts_after_the_window(self):
        state = {"healthy": False,
                 "last_alert_at": int((NOW - timedelta(hours=7)).timestamp())}
        self.assertIn("🚨", wd.decide(down("bot"), state, NOW))

    def test_recovery_is_announced(self):
        msg = wd.decide(healthy(), {"healthy": False}, NOW)
        self.assertIn("رجع كل شي", msg)

    def test_healthy_healthy_is_silent(self):
        self.assertIsNone(wd.decide(healthy(), {"healthy": True}, NOW))


class CheckBotTests(unittest.TestCase):
    def test_recent_success_is_healthy(self):
        recent = (NOW - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
        wd._get_json = lambda url, timeout: {"workflow_runs": [
            {"conclusion": "success", "updated_at": recent}]}
        ok, detail = wd.check_bot(NOW)
        self.assertTrue(ok)
        self.assertIn("min ago", detail)

    def test_a_slow_cron_stall_is_not_trouble(self):
        # The scanner rides a best-effort cron, so the threshold is loose
        # enough to survive GitHub stalling it for hours (a 5h stall is real).
        old = (NOW - timedelta(hours=5)).isoformat().replace("+00:00", "Z")
        wd._get_json = lambda url, timeout: {"workflow_runs": [
            {"conclusion": "success", "updated_at": old}]}
        ok, _ = wd.check_bot(NOW)
        self.assertTrue(ok)

    def test_dead_scanner_is_trouble(self):
        old = (NOW - timedelta(hours=8)).isoformat().replace("+00:00", "Z")
        wd._get_json = lambda url, timeout: {"workflow_runs": [
            {"conclusion": "success", "updated_at": old}]}
        ok, _ = wd.check_bot(NOW)
        self.assertFalse(ok)

    def test_no_success_on_record_is_trouble(self):
        wd._get_json = lambda url, timeout: {"workflow_runs": [
            {"conclusion": "failure", "updated_at": NOW.isoformat()}]}
        ok, _ = wd.check_bot(NOW)
        self.assertFalse(ok)

    def test_unreachable_github_does_not_cry_wolf(self):
        wd._get_json = lambda url, timeout: None
        ok, detail = wd.check_bot(NOW)
        self.assertTrue(ok)
        self.assertEqual(detail, "unavailable")


class CheckKeepaliveTests(unittest.TestCase):
    """The chain is the real liveness signal; it must catch a dead chain."""

    def test_a_running_chain_is_healthy(self):
        wd._get_json = lambda url, timeout: {"workflow_runs": [
            {"status": "in_progress", "conclusion": None, "updated_at": "x"}]}
        ok, detail = wd.check_keepalive(NOW)
        self.assertTrue(ok)
        self.assertEqual(detail, "running")

    def test_a_recently_completed_chain_is_healthy(self):
        recent = (NOW - timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
        wd._get_json = lambda url, timeout: {"workflow_runs": [
            {"status": "completed", "conclusion": "success", "updated_at": recent}]}
        ok, _ = wd.check_keepalive(NOW)
        self.assertTrue(ok)

    def test_a_dead_chain_is_trouble(self):
        # Past the 6h job cap with nothing running: a re-arm was dropped.
        old = (NOW - timedelta(hours=8)).isoformat().replace("+00:00", "Z")
        wd._get_json = lambda url, timeout: {"workflow_runs": [
            {"status": "completed", "conclusion": "success", "updated_at": old}]}
        ok, detail = wd.check_keepalive(NOW)
        self.assertFalse(ok)
        self.assertIn("chain", detail)

    def test_a_stuck_pending_run_is_trouble(self):
        # A lone successor can sit 'pending' for hours when no runner picks it
        # up. Past the 6h job cap with nothing running that is a dead chain, so
        # the 'pending' label must not buy it immunity.
        stuck = (NOW - timedelta(hours=8)).isoformat().replace("+00:00", "Z")
        wd._get_json = lambda url, timeout: {"workflow_runs": [
            {"status": "pending", "conclusion": None, "updated_at": stuck}]}
        ok, detail = wd.check_keepalive(NOW)
        self.assertFalse(ok)
        self.assertIn("chain", detail)

    def test_a_queued_successor_does_not_mask_the_running_chain(self):
        # Healthy shape: the current run is in_progress, its successor queued.
        queued = (NOW - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
        wd._get_json = lambda url, timeout: {"workflow_runs": [
            {"status": "pending", "conclusion": None, "updated_at": queued},
            {"status": "in_progress", "conclusion": None, "updated_at": queued}]}
        ok, detail = wd.check_keepalive(NOW)
        self.assertTrue(ok)
        self.assertEqual(detail, "running")

    def test_unreachable_github_does_not_cry_wolf(self):
        wd._get_json = lambda url, timeout: None
        ok, detail = wd.check_keepalive(NOW)
        self.assertTrue(ok)
        self.assertEqual(detail, "unavailable")


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class CheckEngineTests(unittest.TestCase):
    """The 200-but-empty bug must be caught, not read as healthy."""

    def setUp(self):
        self._orig = wd.urllib.request.urlopen
        self.addCleanup(lambda: setattr(wd.urllib.request, "urlopen", self._orig))

    def test_available_true_is_healthy(self):
        wd.urllib.request.urlopen = lambda *a, **k: _Resp(
            {"available": True, "verdict": "NO_EDGE_MEASURED"})
        ok, detail = wd.check_engine()
        self.assertTrue(ok)
        self.assertEqual(detail, "NO_EDGE_MEASURED")

    def test_available_false_is_trouble(self):
        wd.urllib.request.urlopen = lambda *a, **k: _Resp(
            {"available": False, "reason": "engine unavailable"})
        ok, detail = wd.check_engine()
        self.assertFalse(ok)
        self.assertIn("engine unavailable", detail)

    def test_missing_route_is_not_trouble(self):
        def raise404(*a, **k):
            raise urllib.error.HTTPError("u", 404, "nf", {}, None)
        wd.urllib.request.urlopen = raise404
        ok, detail = wd.check_engine()
        self.assertTrue(ok)
        self.assertEqual(detail, "unavailable")

    def test_gate_401_without_a_token_is_not_trouble(self):
        # The access gate answers 401 when the monitor has no token. That is a
        # missing credential, not a dead engine; it must never cry wolf.
        def raise401(*a, **k):
            raise urllib.error.HTTPError("u", 401, "unauth", {}, None)
        wd.urllib.request.urlopen = raise401
        ok, detail = wd.check_engine()
        self.assertTrue(ok)
        self.assertIn("gate", detail)

    def test_other_http_error_is_trouble(self):
        def raise500(*a, **k):
            raise urllib.error.HTTPError("u", 500, "err", {}, None)
        wd.urllib.request.urlopen = raise500
        ok, _ = wd.check_engine()
        self.assertFalse(ok)

    def test_access_cookie_is_sent_when_a_token_is_set(self):
        # With the phone server's gate on, the poll must carry the cookie or it
        # reads a 401 as "trouble" and cries wolf every hour.
        import astra_env
        seen = {}
        def capture(req, *a, **k):
            seen["cookie"] = req.get_header("Cookie")
            return _Resp({"available": True, "verdict": "OK"})
        wd.urllib.request.urlopen = capture
        saved = os.environ.get("ASTRA_ACCESS_TOKEN")
        try:
            astra_env.TOKEN = "tok-123"
            ok, _ = wd.check_engine()
            self.assertTrue(ok)
            self.assertEqual(seen["cookie"], "astra_key=tok-123")
        finally:
            astra_env.TOKEN = (saved or "").strip()




class CheckPollerTests(unittest.TestCase):
    """A silent poller must be caught, but an absent one must not cry wolf."""

    def setUp(self):
        self._orig = wd.urllib.request.urlopen
        self.addCleanup(lambda: setattr(wd.urllib.request, "urlopen", self._orig))

    def test_fresh_beat_is_healthy(self):
        wd.urllib.request.urlopen = lambda *a, **k: _Resp(
            {"available": True, "age_s": 30, "pid": 5})
        ok, detail = wd.check_poller()
        self.assertTrue(ok)
        self.assertIn("min ago", detail)

    def test_stale_beat_is_trouble(self):
        wd.urllib.request.urlopen = lambda *a, **k: _Resp(
            {"available": False, "reason": "stale", "age_s": 400})
        ok, detail = wd.check_poller()
        self.assertFalse(ok)
        self.assertIn("no beat", detail)

    def test_missing_heartbeat_is_not_trouble(self):
        wd.urllib.request.urlopen = lambda *a, **k: _Resp(
            {"available": False, "reason": "no heartbeat"})
        ok, detail = wd.check_poller()
        self.assertTrue(ok)
        self.assertEqual(detail, "unavailable")

    def test_missing_route_is_not_trouble(self):
        def raise404(*a, **k):
            raise urllib.error.HTTPError("u", 404, "nf", {}, None)
        wd.urllib.request.urlopen = raise404
        ok, detail = wd.check_poller()
        self.assertTrue(ok)
        self.assertEqual(detail, "unavailable")

    def test_unreachable_server_is_not_trouble(self):
        def raise_url(*a, **k):
            raise urllib.error.URLError("down")
        wd.urllib.request.urlopen = raise_url
        ok, _ = wd.check_poller()
        self.assertTrue(ok)


class CheckCommitTests(unittest.TestCase):
    def test_recent_commit_is_healthy(self):
        d = (NOW - timedelta(days=3)).isoformat().replace("+00:00", "Z")
        wd._get_json = lambda url, timeout: [
            {"commit": {"committer": {"date": d}}}]
        ok, _ = wd.check_commit(NOW)
        self.assertTrue(ok)

    def test_idle_repo_is_trouble(self):
        d = (NOW - timedelta(days=58)).isoformat().replace("+00:00", "Z")
        wd._get_json = lambda url, timeout: [
            {"commit": {"committer": {"date": d}}}]
        ok, _ = wd.check_commit(NOW)
        self.assertFalse(ok)


class StateTests(unittest.TestCase):
    def test_state_round_trips(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            old = wd.STATE_PATH
            wd.STATE_PATH = os.path.join(d, "w.json")
            try:
                self.assertEqual(wd.load_state(), {})
                wd.save_state({"healthy": True, "x": 1})
                self.assertEqual(wd.load_state()["x"], 1)
            finally:
                wd.STATE_PATH = old


if __name__ == "__main__":
    unittest.main()
