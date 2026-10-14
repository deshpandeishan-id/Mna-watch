"""Command line entry point.

  python -m mna_watch once                 # one polling cycle
  python -m mna_watch loop                 # poll forever
  python -m mna_watch loop --minutes 55 --commit-state   # what GitHub Actions runs
  python -m mna_watch test-email           # send a test alert
  python -m mna_watch check-sources        # fetch each source once and report counts (no state, no email)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv=None) -> int:
    _load_dotenv()
    p = argparse.ArgumentParser(prog="mna_watch")
    p.add_argument("command", choices=["once", "loop", "test-email", "check-sources"])
    p.add_argument("--minutes", type=float, default=0, help="loop: stop after this many minutes (0 = forever)")
    p.add_argument("--commit-state", action="store_true", help="git-commit the state folder after running (GitHub Actions)")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)

    from .config import Settings
    s = Settings()

    if a.command == "test-email":
        from .notify import send_test
        send_test(s)
        return 0

    if a.command == "check-sources":
        from .sources import registry
        from .sources import rss
        names = list(dict.fromkeys(s.sources + list(rss.extra_feeds())))
        for name, (fetch, _) in registry(names).items():
            try:
                items = fetch(s)
                print(f"{name:14s} {len(items):4d} items" + (f"   e.g. {items[0].title[:80]}" if items else "   <- nothing returned (blocked or down?)"))
            except Exception as e:
                print(f"{name:14s} ERROR {e}")
        return 0

    from .pipeline import Runner
    r = Runner(s)
    if a.command == "once":
        r.cycle()
        if a.commit_state:
            from .pipeline import commit_state_to_git
            commit_state_to_git()
    else:
        r.run(max_minutes=a.minutes, commit_state=a.commit_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
