#!/usr/bin/env python3
"""Send a notification to Discord.
Silent no-op if coordination/notify/config is missing."""
import json
import pathlib
import sys
import urllib.request


def main():
    msg = " ".join(sys.argv[1:]) or "(no message)"
    cfg = pathlib.Path(__file__).parent / "config"
    if not cfg.exists():
        return 0
    url = ""
    for line in cfg.read_text().splitlines():
        if line.startswith("WEBHOOK_URL="):
            url = line.split("=", 1)[1].strip()
    if not url:
        return 0
    data = json.dumps({
        "content": msg[:1900],
        "allowed_mentions": {"parse": []},
    }).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:  # noqa: BLE001
        print(f"notify: failed ({type(e).__name__})",
              file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
