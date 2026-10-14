"""
run_tests.py — standard way to run the test suite.

The RADIUS/MySQL seed data's timestamps are NOW()-relative
(DATE_SUB(NOW(), INTERVAL ...) in mysql/seed.sql), but
docker-entrypoint-initdb.d only runs seed.sql once, against an empty data
directory — after that, those "recent" auth-failure / session records
quietly go stale as real time passes, since get_auth_failures() and
get_session_history() check a rolling time window (e.g. "last 24 hours").

This script re-runs seed.sql (refreshing every NOW()-relative timestamp to
the current moment — seed.sql truncates its own tables first, so it's safe
to re-run any number of times) immediately before running both test suites,
so tests are never run against stale data again.

test_scenarios.py and test_scenarios_extended.py also reseed themselves
(via seed_utils.reseed_unless_skipped()) at the top of their own __main__
block, so running either of them directly — without going through this
script — is equally safe. --no-reseed here propagates to those subprocesses
via ISPILOT_SKIP_RESEED so it isn't silently undone by their own reseed.

Usage:
    python run_tests.py                  # reseed + run both suites (standard)
    python run_tests.py --no-reseed       # skip reseed (debugging only)
    python run_tests.py --base-only       # only test_scenarios.py
    python run_tests.py --extended-only   # only test_scenarios_extended.py
"""

import argparse
import os
import subprocess
import sys

import seed_utils

REPO_ROOT = seed_utils.REPO_ROOT


def run_suite(script: str, no_reseed: bool) -> int:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"  # avoid cp1252 crashes on Windows consoles
    if no_reseed:
        env[seed_utils.SKIP_RESEED_ENV] = "1"
    print("=" * 60)
    print(f"  Running {script}")
    print("=" * 60)
    result = subprocess.run([sys.executable, script, "--save"], env=env, cwd=REPO_ROOT)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description="Reseed the DB, then run the test suite")
    parser.add_argument("--no-reseed", action="store_true", help="skip re-seeding the database")
    parser.add_argument("--base-only", action="store_true", help="only run test_scenarios.py")
    parser.add_argument("--extended-only", action="store_true", help="only run test_scenarios_extended.py")
    args = parser.parse_args()

    if args.no_reseed:
        print("Skipping reseed (--no-reseed passed).\n")
    else:
        seed_utils.reseed()

    exit_codes = []
    if not args.extended_only:
        exit_codes.append(run_suite("test_scenarios.py", args.no_reseed))
    if not args.base_only:
        exit_codes.append(run_suite("test_scenarios_extended.py", args.no_reseed))

    sys.exit(1 if any(code != 0 for code in exit_codes) else 0)


if __name__ == "__main__":
    main()
