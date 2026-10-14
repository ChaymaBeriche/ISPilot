import os
from langchain.tools import tool
from mysql.connector import pooling, Error as MySQLError

# ─────────────────────────────────────────────
# Structured error codes
# ─────────────────────────────────────────────
ERR_NOT_FOUND     = "[NOT_FOUND]"
ERR_ALREADY_EXISTS = "[ALREADY_EXISTS]"
ERR_RADIUS_ERROR  = "[RADIUS_ERROR]"
ERR_DB_ERROR      = "[DB_ERROR]"

# ─────────────────────────────────────────────
# Connection pool — one pool shared across all tool calls
# Avoids opening a new MySQL connection on every request
# ─────────────────────────────────────────────
_pool = pooling.MySQLConnectionPool(
    pool_name="radius_pool",
    pool_size=5,
    host=os.getenv("MYSQL_HOST", "radius-mysql"),
    user=os.getenv("MYSQL_USER", "radius"),
    password=os.environ["MYSQL_PASSWORD"],
    database=os.getenv("MYSQL_DB", "radius"),
    connection_timeout=5,
    connect_timeout=5,
)


def get_db():
    """Get a connection from the pool. Always use in a try/finally block."""
    return _pool.get_connection()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

# Reusable WHERE clause for open RADIUS sessions
# Handles both NULL and legacy MySQL zero-date values
OPEN_SESSION_WHERE = """
    (acctstoptime IS NULL
     OR CAST(acctstoptime AS CHAR(19)) = '0000-00-00 00:00:00')
"""


def _disable_strict(cursor):
    """
    Disable MySQL strict mode for this session.
    Required to handle legacy zero-date values in radacct without errors.
    Call this before ANY query that touches radacct.
    """
    cursor.execute("SET SESSION sql_mode = 'NO_ENGINE_SUBSTITUTION'")


def _user_exists(cursor, username: str) -> bool:
    """Check if a user exists in radcheck with a Cleartext-Password entry."""
    cursor.execute(
        "SELECT 1 FROM radcheck WHERE username = %s AND attribute = 'Cleartext-Password'",
        (username,)
    )
    return cursor.fetchone() is not None


# ─────────────────────────────────────────────
# ADMIN tools (not exposed to support agent)
# ─────────────────────────────────────────────

@tool
def list_radius_users(limit: int = 10) -> str:
    """
    List users registered in the RADIUS database with their assigned group/plan.
    Admin tool — not for customer support sessions.
    """
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute("""
            SELECT r.username, g.groupname
            FROM radcheck r
            LEFT JOIN radusergroup g ON r.username = g.username
            WHERE r.attribute = 'Cleartext-Password'
            LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        if not rows:
            return "No users found."
        result = [f"- {row[0]} (group: {row[1] or 'none'})" for row in rows]
        return f"Found {len(rows)} users:\n" + "\n".join(result)
    except MySQLError as e:
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


@tool
def add_radius_user(username: str, password: str, group: str = "basic") -> str:
    """
    Create a new user account in the RADIUS database.
    Admin tool — not for customer support sessions.
    """
    db = get_db()
    try:
        cursor = db.cursor()

        # Prevent duplicate username
        if _user_exists(cursor, username):
            return f"{ERR_ALREADY_EXISTS} User '{username}' already exists in RADIUS."

        cursor.execute(
            "INSERT INTO radcheck (username, attribute, op, value) "
            "VALUES (%s, 'Cleartext-Password', ':=', %s)",
            (username, password)
        )
        cursor.execute(
            "INSERT INTO radusergroup (username, groupname, priority) VALUES (%s, %s, 1)",
            (username, group)
        )
        db.commit()
        return f"User '{username}' added to group '{group}' successfully."
    except MySQLError as e:
        db.rollback()
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


@tool
def delete_radius_user(username: str) -> str:
    """
    Remove a user account and all associated RADIUS records.
    Admin tool — not for customer support sessions.
    Irreversible — use with caution.
    """
    db = get_db()
    try:
        cursor = db.cursor()

        # Verify user exists before attempting delete
        if not _user_exists(cursor, username):
            return f"{ERR_NOT_FOUND} User '{username}' not found in RADIUS."

        cursor.execute("DELETE FROM radcheck WHERE username = %s", (username,))
        cursor.execute("DELETE FROM radusergroup WHERE username = %s", (username,))
        cursor.execute("DELETE FROM radreply WHERE username = %s", (username,))
        db.commit()
        return f"User '{username}' and all associated records deleted successfully."
    except MySQLError as e:
        db.rollback()
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


# ─────────────────────────────────────────────
# SUPPORT AGENT tools — read only
# ─────────────────────────────────────────────

@tool
def get_radius_user(username: str) -> str:
    """
    Retrieve a RADIUS user's account details including group/plan,
    authentication attributes, and reply attributes (quotas, session limits).
    Use at the start of any support session to understand the customer's plan.
    Password is masked for security.
    """
    db = get_db()
    try:
        cursor = db.cursor()

        cursor.execute(
            "SELECT attribute, op, value FROM radcheck WHERE username = %s",
            (username,)
        )
        checks = cursor.fetchall()

        if not checks:
            return f"{ERR_NOT_FOUND} User '{username}' not found in RADIUS."

        cursor.execute(
            "SELECT groupname FROM radusergroup WHERE username = %s",
            (username,)
        )
        groups = cursor.fetchall()

        cursor.execute(
            "SELECT attribute, value FROM radreply WHERE username = %s",
            (username,)
        )
        replies = cursor.fetchall()

        result = [f"Username: {username}"]

        # Mask password — agent should never see or expose plaintext credentials
        result += [
            f"Check - {a} {o} {'***' if a == 'Cleartext-Password' else v}"
            for a, o, v in checks
        ]
        result += [f"Group: {g[0]}" for g in groups]
        result += [f"Reply - {a}: {v}" for a, v in replies]

        return "\n".join(result)
    except MySQLError as e:
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


# ─────────────────────────────────────────────
# SUPPORT AGENT tools — interventions
# ─────────────────────────────────────────────

@tool
def change_user_group(username: str, new_group: str) -> str:
    """
    Change a customer's subscription plan/group in RADIUS.
    Use when upgrading or downgrading a customer's service plan.
    Requires explicit customer confirmation before executing — affects billing.
    The new group must already exist in radgroupcheck/radgroupreply.
    """
    db = get_db()
    try:
        cursor = db.cursor()

        # Verify user exists first
        if not _user_exists(cursor, username):
            return f"{ERR_NOT_FOUND} User '{username}' not found in RADIUS."

        # Fetch current group for audit trail
        cursor.execute(
            "SELECT groupname FROM radusergroup WHERE username = %s",
            (username,)
        )
        current = cursor.fetchone()
        current_group = current[0] if current else "none"

        if current_group == new_group:
            return (
                f"User '{username}' is already in group '{new_group}'. "
                "No change made."
            )

        cursor.execute(
            "UPDATE radusergroup SET groupname = %s WHERE username = %s",
            (new_group, username)
        )

        if cursor.rowcount == 0:
            # User exists in radcheck but not in radusergroup — insert instead
            cursor.execute(
                "INSERT INTO radusergroup (username, groupname, priority) VALUES (%s, %s, 1)",
                (username, new_group)
            )

        db.commit()
        return (
            f"Plan updated for '{username}':\n"
            f"Previous group: {current_group}\n"
            f"New group: {new_group}\n"
            "The new plan will take effect on next session reconnection."
        )
    except MySQLError as e:
        db.rollback()
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


@tool
def change_radius_password(username: str, new_password: str) -> str:
    """
    Update a customer's PPPoE/RADIUS password in the authentication database.
    Use when diagnosing PPPoE auth failures (ERROR_AUTHENTICATION_FAILURE)
    or when a customer has forgotten their password.
    After changing, always follow up with push_pppoe_credentials to sync
    the new password to the physical router via TR-069.
    """
    db = get_db()
    try:
        cursor = db.cursor()

        if not _user_exists(cursor, username):
            return f"{ERR_NOT_FOUND} User '{username}' not found in RADIUS."

        cursor.execute(
            "UPDATE radcheck SET value = %s "
            "WHERE username = %s AND attribute = 'Cleartext-Password'",
            (new_password, username)
        )
        db.commit()
        return (
            f"Password updated for '{username}' in RADIUS.\n"
            "Next step: call push_pppoe_credentials to sync this new password "
            "to the customer's router via TR-069."
        )
    except MySQLError as e:
        db.rollback()
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


@tool
def disconnect_session(username: str) -> str:
    """
    Terminate stuck or zombie RADIUS sessions for a customer.
    Use when:
    - Customer cannot connect because RADIUS sees them as already online
    - A session has been open for an abnormally long time with zero traffic
    - A power cut left an unclosed accounting session in radacct
    - Last session disconnect reason was ERROR_ISP_TIME_OUT

    Closes the open session(s) in the DB so the customer can reconnect.
    Does NOT affect the customer's account, password, or plan.
    """
    db = get_db()
    try:
        cursor = db.cursor()

        # Must disable strict mode before ANY radacct query
        # to handle legacy zero-date values without errors
        _disable_strict(cursor)

        # Verify user exists
        if not _user_exists(cursor, username):
            return f"{ERR_NOT_FOUND} User '{username}' not found in RADIUS."

        # Find all open sessions
        cursor.execute(f"""
            SELECT acctsessionid, nasipaddress, framedipaddress,
                   acctstarttime, acctinputoctets, acctoutputoctets
            FROM radacct
            WHERE username = %s
              AND {OPEN_SESSION_WHERE}
        """, (username,))
        open_sessions = cursor.fetchall()

        if not open_sessions:
            return (
                f"No open sessions found for '{username}'. "
                "The account is not currently marked as online in RADIUS. "
                "The connection issue may be on the device side — check GenieACS device status."
            )

        session_details = "\n".join(
            f"  - Session {row[0]} | NAS: {row[1]} | IP: {row[2]} | "
            f"Started: {row[3]} | In: {row[4]} bytes | Out: {row[5]} bytes"
            for row in open_sessions
        )

        # Close all open sessions with Admin-Reset cause
        cursor.execute(f"""
            UPDATE radacct
            SET acctstoptime       = NOW(),
                acctsessiontime    = TIMESTAMPDIFF(SECOND, acctstarttime, NOW()),
                acctterminatecause = 'Admin-Reset'
            WHERE username = %s
              AND {OPEN_SESSION_WHERE}
        """, (username,))

        affected = cursor.rowcount
        db.commit()

        return (
            f"Disconnected {affected} open session(s) for '{username}':\n"
            f"{session_details}\n\n"
            "Sessions closed with cause: Admin-Reset.\n"
            "The customer can now attempt to reconnect. "
            "If reconnection still fails, check their device status in GenieACS "
            "or verify their RADIUS password with get_radius_user."
        )
    except MySQLError as e:
        db.rollback()
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


@tool
def reset_daily_quota(username: str) -> str:
    """
    Reset the daily quota for a customer whose session was terminated
    due to Session-Timeout (plan quota exhausted).
    Use when:
    - The last session disconnect reason was Session-Timeout
    - The customer explicitly requests a quota reset
    - The customer confirms they understand this uses their next day's quota early

    Removes the Session-Timeout reply attribute so the customer gets
    a fresh session. The attribute is re-added automatically at midnight
    by the billing system.
    """
    db = get_db()
    try:
        cursor = db.cursor()

        if not _user_exists(cursor, username):
            return f"{ERR_NOT_FOUND} User '{username}' not found in RADIUS."

        # Check if they actually have a quota limit set
        cursor.execute("""
            SELECT attribute, value FROM radreply
            WHERE username = %s
              AND attribute IN ('Session-Timeout', 'Max-All-Session')
        """, (username,))
        quota_rows = cursor.fetchall()

        if not quota_rows:
            return (
                f"No quota limit found for '{username}'. "
                "Their plan does not have a daily Session-Timeout restriction. "
                "If they cannot connect, check for other causes: "
                "auth failure, device issue, or account suspension."
            )

        quota_info = ", ".join(f"{a}={v}" for a, v in quota_rows)

        # Remove quota attribute to grant fresh session
        cursor.execute("""
            DELETE FROM radreply
            WHERE username = %s
              AND attribute IN ('Session-Timeout', 'Max-All-Session')
        """, (username,))

        db.commit()
        return (
            f"Daily quota reset for '{username}'.\n"
            f"Removed quota attributes: {quota_info}\n"
            "The customer can now reconnect with a fresh session.\n"
            "The quota limit will be automatically restored at midnight "
            "by the billing cycle.\n"
            "If the customer needs a permanent plan upgrade, "
            "use change_user_group to move them to a higher tier."
        )
    except MySQLError as e:
        db.rollback()
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()