"""
ingest_docs.py — Populate ChromaDB with the comprehensive ISP troubleshooting knowledge base.
Run once (or after any update):
    docker exec -it agent python ingest_docs.py

Embedding model: DefaultEmbeddingFunction (all-MiniLM-L6-v2)
Must match rag_tools.py embedding function.
"""

import chromadb
from chromadb.utils import embedding_functions
import os

CHROMA_URL      = os.getenv("CHROMA_URL", "http://chromadb:8000")
COLLECTION_NAME = "isp_troubleshooting"

# ─────────────────────────────────────────────────────────────────
# Knowledge base — 20 documents
#
# Format per document:
#   id       — unique identifier
#   content  — troubleshooting guide (what the agent reads)
#   metadata — category, type, priority_threshold, tools_to_use
#
# CATEGORIES:
#   authentication  — PPPoE login failures, credential issues
#   disconnection   — session drops, zombie sessions, intermittent cuts
#   device          — offline, firmware, MTU, line quality
#   performance     — slow speed, latency, WiFi vs Ethernet
#   account         — suspended, quota, plan upgrades
#   procedure       — general flows, outage handling, escalation
# ─────────────────────────────────────────────────────────────────

DOCUMENTS = [

    # ══════════════════════════════════════════
    # CATEGORY 1: AUTHENTICATION
    # ══════════════════════════════════════════

    {
        "id": "doc_auth_failure",
        "content": """Échecs d'authentification PPPoE — Access-Reject.
Symptôme: entrées Access-Reject dans radpostauth, client ne peut pas établir de connexion PPPoE.
Le routeur tente de se connecter mais reçoit un refus du serveur RADIUS.

Causes possibles:
- Mot de passe PPPoE incorrect sur le routeur (mismatch avec RADIUS)
- Username mal configuré sur le routeur (sensible à la casse)
- Compte suspendu ou expiré dans RADIUS
- Credentials jamais synchronisés après un changement de mot de passe

Procédure de diagnostic:
1. get_auth_failures(username) → compter le nombre de rejets récents
2. get_radius_user(username) → vérifier que le compte existe et est actif
3. check_account_status(username) → confirmer que le compte n'est pas suspendu
4. get_device_parameters(serial_number) → vérifier LastConnectionError sur le routeur

Interprétation:
- 1-2 rejets → peut être une tentative manuelle, surveiller
- 3-9 rejets → forte probabilité de mismatch credentials
- 10+ rejets → mismatch confirmé, action immédiate requise

Solution:
1. change_radius_password(username, "TempPass2024!")
2. push_pppoe_credentials(serial_number, username, "TempPass2024!")
3. Informer le client: "J'ai réinitialisé vos identifiants et synchronisé votre routeur. Testez dans 1 minute."
4. Si toujours en échec après 2 minutes: create_ticket(priority=HAUTE, type=Réinitialisation credentials)

Règle d'escalade: ticket HAUTE si plus de 10 échecs, MOYENNE entre 3 et 9.""",
        "metadata": {
            "category": "authentication",
            "type": "Access-Reject",
            "priority_threshold": 10,
            "tools_to_use": "get_auth_failures, get_radius_user, change_radius_password, push_pppoe_credentials",
        },
    },

    {
        "id": "doc_pppoe_sync",
        "content": """Synchronisation des credentials PPPoE vers le routeur.
Contexte: après tout changement de mot de passe dans RADIUS, le routeur garde l'ancien mot de passe
jusqu'à ce que push_pppoe_credentials soit appelé explicitement.

Quand appeler push_pppoe_credentials:
- Après change_radius_password (toujours, sans exception)
- Après factory_reset_device (re-provisioning)
- Quand get_device_parameters montre un username PPPoE incorrect
- Quand ERROR_AUTHENTICATION_FAILURE est présent malgré un compte actif

Procédure:
1. Obtenir les credentials actuels: get_radius_user(username) → lire le username
2. Obtenir le nouveau mot de passe (celui qui vient d'être défini dans RADIUS)
3. push_pppoe_credentials(serial_number, username, new_password)
4. Attendre 30-60 secondes
5. force_connection_request(serial_number) → forcer une resynchronisation
6. get_device(serial_number) → vérifier que le statut est revenu Online

Attention:
- Ne jamais pousser des credentials sans avoir changé le mot de passe dans RADIUS d'abord
- Le push échoue si le device est offline — vérifier statut avant
- Si le device est offline, la tâche sera exécutée au prochain cycle d'inform (15-30 min)

Escalade: si push réussit mais connexion toujours en échec après 5 minutes
→ create_ticket(priority=HAUTE, type=Réinitialisation credentials)""",
        "metadata": {
            "category": "authentication",
            "type": "PPPoE-Sync",
            "priority_threshold": 0,
            "tools_to_use": "push_pppoe_credentials, force_connection_request, get_device",
        },
    },

    {
        "id": "doc_credential_reset_flow",
        "content": """Procédure complète de réinitialisation des credentials PPPoE.
Utiliser quand: client ne peut pas se connecter, Access-Reject confirmés, ou mot de passe oublié.

Flux complet étape par étape:
1. Vérifier que le compte existe: get_radius_user(username)
   → Si NOT_FOUND: escalade immédiate, le compte n'existe pas dans RADIUS
2. Vérifier le statut: check_account_status(username)
   → Si suspendu: rediriger vers facturation, ne pas réinitialiser
3. Changer le mot de passe dans RADIUS: change_radius_password(username, "TempPass2024!")
   → Confirmer le succès avant de continuer
4. Synchroniser vers le routeur: push_pppoe_credentials(serial_number, username, "TempPass2024!")
   → Attend que la tâche GenieACS soit en queue
5. Forcer reconnexion: force_connection_request(serial_number)
6. Vérifier après 60 secondes: get_device(serial_number)
   → Online: succès, informer le client
   → Toujours offline: create_ticket(priority=HAUTE)

Message client après succès:
"J'ai réinitialisé vos identifiants PPPoE et synchronisé votre routeur.
Votre nouveau mot de passe temporaire est actif. Testez votre connexion dans 1 minute."

Règle de sécurité: ne jamais communiquer le mot de passe par chat non sécurisé.
Toujours conseiller au client de changer son mot de passe depuis l'espace client.""",
        "metadata": {
            "category": "authentication",
            "type": "Credential-Reset",
            "priority_threshold": 0,
            "tools_to_use": "get_radius_user, check_account_status, change_radius_password, push_pppoe_credentials, force_connection_request",
        },
    },

    # ══════════════════════════════════════════
    # CATEGORY 2: DISCONNECTIONS
    # ══════════════════════════════════════════

    {
        "id": "doc_lost_carrier",
        "content": """Déconnexions fréquentes Lost-Carrier.
Symptôme: déconnexions répétées avec cause Lost-Carrier dans l'historique RADIUS.
Lost-Carrier signifie que la liaison physique entre le routeur et la prise FAI a été perdue.

Causes possibles:
- Câble téléphonique (RJ11) endommagé ou mal branché
- Alimentation instable ou microcoupures électriques
- Surchauffe du routeur (ventilation insuffisante)
- Signal DSL faible (SNR bas, atténuation élevée)
- Firmware obsolète avec bug de keepalive PPPoE

Procédure de diagnostic:
1. get_session_history(username) → compter les Lost-Carrier, noter la fréquence
2. get_device_parameters(serial_number) → vérifier SNR downstream/upstream
3. check_firmware_version(serial_number) → vérifier si firmware obsolète

Interprétation SNR:
- SNR ≥ 15 dB → ligne correcte, chercher cause physique (câble, alimentation)
- SNR 6-14 dB → ligne marginale, instabilité possible
- SNR < 6 dB → ligne dégradée, cause principale des Lost-Carrier

Interprétation fréquence:
- 1-2 par semaine → surveiller, probablement ponctuel
- 2-5 par jour → problème actif, intervention recommandée
- 5+ par jour → problème critique, escalade prioritaire

Solution selon diagnostic:
- SNR < 6 dB: reboot_device() + create_ticket(priority=HAUTE, type=Déconnexion physique)
- Firmware obsolète: check_firmware_version() + reboot_device() + create_ticket(priority=HAUTE)
- Ligne correcte mais déconnexions: reboot_device(), conseiller de vérifier câbles
- Toujours en échec: create_ticket(priority=HAUTE, type=Déconnexion physique)

Règle d'escalade: ticket HAUTE si plus de 5 Lost-Carrier par jour ou SNR < 6 dB.""",
        "metadata": {
            "category": "disconnection",
            "type": "Lost-Carrier",
            "priority_threshold": 5,
            "tools_to_use": "get_session_history, get_device_parameters, check_firmware_version, reboot_device",
        },
    },

    {
        "id": "doc_session_timeout",
        "content": """Quota journalier épuisé — Session-Timeout.
Symptôme: déconnexions avec cause Session-Timeout dans radacct, souvent à heure fixe
ou après une durée déterminée. Client peut parfois naviguer puis se retrouve coupé.

Différence Session-Timeout vs Lost-Carrier:
- Session-Timeout = déconnexion propre initiée par RADIUS (quota atteint)
- Lost-Carrier = perte physique de signal

Causes:
- Attribut Session-Timeout dans le plan RADIUS (limite de durée par session)
- Attribut Max-All-Session (quota total de données ou de temps)
- Plan journalier avec limite de volume

Procédure de diagnostic:
1. get_session_history(username) → confirmer Session-Timeout comme cause principale
2. get_plan_info(username) → vérifier l'attribut Session-Timeout et Max-All-Session
3. Calculer si la fréquence correspond à une limite de plan

Solution:
1. Expliquer au client les limites de son plan actuel
2. Proposer un reset: "Votre quota journalier est épuisé. Voulez-vous que je le réinitialise?"
3. Si oui: reset_daily_quota(username)
4. Informer: "Quota réinitialisé, reconnectez-vous. La limite sera rétablie à minuit."
5. Si le client souhaite un plan supérieur: change_user_group(username, new_group) avec confirmation

Note: le reset_daily_quota est une mesure temporaire.
La limite revient automatiquement à minuit. Pour une solution permanente,
le client doit changer de plan via change_user_group.

Escalade: ticket type Mise à niveau plan si le client souhaite changer définitivement.""",
        "metadata": {
            "category": "disconnection",
            "type": "Session-Timeout",
            "priority_threshold": 0,
            "tools_to_use": "get_session_history, get_plan_info, reset_daily_quota, change_user_group",
        },
    },

    {
        "id": "doc_zombie_session",
        "content": """Session zombie bloquante — ERROR_ISP_TIME_OUT.
Symptôme: client ne peut pas se connecter malgré un compte actif et des credentials corrects.
Le serveur RADIUS rejette la connexion car il croit que le client est déjà connecté.
Souvent après une coupure de courant ou un redémarrage brutal du routeur.

Caractéristiques d'une session zombie:
- Session ouverte dans radacct sans acctstoptime (ou date zéro)
- Session datant de plusieurs heures avec zéro trafic
- get_auth_failures retourne peu ou pas d'Access-Reject
- get_device montre le routeur Online mais pas de WAN IP
- LastConnectionError = ERROR_ISP_TIME_OUT

Différence avec Access-Reject:
- Access-Reject = RADIUS refuse activement (mauvais credentials)
- Session zombie = RADIUS croit que l'utilisateur est déjà connecté (accounting bug)

Procédure de diagnostic:
1. get_session_history(username) → chercher session longue ouverte avec peu de trafic
2. get_auth_failures(username) → confirmer peu ou pas d'Access-Reject
3. get_device_parameters(serial_number) → confirmer ERROR_ISP_TIME_OUT

Solution:
1. disconnect_session(username) → fermer la session zombie dans radacct
2. force_connection_request(serial_number) → forcer reconnexion immédiate
3. Attendre 30-60 secondes
4. get_device(serial_number) → vérifier que le routeur est revenu Online avec une WAN IP
5. Message client: "Session bloquée libérée. Votre connexion devrait être rétablie dans 30 secondes."
6. Si toujours en échec: vérifier les credentials avec get_auth_failures, puis create_ticket()

Escalade: ticket MOYENNE si disconnect_session réussit mais connexion toujours impossible.""",
        "metadata": {
            "category": "disconnection",
            "type": "Zombie-Session",
            "priority_threshold": 0,
            "tools_to_use": "get_session_history, get_auth_failures, get_device_parameters, disconnect_session, force_connection_request",
        },
    },

    {
        "id": "doc_intermittent_disco",
        "content": """Déconnexions intermittentes — connexion instable.
Symptôme: client signale des coupures courtes et répétées, connexion qui "saute",
ping instable, appels VoIP qui se coupent. Différent d'une coupure totale.

Causes fréquentes:
- SNR marginal (6-10 dB) → ligne instable mais pas totalement perdue
- Interférences sur la ligne téléphonique (appareils branchés sur la même ligne)
- Micropannes électriques (onduleur recommandé)
- Congestion réseau à certaines heures
- WiFi faible (artefact — en réalité c'est le WiFi, pas l'internet)

Procédure de diagnostic:
1. get_session_history(username) → analyser la durée et fréquence des sessions
2. get_device_parameters(serial_number) → vérifier SNR et taux d'erreurs
3. get_network_metrics(username) → vérifier latence et packet loss

Interprétation:
- Sessions de 5-30 min répétées → ligne instable (SNR marginal probable)
- Packet loss > 2% → instabilité réseau confirmée
- Latence > 100ms → congestion ou ligne dégradée
- Sessions longues mais "sauts" ressentis → peut être WiFi, pas la ligne

Distinction WiFi vs ligne:
Demander: "Le problème existe-t-il aussi quand vous êtes connecté en câble Ethernet directement au routeur?"
- Si en câble ça marche → problème WiFi, pas la ligne
- Si en câble ça coupe aussi → problème de ligne confirmé

Solution:
- SNR marginal: reboot_device(), conseiller câble Ethernet, create_ticket(priority=MOYENNE)
- Packet loss élevé: reboot_device() + create_ticket(priority=HAUTE)
- Problème WiFi seul: conseiller de rapprocher l'appareil du routeur ou utiliser le câble

Escalade: ticket HAUTE si packet loss > 5% ou latence > 200ms.""",
        "metadata": {
            "category": "disconnection",
            "type": "Intermittent",
            "priority_threshold": 0,
            "tools_to_use": "get_session_history, get_device_parameters, get_network_metrics, reboot_device",
        },
    },

    # ══════════════════════════════════════════
    # CATEGORY 3: DEVICE / LINE
    # ══════════════════════════════════════════

    {
        "id": "doc_offline",
        "content": """Routeur complètement hors ligne — GenieACS Offline.
Symptôme: get_device retourne Offline, routeur n'envoie plus de TR-069 Inform.
Client n'a aucune connexion internet.

Attention — interprétation correcte de GenieACS Offline:
- "Offline" dans GenieACS peut signifier simplement "entre deux cycles d'inform" (15-90 secondes)
- Un routeur "Offline" depuis moins de 3 minutes peut être en train d'envoyer un Inform
- Ne conclure à un vrai offline QUE si last_seen > 3 minutes ET get_device_parameters ne retourne pas de SNR

Causes réelles d'offline:
- Panne de courant ou routeur éteint
- Câble d'alimentation débranché
- Câble WAN (RJ45 ou RJ11) débranché
- Routeur en panne matérielle
- Panne réseau régionale (vérifier check_region_outage d'abord)

Procédure de diagnostic:
1. check_region_outage(city) → exclure une panne zone avant tout
2. get_device(serial_number) → confirmer offline et durée depuis last_seen
3. get_device_parameters(serial_number) → si retourne SNR → routeur en fait connecté
4. get_session_history(username) → dernière session connue

Interprétation LED SimRouter:
- Power verte + WAN verte → tout fonctionne (mais GenieACS dit offline = cycle inform)
- Power verte + WAN rouge → problème ligne FAI ou câble WAN
- Power verte + WAN éteinte → câble WAN débranché
- Power rouge → panne matérielle

Solution:
- Offline < 3 min: attendre un cycle d'inform, forcer avec force_connection_request()
- Offline 3-60 min: demander au client de vérifier câbles et alimentation + redémarrage manuel
- Offline > 24h: create_ticket(priority=HAUTE, type=Intervention terrain)

Instructions redémarrage manuel:
"Débranchez le câble d'alimentation du routeur, attendez 30 secondes, rebranchez.
Attendez 2-3 minutes pour que la connexion se rétablisse."

Escalade: ticket HAUTE si offline depuis plus de 24h ou panne matérielle suspectée.""",
        "metadata": {
            "category": "device",
            "type": "Device-Offline",
            "priority_threshold": 24,
            "tools_to_use": "check_region_outage, get_device, get_device_parameters, force_connection_request",
        },
    },

    {
        "id": "doc_firmware_obsolete",
        "content": """Firmware obsolète — instabilité et déconnexions périodiques.
Symptôme: déconnexions Lost-Carrier répétées toutes les 2-4 heures, client se reconnecte
puis se déconnecte à nouveau. check_firmware_version retourne "Outdated".

Lien firmware obsolète → instabilité:
- Versions < 3.0.0 ont un bug de keepalive PPPoE: la connexion se perd après ~3h
- Le routeur ne renouvelle pas correctement la session PPPoE
- Redémarrage résout temporairement mais le problème revient

Diagnostic:
1. check_firmware_version(serial_number) → confirmer version actuelle vs minimum requis
2. get_session_history(username) → confirmer pattern toutes les 2-4h
3. get_device_parameters(serial_number) → vérifier SNR (exclure problème de ligne)

Interprétation:
- Firmware Outdated + Lost-Carrier toutes les 2-4h → firmware confirmé comme cause
- Firmware Outdated + Lost-Carrier aléatoires → peut être combiné avec problème de ligne
- Firmware Up to date + Lost-Carrier → chercher autre cause (ligne, câble, alimentation)

Solution immédiate:
1. reboot_device(serial_number) → redémarre, résout temporairement
2. create_ticket(priority=HAUTE, type=Firmware obsolète)
   → Description: "Firmware {version} < minimum {min_version}. Mise à jour TR-069 requise."
3. Message client: "J'ai redémarré votre routeur. Cela devrait stabiliser votre connexion
   pour quelques heures, mais une mise à jour de votre équipement est nécessaire.
   Un technicien vous contactera pour planifier l'intervention."

Note: la mise à jour firmware via TR-069 nécessite une intervention technique planifiée.
Le support agent ne peut pas déclencher une mise à jour firmware directement.

Escalade: ticket HAUTE systématiquement pour tout firmware obsolète avec instabilité.""",
        "metadata": {
            "category": "device",
            "type": "Firmware-Outdated",
            "priority_threshold": 0,
            "tools_to_use": "check_firmware_version, get_session_history, reboot_device, create_ticket",
        },
    },

    {
        "id": "doc_mtu_mismatch",
        "content": """Problème MTU — vidéos et gros fichiers bloqués mais navigation OK.
Symptôme: la navigation web basique fonctionne, mais les vidéos YouTube/Netflix bufferisent,
les gros téléchargements s'arrêtent, certains sites ne chargent pas complètement.
C'est le symptôme classique d'un MTU mal configuré.

Explication technique:
- PPPoE ajoute 8 octets d'overhead par paquet
- MTU correct pour PPPoE = 1492 octets (1500 - 8)
- Si MTU = 1500 sur une ligne PPPoE, les gros paquets sont fragmentés ou rejetés
- Les petits paquets (pages web simples) passent, les gros (vidéo, téléchargements) échouent

Diagnostic:
1. get_device_parameters(serial_number) → vérifier MTU configured
   → Si MTU = 1500 sur connexion PPPoE → cause confirmée
2. get_plan_info(username) → confirmer que c'est une connexion PPPoE
3. Confirmer avec le client: "La navigation web basique fonctionne mais les vidéos ou gros
   fichiers sont bloqués ou très lents?"

Solution:
1. set_mtu(serial_number, 1492)
2. force_connection_request(serial_number) → forcer reconnexion avec nouveau MTU
3. Attendre 30-60 secondes
4. get_device(serial_number) → vérifier Online
5. Message client: "J'ai corrigé la configuration MTU de votre routeur (1500→1492).
   Votre connexion va se rétablir dans 30 secondes. Les vidéos et téléchargements
   devraient maintenant fonctionner normalement."

Valeurs MTU de référence:
- PPPoE: 1492 (obligatoire)
- DHCP/IPoE: 1500 (standard)
- Tunnel VPN: 1400 (conseillé)

Escalade: si set_mtu réussit mais problème persiste → create_ticket(priority=MOYENNE)
Le problème peut alors être côté réseau FAI (fragmentation en amont).""",
        "metadata": {
            "category": "device",
            "type": "MTU-Mismatch",
            "priority_threshold": 0,
            "tools_to_use": "get_device_parameters, set_mtu, force_connection_request, get_device",
        },
    },

    {
        "id": "doc_line_quality_snr",
        "content": """Qualité de ligne DSL dégradée — SNR bas et atténuation élevée.
Symptôme: connexion instable, débit très inférieur au plan, déconnexions fréquentes.
get_device_parameters retourne SNR faible ou atténuation élevée.

Interprétation SNR (Signal-to-Noise Ratio):
- SNR ≥ 20 dB → Excellente (connexion très stable)
- SNR 15-19 dB → Bonne (connexion stable)
- SNR 10-14 dB → Correcte (quelques instabilités possibles)
- SNR 6-9 dB → Marginale (instabilité fréquente, débit réduit)
- SNR < 6 dB → Mauvaise (déconnexions certaines, intervention requise)

Interprétation atténuation:
- < 20 dB → Excellente (ligne courte)
- 20-40 dB → Normale
- 40-55 dB → Élevée (ligne longue, débit limité)
- > 55 dB → Très élevée (intervention terrain requise)

Causes d'un SNR faible:
- Distance entre le DSLAM et le domicile trop grande
- Câble téléphonique de mauvaise qualité ou endommagé
- Interférences électromagnétiques (appareils électriques proches de la ligne)
- Connexions parasites sur la ligne (splitter défectueux)
- Humidité dans les câbles ou les prises

Procédure:
1. get_device_parameters(serial_number) → relever SNR et atténuation
2. Comparer avec les valeurs de référence ci-dessus
3. Si SNR < 6 dB: reboot_device() puis create_ticket(priority=HAUTE)
4. Si SNR 6-10 dB: reboot_device() + conseiller Ethernet + surveiller
5. Documenter les valeurs exactes dans le ticket pour le technicien terrain

Message technicien dans le ticket:
"SNR downstream: X dB, atténuation: Y dB. Intervention terrain requise
pour vérifier qualité ligne et câblage interne."

Escalade: ticket HAUTE systématiquement si SNR < 6 dB.""",
        "metadata": {
            "category": "device",
            "type": "Line-Quality",
            "priority_threshold": 6,
            "tools_to_use": "get_device_parameters, reboot_device, create_ticket",
        },
    },

    # ══════════════════════════════════════════
    # CATEGORY 4: PERFORMANCE
    # ══════════════════════════════════════════

    {
        "id": "doc_slow_speed",
        "content": """Lenteur de connexion — débit insuffisant.
Symptôme: client signale internet lent, vidéos qui bufferisent, téléchargements lents.
Avant tout diagnostic technique, vérifier si la vitesse correspond simplement au plan souscrit.

Ordre de diagnostic obligatoire:
1. get_plan_info(username) → connaître le débit max du plan
2. get_network_metrics(username) → mesurer le débit réel actuel
3. Comparer: débit réel vs débit plan

Interprétation comparaison:
- Débit réel ≥ 80% du plan → vitesse normale, expliquer les limites du plan
- Débit réel 50-79% du plan → dégradation modérée, investiguer
- Débit réel < 50% du plan → dégradation significative, action requise

Si débit conforme au plan:
→ Expliquer: "Votre connexion fonctionne normalement à X Mbps, ce qui correspond
  à votre plan actuel. Si vous souhaitez plus de débit, je peux vous présenter
  nos offres supérieures."
→ Ne pas ouvrir de ticket, ne pas rebooter

Si débit inférieur au plan:
1. get_device_parameters(serial_number) → vérifier SNR et état de la ligne
2. Si SNR < 6 dB → scenario doc_line_quality_snr
3. Si SNR correct → reboot_device(serial_number)
4. Attendre réponse client après reboot
5. Si toujours lent: create_ticket(priority=HAUTE si SNR<6dB, MOYENNE sinon)

Facteurs qui réduisent le débit mesurable:
- WiFi vs Ethernet (WiFi peut réduire de 30-50%)
- Distance au routeur WiFi
- Nombre d'appareils connectés simultanément
- Heure de pointe (18h-22h) → congestion normale

Escalade: ticket HAUTE si débit < 50% du plan ET SNR correct (problème réseau FAI).""",
        "metadata": {
            "category": "performance",
            "type": "Low-Bandwidth",
            "priority_threshold": 0,
            "tools_to_use": "get_plan_info, get_network_metrics, get_device_parameters, reboot_device",
        },
    },

    {
        "id": "doc_wifi_vs_ethernet",
        "content": """Distinction WiFi vs connexion internet — diagnostic essentiel.
Problème fréquent: client signale "internet lent" ou "coupures" mais le problème
vient en réalité du WiFi, pas de la connexion internet elle-même.

Test de confirmation obligatoire pour toute plainte de lenteur ou coupure:
Demander au client: "Est-ce que le problème se produit aussi quand votre appareil
est connecté directement au routeur par câble Ethernet?"

Interprétation:
- Câble Ethernet = bon, WiFi = problème → problème WiFi local, pas la ligne
- Câble Ethernet = problème aussi → problème de ligne ou connexion confirmé

Si problème WiFi uniquement:
Causes possibles:
- Distance trop grande entre l'appareil et le routeur
- Obstacles (murs épais, béton armé)
- Interférences (voisins sur le même canal WiFi, fours micro-ondes)
- Bande 2.4 GHz saturée (trop d'appareils)

Conseils à donner au client:
1. "Rapprochez-vous du routeur pour tester"
2. "Utilisez la bande 5 GHz si votre appareil le supporte (plus rapide, moins d'interférences)"
3. "Pour un usage intensif (streaming, gaming), privilégiez le câble Ethernet"
4. "Redémarrez le routeur pour forcer un changement de canal WiFi automatique"

Actions techniques possibles:
- reboot_device() → routeur choisit automatiquement le canal WiFi le moins encombré
- Si persistant: create_ticket(priority=BASSE) pour configuration WiFi par technicien

Ne pas ouvrir un ticket HAUTE pour un problème purement WiFi.
Escalade: ticket BASSE uniquement si tous les conseils ont été suivis sans amélioration.""",
        "metadata": {
            "category": "performance",
            "type": "WiFi-Issue",
            "priority_threshold": 0,
            "tools_to_use": "reboot_device, create_ticket",
        },
    },

    {
        "id": "doc_latency_packet_loss",
        "content": """Latence élevée et perte de paquets — instabilité réseau.
Symptôme: ping élevé, jeux en ligne laggy, appels VoIP qui se coupent,
visioconférence avec saccades. Différent d'une lenteur de débit.

Rappel: latence ≠ débit
- Débit = combien de données passent (Mbps)
- Latence = combien de temps ça prend (ms)
Un client peut avoir 100 Mbps mais 200ms de latence → bon débit, mauvaise latence.

Valeurs de référence latence:
- < 20 ms → Excellente (gaming, VoIP parfait)
- 20-50 ms → Bonne (usage standard)
- 50-100 ms → Acceptable (peut affecter gaming)
- > 100 ms → Élevée (VoIP dégradé, gaming difficile)
- > 200 ms → Critique (VoIP inutilisable, gaming impossible)

Valeurs de référence perte de paquets:
- 0% → Parfait
- < 1% → Acceptable
- 1-5% → Dégradé (VoIP affecté)
- > 5% → Critique (toutes applications affectées)

Procédure:
1. get_network_metrics(username) → relever latence et packet_loss
2. get_device_parameters(serial_number) → vérifier SNR et qualité ligne
3. get_session_history(username) → chercher des déconnexions récentes

Solutions:
- Packet loss > 2%: reboot_device() + surveiller
- Latence > 100ms + SNR correct: problème réseau FAI → create_ticket(priority=HAUTE)
- Latence > 100ms + SNR < 6dB: problème ligne → create_ticket(priority=HAUTE)
- WiFi seul: voir doc_wifi_vs_ethernet

Escalade: ticket HAUTE si packet loss > 5% ou latence > 200ms.""",
        "metadata": {
            "category": "performance",
            "type": "Latency-PacketLoss",
            "priority_threshold": 5,
            "tools_to_use": "get_network_metrics, get_device_parameters, reboot_device, create_ticket",
        },
    },

    # ══════════════════════════════════════════
    # CATEGORY 5: ACCOUNT / BILLING
    # ══════════════════════════════════════════

    {
        "id": "doc_account_suspended",
        "content": """Compte suspendu — impayé ou suspension administrative.
Symptôme: check_account_status retourne "suspendu". Client signale une perte totale de connexion
sans cause technique apparente. Tous les diagnostics techniques reviennent normaux.

Caractéristiques d'une suspension:
- Connexion coupée d'un coup, sans dégradation progressive
- get_auth_failures peut montrer Access-Reject (RADIUS bloque le compte)
- get_device peut montrer Online (routeur fonctionne mais RADIUS refuse l'accès)
- Différent d'un problème technique: tout l'équipement est en ordre

Règle absolue: ne jamais effectuer de diagnostic technique sur un compte suspendu.
Un reboot, un push_pppoe ou un set_mtu ne résoudra pas une suspension.

Procédure:
1. check_account_status(username) → confirmer la suspension
2. Si suspendu: STOP — ne pas continuer le diagnostic technique
3. Message au client:
   "Votre compte est actuellement suspendu suite à un impayé ou une décision administrative.
   Je ne peux pas résoudre ce problème depuis le support technique.
   Veuillez contacter notre service facturation au 71 XXX XXX
   du lundi au vendredi de 8h à 17h pour régulariser votre situation."

Si le client insiste que c'est une erreur:
→ create_ticket(priority=MOYENNE, type=Compte suspendu)
→ Description: "Client conteste la suspension. Vérification facturation requise."

Ne jamais:
- Promettre de réactiver le compte depuis le support technique
- Effectuer des actions techniques sur un compte suspendu
- Communiquer les détails de la dette ou du motif de suspension

Escalade: redirection systématique vers facturation. Ticket si le client conteste.""",
        "metadata": {
            "category": "account",
            "type": "Account-Suspended",
            "priority_threshold": 0,
            "tools_to_use": "check_account_status",
        },
    },

    {
        "id": "doc_quota_exhausted",
        "content": """Quota de données épuisé — connexion coupée par le plan.
Symptôme: client avait internet, connexion coupée à un moment précis.
Session history montre Session-Timeout. Plan a un attribut Max-All-Session ou quota données.

Différence quota vs suspension:
- Quota épuisé = limite technique du plan atteinte (reset possible)
- Suspension = décision administrative ou impayé (nécessite facturation)

Procédure de confirmation:
1. get_session_history(username) → confirmer Session-Timeout comme dernière cause
2. get_plan_info(username) → confirmer présence d'attribut Session-Timeout ou Max-All-Session
3. Calculer: fréquence des Session-Timeout = usage intensif du quota?

Solution:
1. Expliquer la situation: "Votre quota de données pour aujourd'hui est épuisé.
   C'est une limitation de votre plan actuel."
2. Proposer le reset: "Je peux réinitialiser votre quota pour aujourd'hui.
   Cela vous permettra de vous reconnecter jusqu'à minuit,
   mais votre quota normal reprendra demain."
3. Si le client accepte: reset_daily_quota(username)
4. Informer: "Quota réinitialisé. Reconnectez-vous — votre connexion sera rétablie
   dans 30 secondes."

Proposer une mise à niveau si récurrent:
"Si ce problème se reproduit souvent, notre plan Premium offre un quota
10 fois supérieur. Souhaitez-vous que je vous présente les détails?"
→ Si intéressé: change_user_group(username, new_group) avec confirmation explicite

Escalade: pas de ticket nécessaire sauf si reset_daily_quota échoue.""",
        "metadata": {
            "category": "account",
            "type": "Quota-Exhausted",
            "priority_threshold": 0,
            "tools_to_use": "get_session_history, get_plan_info, reset_daily_quota, change_user_group",
        },
    },

    {
        "id": "doc_plan_upgrade",
        "content": """Changement de plan — mise à niveau ou déclassement.
Contexte: client demande à changer de plan, ou l'agent propose une mise à niveau
suite à des problèmes récurrents de quota ou de débit insuffisant.

Règle: change_user_group nécessite toujours une confirmation explicite du client.
Un changement de plan affecte la facturation — ne jamais faire sans accord.

Procédure:
1. get_plan_info(username) → identifier le plan actuel
2. Présenter les options disponibles au client
3. Obtenir confirmation explicite: "Confirmez-vous le changement de votre plan
   de [plan actuel] vers [nouveau plan]? Ce changement affectera votre prochaine facture."
4. Si confirmation: change_user_group(username, new_group)
5. Message: "Votre plan a été mis à jour vers [nouveau plan].
   Le nouveau débit prendra effet à votre prochaine reconnexion."
6. disconnect_session(username) → forcer reconnexion avec nouveau profil RADIUS
7. force_connection_request(serial_number) → vérifier la reconnexion

Plans disponibles (adapter selon votre catalogue):
- basic → débit standard, quota journalier
- premium → débit élevé, quota important
- unlimited → débit maximum, sans quota

Note: les changements de plan sont irréversibles sans contact commercial.
Documenter le changement dans le ticket si nécessaire.

Escalade: ticket si le client signale que le nouveau plan ne s'applique pas après reconnexion.""",
        "metadata": {
            "category": "account",
            "type": "Plan-Change",
            "priority_threshold": 0,
            "tools_to_use": "get_plan_info, change_user_group, disconnect_session, force_connection_request",
        },
    },

    # ══════════════════════════════════════════
    # CATEGORY 6: PROCEDURES
    # ══════════════════════════════════════════

    {
        "id": "doc_general_procedure",
        "content": """Procédure générale de support ISP — ordre d'investigation.
Cette procédure s'applique à tout problème signalé par un client.

Étapes obligatoires dans l'ordre:
1. find_customer(CIN ou téléphone) → identifier le client et récupérer username + serial
2. check_account_status(username) → si suspendu: STOP, rediriger facturation
3. check_region_outage(city) → si panne active: STOP, informer le client
4. Si pas de panne et compte actif, continuer avec:
   get_session_history(username)
   get_auth_failures(username)
   get_device(serial_number)
   get_plan_info(username)
   search_troubleshooting_guide(description du problème)
5. Si lenteur/déconnexions: ajouter get_device_parameters + get_network_metrics
6. Choisir le bon scénario selon les résultats
7. Appliquer la solution, attendre retour client
8. Si problème persiste: create_ticket()

Règles générales:
- Toujours tenter une intervention avant d'ouvrir un ticket
- Ne jamais inventer un numéro de ticket — toujours appeler create_ticket()
- Ne jamais révéler les mots de passe en clair
- Une seule question à la fois au client
- Utiliser le prénom du client pour personnaliser

Priorités de ticket:
- HAUTE: SNR < 6 dB, firmware obsolète, offline > 24h, auth failures > 10, packet loss > 5%
- MOYENNE: offline < 24h, 2-5 Lost-Carrier/jour, session zombie persistante, débit 50-80% plan
- BASSE: lenteur conforme au plan, questions sur le plan, WiFi seul

Escalade systématique:
- Panne matérielle suspectée (Power rouge) → ticket HAUTE + intervention terrain
- Problème persiste après 2 interventions → ticket HAUTE
- Client demande un technicien → create_ticket() immédiatement""",
        "metadata": {
            "category": "procedure",
            "type": "General",
            "priority_threshold": 0,
            "tools_to_use": "all",
        },
    },

    {
        "id": "doc_outage_procedure",
        "content": """Procédure panne régionale — comment gérer une panne active.
Contexte: check_region_outage retourne une panne active dans la région du client.

Règle absolue: ne jamais effectuer de diagnostic ou d'intervention technique
pendant une panne régionale. Tout effort est inutile — le problème est du côté FAI.

Ce qu'il ne faut PAS faire pendant une panne:
- Rebooter le routeur (inutile, la ligne réseau est coupée)
- Pousser des credentials (inutile)
- Ouvrir un ticket individuel (la panne est déjà enregistrée)
- Promettre une heure de résolution précise si l'ETA est incertain

Ce qu'il FAUT faire:
1. Confirmer la panne avec les détails retournés par check_region_outage
2. Informer le client de manière empathique et claire:
   "Nous avons détecté une panne technique active dans votre région [région].
   [Description de la panne]
   Délai estimé de résolution: [ETA ou 'en cours d'évaluation'].
   Nos équipes techniques travaillent activement à la résolution.
   Nous nous excusons sincèrement pour la gêne occasionnée."
3. Ne pas demander de diagnostic supplémentaire
4. Si le client insiste pour une intervention: expliquer que c'est inutile pendant une panne
5. Proposer un suivi: "Nous vous enverrons un SMS dès que la panne sera résolue."

Après la résolution de la panne:
- Si le client rappelle et la connexion ne revient pas → lancer le diagnostic normal
- Le problème peut être résiduel (session zombie, credentials) → appliquer les procédures standard

Note: si la panne est résolue mais un client spécifique n'a toujours pas de connexion
→ lancer le diagnostic complet, le problème est probablement individuel.""",
        "metadata": {
            "category": "procedure",
            "type": "Regional-Outage",
            "priority_threshold": 0,
            "tools_to_use": "check_region_outage",
        },
    },

    {
        "id": "doc_escalation_criteria",
        "content": """Critères d'escalade et de création de tickets — guide complet.
Quand créer un ticket et avec quelle priorité.

Priorité CRITIQUE:
- Panne matérielle confirmée (Power LED rouge, routeur ne démarre plus)
- Offline depuis plus de 48h sans cause identifiée
- Problème affectant plusieurs clients dans la même zone (hors panne déclarée)

Priorité HAUTE:
- SNR downstream < 6 dB (ligne dégradée, intervention terrain requise)
- Auth failures > 10 en 24h (après réinitialisation credentials échouée)
- Offline depuis plus de 24h
- Lost-Carrier > 5 par jour
- Firmware obsolète avec instabilité confirmée
- Packet loss > 5%
- Latence > 200ms persistante
- Toute intervention technique ayant échoué 2 fois

Priorité MOYENNE:
- Offline depuis 3-24h
- Lost-Carrier 2-5 par jour
- Session zombie persistante après disconnect_session
- Débit mesuré entre 50-80% du plan garanti
- Packet loss 2-5%
- Latence 100-200ms
- Firmware obsolète sans instabilité immédiate

Priorité BASSE:
- Lenteur conforme au plan (client sous-estime son plan)
- Problème WiFi uniquement
- Questions sur le plan ou demande de changement
- Lost-Carrier < 2 par semaine

Contenu obligatoire d'un bon ticket:
- username et serial_number
- Description précise du problème et des symptômes
- Valeurs relevées: SNR, firmware, nombre de failures, durée offline
- Actions déjà effectuées et leurs résultats
- Comportement du client (confirmé le reset? refuse l'intervention?)

Ne jamais créer un ticket sans avoir d'abord tenté au moins une intervention.
Exception: compte suspendu, panne régionale, panne matérielle → ticket direct.""",
        "metadata": {
            "category": "procedure",
            "type": "Escalation",
            "priority_threshold": 0,
            "tools_to_use": "create_ticket",
        },
    },

    {
        "id": "doc_factory_reset_procedure",
        "content": """Procédure réinitialisation d'usine — dernier recours.
Contexte: factory_reset_device est l'action la plus destructive disponible.
À utiliser UNIQUEMENT quand toutes les autres interventions ont échoué.

Conditions pour déclencher un factory reset:
1. reboot_device a été tenté → connexion non rétablie
2. push_pppoe_credentials a été tenté → auth failures persistent
3. set_mtu a été tenté si pertinent → problème persiste
4. Le problème est confirmé être côté configuration routeur, pas côté ligne ou RADIUS
5. Le client a été informé des conséquences et donne son accord explicite

Conséquences du factory reset:
- Toute la configuration WiFi est effacée (SSID, mot de passe WiFi)
- Les credentials PPPoE sont effacés
- Tous les paramètres personnalisés sont perdus
- Le routeur revient à son état d'usine

Message de confirmation obligatoire au client AVANT d'exécuter:
"Avant de procéder, je dois vous informer que cette action va effacer TOUTE
la configuration de votre routeur: votre réseau WiFi, son mot de passe,
et tous vos paramètres personnalisés. Votre routeur sera remis à zéro.
Après la réinitialisation, je reconfigurerai automatiquement votre connexion
internet, mais vous devrez reconfigurer votre WiFi.
Confirmez-vous cette réinitialisation complète?"

Procédure après confirmation:
1. factory_reset_device_safe(serial_number) → exécuter seulement après "oui"
2. Attendre 3-5 minutes (re-provisioning automatique GenieACS)
3. get_radius_user(username) → récupérer les credentials actuels
4. push_pppoe_credentials(serial_number, username, password) → re-provisionner
5. force_connection_request(serial_number) → vérifier reconnexion
6. get_device(serial_number) → confirmer Online
7. Si Online: "Votre routeur est réinitialisé et votre connexion internet est rétablie.
   Vous devez maintenant reconfigurer votre réseau WiFi depuis l'interface du routeur."
8. Si toujours Offline après 10 min: create_ticket(priority=HAUTE)

Escalade: ticket HAUTE si le routeur ne revient pas online après factory reset.""",
        "metadata": {
            "category": "procedure",
            "type": "Factory-Reset",
            "priority_threshold": 0,
            "tools_to_use": "factory_reset_device_safe, push_pppoe_credentials, force_connection_request, get_device",
        },
    },
]


def ingest():
    # Parse host and port from CHROMA_URL
    url  = CHROMA_URL.replace("http://", "").replace("https://", "")
    host = url.split(":")[0]
    port = int(url.split(":")[-1]) if ":" in url else 8000

    client = chromadb.HttpClient(host=host, port=port)

    # Must use DefaultEmbeddingFunction — matches rag_tools.py
    ef = embedding_functions.DefaultEmbeddingFunction()

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    ids       = [d["id"]       for d in DOCUMENTS]
    contents  = [d["content"]  for d in DOCUMENTS]
    metadatas = [d["metadata"] for d in DOCUMENTS]

    # Upsert — safe to run multiple times, updates existing docs
    collection.upsert(documents=contents, ids=ids, metadatas=metadatas)

    print(f"✅ Ingested {len(DOCUMENTS)} documents into '{COLLECTION_NAME}'")
    print(f"   Collection total: {collection.count()} documents")
    print()
    print("Documents ingested:")
    for d in DOCUMENTS:
        print(f"  - {d['id']} [{d['metadata']['category']}]")


if __name__ == "__main__":
    ingest()