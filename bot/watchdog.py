#!/usr/bin/env python3
"""ASTRA watchdog: tell the user when the always-on bot stops being always-on.

Three silent failures are worth catching, none of which the trading jobs can
report on their own:

  * the bot stopped running — GitHub's best-effort cron dropped, a job started
    failing, or a workflow was disabled;
  * the web server is down — the phone app and the review endpoint go dark;
  * the repository went quiet for ~60 days, at which point GitHub disables the
    scheduled workflows outright and every alert stops with no warning.

It is a dead-man's switch: it alerts on the *transition* into trouble and again
on recovery, then re-alerts every few hours while the trouble persists, so a
missed first message is not a missed outage. Run it from a cron; it is one-shot.

Run: python3 bot/watchdog.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import telegram_bot as bot  # noqa: E402
import astra_env  # noqa: E402

ASTRA_URL = astra_env.URL
REPO = astra_env.REPO
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
HTTP_TIMEOUT = float(os.environ.get("ASTRA_WATCHDOG_TIMEOUT", "90"))
# The scanner's own cron is best-effort: GitHub delays it to the point where
# gaps of hours are routine (median ~28 min, but a 5-hour stall has happened).
# A threshold tight enough to catch a truly dead bot therefore fires false
# alarms whenever GitHub is merely slow. So the liveness signal is the
# keepalive chain instead — it re-arms itself and pings every 5 minutes, so a
# long stall there means something is genuinely broken. This stays as the
# secondary signal and only alerts on an unmistakable stall.
BOT_MAX_AGE_MIN = float(os.environ.get("ASTRA_BOT_MAX_AGE_MIN", "360"))
# The keepalive chain pings every 5 min and re-arms just before the 6h job cap.
# A gap beyond the cap means a re-arm was missed and the chain is dead.
KEEPALIVE_MAX_AGE_MIN = float(os.environ.get("ASTRA_KEEPALIVE_MAX_AGE_MIN", "400"))
# GitHub disables scheduled workflows after ~60 idle days; warn well before.
COMMIT_WARN_DAYS = float(os.environ.get("ASTRA_COMMIT_WARN_DAYS", "55"))
# While still in trouble, repeat the alert at most this often (hours).
REALERT_HOURS = float(os.environ.get("ASTRA_WATCHDOG_REALERT_HOURS", "6"))
STATE_PATH = os.environ.get("ASTRA_WATCHDOG_STATE",
                            os.path.join(ROOT, "run", "watchdog_state.json"))
_USER_AGENT = "ASTRA-watchdog/1.0"


def _get_json(url: str, timeout: float) -> dict | None:
    headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    if GH_TOKEN and "api.github.com" in url:
        headers["Authorization"] = "Bearer " + GH_TOKEN
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def check_server() -> tuple[bool, str]:
    """Is the live server answering? One retry, since a cold start is slow."""
    for _ in range(2):
        code = None
        try:
            req = urllib.request.Request(
                ASTRA_URL + "/api/v1/health",
                headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                code = resp.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        except (urllib.error.URLError, OSError):
            code = None
        if code == 200:
            return True, "OK"
    return False, "no 200 from /api/v1/health"


def check_engine() -> tuple[bool, str]:
    """Does the akoan engine actually serve data, not just answer 200?

    The bug this guards against shipped a route that returned HTTP 200 with
    {"available": false} — invisible to a plain status check. This reads the
    body and insists the engine produced a verdict. A 404 (older deploy without
    the route) or a connection error reads as unavailable and never cries wolf;
    a 401/403 means the access gate is on and this monitor had no token, which
    is a missing credential rather than a dead engine, so it also stays quiet.
    Any other failure is real trouble.
    """
    req = urllib.request.Request(
        ASTRA_URL + "/akoan.json",
        headers={"Accept": "application/json", "User-Agent": _USER_AGENT,
                 **astra_env.auth_headers()})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True, "unavailable"
        if exc.code in (401, 403):
            # The access gate is on but this monitor had no (or a wrong) token.
            # That is a missing credential, not a dead engine; crying wolf here
            # would page the operator every hour a secret is unset.
            return True, "unavailable (gate)"
        return False, "akoan route HTTP %d" % exc.code
    except (urllib.error.URLError, OSError, ValueError):
        return True, "unavailable"
    if body.get("available"):
        return True, str(body.get("verdict", "ok"))
    return False, "akoan returned available=false (%s)" % body.get("reason", "no reason")


def check_poller() -> tuple[bool, str]:
    """Is the interactive Telegram poller still alive?

    The bot long-polls getUpdates and writes a heartbeat every minute; the phone
    server exposes its age at /bot.json. A poller that stops is otherwise
    silent — the scanner check above only covers the GitHub cron. A stale beat
    is real trouble; a missing file or a 404 (an older deploy, or a host with no
    token) cannot be judged and never cries wolf.
    """
    req = urllib.request.Request(
        ASTRA_URL + "/bot.json",
        headers={"Accept": "application/json", "User-Agent": _USER_AGENT,
                 **astra_env.auth_headers()})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True, "unavailable"
        if exc.code in (401, 403):
            return True, "unavailable (gate)"  # see check_engine
        return False, "bot route HTTP %d" % exc.code
    except (urllib.error.URLError, OSError, ValueError):
        return True, "unavailable"
    if body.get("available"):
        return True, "%.0f min ago" % (body.get("age_s", 0) / 60.0)
    if body.get("reason") == "stale":
        return False, "no beat for %.0f min" % (body.get("age_s", 0) / 60.0)
    return True, "unavailable"


def _parse_utc(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def check_bot(now: datetime) -> tuple[bool, str]:
    """Has the scanner produced a successful run recently?

    Secondary signal only: the scanner rides GitHub's best-effort cron, which
    stalls for hours at a time, so this is deliberately loose and
    ``check_keepalive`` carries the real liveness.
    """
    url = ("https://api.github.com/repos/%s/actions/workflows/astra-bot.yml/"
           "runs?per_page=10" % REPO)
    payload = _get_json(url, 30)
    if not payload:
        return True, "unavailable"  # cannot judge: never cry wolf on this
    for run in payload.get("workflow_runs", []):
        if run.get("conclusion") == "success":
            when = _parse_utc(run.get("updated_at", ""))
            if when is None:
                return True, "unavailable"
            age_min = (now - when).total_seconds() / 60.0
            if age_min > BOT_MAX_AGE_MIN:
                return False, "last success %.0f min ago" % age_min
            return True, "%.0f min ago" % age_min
    return False, "no successful run on record"


def check_keepalive(now: datetime) -> tuple[bool, str]:
    """Is the self-re-arming keepalive chain still alive?

    This is the real liveness signal. The chain holds one long run that pings
    every 5 minutes and dispatches its successor just before the 6-hour cap, so
    a healthy chain always has a run either in progress or finished within the
    cap. A completed latest run older than the cap means a re-arm was dropped
    and the Render service is no longer being kept awake.
    """
    url = ("https://api.github.com/repos/%s/actions/workflows/"
           "astra-keepalive.yml/runs?per_page=5" % REPO)
    payload = _get_json(url, 30)
    if not payload or not payload.get("workflow_runs"):
        return True, "unavailable"  # cannot judge: never cry wolf on this
    latest = payload["workflow_runs"][0]
    if latest.get("status") in ("queued", "in_progress", "waiting", "pending"):
        return True, "running"
    when = _parse_utc(latest.get("updated_at", ""))
    if when is None:
        return True, "unavailable"
    age_min = (now - when).total_seconds() / 60.0
    if age_min > KEEPALIVE_MAX_AGE_MIN:
        return False, "chain last ran %.0f min ago" % age_min
    return True, "%.0f min ago" % age_min


def check_commit(now: datetime) -> tuple[bool, str]:
    """Is the repository still active enough to keep the crons enabled?"""
    payload = _get_json(
        "https://api.github.com/repos/%s/commits?per_page=1" % REPO, 30)
    if not isinstance(payload, list) or not payload:
        return True, "unavailable"
    date = _parse_utc(payload[0].get("commit", {}).get("committer", {}).get("date", ""))
    if date is None:
        return True, "unavailable"
    days = (now - date).total_seconds() / 86400.0
    if days > COMMIT_WARN_DAYS:
        return False, "no commit in %.0f days" % days
    return True, "%.0f days" % days


def evaluate(now: datetime | None = None) -> dict:
    """Run every check and collect the problems, in plain Arabic-ready parts."""
    now = now or datetime.now(timezone.utc)
    problems: list[dict] = []
    ok, detail = check_bot(now)
    if not ok:
        problems.append({"key": "bot", "ar": "توقّف البوت عن الفحص", "detail": detail})
    ok, detail = check_keepalive(now)
    if not ok:
        problems.append({"key": "keepalive",
                         "ar": "انقطعت سلسلة الإبقاء على السيرفر صاحي",
                         "detail": detail})
    ok, detail = check_server()
    if not ok:
        problems.append({"key": "server", "ar": "السيرفر ما عم يستجيب", "detail": detail})
    ok, detail = check_engine()
    if not ok:
        problems.append({"key": "engine",
                         "ar": "محرك الخبر بيرجّع فاضي (بيانات غير متاحة)",
                         "detail": detail})
    ok, detail = check_poller()
    if not ok:
        problems.append({"key": "poller",
                         "ar": "بوت التلغرام ما عم يستجيب (توقّف عن الدوران)",
                         "detail": detail})
    ok, detail = check_commit(now)
    if not ok:
        problems.append({"key": "commit",
                         "ar": "المستودع صار هادي (GitHub بوقف التنبيهات بعد ~60 يوم)",
                         "detail": detail})
    return {"healthy": not problems, "problems": problems}


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass


def decide(verdict: dict, state: dict, now: datetime) -> str | None:
    """The message to send, or None. Alerts on transition and on re-alert."""
    was_healthy = state.get("healthy", True)
    last_alert = state.get("last_alert_at")
    if verdict["healthy"]:
        return "✅ رجع كل شي يشتغل" if not was_healthy else None
    lines = ["🚨 في خلل بتشغيل ASTRA"]
    for p in verdict["problems"]:
        lines.append("• %s (%s)" % (p["ar"], p["detail"]))
    lines.append("افحص المستودع أو السيرفر.")
    if was_healthy:
        return "\n".join(lines)
    # Still down: re-alert only every few hours, so a long outage is not a
    # single easily-missed message.
    if last_alert is None:
        return "\n".join(lines)
    elapsed_h = (now - datetime.fromtimestamp(last_alert, timezone.utc)).total_seconds() / 3600.0
    return "\n".join(lines) if elapsed_h >= REALERT_HOURS else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA watchdog")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the verdict instead of sending")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    verdict = evaluate(now)
    state = load_state()
    message = decide(verdict, state, now)
    print(json.dumps({"healthy": verdict["healthy"],
                      "problems": [p["key"] for p in verdict["problems"]],
                      "alert": bool(message)}, ensure_ascii=False))

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")

    if message and args.dry_run:
        print(message)
        return 0
    if message and token and chat:
        bot.Telegram(token).send(chat, message)
        state["last_alert_at"] = int(now.timestamp())

    state["healthy"] = verdict["healthy"]
    if verdict["healthy"]:
        state.pop("last_alert_at", None)
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
