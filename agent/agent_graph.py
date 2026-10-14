"""
agent_graph.py — LangGraph workflow for ISP support agent.

Architecture:
- Python handles: tool execution, routing, scenario selection (clear cases)
- LLM handles: response generation, scenario selection (ambiguous cases)
- Session state passed in from main.py preserves context across turns

Graph nodes:
    check_account → check_outage → collect_diagnostics → select_scenario
    → execute_intervention → generate_response / create_ticket
    (suspended / outage_active as terminal nodes)
"""

import re
import os
import unicodedata
from typing import TypedDict, Optional
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END

from tools.genieacs_tools import (
    get_device, get_device_parameters,
    reboot_device, factory_reset_device,
    push_pppoe_credentials, set_mtu,
    force_connection_request, check_firmware_version,
)
from tools.radius_tools import (
    get_radius_user, change_user_group,
    change_radius_password, disconnect_session,
    reset_daily_quota,
)
from tools.isp_tools import (
    find_customer, check_account_status,
    check_region_outage, get_session_history,
    get_auth_failures, get_plan_info, get_group_plan_specs,
    get_network_metrics, create_ticket,
)
from tools.rag_tools import search_troubleshooting_guide, get_scenario_guide, search_extended_knowledge
# search_extended_knowledge (v1, MiniLM, English-only) is kept imported but
# unrouted — rollback path in case v2 has unexpected issues in production.
# See agent/compare_v1_v2.py for the comparison that motivated the switch.
from tools.rag_tools import search_extended_knowledge_v2

# ─────────────────────────────────────────────
# LLM
# ─────────────────────────────────────────────

llm = ChatOllama(
    model=os.getenv("OLLAMA_MODEL", "qwen2.5:14b"),
    base_url=os.getenv("OLLAMA_URL", "http://ollama:11434"),
    temperature=0,
    num_predict=1024,
)

# ─────────────────────────────────────────────
# State definition
# All fields optional so we can pass partial
# state from session store between turns
# ─────────────────────────────────────────────

class AgentState(TypedDict):
    # ── Customer context ──────────────────────
    username:      str
    serial:        str
    city:          str
    region:        str
    customer_name: str

    # ── Conversation ──────────────────────────
    user_message:       str
    conversation_history: list      # last N user/assistant turns
    final_response:     Optional[str]

    # ── Account / outage checks ───────────────
    account_status: Optional[str]
    outage_status:  Optional[str]

    # ── Diagnostic results (persisted across turns) ───────────
    session_history:    Optional[str]
    auth_failures:      Optional[str]
    device_status:      Optional[str]
    plan_info:          Optional[str]
    rag_guide:          Optional[str]
    device_parameters:  Optional[str]
    network_metrics:    Optional[str]
    firmware_status:    Optional[str]
    extended_knowledge: Optional[str]  # additive — search_extended_knowledge result

    # ── Scenario and intervention ─────────────
    scenario:             Optional[str]
    intervention_result:  Optional[str]
    intervention_done:    bool
    snr_poor:             bool
    needs_ticket:         bool
    ticket_priority:      Optional[str]

    # ── Multi-turn flags ──────────────────────
    problem_persists:     bool     # customer said "still broken" after intervention
    problem_resolved:     bool     # customer said "works now"
    diagnostics_done:     bool     # diagnostics ran this session, skip re-run
    awaiting_factory_reset: bool   # waiting for customer confirmation
    awaiting_plan_upgrade:  bool   # waiting for customer confirmation on a plan upgrade
    outage_notified:        bool   # customer was told about an active regional outage this session

    # ── Turn counter ──────────────────────────
    turn_count: int


# ─────────────────────────────────────────────
# Helper — LLM for language tasks only
# ─────────────────────────────────────────────

def _llm_call(system: str, user: str) -> str:
    """Call LLM for language tasks only — response generation, ambiguous decisions."""
    messages = [
        SystemMessage(content=system),
        HumanMessage(content=user),
    ]
    return llm.invoke(messages).content.strip()


# ─────────────────────────────────────────────
# Helper — keyword detectors for multi-turn
# ─────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase and strip accents so keyword matching survives customers
    (or a nervous demo typist) dropping accents — "probleme" must match
    "problème" just as reliably as the accented form does. Applied to both
    the incoming message and every keyword list below, so comparisons stay
    consistent on both sides."""
    text = (text or "").lower()
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _kw(keywords: list) -> list:
    """Pre-normalize a keyword list at definition time."""
    return [_normalize(k) for k in keywords]


RESOLVED_KEYWORDS = _kw([
    "ça marche", "merci", "parfait", "nickel", "super",
    "ok merci", "fonctionne", "impeccable", "top", "résolu",
    "c'est bon", "tout va bien", "excellent", "génial",
])

# Several RESOLVED_KEYWORDS are bare substrings of extremely common ways to
# report an UNRESOLVED problem in French ("fonctionne" inside "ne fonctionne
# pas" / "plus rien ne fonctionne", "ça marche" inside "ça marche pas",
# "résolu" inside "pas résolu"). A resolved-keyword substring match next to a
# negation marker is not a resolution confirmation — it's a complaint. Same
# bug class as the earlier "change rien" fix in STRONG_PERSISTS_KEYWORDS;
# a blanket "does the message contain a negation word at all" check is
# deliberately not applied to the PERSISTS keyword lists, since those are
# already built from negated phrases ("pas", "toujours", "rien") and would
# be suppressed entirely by the same guard.
NEGATION_MARKERS = _kw(["pas", "plus", "jamais", "aucun", "aucune", "sans"])

def _has_negation(message: str) -> bool:
    msg = _normalize(message)
    return any(re.search(rf"\b{re.escape(marker)}\b", msg) for marker in NEGATION_MARKERS)

STRONG_PERSISTS_KEYWORDS = _kw([
    "toujours pareil", "toujours pas", "encore pareil",
    "rien changé", "ça ne marche toujours pas",
    "marche toujours pas", "tjrs pareil", "pas mieux",
    "change rien", "pas d'amélioration",
    "ne fonctionne toujours pas", "toujours le même problème",
    "encore le même problème", "rien ne marche encore",
])

WEAK_PERSISTS_KEYWORDS = _kw([
    "toujours", "encore", "pareil", "pas résolu",
    "marche pas", "tjrs", "persiste",
])

NEEDS_MORE_HELP_KEYWORDS = _kw([
    "autre", "aussi", "encore un", "autre problème",
    "maintenant", "deuxième", "nouvelle question",
])

PROBLEM_KEYWORDS = _kw([
    "internet", "connexion", "marche pas", "connecter", "lent", "lenteur",
    "déconnect", "coupure", "bloqué", "vidéo", "wifi", "routeur", "panne",
    "problème", "pb", "speed", "ping", "lag", "pas d'internet", "plus internet",
    "rien ne marche", "ne marche", "ne fonctionne", "essayé", "résolu",
    "persiste", "toujours pas", "rien", "impossible", "marche plus",
])

def _is_problem_description(message: str) -> bool:
    """Return True if message looks like a problem description."""
    msg = _normalize(message)
    return any(kw in msg for kw in PROBLEM_KEYWORDS)


# ─────────────────────────────────────────────
# Extended knowledge — additive only.
# Detects questions outside the standard diagnostic tree (pricing/offers,
# installation procedures, technical concepts) so they can be routed to
# search_extended_knowledge instead of the generic "how can I help" fallback.
# Does not affect any message that already matches PROBLEM_KEYWORDS.
# ─────────────────────────────────────────────

EXTENDED_KNOWLEDGE_KEYWORDS = _kw([
    "tarif", "prix", "offre", "abonnement", "forfait", "promotion",
    "installation", "installer", "raccordement", "rendez-vous",
    "devis", "souscrire", "souscription",
    "c'est quoi", "qu'est-ce que", "qu'est ce que", "expliquer",
    "comment fonctionne", "à quoi sert", "protocole",
    "adsl", "vdsl", "fibre", "dslam", "rfc",
])

def _is_extended_knowledge_question(message: str) -> bool:
    """Return True if message looks like a question outside the standard
    diagnostic tree (pricing, installation, technical concept)."""
    msg = _normalize(message)
    return any(kw in msg for kw in EXTENDED_KNOWLEDGE_KEYWORDS)


# Narrower topic-only list (no generic question-starters like "c'est quoi" /
# "qu'est-ce que") used ONLY for mid-conversation follow-ups, once an
# intervention has already run. A generic "what did you do?" follow-up about
# the CURRENT fix must stay on generate_response, not get treated as a fresh
# knowledge-base lookup — but "what's the difference with the premium plan?"
# genuinely is a new topic. Requiring a concrete topic noun (not just a
# question-starter phrase) plus "?" keeps that distinction.
EXTENDED_KNOWLEDGE_TOPIC_KEYWORDS = _kw([
    "tarif", "prix", "offre", "abonnement", "forfait", "promotion",
    "installation", "installer", "raccordement", "rendez-vous",
    "devis", "souscrire", "souscription",
    "protocole", "adsl", "vdsl", "fibre", "dslam", "rfc",
])

def _is_extended_knowledge_topic_followup(message: str) -> bool:
    """Return True if a mid-conversation follow-up question is about an
    extended-knowledge topic (pricing/offers/installation/protocol) rather
    than the customer's ongoing technical issue."""
    if "?" not in message:
        return False
    msg = _normalize(message)
    return any(kw in msg for kw in EXTENDED_KNOWLEDGE_TOPIC_KEYWORDS)

def _detect_resolved(message: str) -> bool:
    msg = _normalize(message)
    if not any(kw in msg for kw in RESOLVED_KEYWORDS):
        return False
    if _has_negation(msg):
        return False
    return True

def _detect_persists(message: str, scenario: str = None) -> bool:
    msg = _normalize(message)
    if any(kw in msg for kw in STRONG_PERSISTS_KEYWORDS):
        return True
    if any(kw in msg for kw in WEAK_PERSISTS_KEYWORDS):
        if scenario in ("snr_marginal", "line_instability"):
            line_weak = _kw(["toujours instable", "toujours lent", "toujours déconnect", "encore coupé"])
            if any(kw in msg for kw in line_weak):
                return True
        if scenario == "high_latency":
            latency_weak = _kw(["toujours lag", "toujours lent", "encore lag"])
            if any(kw in msg for kw in latency_weak):
                return True
        if scenario == "slow_speed":
            speed_weak = _kw(["toujours lent", "encore lent", "toujours pareil"])
            if any(kw in msg for kw in speed_weak):
                return True
        if scenario in ("zombie_session", "mtu_mismatch"):
            return True
    return False

def _detect_customer_intent(message: str, scenario: str = None) -> str:
    if _detect_resolved(message):
        return "resolved"
    if _detect_persists(message, scenario):
        return "persists"
    msg = _normalize(message)
    if any(kw in msg for kw in NEEDS_MORE_HELP_KEYWORDS):
        return "new_problem"
    return "describing_problem"


# ─────────────────────────────────────────────
# Helper — diagnostic signal extractors
# ─────────────────────────────────────────────

def _count_auth_failures(auth_result: str) -> int:
    if not auth_result or "Aucun échec" in auth_result:
        return 0
    match = re.search(r"(\d+)\s+échec", auth_result)
    return int(match.group(1)) if match else 0


def _has_session_timeout(session_result: str) -> bool:
    """Return True if Session-Timeout is the main disconnect cause."""
    if not session_result:
        return False
    return any(
        "Cause fin: Session-Timeout" in line
        for line in session_result.split("\n")
    )


def _is_recurring_timeout(session_result: str) -> bool:
    """
    Return True if Session-Timeout appears as disconnect cause
    5 or more times in actual session lines (not summary).
    """
    if not session_result:
        return False
    # Count only actual session lines, not summary/warning lines
    count = sum(
        1 for line in session_result.split("\n")
        if "Cause fin: Session-Timeout" in line
    )
    return count >= 5


def _is_zombie_session(session_result: str, device_params: str = "") -> bool:
    if not session_result:
        return False
    if "ERROR_ISP_TIME_OUT" in session_result:
        return True
    # Also check device parameters for zombie error code
    if "ERROR_ISP_TIME_OUT" in (device_params or ""):
        return True
    if "Admin-Reset" in session_result:
        return False
    if "User-Request" in session_result:
        return False
    if "En cours" in session_result:
        import re
        match = re.search(r"Durée:\s*(\d+)min", session_result)
        if match:
            duration_min = int(match.group(1))
            if duration_min >= 180:
                return True
    return False

def _is_physical_offline(device_result: str, device_params: Optional[str]) -> bool:
    if not device_result:
        return False
    is_offline = "Offline" in device_result
    has_snr    = bool(device_params and re.search(r"SNR", device_params))
    return is_offline and not has_snr


def _get_snr_value(device_params: str) -> Optional[float]:
    if not device_params:
        return None
    match = re.search(r"SNR downstream:\s*([\d.]+)\s*dB", device_params)
    return float(match.group(1)) if match else None


def _has_mtu_mismatch(device_params: str) -> bool:
    return bool(device_params and "MTU configured: 1500" in device_params)


def _has_lost_carrier(session_result: str, threshold: int = 3) -> bool:
    if not session_result:
        return False
    return session_result.count("Lost-Carrier") >= threshold


def _is_firmware_outdated(firmware_result: str) -> bool:
    return bool(firmware_result and "Outdated" in firmware_result)


def _get_speed_ratio(network_metrics: str, plan_info: str) -> Optional[float]:
    if not network_metrics or not plan_info:
        return None
    speed_match = re.search(r"Download moyen:\s*([\d.]+)\s*Mbps", network_metrics)
    plan_match  = re.search(r"Max-Down.*?(\d+)", plan_info)
    if not speed_match or not plan_match:
        return None
    try:
        measured  = float(speed_match.group(1))
        plan_bps  = float(plan_match.group(1))
        # Convert bps to Mbps
        plan_mbps = plan_bps / 1_000_000
        return measured / plan_mbps if plan_mbps > 0 else None
    except Exception:
        return None


def _has_high_latency(network_metrics: str) -> bool:
    if not network_metrics:
        return False
    latency_match = re.search(r"Latence:\s*(\d+)\s*ms", network_metrics)
    loss_match    = re.search(r"Perte.*?:\s*([\d.]+)\s*%", network_metrics)
    high_latency  = latency_match and int(latency_match.group(1)) > 100
    high_loss     = loss_match and float(loss_match.group(1)) > 2.0
    return bool(high_latency or high_loss)

def node_ask_how_to_help(state: AgentState) -> AgentState:
    """Customer identified but hasn't described their problem yet."""
    name = state.get("customer_name", "")
    state["final_response"] = (
        f"Bonjour M./Mme {name}, votre compte est actif. "
        "Comment puis-je vous aider aujourd'hui ?"
    )
    return state


# ─────────────────────────────────────────────
# Node: extended knowledge — additive only.
# Reached only for questions outside the standard diagnostic tree
# (pricing/offers, installation procedures, technical concepts) that would
# otherwise fall through to node_ask_how_to_help's generic greeting.
# ─────────────────────────────────────────────

def _translate_query_for_retrieval(message: str) -> str:
    """
    Translate the customer's (French) question to English before searching
    the extended knowledge base. Its documents (RFCs, vendor docs, mocked
    pricing/installation guides) are English-only, and the embedding model
    (all-MiniLM-L6-v2) matches poorly across languages — empirically, a
    French query scores ~20% relevance against a document an equivalent
    English query scores ~50%+ against, pushing it below MIN_RELEVANCE and
    causing a false "nothing found" result. Falls back to the original
    message if translation fails for any reason.
    """
    try:
        translated = _llm_call(
            system=(
                "Translate the following customer-support question into English. "
                "Output ONLY the translated question, nothing else — no quotes, "
                "no explanation."
            ),
            user=message,
        )
        return translated.strip() or message
    except Exception:
        return message


def node_extended_knowledge(state: AgentState) -> AgentState:
    """Answer questions not covered by the standard diagnostic tree using
    search_extended_knowledge_v2 (RFC/protocol, vendor docs, DSL thresholds,
    offers/pricing, installation procedures). Promoted from v1 (MiniLM,
    English-only) after a French/English cross-lingual comparison showed v2
    (multilingual MiniLM) outperformed it in an internal French/English
    comparison. v1 (search_extended_knowledge,
    isp_knowledge_extended) is kept in place, unrouted, as a rollback path."""
    message = state["user_message"]
    search_query = _translate_query_for_retrieval(message)
    info = search_extended_knowledge_v2.invoke({"query": search_query})
    state["extended_knowledge"] = info

    # Ground any "my current plan" comparison in the customer's real plan
    # data instead of letting the LLM guess a plausible-sounding tier name.
    # plan_info is usually already in state (node_collect_diagnostics runs it
    # on turn 1 of any diagnostic-tree scenario) — but this node can also be
    # reached on a fresh first message with no diagnostics run yet, so fetch
    # it directly if it's not there.
    plan_info = state.get("plan_info")
    if not plan_info:
        plan_info = get_plan_info.invoke({"username": state["username"]})
        state["plan_info"] = plan_info

    history_text = ""
    try:
        history = state.get("conversation_history") or []
        history_text = "\n".join(
            f"{m.get('role', '').upper()}: {m.get('content', '')[:200]}"
            for m in history[-4:]
            if isinstance(m, dict)
        )
    except Exception:
        history_text = ""

    system = (
        "Tu es un agent support technique pour un FAI tunisien. "
        "Tu réponds UNIQUEMENT en français. Tu es poli, professionnel et empathique. "
        "RÈGLES ABSOLUES:\n"
        "1. JAMAIS de formule de fin.\n"
        "2. Réponds DIRECTEMENT à la question du client en te basant "
        "UNIQUEMENT sur les informations fournies ci-dessous (prix, étapes, "
        "délais, valeurs). Si un prix ou un détail précis figure dans les "
        "informations trouvées, cite-le explicitement.\n"
        "3. Si les informations fournies ne permettent pas de répondre "
        "('Aucune information trouvée...'), dis-le simplement et propose de "
        "transférer la question à un conseiller — SANS donner aucun moyen de "
        "contact précis (voir règle 4).\n"
        "4. JAMAIS inventer ou deviner un numéro de téléphone, une adresse "
        "email, un prix ou un délai qui n'apparaît PAS explicitement dans "
        "les informations fournies. Si aucun moyen de contact n'est fourni, "
        "dis uniquement 'notre service client' sans aucun numéro ni adresse.\n"
        "5. Pour tout chiffre, prix, durée d'engagement ou condition "
        "contractuelle présent dans 'Informations trouvées': reproduis-le "
        "EXACTEMENT tel quel (même valeur, même sens). Ne le paraphrase pas "
        "librement et ne l'inverse JAMAIS (ex: si le document dit 'engagement "
        "de 24 mois', ne dis jamais 'sans engagement').\n"
        "6. Si le client demande une comparaison avec SON forfait actuel: "
        "décris-le UNIQUEMENT avec les valeurs exactes de 'Forfait actuel "
        "réel du client' ci-dessous (débit download/upload, converti en "
        "Mbps en divisant par 1 000 000). N'associe JAMAIS ce forfait à un "
        "nom commercial de la base de connaissances (ex: 'ADSL Essential', "
        "'ADSL Confort', 'Fibre Start') — ces noms sont des catégories "
        "génériques de la base de connaissances, PAS le forfait réel du "
        "client, et ne doivent jamais lui être attribués. N'utilise JAMAIS "
        "les valeurs de débit de la base de connaissances pour décrire le "
        "forfait actuel du client — uniquement les valeurs réelles "
        "fournies. Compare ensuite ces valeurs réelles à celles du forfait "
        "premium (qui, lui, vient de la base de connaissances).\n"
        "7. Maximum 4-5 phrases."
    )
    user = (
        f"Client: {state.get('customer_name', '')}\n"
        f"Historique récent:\n{history_text}\n"
        f"Forfait actuel réel du client (source RADIUS, pas la base de "
        f"connaissances): {plan_info}\n"
        f"Question du client: {message}\n"
        f"Informations trouvées (base de connaissances): {info}\n\n"
        "Réponds directement à la question du client à partir de ces informations."
    )

    try:
        state["final_response"] = _llm_call(system=system, user=user)
    except Exception:
        state["final_response"] = (
            "Je comprends votre question. Je vais transmettre votre demande "
            "à un conseiller qui pourra vous répondre précisément."
        )
    return state


# ─────────────────────────────────────────────
# Routing — entry point
# Decides which node to start based on turn state
# ─────────────────────────────────────────────

def route_entry(state: AgentState) -> str:
    message  = state["user_message"]
    scenario = state.get("scenario")

    if _detect_resolved(message):
        return "resolved"

    if state.get("awaiting_factory_reset"):
        return "factory_reset_confirmation"

    if state.get("awaiting_plan_upgrade"):
        return "plan_upgrade_confirmation"

    # Customer was told about an active regional outage this session —
    # every follow-up must re-check the outage rather than falling through
    # to a generic greeting/diagnostic flow that's forgotten it ever
    # happened. Route to check_outage (not check_account — account status
    # is already known) so route_after_outage re-evaluates fresh: repeats
    # the notice if still active, or lets the customer through to
    # collect_diagnostics once it's cleared.
    if state.get("outage_notified"):
        return "check_outage"

    if message == "[IDENTIFICATION]":
        return "check_account"

    if state.get("intervention_done"):
        if _detect_persists(message, scenario):
            return "create_ticket_persists"
        # A mid-conversation question about a different topic (pricing,
        # installation...) shouldn't be forced through generate_response,
        # which only knows about the current technical intervention.
        if not _is_problem_description(message) and _is_extended_knowledge_topic_followup(message):
            return "extended_knowledge"
        return "generate_response"

    # Once we know (from an earlier check_account this session) that the
    # account is suspended, every subsequent non-problem message should stay
    # in the suspended flow — never silently drop back to the "how can I
    # help" default, which would falsely imply the account is active again.
    if not _is_problem_description(message) and _is_suspended(state.get("account_status")):
        return "suspended_followup"

    if not _is_problem_description(message):
        if _is_extended_knowledge_question(message):
            return "extended_knowledge"
        return "ask_how_to_help"

    if state.get("diagnostics_done"):
        intent = _detect_customer_intent(message, scenario)
        if intent != "new_problem":
            return "select_scenario"

    return "check_account"


# ─────────────────────────────────────────────
# Node: check account status
# ─────────────────────────────────────────────

def node_check_account(state: AgentState) -> AgentState:
    result = check_account_status.invoke({"username": state["username"]})
    state["account_status"] = result
    return state


def _is_suspended(status: str) -> bool:
    return any(kw in (status or "").lower() for kw in ["suspendu", "expiré", "résilié"])


def route_after_account(state: AgentState) -> str:
    status = state.get("account_status", "")
    if _is_suspended(status):
        return "suspended"
    return "check_outage"


# ─────────────────────────────────────────────
# Node: check regional outage
# ─────────────────────────────────────────────

def node_check_outage(state: AgentState) -> AgentState:
    result = check_region_outage.invoke({"city": state["city"]})
    state["outage_status"] = result
    if "PANNE ACTIVE" not in result:
        # Outage has cleared since the customer was last notified (or this
        # is a first check that found nothing) — clear the flag so a later
        # turn doesn't get incorrectly rerouted back through check_outage
        # once the conversation has moved into normal diagnostics.
        state["outage_notified"] = False
    return state


def route_after_outage(state: AgentState) -> str:
    if "PANNE ACTIVE" in state.get("outage_status", ""):
        return "outage_active"
    # First turn after identification — just greet
    if state.get("turn_count", 0) == 1:
        return "greet_customer"
    return "collect_diagnostics"


# ─────────────────────────────────────────────
# Node: greet customer (Phase A response)
# ─────────────────────────────────────────────

def node_greet_customer(state: AgentState) -> AgentState:
    """Generate greeting after successful identification + checks."""
    name = state.get("customer_name", "")
    state["final_response"] = (
        f"Bonjour M./Mme {name}, votre compte est actif et "
        "aucune panne n'est signalée dans votre région. "
        "Comment puis-je vous aider aujourd'hui ?"
    )
    return state


# ─────────────────────────────────────────────
# Node: collect diagnostics
# ─────────────────────────────────────────────

def node_collect_diagnostics(state: AgentState) -> AgentState:
    """
    Run all 5 mandatory diagnostic tools.
    Always runs — no skipping possible.
    Additional tools run based on problem keywords.
    """
    username = state["username"]
    serial   = state["serial"]
    message  = state["user_message"]

    # Core 6 tools — always run
    state["session_history"]    = get_session_history.invoke({"username": username})
    state["auth_failures"]      = get_auth_failures.invoke({"username": username})
    state["device_status"]      = get_device.invoke({"serial_number": serial})
    state["device_parameters"]  = get_device_parameters.invoke({"serial_number": serial})  # ← add
    state["plan_info"]          = get_plan_info.invoke({"username": username})
    state["rag_guide"] = search_troubleshooting_guide.invoke(
    {"problem_description": message}
)

    # Additional tools based on problem type
    needs_line_check = any(
        kw in _normalize(message)
        for kw in _kw(["lent", "lenteur", "vidéo", "bloqué", "déconnect",
                       "coupe", "instable", "ping", "lag", "vitesse"])
    )
    if needs_line_check:
        state["device_parameters"] = get_device_parameters.invoke(
            {"serial_number": serial}
        )
        state["network_metrics"] = get_network_metrics.invoke(
            {"username": username}
        )

    state["diagnostics_done"] = True
    return state


def node_load_guide(state: AgentState) -> AgentState:
    """
    Load the specific troubleshooting guide for the detected scenario.
    Uses targeted ChromaDB retrieval filtered by scenario type.
    Called AFTER scenario is selected — more precise than semantic search.
    """
    scenario = state.get("scenario", "general")
    state["rag_guide"] = get_scenario_guide(scenario)
    return state


# ─────────────────────────────────────────────
# Node: select scenario
# ─────────────────────────────────────────────

def node_select_scenario(state: AgentState) -> AgentState:
    """
    Priority-ordered scenario selection.
    Python rules for clear signals, LLM for ambiguous cases.
    """
    auth_count  = _count_auth_failures(state.get("auth_failures", ""))
    session_res = state.get("session_history", "")
    device_res  = state.get("device_status", "")
    dev_params  = state.get("device_parameters")
    net_metrics = state.get("network_metrics")
    plan_res    = state.get("plan_info", "")

    if auth_count >= 3:
        state["scenario"] = "auth_failure"

    elif _is_recurring_timeout(session_res):
        state["scenario"] = "plan_upgrade"

    elif _has_session_timeout(session_res):
    # Count only actual session lines, not summary/warning lines
        timeout_count = sum(
            1 for line in session_res.split("\n")
            if "Cause fin: Session-Timeout" in line
        )
        # Check customer message for upgrade intent
        msg = _normalize(state["user_message"])
        upgrade_signals = _kw(["tous les jours", "chaque jour", "toujours",
                        "encore", "améliorer", "upgrade", "meilleur"])
        if timeout_count >= 5 or any(kw in msg for kw in upgrade_signals):
            state["scenario"] = "plan_upgrade"
        else:
            state["scenario"] = "quota_exhausted"
    elif _is_zombie_session(session_res, state.get("device_parameters", "")):
        state["scenario"] = "zombie_session"

    elif _is_physical_offline(device_res, dev_params):
        state["scenario"] = "offline_physical"

    elif dev_params:
        snr = _get_snr_value(dev_params)
        if snr is not None and snr < 6:
            state["scenario"] = "snr_critical"
            state["snr_poor"] = True
        elif snr is not None and snr < 10:
            state["scenario"] = "snr_marginal"
        elif _has_mtu_mismatch(dev_params):
            state["scenario"] = "mtu_mismatch"
        elif _has_lost_carrier(session_res):
            fw = check_firmware_version.invoke({"serial_number": state["serial"]})
            state["firmware_status"] = fw
            state["scenario"] = "firmware_obsolete" if _is_firmware_outdated(fw) \
                else "line_instability"
        elif net_metrics and _has_high_latency(net_metrics):
            state["scenario"] = "high_latency"
        elif net_metrics:
            ratio = _get_speed_ratio(net_metrics, plan_res)
            state["scenario"] = "slow_speed" if (ratio and ratio < 0.5) \
                else _llm_select_ambiguous(state)
        else:
            state["scenario"] = _llm_select_ambiguous(state)

    elif _has_lost_carrier(session_res):
        fw = check_firmware_version.invoke({"serial_number": state["serial"]})
        state["firmware_status"] = fw
        state["scenario"] = "firmware_obsolete" if _is_firmware_outdated(fw) \
            else "line_instability"

    else:
        # Check for WiFi confusion before calling LLM
        wifi_keywords = _kw([
            "chambre", "salon", "cuisine", "étage", "loin", "wifi",
            "wi-fi", "wireless", "signal", "portable", "téléphone",
            "tablette", "laptop", "pièce", "distance", "bureau"
        ])
        msg = _normalize(state["user_message"])
        if any(kw in msg for kw in wifi_keywords):
            state["scenario"] = "wifi_confusion"
        else:
            state["scenario"] = _llm_select_ambiguous(state)

    return state


def _llm_select_ambiguous(state: AgentState) -> str:
    context = (
        f"Customer problem: {state['user_message']}\n"
        f"Session history: {state.get('session_history', 'N/A')}\n"
        f"Device status: {state.get('device_status', 'N/A')}\n"
        f"Network metrics: {state.get('network_metrics', 'N/A')}\n"
        f"RAG guide: {state.get('rag_guide', 'N/A')}\n\n"
        "IMPORTANT: If network metrics show good speed, low latency, no packet loss, "
        "AND customer mentions a specific room, location, or device that can't connect "
        "while others work fine → select wifi_confusion.\n"
        "If no clear signal → select general."
    )
    response = _llm_call(
        system=(
            "You are an ISP support diagnostic system. "
            "Based on the diagnostic data, select the most likely scenario. "
            "Reply with ONLY one of: general, wifi_confusion, slow_speed, high_latency"
        ),
        user=context,
    )
    valid = {"general", "wifi_confusion", "slow_speed", "high_latency"}
    return response.strip().lower() if response.strip().lower() in valid else "general"


# ─────────────────────────────────────────────
# Node: execute intervention
# ─────────────────────────────────────────────

def node_execute_intervention(state: AgentState) -> AgentState:
    """
    Execute the correct intervention for the selected scenario.
    All tool calls are deterministic Python.
    """
    scenario = state["scenario"]
    username = state["username"]
    serial   = state["serial"]
    result   = ""

    state["needs_ticket"]  = False
    state["ticket_priority"] = "MOYENNE"

    if scenario == "auth_failure":
        # Look up the account (current group/plan, existing radcheck
        # attributes) before mutating it — a real diagnostic step, not just
        # box-checking. Internal only: not customer-facing, so it's kept out
        # of the phrasing generate_response is allowed to draw on.
        r0 = get_radius_user.invoke({"username": username})
        r1 = change_radius_password.invoke({
            "username": username, "new_password": "TempPass2024!"
        })
        r2 = push_pppoe_credentials.invoke({
            "serial_number": serial, "username": username, "password": "TempPass2024!"
        })
        result = (
            f"Vérification interne du compte RADIUS avant intervention "
            f"(ne pas mentionner au client): {r0}\n"
            f"{r1}\n{r2}\n"
            "Identifiants réinitialisés et synchronisés avec le routeur automatiquement. "
            "Le routeur va tenter de se reconnecter dans les 30 secondes. "
            "Ne pas mentionner le mot de passe dans la réponse au client."
        )

    elif scenario == "zombie_session":
        r1 = disconnect_session.invoke({"username": username})
        r2 = force_connection_request.invoke({"serial_number": serial})
        result = f"{r1}\n{r2}"

    elif scenario == "mtu_mismatch":
        r1 = set_mtu.invoke({"serial_number": serial, "mtu_value": 1492})
        r2 = force_connection_request.invoke({"serial_number": serial})
        result = f"{r1}\n{r2}"

    elif scenario == "snr_critical":
        result = reboot_device.invoke({"serial_number": serial})
        state["needs_ticket"]    = True
        state["ticket_priority"] = "HAUTE"

    elif scenario == "snr_marginal":
        result = reboot_device.invoke({"serial_number": serial})
        result += (
            "\nSNR marginal (6-9 dB). Conseiller le client de vérifier "
            "le câblage interne et retirer les splitters inutiles."
        )
        state["needs_ticket"] = False  # ticket only if problem persists

    elif scenario == "high_latency":
        result = reboot_device.invoke({"serial_number": serial})
        state["needs_ticket"] = False  # ticket only if problem persists

    elif scenario == "slow_speed":
        result = reboot_device.invoke({"serial_number": serial})
        state["needs_ticket"] = False  # ticket only if problem persists

    elif scenario == "firmware_obsolete":
        result = reboot_device.invoke({"serial_number": serial})
        state["needs_ticket"]    = True
        state["ticket_priority"] = "HAUTE"

    elif scenario == "line_instability":
        result = reboot_device.invoke({"serial_number": serial})

    elif scenario == "offline_physical":
        result = "Device offline — remote reboot not possible."
        state["needs_ticket"]    = True
        state["ticket_priority"] = "HAUTE"

    elif scenario == "quota_exhausted":
        result = (
            "Quota journalier épuisé. "
            "Le client a atteint la limite de son abonnement actuel (1 heure par jour). "
            "La connexion reprendra automatiquement demain à la même heure. "
            "Aucune action technique possible — problème de quota, pas de panne."
        )
        state["needs_ticket"] = False

    elif scenario == "plan_upgrade":
        # Propose the upgrade and wait for explicit confirmation before
        # calling change_user_group — its own docstring requires this
        # (billing-affecting). route_entry + node_plan_upgrade_confirmation
        # pick up the confirmation on a later turn, same two-step pattern as
        # the auth_failure "problem persists" -> factory-reset confirm flow.
        state["awaiting_plan_upgrade"] = True
        result = (
            "Le client dépasse son quota tous les jours. "
            "Proposer une mise à niveau vers le forfait premium au client et "
            "lui demander une confirmation explicite avant de l'appliquer — "
            "cette opération affecte la facturation."
        )
        state["needs_ticket"]    = True
        state["ticket_priority"] = "BASSE"


    elif scenario == "wifi_confusion":
        # WiFi-Issue doc's "Actions techniques possibles" recommends
        # reboot_device() — the router auto-selects the least congested
        # WiFi channel on restart.
        result = reboot_device.invoke({"serial_number": serial})
        result += (
            "\nProblème localisé au WiFi (le câble Ethernet fonctionne "
            "normalement). Routeur redémarré pour forcer un changement "
            "automatique de canal WiFi."
        )

    elif scenario == "general":
        result = reboot_device.invoke({"serial_number": serial})

    state["intervention_result"] = result
    state["intervention_done"]   = True
    return state


def route_after_intervention(state: AgentState) -> str:
    # Every scenario responds first and only escalates to a ticket if the
    # customer reports on a later turn that the problem persists (see
    # _detect_persists / route_entry). No scenario tickets immediately after
    # a single intervention — this was previously bypassed for snr_critical
    # and firmware_obsolete, which is inconsistent with the conversational-
    # continuity behavior this project aims for and has been removed.
    #
    # offline_physical is a separate case: no reboot is actually attempted
    # (the device is already confirmed offline from diagnostic data, not a
    # hypothesis), so "wait to see if it persists" doesn't apply the same
    # way. Left ticketing immediately for now — flag if this should change.
    if state.get("needs_ticket") and state["scenario"] in (
        "offline_physical",
    ):
        return "create_ticket"
    return "generate_response"


# ─────────────────────────────────────────────
# Node: generate response
# ─────────────────────────────────────────────

def node_generate_response(state: AgentState) -> AgentState:
    """LLM generates French customer-facing response. All decisions already made."""
    history_text = ""
    try:
        history = state.get("conversation_history") or []
        history_text = "\n".join(
            f"{m.get('role', '').upper()}: {m.get('content', '')[:200]}"
            for m in history[-4:]
            if isinstance(m, dict)
        )
    except Exception:
        history_text = ""

    system = (
        "Tu es un agent support technique pour un FAI tunisien. "
        "Tu réponds UNIQUEMENT en français. Tu es poli, professionnel et empathique. "
        "RÈGLES ABSOLUES:\n"
        "1. JAMAIS de formule de fin.\n"
        "2. JAMAIS de termes techniques: pas de 'RADIUS', 'GenieACS', 'TR-069', "
        "'SNR', 'MTU', 'PPPoE', 'Session-Timeout', 'signal-to-noise ratio'. "
        "Utilise: 'votre routeur', 'votre connexion', 'qualité du signal', "
        "'limite de votre abonnement', 'connexion illimitée'.\n"
        "3. JAMAIS inventer des actions qui n'ont pas été faites. "
        "Si seul un redémarrage a été effectué, ne dis pas qu'une mise à jour a été faite.\n"
        "4. JAMAIS inventer un numéro de téléphone.\n"
        "5. Maximum 4 phrases.\n"
        "6. RÉPONDS DIRECTEMENT à la question du client. Distingue bien deux "
        "types de question, qui ne sont PAS la même chose:\n"
        "   a) Le client demande QUEL EST LE PROBLÈME ou POURQUOI ça arrive "
        "(ex: 'pourquoi', 'c'est quoi le problème', 'quel est le souci', "
        "'qu'est-ce qui se passe') → explique la CAUSE en langage simple, "
        "à partir du Scénario et du Guide RAG (ex: qualité du signal, "
        "quota atteint, appareil non à jour) — PAS ce qui a été fait.\n"
        "   b) Le client demande CE QUE VOUS AVEZ FAIT (ex: 'qu'est-ce que "
        "vous avez fait', 'qu'avez-vous fait exactement') → décris "
        "UNIQUEMENT les actions listées dans 'Ce qui a été fait exactement', "
        "sans reparler de la cause.\n"
        "Si le client demande 'quand', donne un délai (24-48h).\n"
        "Pour les délais technicien: toujours dire '24 à 48 heures ouvrables'."
    )

    user = (
        f"Client: {state.get('customer_name', '')}\n"
        f"Historique récent:\n{history_text}\n"
        f"QUESTION DU CLIENT: {state.get('user_message', '')}\n"
        f"Scénario: {state.get('scenario')}\n"
        f"Ce qui a été fait exactement: {state.get('intervention_result') or 'aucune action'}\n"
        f"Guide RAG: {(state.get('rag_guide') or '')[:200]}\n\n"
        "Réponds DIRECTEMENT à la question du client. "
        "Mentionne UNIQUEMENT les actions listées dans 'Ce qui a été fait exactement'. "
        "Ne mentionne pas d'actions qui ne sont pas dans cette liste."
    )

    try:
        state["final_response"] = _llm_call(system=system, user=user)
    except Exception as e:
        state["final_response"] = (
            "Je comprends votre question. "
            "Vos identifiants ont été mis à jour et votre routeur "
            "se reconnectera automatiquement. "
            "Contactez notre service client si besoin."
        )
    return state
    


# ─────────────────────────────────────────────
# Node: create ticket
# ─────────────────────────────────────────────

def node_create_ticket(state: AgentState) -> AgentState:
    """Create support ticket and generate customer-facing response."""
    username  = state["username"]
    scenario  = state.get("scenario", "general")
    priority  = state.get("ticket_priority", "MOYENNE")

    snr_info = ""
    if state.get("device_parameters"):
        m = re.search(r"SNR downstream:\s*([\d.]+\s*dB[^\n]*)", state["device_parameters"])
        if m:
            snr_info = f" SNR: {m.group(1)}."

    fw_info = " Firmware obsolète." if (
        state.get("firmware_status") and "Outdated" in state["firmware_status"]
    ) else ""

    description = (
        f"Problème persistant après intervention automatique. "
        f"Scénario: {scenario}.{snr_info}{fw_info} "
        f"Intervention technicien requise."
    )

    ticket_result = create_ticket.invoke({
        "username":     username,
        "problem_type": scenario,
        "description":  description,
        "priority":     priority,
    })

    state["final_response"] = _llm_call(
        system = (
    "Tu es un agent support technique pour un FAI tunisien. "
    "Tu réponds UNIQUEMENT en français. Tu es poli, professionnel et empathique. "
    "RÈGLES ABSOLUES:\n"
    "- Ne termine JAMAIS par une formule comme 'Bien cordialement', "
    "'Cordialement' ou '[Votre nom]'. C'est un chat, pas un email.\n"
    "- N'utilise JAMAIS les termes techniques internes: "
    "RADIUS, TR-069, PPPoE, SIM-000XXX, GenieACS, MTU. "
    "Dis simplement 'votre routeur', 'votre connexion', 'vos identifiants'.\n"
    "- N'invente JAMAIS un numéro de téléphone ou une adresse. "
    "Si le client doit contacter un service, dis uniquement "
    "'notre service client' sans préciser de numéro.\n"
    "- Sois concis. Maximum 4-5 phrases. Pas de longs paragraphes.\n"
    "Le diagnostic a déjà été effectué et l'intervention a déjà été exécutée. "
    "Explique simplement ce qui a été fait et ce que le client doit faire maintenant."
),
        user=(
            f"Client: {state.get('customer_name', '')}\n"
            f"Scénario: {scenario}\n"
            f"Ticket: {ticket_result}\n"
            f"Intervention déjà effectuée: {state.get('intervention_result', 'aucune')}\n"
            "Explique au client qu'un ticket prioritaire a été ouvert "
            "et qu'un technicien le contactera."
        ),
    )
    return state


# ─────────────────────────────────────────────
# Node: create ticket — problem persists
# ─────────────────────────────────────────────

def node_create_ticket_persists(state: AgentState) -> AgentState:
    """
    Customer confirmed problem persists after intervention.
    For auth_failure: propose factory reset as last resort.
    For others: create ticket.
    """
    scenario = state.get("scenario", "general")
    username = state["username"]

    # Factory reset for auth_failure that persists
    if scenario == "auth_failure":
        state["awaiting_factory_reset"] = True
        state["final_response"] = (
            "Malgré la réinitialisation de vos identifiants, votre routeur "
            "ne parvient toujours pas à se connecter. "
            "Cela indique que la configuration de votre routeur est corrompue.\n\n"
            "La solution est une réinitialisation complète du routeur "
            "(retour aux paramètres d'usine). "
            "Attention: toutes vos configurations WiFi seront effacées "
            "et vous devrez reconnecter tous vos appareils.\n\n"
            "Confirmez-vous la réinitialisation complète ? "
            "Répondez 'oui je confirme' pour procéder."
        )
        return state

    # For all other scenarios → create ticket
    priority = "HAUTE" if state.get("snr_poor") else "MOYENNE"

    description = (
        f"Problème persistant après intervention pour {username}. "
        f"Scénario précédent: {scenario}. "
        f"Intervention effectuée sans succès. Technicien requis."
    )
    if state.get("snr_poor"):
        description += " SNR faible détecté."

    ticket_result = create_ticket.invoke({
        "username":     username,
        "problem_type": scenario,
        "description":  description,
        "priority":     priority,
    })

    state["final_response"] = (
        f"Je comprends que le problème persiste malgré notre intervention.\n\n"
        f"J'ai ouvert un ticket d'intervention technique :\n"
        f"{ticket_result}\n\n"
        "Un technicien spécialisé vous contactera dans les meilleurs délais. "
        "Nous nous excusons sincèrement pour la gêne occasionnée."
    )
    return state


# ─────────────────────────────────────────────
# Node: problem resolved
# ─────────────────────────────────────────────

def node_resolved(state: AgentState) -> AgentState:
    """
    Customer confirmed problem is resolved. Uses a fast static reply for the
    common case ("merci, ça marche"), but a compound message that ALSO asks
    something ("ça marche, mais pourquoi ça s'est passé ?") must not have
    that question silently dropped just because it also contains a
    resolution keyword — route those through the LLM instead, same
    cause-vs-action framing as node_generate_response.
    """
    name = state.get("customer_name", "")
    state["problem_resolved"] = True

    if "?" not in state.get("user_message", ""):
        state["final_response"] = (
            f"Je suis ravi que votre problème soit résolu, M./Mme {name}. "
            "N'hésitez pas à nous recontacter si vous avez d'autres questions. "
            "Bonne journée !"
        )
        return state

    system = (
        "Tu es un agent support technique pour un FAI tunisien. "
        "Tu réponds UNIQUEMENT en français. Tu es poli, professionnel et empathique. "
        "Le client confirme que son problème est résolu, ET pose une question "
        "dans le même message (par exemple 'pourquoi'). "
        "RÈGLES ABSOLUES:\n"
        "1. Confirme brièvement que tu es content que ça fonctionne.\n"
        "2. Réponds ensuite à sa question. Si elle porte sur la CAUSE "
        "(ex: 'pourquoi ça s'est passé'), explique la cause en langage "
        "simple à partir du Scénario et du Guide RAG.\n"
        "3. JAMAIS de termes techniques: pas de 'RADIUS', 'GenieACS', 'TR-069', "
        "'SNR', 'MTU', 'PPPoE'. Utilise 'votre routeur', 'votre connexion', "
        "'la qualité du signal', 'la configuration réseau'.\n"
        "4. JAMAIS de formule de fin (pas de 'Cordialement').\n"
        "5. Maximum 4 phrases."
    )
    user = (
        f"Client: {name}\n"
        f"Message du client: {state.get('user_message', '')}\n"
        f"Scénario: {state.get('scenario')}\n"
        f"Ce qui a été fait exactement: {state.get('intervention_result') or 'aucune action'}\n"
        f"Guide RAG: {(state.get('rag_guide') or '')[:200]}"
    )
    try:
        state["final_response"] = _llm_call(system=system, user=user)
    except Exception:
        state["final_response"] = (
            f"Je suis ravi que votre problème soit résolu, M./Mme {name}. "
            "N'hésitez pas à nous recontacter si vous avez d'autres questions. "
            "Bonne journée !"
        )
    return state


# ─────────────────────────────────────────────
# Node: factory reset confirmation
# ─────────────────────────────────────────────

def node_factory_reset_confirmation(state: AgentState) -> AgentState:
    """Handle factory reset flow — ask or execute based on confirmation."""
    from tools.genieacs_tools import factory_reset_device

    system = (
    "Tu es un agent support technique pour un FAI tunisien. "
    "Tu réponds UNIQUEMENT en français. Tu es poli, professionnel et empathique. "
    "RÈGLES ABSOLUES — violation = réponse incorrecte:\n"
    "1. JAMAIS de formule de fin: pas de 'Bien cordialement', 'Cordialement', "
    "'Bien à vous', '[Votre nom]', 'L équipe technique' ou similaire.\n"
    "2. JAMAIS de termes techniques internes: pas de 'RADIUS', 'GenieACS', "
    "'TR-069', 'SNR', 'MTU', 'PPPoE', 'SIM-000XXX'. "
    "Utilise: 'votre routeur', 'votre connexion', 'vos identifiants', "
    "'la qualité du signal', 'la configuration réseau'.\n"
    "3. JAMAIS inventer un numéro de téléphone. "
    "Dire uniquement 'notre service client'.\n"
    "4. Maximum 4 phrases. Sois concis et direct.\n"
    "5. Termine par une phrase utile pour le client, pas une formule.\n"
    "Le diagnostic est terminé et l'intervention a été exécutée. "
    "Explique ce qui a été fait et ce que le client doit faire maintenant."
)
    
    user_message = _normalize(state["user_message"])
    confirmed = any(kw in user_message for kw in _kw([
        "oui", "confirme", "d'accord", "ok", "yes", "je confirme", "allez-y"
    ]))

    if confirmed:
        serial = state["serial"]
        r1 = factory_reset_device.invoke({"serial_number": serial})
        r2 = push_pppoe_credentials.invoke({
            "serial_number": serial,
            "username":      state["username"],
            "password":      "TempPass2024!"
        })
        r3 = force_connection_request.invoke({"serial_number": serial})
        state["awaiting_factory_reset"] = False
        state["intervention_done"]      = True
        state["final_response"] = _llm_call(
            system=(
                "Tu es un agent support FAI. Réponds en français. "
                "La réinitialisation d'usine a été effectuée avec succès."
            ),
            user=(
                f"Résultat réinitialisation: {r1}\n"
                f"Credentials re-provisionnés: {r2}\n"
                f"Reconnexion forcée: {r3}\n"
                "Explique au client que la réinitialisation est terminée "
                "et qu'il doit reconfigurer son WiFi."
            ),
        )
    else:
        state["awaiting_factory_reset"] = False
        state["final_response"] = (
            "Très bien, la réinitialisation a été annulée. "
            "Y a-t-il autre chose que je puisse faire pour vous ?"
        )

    return state


# ─────────────────────────────────────────────
# Node: plan upgrade confirmation
# ─────────────────────────────────────────────

def node_plan_upgrade_confirmation(state: AgentState) -> AgentState:
    """
    Handle the plan-upgrade confirmation flow — same confirm/decline pattern
    as node_factory_reset_confirmation, plus a third branch: a mid-flow
    question that's neither a confirm nor a decline (e.g. "what's the
    difference with my current plan?", as in S13a's actual turn 2) is
    answered directly instead of being misread as a decline — a strict
    binary confirm-or-cancel would silently drop the upgrade offer the first
    time the customer asks a clarifying question before confirming.
    """
    user_message = _normalize(state["user_message"])

    confirm_keywords = _kw(["oui", "confirme", "d'accord", "ok", "yes", "je confirme", "allez-y"])
    decline_keywords = _kw(["non", "annule", "pas maintenant", "non merci", "je ne veux pas", "plus tard"])

    confirmed = any(kw in user_message for kw in confirm_keywords)
    declined  = any(kw in user_message for kw in decline_keywords)

    if confirmed:
        result = change_user_group.invoke({
            "username": state["username"], "new_group": "premium_users"
        })
        state["awaiting_plan_upgrade"] = False
        state["intervention_done"]     = True
        state["final_response"] = _llm_call(
            system=(
                "Tu es un agent support FAI. Réponds en français. "
                "La mise à niveau vers le forfait premium a été effectuée avec succès."
            ),
            user=(
                f"Résultat changement de forfait: {result}\n"
                "Confirme au client que son forfait a bien été mis à niveau "
                "vers le forfait premium, et que le changement prendra effet "
                "à la prochaine reconnexion."
            ),
        )
    elif declined:
        state["awaiting_plan_upgrade"] = False
        state["final_response"] = (
            "Très bien, la mise à niveau n'a pas été effectuée. "
            "N'hésitez pas à nous recontacter si vous changez d'avis."
        )
    else:
        # Neither a clear confirm nor decline — answer the question and
        # keep the offer open (awaiting_plan_upgrade stays True) so a later
        # turn can still confirm it.
        #
        # Ground the answer in real data instead of letting the LLM guess:
        #   - premium_specs: the actual RADIUS group the customer will be
        #     moved into on confirmation (change_user_group -> "premium_users"),
        #     so any bandwidth figure quoted here is what they'll really get.
        #   - catalog_info: the extended-knowledge base's mocked commercial
        #     offers catalog (ADSL/Fibre named tiers), fetched ONLY for
        #     price-like questions. That catalog describes a DIFFERENT,
        #     unrelated product line from this lab's RADIUS groups — it
        #     also documents a procedure ("Plan Upgrade Process") that
        #     includes technician visits for cross-technology upgrades.
        #     Fetching it for every question (an earlier version of this
        #     fix did) let the LLM pick up that unrelated technician-visit
        #     detail and wrongly apply it to a plain RADIUS group change
        #     (change_user_group), which needs no visit and no equipment.
        #     So it's only pulled in when the question actually looks
        #     price-related, and the prompt is told to ignore anything in
        #     it about procedure/equipment/technician visits.
        plan_info = state.get("plan_info") or ""
        premium_specs = get_group_plan_specs.invoke({"groupname": "premium_users"})

        message_norm = _normalize(state.get("user_message", ""))
        price_keywords = _kw(["prix", "tarif", "coute", "coûte", "cher", "combien"])
        catalog_info = None
        if any(kw in message_norm for kw in price_keywords):
            catalog_info = search_extended_knowledge_v2.invoke(
                {"query": _translate_query_for_retrieval(state.get("user_message", ""))}
            )

        catalog_block = (
            f"Extrait du catalogue d'offres commerciales, contenant 5 offres "
            f"nommées (ADSL Essential, ADSL Confort, Fibre Start, Fibre "
            f"Premium, Fibre Ultra). CORRESPONDANCE À UTILISER: le forfait "
            f"'premium_users' proposé à ce client correspond, pour la "
            f"tarification uniquement, à l'offre 'Fibre Premium' de ce "
            f"catalogue — utilise SON prix et SON engagement pour répondre à "
            f"toute question de prix, et ignore les 4 autres offres ainsi que "
            f"toute mention de visite technicien, d'installation ou "
            f"d'équipement (cela ne s'applique pas à cette mise à niveau, qui "
            f"est un simple changement de groupe RADIUS). Le débit annoncé "
            f"dans le catalogue pour 'Fibre Premium' ne correspond PAS au "
            f"débit réel de 'premium_users' fourni ci-dessus — pour le débit, "
            f"utilise toujours les spécifications RADIUS ci-dessus, jamais le "
            f"débit du catalogue.\n\n{catalog_info}\n\n"
            if catalog_info else ""
        )

        state["final_response"] = _llm_call(
            system=(
                "Tu es un agent support technique pour un FAI tunisien. "
                "Tu réponds UNIQUEMENT en français, de façon polie et concise "
                "(maximum 4 phrases). Une mise à niveau vers le forfait premium "
                "a été proposée au client mais n'est pas encore confirmée. "
                "FAIT TECHNIQUE RÉEL (toujours vrai pour cette mise à niveau): "
                "il s'agit d'un simple changement de groupe RADIUS, appliqué "
                "à distance et automatiquement à la prochaine reconnexion — "
                "AUCUNE visite technicien et AUCUN nouvel équipement ne sont "
                "nécessaires. RÈGLES ABSOLUES:\n"
                "1. Réponds à la question du client en te basant STRICTEMENT "
                "sur les données fournies ci-dessous.\n"
                "2. JAMAIS inventer un débit, un prix, une caractéristique ou "
                "une étape (comme une visite technicien) qui n'apparaît pas "
                "explicitement dans les données fournies ou le fait technique "
                "ci-dessus.\n"
                "3. Si le prix de l'offre 'Fibre Premium' est présent dans le "
                "catalogue fourni, utilise-le directement comme prix du "
                "forfait premium — ne dis PAS que le prix est inconnu dans ce "
                "cas. Ce n'est que si AUCUNE donnée de prix n'est fournie du "
                "tout que tu dois le dire clairement et orienter vers le "
                "service commercial, sans jamais inventer de chiffre.\n"
                "4. Termine toujours en redemandant au client s'il confirme "
                "la mise à niveau."
            ),
            user=(
                f"Forfait actuel du client: {plan_info}\n\n"
                f"Spécifications réelles du forfait premium (RADIUS — ce que "
                f"le client recevra concrètement après confirmation):\n"
                f"{premium_specs}\n\n"
                f"{catalog_block}"
                f"Question du client: {state.get('user_message', '')}\n"
            ),
        )

    return state


# ─────────────────────────────────────────────
# Terminal nodes
# ─────────────────────────────────────────────

def node_suspended(state: AgentState) -> AgentState:
    state["final_response"] = (
        f"Bonjour M./Mme {state.get('customer_name', '')}. "
        "Votre compte est actuellement suspendu. "
        "Veuillez contacter notre service facturation "
        "du lundi au vendredi de 8h à 17h."
    )
    return state


# ─────────────────────────────────────────────
# Node: suspended-account follow-up — additive only.
# Reached for any later message from a customer already known to be
# suspended, once their message stops looking like a fresh problem report
# (e.g. "but I paid last month", "how do I reach billing"). Never reverses
# the suspension based on the customer's own claim — only a real
# verification tool result could do that, and none exists in this system —
# and always answers the actual question instead of repeating a canned line.
# ─────────────────────────────────────────────

def node_suspended_followup(state: AgentState) -> AgentState:
    system = (
        "Tu es un agent support technique pour un FAI tunisien. "
        "Tu réponds UNIQUEMENT en français, de façon polie et empathique. "
        "Le compte de ce client est actuellement SUSPENDU, confirmé par notre système. "
        "RÈGLES ABSOLUES:\n"
        "1. Ne dis JAMAIS que le compte est actif ou réactivé. Une simple affirmation "
        "du client (ex: 'j'ai payé') ne suffit JAMAIS à confirmer un paiement ou une "
        "réactivation — seul le service facturation peut vérifier cela.\n"
        "2. Si le client affirme avoir payé ou conteste la suspension, reconnais ce "
        "qu'il dit sans jamais confirmer ni infirmer: 'Je comprends, mais je ne peux "
        "pas vérifier les paiements depuis le support technique.'\n"
        "3. Réponds DIRECTEMENT à la question du client si elle est identifiable "
        "(ex: comment contacter la facturation, quels sont les horaires).\n"
        "4. Le service facturation est joignable du lundi au vendredi de 8h à 17h.\n"
        "5. JAMAIS inventer un numéro de téléphone, une adresse email ou tout autre "
        "moyen de contact précis — dis uniquement 'notre service facturation'.\n"
        "6. Termine toujours en orientant le client vers le service facturation.\n"
        "7. Maximum 4 phrases."
    )
    user = (
        f"Client: {state.get('customer_name', '')}\n"
        f"Message du client: {state.get('user_message', '')}\n"
        "Réponds en respectant strictement les règles ci-dessus."
    )
    try:
        state["final_response"] = _llm_call(system=system, user=user)
    except Exception:
        state["final_response"] = (
            "Je comprends votre message, mais je ne peux pas vérifier les paiements "
            "depuis le support technique. Veuillez contacter notre service "
            "facturation du lundi au vendredi de 8h à 17h."
        )
    return state


def node_outage_active(state: AgentState) -> AgentState:
    outage_info = state.get("outage_status", "")
    state["final_response"] = (
        f"Bonjour M./Mme {state.get('customer_name', '')}. "
        "Une panne technique est active dans votre région. "
        f"{outage_info} "
        "Nos équipes travaillent à la résolution. "
        "Nous nous excusons pour la gêne occasionnée."
    )
    state["outage_notified"] = True
    return state


# ─────────────────────────────────────────────
# Build the graph
# ─────────────────────────────────────────────

def build_graph():
    workflow = StateGraph(AgentState)

    # Add all nodes
    workflow.add_node("check_account",              node_check_account)
    workflow.add_node("check_outage",               node_check_outage)
    workflow.add_node("greet_customer",             node_greet_customer)
    workflow.add_node("collect_diagnostics",        node_collect_diagnostics)
    workflow.add_node("select_scenario",            node_select_scenario)
    workflow.add_node("load_guide",                 node_load_guide)
    workflow.add_node("execute_intervention",       node_execute_intervention)
    workflow.add_node("generate_response",          node_generate_response)
    workflow.add_node("create_ticket",              node_create_ticket)
    workflow.add_node("create_ticket_persists",     node_create_ticket_persists)
    workflow.add_node("resolved",                   node_resolved)
    workflow.add_node("factory_reset_confirmation", node_factory_reset_confirmation)
    workflow.add_node("plan_upgrade_confirmation",  node_plan_upgrade_confirmation)
    workflow.add_node("suspended",                  node_suspended)
    workflow.add_node("outage_active",              node_outage_active)
    workflow.add_node("ask_how_to_help",            node_ask_how_to_help)
    workflow.add_edge("ask_how_to_help",            END)
    workflow.add_node("extended_knowledge",         node_extended_knowledge)
    workflow.add_edge("extended_knowledge",         END)
    workflow.add_node("suspended_followup",         node_suspended_followup)
    workflow.add_edge("suspended_followup",         END)

    # Entry point with multi-turn routing
    workflow.set_conditional_entry_point(
    route_entry,
    {
        "check_account":              "check_account",
        "select_scenario":            "select_scenario",
        "create_ticket_persists":     "create_ticket_persists",
        "factory_reset_confirmation": "factory_reset_confirmation",
        "resolved":                   "resolved",
        "ask_how_to_help":            "ask_how_to_help",
        "extended_knowledge":         "extended_knowledge",
        "suspended_followup":         "suspended_followup",
        "generate_response":          "generate_response",  # ← add this
        "plan_upgrade_confirmation":  "plan_upgrade_confirmation",
        "check_outage":               "check_outage",
    }
)

    # Account check routing
    workflow.add_conditional_edges(
        "check_account",
        route_after_account,
        {
            "suspended":    "suspended",
            "check_outage": "check_outage",
        }
    )

    # Outage check routing
    workflow.add_conditional_edges(
        "check_outage",
        route_after_outage,
        {
            "outage_active":       "outage_active",
            "greet_customer":      "greet_customer",
            "collect_diagnostics": "collect_diagnostics",
        }
    )

    # Intervention routing
    workflow.add_conditional_edges(
        "execute_intervention",
        route_after_intervention,
        {
            "create_ticket":     "create_ticket",
            "generate_response": "generate_response",
        }
    )

    # Fixed edges
    workflow.add_edge("collect_diagnostics",        "select_scenario")
    workflow.add_edge("select_scenario",            "load_guide")
    workflow.add_edge("load_guide",                 "execute_intervention")
    workflow.add_edge("generate_response",          END)
    workflow.add_edge("create_ticket",              END)
    workflow.add_edge("create_ticket_persists",     END)
    workflow.add_edge("resolved",                   END)
    workflow.add_edge("greet_customer",             END)
    workflow.add_edge("suspended",                  END)
    workflow.add_edge("outage_active",              END)
    workflow.add_edge("factory_reset_confirmation", END)
    workflow.add_edge("plan_upgrade_confirmation",  END)

    return workflow.compile()


# Compile once at import time
isp_graph = build_graph()


# ─────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────

def run_graph(
    username:             str,
    serial:               str,
    city:                 str,
    region:               str,
    customer_name:        str,
    user_message:         str,
    conversation_history: list,
    # Persisted state from previous turns
    intervention_done:    bool  = False,
    snr_poor:             bool  = False,
    diagnostics_done:     bool  = False,
    scenario:             Optional[str] = None,
    session_history:      Optional[str] = None,
    auth_failures:        Optional[str] = None,
    device_status:        Optional[str] = None,
    plan_info:            Optional[str] = None,
    device_parameters:    Optional[str] = None,
    network_metrics:      Optional[str] = None,
    awaiting_factory_reset: bool = False,
    awaiting_plan_upgrade: bool = False,
    outage_notified:       bool = False,
    turn_count:           int   = 1,
    account_status:       Optional[str] = None,
    intervention_result:  Optional[str] = None,
) -> dict:
    """
    Run the ISP support graph for one turn.
    Returns dict with final_response and updated state fields
    to persist in the session store.
    """
    initial_state: AgentState = {
        # Customer context
        "username":      username,
        "serial":        serial,
        "city":          city,
        "region":        region,
        "customer_name": customer_name,
        # Conversation
        "user_message":         user_message,
        "conversation_history": conversation_history,
        "final_response":       None,
        # Account / outage — account_status carries over so a follow-up
        # message from an already-known-suspended customer stays in that
        # flow instead of silently forgetting the suspension (see
        # node_suspended_followup / route_entry).
        "account_status": account_status,
        "outage_status":  None,
        # Diagnostic results — carry over from previous turns
        "session_history":   session_history,
        "auth_failures":     auth_failures,
        "device_status":     device_status,
        "plan_info":         plan_info,
        "rag_guide":         None,
        "device_parameters": device_parameters,
        "network_metrics":   network_metrics,
        "firmware_status":   None,
        # Scenario and intervention — carry over. intervention_result must
        # persist so a later follow-up ("did you really do that?") can
        # honestly confirm the real action instead of denying it happened
        # (node_generate_response reads this field).
        "scenario":             scenario,
        "intervention_result":  intervention_result,
        "intervention_done":    intervention_done,
        "snr_poor":             snr_poor,
        "needs_ticket":         False,
        "ticket_priority":      None,
        # Multi-turn flags
        "problem_persists":       False,
        "problem_resolved":       False,
        "diagnostics_done":       diagnostics_done,
        "awaiting_factory_reset": awaiting_factory_reset,
        "awaiting_plan_upgrade":  awaiting_plan_upgrade,
        "outage_notified":        outage_notified,
        # Turn counter
        "turn_count": turn_count,
    }

    result = isp_graph.invoke(initial_state)

    # Return response + updated state fields to persist
    return {
        "response": result.get("final_response", "Je suis désolé, une erreur est survenue."),
        # Fields to save back to session store
        "intervention_done":    result.get("intervention_done", intervention_done),
        "snr_poor":             result.get("snr_poor", snr_poor),
        "diagnostics_done":     result.get("diagnostics_done", diagnostics_done),
        "scenario":             result.get("scenario", scenario),
        "session_history":      result.get("session_history", session_history),
        "auth_failures":        result.get("auth_failures", auth_failures),
        "device_status":        result.get("device_status", device_status),
        "plan_info":            result.get("plan_info", plan_info),
        "device_parameters":    result.get("device_parameters", device_parameters),
        "network_metrics":      result.get("network_metrics", network_metrics),
        "awaiting_factory_reset": result.get("awaiting_factory_reset", False),
        "awaiting_plan_upgrade": result.get("awaiting_plan_upgrade", False),
        "outage_notified":      result.get("outage_notified", False),
        "problem_resolved":     result.get("problem_resolved", False),
        "account_status":       result.get("account_status", account_status),
        "intervention_result":  result.get("intervention_result", intervention_result),
    }