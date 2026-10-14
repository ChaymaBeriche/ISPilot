import os
import random
import string
from langchain_core.tools import tool
from datetime import datetime
from mysql.connector import pooling, Error as MySQLError

# ─────────────────────────────────────────────
# Structured error codes
# ─────────────────────────────────────────────
ERR_NOT_FOUND = "[NOT_FOUND]"
ERR_DB_ERROR  = "[DB_ERROR]"

# ─────────────────────────────────────────────
# Connection pool — shared with radius_tools
# Same DB, same pool settings for consistency
# ─────────────────────────────────────────────
_pool = pooling.MySQLConnectionPool(
    pool_name="isp_pool",
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

def _disable_strict(cursor):
    """
    Disable MySQL strict mode for this session.
    Required before any query touching radacct (zero-date legacy values).
    """
    cursor.execute("SET SESSION sql_mode = 'NO_ENGINE_SUBSTITUTION'")


# Region mapping — city (last word of address) → ISP region
CITY_TO_REGION = {
    # Nord-Est
    "tunis": "Nord-Est", "ariana": "Nord-Est", "arous": "Nord-Est",
    "manouba": "Nord-Est", "nabeul": "Nord-Est", "zaghouan": "Nord-Est",
    "hammamet": "Nord-Est", "grombalia": "Nord-Est",
    # Nord-Ouest
    "bizerte": "Nord-Ouest", "béja": "Nord-Ouest", "beja": "Nord-Ouest",
    "jendouba": "Nord-Ouest", "kef": "Nord-Ouest", "siliana": "Nord-Ouest",
    # Centre-Est
    "sousse": "Centre-Est", "monastir": "Centre-Est", "mahdia": "Centre-Est",
    "kairouan": "Centre-Est",
    # Centre-Ouest
    "kasserine": "Centre-Ouest", "sidi": "Centre-Ouest", "gafsa": "Centre-Ouest",
    "sfax": "Centre-Ouest",
    # Sud
    "gabès": "Sud", "gabes": "Sud", "médenine": "Sud", "medenine": "Sud",
    "tataouine": "Sud", "tozeur": "Sud", "kébili": "Sud", "kebili": "Sud",
}

# Valid account statuses with agent-facing messages
ACCOUNT_STATUS_MESSAGES = {
    "active": lambda name: f"Compte actif. Le client {name} est en règle.",
    "suspended": lambda name: (
        f"Compte suspendu. Le client {name} a un impayé ou une suspension administrative. "
        "Rediriger vers le service facturation au 71 XXX XXX."
    ),
    "expired": lambda name: (
        f"Compte expiré. L'abonnement de {name} n'a pas été renouvelé. "
        "Rediriger vers le service commercial."
    ),
    "pending": lambda name: (
        f"Compte en attente d'activation pour {name}. "
        "Le compte n'est pas encore actif — contacter le service technique."
    ),
    "cancelled": lambda name: (
        f"Compte résilié. Le contrat de {name} a été annulé. "
        "Rediriger vers le service commercial si le client souhaite se réabonner."
    ),
}

# Valid ticket problem types for normalization
VALID_PROBLEM_TYPES = [
    "Problème débit",
    "Déconnexion physique",
    "Réinitialisation credentials",
    "Quota épuisé",
    "Firmware obsolète",
    "Problème MTU",
    "Panne régionale",
    "Compte suspendu",
    "Problème de connexion persistant",
]


def extract_city(address: str) -> str:
    """Extract city from address (last word, lowercased)."""
    if not address:
        return ""
    return address.strip().split()[-1].lower()


def city_to_region(city: str) -> str:
    """Map city name to ISP region."""
    return CITY_TO_REGION.get(city.lower(), "Inconnue")


def _normalize_problem_type(problem_type: str) -> str:
    """
    Normalize problem_type to closest valid type.
    Returns original if no match found (permissive fallback).
    """
    pt_lower = problem_type.lower()
    for valid in VALID_PROBLEM_TYPES:
        if any(word in pt_lower for word in valid.lower().split()):
            return valid
    return problem_type  # keep original if no match


# ─────────────────────────────────────────────
# Tools
# ─────────────────────────────────────────────

@tool
def find_customer(identifier: str) -> str:
    """
    Find a customer by their CIN or phone number.
    Returns complete account information including username, serial number,
    address, city, ISP region, and account status.
    This is the entry point of every support session — call this first.
    """
    db = get_db()
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM users WHERE cin = %s OR phone = %s",
            (identifier, identifier)
        )
        user = cursor.fetchone()
        if not user:
            return f"{ERR_NOT_FOUND} Aucun client trouvé avec l'identifiant '{identifier}'."

        serial = user.get("serial_number") or None
        city   = extract_city(user.get("address", ""))
        region = city_to_region(city)

        # Flag unassigned serial explicitly so agent knows GenieACS tools won't work
        if serial:
            serial_line = f"- Serial number: {serial}"
        else:
            serial_line = (
                "- Serial number: NON ASSIGNÉ "
                "(aucun routeur associé à ce compte — les outils GenieACS ne sont pas disponibles)"
            )

        return (
            f"Client trouvé:\n"
            f"- Nom: {user['full_name']}\n"
            f"- CIN: {user['cin']}\n"
            f"- Téléphone: {user['phone']}\n"
            f"- Email: {user['email']}\n"
            f"- Adresse: {user['address']}\n"
            f"- Ville: {city.capitalize()}\n"
            f"- Région ISP: {region}\n"
            f"- Username: {user['username']}\n"
            f"{serial_line}\n"
            f"- Statut du compte: {user['status']}"
        )
    except MySQLError as e:
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


@tool
def check_account_status(username: str) -> str:
    """
    Check the financial and administrative status of a customer account.
    Always call this immediately after identifying the customer.
    If account is suspended/expired/cancelled, stop technical diagnosis —
    redirect to billing or commercial service instead.
    """
    db = get_db()
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT status, full_name FROM users WHERE username = %s",
            (username,)
        )
        user = cursor.fetchone()
        if not user:
            return f"{ERR_NOT_FOUND} Aucun compte trouvé pour le username '{username}'."

        status = user["status"]
        name   = user["full_name"]

        message_fn = ACCOUNT_STATUS_MESSAGES.get(status)
        if message_fn:
            return message_fn(name)
        return f"Statut du compte de {name}: {status}."

    except MySQLError as e:
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


@tool
def check_region_outage(city: str) -> str:
    """
    Check if there is an active outage affecting the customer's region.
    Always call this before starting technical diagnosis.
    If an outage is active, stop all technical actions and inform the customer —
    rebooting or reconfiguring devices is pointless during a regional outage.
    """
    region = city_to_region(city.lower())
    if region == "Inconnue":
        return (
            f"Ville '{city}' non reconnue dans la carte des régions ISP. "
            "Vérifiez l'orthographe ou consultez manuellement le tableau de bord réseau."
        )

    db = get_db()
    try:
        cursor = db.cursor(dictionary=True)
        # Fixed: explicit parentheses around OR to ensure correct precedence
        cursor.execute(
            """
            SELECT * FROM outages
            WHERE status = 'active'
              AND (
                FIND_IN_SET(%s, REPLACE(governorates, ', ', ',')) > 0
                OR region = %s
              )
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (city.capitalize(), region)
        )
        outage = cursor.fetchone()
        if not outage:
            return (
                f"Aucune panne active détectée dans la région {region} "
                f"(ville: {city.capitalize()})."
            )

        estimated = outage.get("estimated_fix")
        if estimated:
            if isinstance(estimated, datetime):
                estimated_str = estimated.strftime("%d/%m/%Y à %Hh%M")
            else:
                estimated_str = str(estimated)
        else:
            estimated_str = "non défini"

        return (
            f"PANNE ACTIVE dans la région {region}:\n"
            f"- Description: {outage['description']}\n"
            f"- Gouvernorats affectés: {outage['governorates']}\n"
            f"- Délai estimé de résolution: {estimated_str}\n"
            f"- Signalée le: {outage['created_at']}"
        )
    except MySQLError as e:
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


@tool
def get_session_history(username: str) -> str:
    """
    Retrieve the customer's RADIUS session history from the last 7 days.
    Shows disconnect causes (Lost-Carrier, Session-Timeout, Admin-Reset, etc.)
    to identify patterns: frequent disconnects, line instability, quota exhaustion.
    Call this as part of the mandatory 5-tool diagnostic sequence.
    """
    db = get_db()
    try:
        cursor = db.cursor(dictionary=True)

        # Disable strict mode before querying radacct (zero-date legacy values)
        _disable_strict(cursor)

        cursor.execute(
            """
            SELECT acctstarttime, acctstoptime, acctsessiontime,
                   acctterminatecause, framedipaddress
            FROM radacct
            WHERE username = %s
              AND acctstarttime >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            ORDER BY acctstarttime DESC
            LIMIT 20
            """,
            (username,)
        )
        rows = cursor.fetchall()
        if not rows:
            return (
                f"Aucun historique de session trouvé pour '{username}' "
                "sur les 7 derniers jours. "
                "Le client n'a peut-être jamais été connecté ou "
                "le compte est récemment créé."
            )

        lines = [f"Historique de sessions pour '{username}' (7 derniers jours):"]
        cause_count = {}

        for r in rows:
            cause    = r.get("acctterminatecause") or "En cours"
            duration = r.get("acctsessiontime") or 0
            cause_count[cause] = cause_count.get(cause, 0) + 1
            lines.append(
                f"  - Début: {r['acctstarttime']} | "
                f"Durée: {duration // 60}min | "
                f"Cause fin: {cause}"
            )

        # Lead with the summary so agent sees the pattern immediately
        summary = ", ".join(f"{cause}: {count}x" for cause, count in cause_count.items())
        lines.append(f"\nRésumé des causes de déconnexion: {summary}")

        # Flag key patterns explicitly for agent decision-making
        if cause_count.get("Lost-Carrier", 0) >= 3:
            lines.append(
                "⚠️ Lost-Carrier répété — probable problème physique de ligne "
                "ou firmware obsolète."
            )
        if cause_count.get("Session-Timeout", 0) >= 1:
            lines.append(
                "⚠️ Session-Timeout détecté — quota journalier probablement épuisé."
            )
        if cause_count.get("Admin-Reset", 0) >= 1:
            lines.append(
                "ℹ️ Admin-Reset présent — session déconnectée manuellement par le support."
            )

        return "\n".join(lines)
    except MySQLError as e:
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


@tool
def get_auth_failures(username: str) -> str:
    """
    Retrieve RADIUS authentication failures (Access-Reject) from the last 24 hours.
    Use to diagnose PPPoE credential mismatches.
    3+ recent failures strongly suggest a password mismatch between
    the router and RADIUS — follow with change_radius_password + push_pppoe_credentials.
    """
    db = get_db()
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT authdate, reply
            FROM radpostauth
            WHERE username = %s
              AND reply = 'Access-Reject'
              AND authdate >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            ORDER BY authdate DESC
            LIMIT 20
            """,
            (username,)
        )
        rows = cursor.fetchall()
        if not rows:
            return (
                f"Aucun échec d'authentification pour '{username}' "
                "dans les dernières 24 heures. "
                "La cause de déconnexion n'est pas un problème de credentials."
            )

        first = rows[-1]["authdate"]
        last  = rows[0]["authdate"]

        lines = [
            f"{len(rows)} échec(s) d'authentification (Access-Reject) pour '{username}':",
            f"  Premier échec: {first}",
            f"  Dernier échec: {last}",
        ]
        for r in rows:
            lines.append(f"  - {r['authdate']}")

        if len(rows) >= 3:
            lines.append(
                "\n⚠️ 3+ rejets récents — forte probabilité de mismatch credentials. "
                "Action recommandée: change_radius_password + push_pppoe_credentials."
            )

        return "\n".join(lines)
    except MySQLError as e:
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


@tool
def get_group_plan_specs(groupname: str) -> str:
    """
    Retrieve the bandwidth specification for a RADIUS plan GROUP itself
    (e.g. 'premium_users', 'basic_users'), as opposed to get_plan_info
    which looks up a specific customer's own plan. Use this whenever the
    agent needs to state what another plan (one the customer doesn't
    currently have, e.g. during an upgrade offer) actually provides —
    never guess or recall a plausible-sounding number for a plan the
    customer isn't on; always call this instead.
    """
    db = get_db()
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT attribute, op, value FROM radgroupreply WHERE groupname = %s",
            (groupname,)
        )
        attrs = cursor.fetchall()
        if not attrs:
            return f"{ERR_NOT_FOUND} Aucun forfait nommé '{groupname}' trouvé."

        download = upload = None
        for a in attrs:
            attr = a["attribute"].lower()
            if "down" in attr:
                download = a["value"]
            if "up" in attr:
                upload = a["value"]

        def _to_mbps(v):
            try:
                return f"{int(v) / 1_000_000:g} Mbps"
            except (TypeError, ValueError):
                return v

        lines = [f"Forfait '{groupname}':"]
        if download:
            lines.append(f"- Débit max download: {_to_mbps(download)}")
        if upload:
            lines.append(f"- Débit max upload: {_to_mbps(upload)}")
        return "\n".join(lines)
    except MySQLError as e:
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


@tool
def get_plan_info(username: str) -> str:
    """
    Retrieve customer subscription plan details and bandwidth limits from RADIUS.
    Use to compare expected bandwidth against real measured performance.
    Prevents false 'slow internet' diagnosis when speed matches the plan limit.
    Includes both group-level attributes and any personal overrides.
    """
    db = get_db()
    try:
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            "SELECT groupname FROM radusergroup WHERE username = %s",
            (username,)
        )
        group = cursor.fetchone()
        if not group:
            return f"{ERR_NOT_FOUND} Aucun plan trouvé pour '{username}'."

        groupname = group["groupname"]

        # Group-level attributes (plan definition)
        cursor.execute(
            "SELECT attribute, op, value FROM radgroupreply WHERE groupname = %s",
            (groupname,)
        )
        group_attrs = cursor.fetchall()

        # Personal overrides (quota resets, special limits)
        cursor.execute(
            "SELECT attribute, op, value FROM radreply WHERE username = %s",
            (username,)
        )
        personal_attrs = cursor.fetchall()

        # Extract download/upload for easy agent comparison
        download = None
        upload   = None
        for a in group_attrs:
            attr = a["attribute"].lower()
            if "down" in attr:
                download = a["value"]
            if "up" in attr:
                upload = a["value"]

        lines = [
            f"Plan de '{username}':",
            f"- Nom du plan: {groupname}",
        ]
        if download:
            lines.append(f"- Débit max download: {download}")
        if upload:
            lines.append(f"- Débit max upload: {upload}")

        if group_attrs:
            lines.append("- Attributs du plan:")
            for a in group_attrs:
                lines.append(f"  {a['attribute']}: {a['value']}")

        if personal_attrs:
            lines.append("- Attributs personnels (overrides):")
            for a in personal_attrs:
                lines.append(f"  {a['attribute']}: {a['value']}")

        return "\n".join(lines)
    except MySQLError as e:
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


@tool
def get_network_metrics(username: str) -> str:
    """
    Retrieve recent network performance metrics for a customer:
    average download/upload speed, latency, and packet loss.
    Use for slow internet complaints to get objective measurements
    instead of relying on customer perception.
    Compare results with get_plan_info to determine if speed is
    within plan limits or genuinely degraded.
    """
    db = get_db()
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT avg_download_mbps, avg_upload_mbps,
                   latency_ms, packet_loss, timestamp
            FROM network_metrics
            WHERE username = %s
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (username,)
        )
        row = cursor.fetchone()
        if not row:
            return (
                f"Aucune métrique réseau disponible pour '{username}'. "
                "Les métriques sont collectées toutes les heures. "
                "Utiliser get_device_parameters pour les statistiques "
                "de ligne DSL en temps réel."
            )

        # Flag performance issues explicitly for agent
        notes = []
        if row["packet_loss"] and float(row["packet_loss"]) > 2:
            notes.append(f"⚠️ Perte de paquets élevée ({row['packet_loss']}%) — instabilité réseau détectée.")
        if row["latency_ms"] and float(row["latency_ms"]) > 100:
            notes.append(f"⚠️ Latence élevée ({row['latency_ms']} ms) — peut affecter gaming/VoIP.")

        lines = [
            f"Métriques réseau pour '{username}':",
            f"- Download moyen: {row['avg_download_mbps']} Mbps",
            f"- Upload moyen: {row['avg_upload_mbps']} Mbps",
            f"- Latence: {row['latency_ms']} ms",
            f"- Perte de paquets: {row['packet_loss']} %",
            f"- Mesurée le: {row['timestamp']}",
        ]
        if notes:
            lines.append("")
            lines.extend(notes)

        return "\n".join(lines)
    except MySQLError as e:
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()


@tool
def create_ticket(username: str, problem_type: str, description: str, priority: str = "medium") -> str:
    """
    Create a support escalation ticket for human technician intervention.
    Use when automated diagnosis and interventions have failed to resolve the issue,
    or when the problem requires physical on-site work (line issues, firmware update).
    priority: HAUTE, MOYENNE, BASSE (or high, medium, low, critical).
    Always use the ticket number returned — never invent one.
    """
    db = get_db()
    try:
        cursor = db.cursor(dictionary=True)

        # Get customer info for ticket
        cursor.execute(
            "SELECT full_name, phone FROM users WHERE username = %s",
            (username,)
        )
        user           = cursor.fetchone()
        customer_name  = user["full_name"] if user else username
        customer_phone = user["phone"] if user else ""

        # Normalize problem_type to standard category
        normalized_type = _normalize_problem_type(problem_type)

        # Map French/English priority to DB enum (store English in DB)
        priority_map = {
            "HAUTE": "high", "haute": "high",
            "MOYENNE": "medium", "moyenne": "medium",
            "BASSE": "low", "basse": "low",
            "high": "high", "medium": "medium",
            "low": "low", "critical": "critical",
        }
        priority_en = priority_map.get(priority, "medium")

        # Display map — show French to customer
        priority_display = {
            "high": "HAUTE",
            "medium": "MOYENNE",
            "low": "BASSE",
            "critical": "CRITIQUE",
        }

        # Generate unique ticket number with collision check
        for _ in range(5):
            suffix        = ''.join(random.choices(string.digits, k=6))
            ticket_number = f"TKT-{suffix}"
            cursor.execute(
                "SELECT 1 FROM tickets WHERE ticket_number = %s",
                (ticket_number,)
            )
            if not cursor.fetchone():
                break
        else:
            return f"{ERR_DB_ERROR} Impossible de générer un numéro de ticket unique. Réessayez."

        cursor.execute(
            """
            INSERT INTO tickets
              (ticket_number, customer_username, customer_name, customer_phone,
               problem_description, status, priority, created_at)
            VALUES (%s, %s, %s, %s, %s, 'open', %s, NOW())
            """,
            (
                ticket_number, username, customer_name, customer_phone,
                f"[{normalized_type}] {description}", priority_en,
            )
        )
        db.commit()

        return (
            f"Ticket #{ticket_number} créé avec succès:\n"
            f"- Client: {customer_name} ({customer_phone})\n"
            f"- Type: {normalized_type}\n"
            f"- Priorité: {priority_display.get(priority_en, priority_en)}\n"
            f"- Statut: Ouvert\n"
            f"- Créé le: {datetime.now().strftime('%d/%m/%Y à %Hh%M')}\n"
            f"Un technicien contactera le client dans les meilleurs délais."
        )
    except MySQLError as e:
        db.rollback()
        return f"{ERR_DB_ERROR} {e}"
    finally:
        db.close()