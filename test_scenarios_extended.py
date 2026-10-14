"""
test_scenarios_extended.py — Extended multi-turn scenario testing.
Tests longer conversations with realistic customer behavior:
- Customer asks follow-up questions
- Customer is confused or skeptical
- Customer confirms resolution
- Customer says problem persists
- Customer asks for explanation

Usage:
    python test_scenarios_extended.py              # run all
    python test_scenarios_extended.py --scenario S01
    python test_scenarios_extended.py --save
"""

import requests
import time
import uuid
import argparse
import json

import seed_utils

BASE_URL = "http://localhost:8000"

# ─────────────────────────────────────────────
# Extended scenario definitions
# Each scenario has multiple turns simulating
# realistic customer behavior
# ─────────────────────────────────────────────

SCENARIOS = [
    {
        "id": "S01a", "name": "Karim — Auth failure → resolved",
        "cin": "11223344",
        "expected": "auth_failure",
        "turns": [
            "Mon internet ne marche pas, je ne peux pas me connecter depuis ce matin",
            "Vous avez changé mon mot de passe sans me demander ?",
            "D'accord, j'attends... ça marche maintenant merci",
        ],
    },
    {
        "id": "S01b", "name": "Karim — Auth failure → persists → ticket",
        "cin": "11223344",
        "expected": "auth_failure",
        "turns": [
            "Mon internet ne marche pas depuis hier soir",
            "J'ai attendu 5 minutes mais toujours rien",
            "toujours pareil, ça ne marche pas",
        ],
    },
    {
        "id": "S02a", "name": "Leila — SNR critical → ticket → questions",
        "cin": "22334455",
        "expected": "snr_critical",
        "turns": [
            "Ma connexion est très lente depuis quelques jours, les vidéos ne chargent pas",
            "C'est quoi exactement le problème ?",
            "Combien de temps ça va prendre pour être réparé ?",
        ],
    },
    {
        "id": "S03a", "name": "Rami — Zombie session → resolved",
        "cin": "33445566",
        "expected": "zombie_session",
        "turns": [
            "je n'arrive pas à me connecter, tout était normal hier soir",
            "Pourquoi ma session était bloquée ?",
            "ça marche maintenant, merci beaucoup",
        ],
    },
    {
        "id": "S04a", "name": "Nour — Quota exhausted → upgrade question",
        "cin": "44556677",
        "expected": "quota_exhausted",
        "turns": [
            "mon internet s'est coupé brusquement",
            "C'est la deuxième fois cette semaine que ça arrive",
            "C'est quoi le forfait premium exactement ?",
        ],
    },
    {
        "id": "S05a", "name": "Tarek — Firmware → ticket → when technician",
        "cin": "55667788",
        "expected": "firmware_obsolete",
        "turns": [
            "ma connexion se coupe toutes les 3 heures puis revient",
            "Ce problème dure depuis 2 semaines",
            "Le technicien va venir quand exactement ?",
        ],
    },
    {
        "id": "S06a", "name": "Yasmine — MTU → resolved → another question",
        "cin": "66778899",
        "expected": "mtu_mismatch",
        "turns": [
            "les vidéos YouTube se bloquent mais les sites normaux marchent",
            "Qu'est ce que vous avez fait exactement ?",
            "Super ça marche maintenant, mais pourquoi ça s'est passé ?",
        ],
    },
    {
        # Amine is seeded in Bizerte -> Nord-Ouest, which mysql/seed.sql
        # always gives an active outage by design. route_after_outage checks
        # outages before auth-failure diagnostics, so this customer always
        # correctly gets redirected to the outage response first — that's
        # right behavior, not a bug. "auth_failure" was simply the wrong
        # expectation for how this customer is seeded.
        "id": "S07a", "name": "Amine — Forgot password (redirected to active outage)",
        "cin": "77889900",
        "expected": "outage_active",
        "turns": [
            "je ne me souviens plus de mon mot de passe internet",
            "Comment je configure mon routeur avec le nouveau mot de passe ?",
            "D'accord merci c'est bon maintenant",
        ],
    },
    {
        "id": "S08a", "name": "Sarra — SNR marginal → persists → ticket",
        "cin": "88990011",
        "expected": "snr_marginal",
        "turns": [
            "ma connexion est instable, elle coupe souvent",
            "J'ai vérifié les câbles tout semble normal",
            "ça ne marche pas encore, toujours instable",
        ],
    },
    {
        "id": "S09a", "name": "Mehdi — Line instability → skeptical",
        "cin": "99001122",
        "expected": "snr_marginal",
        "turns": [
            "internet se déconnecte toutes les 20-30 minutes",
            "J'ai déjà redémarré le routeur plusieurs fois ça ne change rien",
            "Bon j'attends mais je pense que ça va recouper",
        ],
    },
    {
        "id": "S10a", "name": "Ines — Slow speed → comparison → ticket",
        "cin": "10111213",
        "expected": "slow_speed",
        "turns": [
            "mon internet est très lent, je suis censée avoir 50 Mbps",
            "J'ai fait un test de vitesse et j'ai seulement 12 Mbps",
            "toujours pareil après le redémarrage",
        ],
    },
    {
        "id": "S11a", "name": "Omar — Suspended → insists → redirected",
        "cin": "14151617",
        "expected": "suspended",
        "turns": [
            "je n'arrive pas à me connecter",
            "Mais j'ai payé ma facture le mois dernier",
            "Comment je contacte le service facturation ?",
        ],
    },
    {
        "id": "S12a", "name": "Rim — High latency → gaming → ticket",
        "cin": "18192021",
        "expected": "high_latency",
        "turns": [
            "mon internet marche mais les jeux en ligne sont impossibles, énorme lag",
            "J'ai 185ms de ping c'est vraiment trop",
            "toujours pareil après le redémarrage",
        ],
    },
    {
        "id": "S13a", "name": "Bilel — Plan upgrade → details → confirms",
        "cin": "22232425",
        "expected": "plan_upgrade",
        "turns": [
            "mon internet se coupe tous les jours à la même heure",
            "C'est quoi la différence entre mon forfait actuel et le premium ?",
            "D'accord je veux passer au forfait premium",
        ],
    },
    {
        "id": "S14a", "name": "Dorra — WiFi → ethernet test → confirmed",
        "cin": "26272829",
        "expected": "wifi_confusion",
        "turns": [
            "mon internet ne marche pas dans ma chambre",
            "Avec le câble directement sur l'ordinateur ça marche",
            "Ah d'accord c'est le WiFi alors, qu'est ce que je fais ?",
        ],
    },
    {
        # Sofien is seeded in Jendouba -> Nord-Ouest, same always-active
        # outage as S07a/Amine above — see that comment for why
        # "auth_failure" is the wrong expectation here too.
        "id": "S15a", "name": "Sofien — Factory reset (redirected to active outage)",
        "cin": "30313233",
        "expected": "outage_active",
        "turns": [
            "j'ai essayé tout ce que vous m'avez dit mais rien ne marche",
            "toujours pareil",
            "oui je confirme, faites la réinitialisation",
        ],
    },
]


# ─────────────────────────────────────────────
# Extended-knowledge scenarios — additive only.
# These test the search_extended_knowledge_v2 tool / routing branch
# (agent_graph.py's route_entry -> extended_knowledge node), added for
# questions outside the standard diagnostic tree (pricing/offers,
# installation, technical concepts the original 20 docs don't cover).
#
# node_extended_knowledge now calls search_extended_knowledge_v2
# (isp_knowledge_extended_v2, multilingual MiniLM) instead of v1
# (isp_knowledge_extended, English-only MiniLM) — promoted after a
# French/English cross-lingual comparison showed v2 clearly outperforms v1
# (see agent/compare_v1_v2.py). v1 is kept in place, unrouted, as a
# rollback path — not deleted.
#
# These CANNOT be graded like the scenarios above: node_extended_knowledge
# never sets state["scenario"] (that field is reserved for the diagnostic
# tree), so get_session_state()["scenario"] stays None/unrelated here.
# Instead we grade on the response text itself:
#   - FAIL if it's the generic ask_how_to_help fallback (would mean the
#     message never reached the new branch — i.e. a routing regression)
#   - PASS if it contains at least one topically-relevant keyword
#
# Each message is deliberately worded to avoid every PROBLEM_KEYWORDS
# substring (see agent_graph.py) so it does NOT match the standard
# diagnostic tree, while containing an EXTENDED_KNOWLEDGE_KEYWORDS term
# so it DOES route to the new branch. Reusing existing, already-passing
# customers (Leila/Rami/Yasmine) to avoid the known seed-data gaps.
# ─────────────────────────────────────────────

EXTENDED_KNOWLEDGE_SCENARIOS = [
    {
        "id": "EK01", "name": "Leila — Pricing/offers question",
        "cin": "22334455",
        "type": "extended_knowledge",
        "turns": [
            "Quels sont vos tarifs et offres disponibles pour un abonnement ?",
        ],
        "expect_keywords": ["tarif", "offre", "forfait", "tnd", "prix"],
    },
    {
        "id": "EK02", "name": "Rami — Installation procedure question",
        "cin": "33445566",
        "type": "extended_knowledge",
        "turns": [
            "Quelles sont les étapes pour l'installation d'un nouvel équipement chez moi ?",
        ],
        "expect_keywords": ["installation", "technicien", "rendez-vous", "équipement", "raccordement"],
    },
    {
        "id": "EK03", "name": "Yasmine — Technical concept question (SNR)",
        "cin": "66778899",
        "type": "extended_knowledge",
        "turns": [
            "C'est quoi le SNR sur une ligne DSL ?",
        ],
        "expect_keywords": ["snr", "signal", "bruit", "db"],
    },
    {
        # Idiomatic pricing phrasing from the v1/v2 comparison
        # (agent/compare_v1_v2.py) where v1 landed on irrelevant TR-069/RADIUS
        # protocol docs (~14-16% relevance) and v2 correctly surfaced the
        # offers catalog (23.7%) — regression check that this holds
        # end-to-end through the live agent, not just in raw retrieval.
        "id": "EK04", "name": "Rami — Idiomatic pricing question (most expensive plan)",
        "cin": "33445566",
        "type": "extended_knowledge",
        "turns": [
            "Combien ça coûte le forfait le plus cher ?",
        ],
        "expect_keywords": ["tarif", "offre", "forfait", "tnd", "prix"],
    },
]

# Included in the default full run / --save output / --scenario filtering,
# same as the 16 scenarios above — run_scenario() dispatches to the
# content-based grader below via the "type" marker.
SCENARIOS.extend(EXTENDED_KNOWLEDGE_SCENARIOS)


# ─────────────────────────────────────────────
# API helpers
# ─────────────────────────────────────────────

def send_message(session_id: str, message: str) -> dict:
    try:
        resp = requests.post(
            f"{BASE_URL}/ask",
            json={"message": message, "session_id": session_id},
            timeout=120,
        )
        return resp.json()
    except requests.Timeout:
        return {"response": "[TIMEOUT]"}
    except Exception as e:
        return {"response": f"[ERROR: {e}]"}


def clear_session(session_id: str):
    try:
        requests.post(
            f"{BASE_URL}/clear",
            json={"session_id": session_id},
            timeout=10,
        )
    except Exception:
        pass


def get_session_state(session_id: str) -> dict:
    try:
        resp = requests.get(f"{BASE_URL}/sessions", timeout=10)
        return resp.json().get(session_id, {})
    except Exception:
        return {}


# ─────────────────────────────────────────────
# Test runner
# ─────────────────────────────────────────────

def run_extended_knowledge_scenario(scenario: dict) -> dict:
    """
    Grader for EXTENDED_KNOWLEDGE_SCENARIOS. Unlike run_scenario(), this does
    NOT check state["scenario"] (extended_knowledge never sets it) — it
    grades the response text: fail on the generic ask_how_to_help fallback
    (routing regression), pass on a topically-relevant keyword match.
    """
    session_id = str(uuid.uuid4()).replace("-", "")
    sid        = scenario["id"]
    name       = scenario["name"]
    cin        = scenario["cin"]
    turns_log  = []

    print(f"\n{'─'*60}")
    print(f"  {sid} — {name}")
    print(f"{'─'*60}")

    resp = send_message(session_id, "Bonjour")
    print(f"  👤 Bonjour")
    print(f"  🤖 {(resp.get('response') or '')[:80]}...")
    turns_log.append({"user": "Bonjour", "agent": resp.get("response", "")})
    time.sleep(0.5)

    resp = send_message(session_id, cin)
    print(f"\n  👤 {cin}")
    print(f"  🤖 {(resp.get('response') or '')[:100]}...")
    turns_log.append({"user": cin, "agent": resp.get("response", "")})
    time.sleep(0.5)

    message = scenario["turns"][0]
    print(f"\n  👤 {message}")
    resp = send_message(session_id, message)
    response = resp.get("response") or "[NO RESPONSE]"
    print(f"  🤖 {response[:200]}...")
    turns_log.append({"user": message, "agent": response})

    response_lower = response.lower()
    fell_through_to_fallback = "comment puis-je vous aider" in response_lower
    matched_keyword = any(kw in response_lower for kw in scenario.get("expect_keywords", []))
    passed = (not fell_through_to_fallback) and matched_keyword

    if fell_through_to_fallback:
        detected = "ask_how_to_help (fallback — did not reach extended_knowledge)"
    elif matched_keyword:
        detected = "extended_knowledge (relevant keyword found)"
    else:
        detected = "extended_knowledge (reached, but no expected keyword found)"

    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"\n  Expected: extended_knowledge routing with relevant answer")
    print(f"  Detected: {detected}")
    print(f"  Result:   {status}")

    clear_session(session_id)

    return {
        "id":       sid,
        "name":     name,
        "expected": "extended_knowledge",
        "detected": detected,
        "passed":   passed,
        "resolved": False,
        "turns":    turns_log,
    }


def run_scenario(scenario: dict) -> dict:
    if scenario.get("type") == "extended_knowledge":
        return run_extended_knowledge_scenario(scenario)

    session_id = str(uuid.uuid4()).replace("-", "")
    sid        = scenario["id"]
    name       = scenario["name"]
    cin        = scenario["cin"]
    expected   = scenario["expected"]
    turns_log  = []

    print(f"\n{'─'*60}")
    print(f"  {sid} — {name}")
    print(f"{'─'*60}")

    # Greeting
    resp = send_message(session_id, "Bonjour")
    print(f"  👤 Bonjour")
    print(f"  🤖 {(resp.get('response') or '')[:80]}...")
    turns_log.append({"user": "Bonjour", "agent": resp.get("response", "")})
    time.sleep(0.5)

    # CIN
    resp = send_message(session_id, cin)
    print(f"\n  👤 {cin}")
    print(f"  🤖 {(resp.get('response') or '')[:100]}...")
    turns_log.append({"user": cin, "agent": resp.get("response", "")})
    time.sleep(0.5)

    # Problem turns
    for i, message in enumerate(scenario["turns"]):
        print(f"\n  👤 {message}")
        resp = send_message(session_id, message)
        response = resp.get("response") or "[NO RESPONSE]"
        print(f"  🤖 {response[:200]}...")
        turns_log.append({"user": message, "agent": response})
        time.sleep(1)

    # Check detected scenario
    state    = get_session_state(session_id)
    detected = state.get("scenario", "unknown")
    resolved = state.get("resolved", False)

    passed = (
        detected == expected or
        detected in ("auth_failure",) and expected == "auth_failure" or
        detected in ("snr_marginal", "line_instability") and expected == "snr_marginal"
    )

    # Also pass if suspended scenario redirected to billing
    if expected == "suspended" and any(
        "suspendu" in t["agent"].lower() or "facturation" in t["agent"].lower()
        for t in turns_log
    ):
        passed = True

    # outage_active is a terminal node outside the diagnostic tree — it
    # never sets state["scenario"], so grade on response text instead, same
    # approach as the "suspended" check above.
    if expected == "outage_active" and any(
        "panne" in t["agent"].lower() for t in turns_log
    ):
        passed = True

    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"\n  Expected: {expected}")
    print(f"  Detected: {detected}")
    print(f"  Resolved: {resolved}")
    print(f"  Result:   {status}")

    clear_session(session_id)

    return {
        "id":       sid,
        "name":     name,
        "expected": expected,
        "detected": detected,
        "passed":   passed,
        "resolved": resolved,
        "turns":    turns_log,
    }


def run_all(scenarios: list) -> list:
    results = []

    print("\n" + "═"*60)
    print("  ISP AGENT — EXTENDED MULTI-TURN TEST SUITE")
    print("═"*60)

    for scenario in scenarios:
        result = run_scenario(scenario)
        results.append(result)
        time.sleep(2)

    # Summary
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed

    print("\n" + "═"*60)
    print("  SUMMARY")
    print("═"*60)
    for r in results:
        icon = "✅" if r["passed"] else "❌"
        resolved = "→ resolved ✓" if r["resolved"] else ""
        print(f"  {icon} {r['id']} {r['name']} {resolved}")
        if not r["passed"]:
            print(f"      Expected: {r['expected']} | Got: {r['detected']}")

    print(f"\n  Total: {len(results)} | Passed: {passed} | Failed: {failed}")
    print("═"*60)

    return results


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # Reseed first, before anything else — guarantees fresh NOW()-relative
    # auth-failure/session data even when this script is run directly
    # instead of through run_tests.py. See seed_utils.py.
    seed_utils.reseed_unless_skipped()

    parser = argparse.ArgumentParser(description="Extended multi-turn scenario tests")
    parser.add_argument("--scenario", type=str, help="Run only this scenario (e.g. S01a)")
    parser.add_argument("--save", action="store_true", help="Save results to results_extended.json")
    args = parser.parse_args()

    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=5)
        print(f"Agent status: {resp.json()}")
    except Exception as e:
        print(f"❌ Agent not reachable: {e}")
        exit(1)

    scenarios_to_run = SCENARIOS
    if args.scenario:
        scenarios_to_run = [
            s for s in SCENARIOS
            if s["id"].upper() == args.scenario.upper()
        ]
        if not scenarios_to_run:
            print(f"❌ Scenario {args.scenario} not found")
            exit(1)

    results = run_all(scenarios_to_run)

    if args.save:
        with open("results_extended.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n  Results saved to results_extended.json")