"""
seed_utils.py — shared MySQL reseed helper for the ISPilot test suite.

The RADIUS/MySQL seed data's timestamps are NOW()-relative
(DATE_SUB(NOW(), INTERVAL ...) in mysql/seed.sql), but
docker-entrypoint-initdb.d only runs seed.sql once, against an empty data
directory — after that, those "recent" auth-failure / session records
quietly go stale as real time passes, since get_auth_failures() and
get_session_history() check a rolling time window (e.g. "last 24 hours").

reseed() re-runs seed.sql (refreshing every NOW()-relative timestamp to the
current moment — seed.sql truncates its own tables first, so it's safe to
re-run any number of times). run_tests.py, test_scenarios.py, and
test_scenarios_extended.py all call this before running scenarios, so none
of them can be run against stale seed data — regardless of which one is
invoked directly.
"""

import os
import subprocess
import sys

REPO_ROOT       = os.path.dirname(os.path.abspath(__file__))
MYSQL_CONTAINER = "radius-mysql"
MYSQL_USER      = "root"
MYSQL_PASSWORD  = os.environ["MYSQL_ROOT_PASSWORD"]   # set in .env (see .env.example)
MYSQL_DB        = "radius"
SEED_FILE       = os.path.join(REPO_ROOT, "mysql", "seed.sql")

# Set by run_tests.py on the subprocess env when the user passes
# --no-reseed, so its debugging-only skip actually propagates instead of
# being immediately undone by test_scenarios[_extended].py's own reseed.
SKIP_RESEED_ENV = "ISPILOT_SKIP_RESEED"


def reseed():
    print("=" * 60)
    print("  Re-seeding radius DB (refreshes NOW()-relative timestamps)")
    print("=" * 60)
    with open(SEED_FILE, "rb") as f:
        seed_sql = f.read()
    result = subprocess.run(
        [
            "docker", "exec", "-i", MYSQL_CONTAINER, "mysql",
            f"-u{MYSQL_USER}", f"-p{MYSQL_PASSWORD}", MYSQL_DB,
        ],
        input=seed_sql,
        capture_output=True,
    )
    if result.stdout:
        print(result.stdout.decode(errors="replace"))
    if result.returncode != 0:
        print(result.stderr.decode(errors="replace"), file=sys.stderr)
        sys.exit(f"Reseed failed (exit {result.returncode})")
    print("Reseed complete.\n")


def reseed_unless_skipped():
    """
    Reseed the DB, unless ISPILOT_SKIP_RESEED=1 is set in the environment.

    Called unconditionally at the top of test_scenarios.py's and
    test_scenarios_extended.py's __main__ block (before any scenario runs)
    so that running either script directly can never see stale seed data.
    """
    if os.environ.get(SKIP_RESEED_ENV) == "1":
        print("Skipping reseed (ISPILOT_SKIP_RESEED=1 set by caller).\n")
        return
    reseed()


if __name__ == "__main__":
    reseed()
