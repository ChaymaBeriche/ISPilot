"""
tools/rag_tools.py — RAG tools for ISP troubleshooting knowledge base.

Two retrieval modes:
1. search_troubleshooting_guide() — semantic search across all documents
   Used during diagnostics BEFORE scenario is known.

2. get_scenario_guide() — targeted retrieval by scenario
   Used AFTER scenario is selected. Filters ChromaDB by metadata 'type'
   field to load only the relevant document. No semantic search needed.
"""

import threading
import os
import re
import chromadb
from chromadb.utils import embedding_functions
from langchain_core.tools import tool

CHROMA_HOST     = os.getenv("CHROMA_HOST", "chromadb")
CHROMA_PORT     = int(os.getenv("CHROMA_PORT", "8000"))
COLLECTION_NAME = "isp_troubleshooting"
MIN_RELEVANCE   = 40.0

# Adaptive gating for the reformulate+rerank pipeline (see _retrieve_candidates
# below): when the raw, non-reformulated top-candidate cosine relevance
# already clears this bar, plain semantic search is trusted as-is and
# reformulation + LLM reranking (both LLM calls) are skipped entirely.
#
# Measured via evaluate_retrieval.py against this collection's 21 documents:
# all 12 labeled test queries had a CORRECT top-1 semantic match, with raw
# top relevance ranging 46.2%-71.9% (comfortably above MIN_RELEVANCE=40.0).
# This corpus is small enough, and each scenario maps cleanly enough to a
# single document, that plain cosine search already hits 100% precision —
# unconditional hybrid (reformulate+rerank on every call) measured 91.7%,
# i.e. it occasionally second-guessed an already-correct match. The gate is
# set just below that observed floor (46.2) so every one of those confident
# matches skips the LLM calls, while a query scoring below it (near the
# MIN_RELEVANCE noise floor, where plain semantic search can't be trusted)
# still gets a chance at rescue via reformulation + rerank.
RERANK_GATE_THRESHOLD = 45.0

# Extended knowledge base (RFC/protocol, vendor docs, DSL thresholds,
# offers/pricing, installation procedures) — separate collection, additive only.
EXTENDED_COLLECTION_NAME = "isp_knowledge_extended"
# Separate (lower) threshold for the extended collection only — does not
# affect MIN_RELEVANCE / search_troubleshooting_guide above. Customer
# queries are French, translated to English before search_extended_knowledge
# is called; even a correctly-matched translated query against this corpus
# empirically scores lower (~35-50%) than same-language queries typically
# would, while genuinely irrelevant matches still score ~15-20% — so 30%
# keeps a solid margin against false positives while not rejecting real hits.
MIN_RELEVANCE_EXTENDED = 30.0

# Same gating idea as RERANK_GATE_THRESHOLD above, but for
# isp_knowledge_extended. This corpus (RFCs, vendor docs, mixed topics) is
# noisier — measured raw top-1 relevance across the 12 labeled test queries
# ranged 10.5%-47.1%, mostly clustered well below any confident zone, with
# only ONE query (pppoe_auth_failure, 47.1%) already correct under plain
# semantic search. Unconditional hybrid measurably helped here (58.3% vs
# 50.0% semantic-only), so the gate is set just above that single
# high-confidence outlier rather than tuned down to skip hybrid broadly —
# reformulation + reranking still fires for ~11 of 12 test queries, where
# it's actually earning its LLM-call cost.
RERANK_GATE_THRESHOLD_EXTENDED = 45.0

# ─────────────────────────────────────────────
# Scenario → ChromaDB document type mapping
#
# Maps our internal scenario names to the 'type'
# metadata field stored in ChromaDB documents.
# Each scenario loads exactly one targeted document.
# ─────────────────────────────────────────────

SCENARIO_TO_DOC_TYPE = {
    # Authentication scenarios
    "auth_failure":      "Access-Reject",
    # Disconnection scenarios
    "zombie_session":    "Zombie-Session",
    "quota_exhausted":   "Session-Timeout",
    "plan_upgrade":      "Plan-Change",
    "line_instability":  "Lost-Carrier",
    "firmware_obsolete": "Firmware-Outdated",
    # Device / line scenarios
    "offline_physical":  "Device-Offline",
    "snr_critical":      "Line-Quality",
    "snr_marginal":      "Line-Quality",
    "mtu_mismatch":      "MTU-Mismatch",
    # Performance scenarios
    "slow_speed":        "Low-Bandwidth",
    "high_latency":      "Latency-PacketLoss",
    "wifi_confusion":    "WiFi-Issue",
    # Account scenarios
    "suspended":         "Account-Suspended",
    # Procedure scenarios
    "general":           "General",
    "factory_reset":     "Factory-Reset",
    "outage_active":     "Regional-Outage",
}

# ─────────────────────────────────────────────
# Query reformulation + LLM reranking
#
# Both helpers use the LLM already configured in agent_graph.py (imported
# lazily, inside the function body, to avoid a circular import — agent_graph
# imports this module at load time, before its `llm` object is defined).
# Both degrade gracefully to the pre-existing behavior on any failure so a
# reformulation/rerank problem never breaks a retrieval tool.
# ─────────────────────────────────────────────

def _reformulate_query(raw_query: str) -> str:
    """
    Reformulate a raw customer query into more technical, keyword-rich
    search terms before embedding/search. French in, French out — this is
    NOT translation, just query expansion in the same language. Falls back
    to the original query on any failure (LLM unavailable, empty/unexpected
    output, etc.).
    """
    try:
        from agent_graph import llm
        from langchain_core.messages import SystemMessage, HumanMessage

        messages = [
            SystemMessage(content=(
                "Tu reformules la question d'un client FAI en termes techniques "
                "et riches en mots-clés, pour améliorer une recherche documentaire. "
                "Réponds TOUJOURS en français — ne traduis JAMAIS vers une autre "
                "langue. Réponds UNIQUEMENT avec la requête reformulée, sans "
                "guillemets ni explication."
            )),
            HumanMessage(content=raw_query),
        ]
        raw_output = llm.invoke(messages).content.strip()
        # qwen2.5:14b occasionally prepends stray reasoning (sometimes in
        # another script/language) before the actual answer despite the
        # "reply with ONLY X" instruction — the real answer still reliably
        # ends up as the last non-empty line, so take that defensively
        # rather than trusting the whole response verbatim.
        lines = [l.strip().strip('"') for l in raw_output.split("\n") if l.strip()]
        reformulated = lines[-1] if lines else ""
        if not reformulated or re.search(r"[　-鿿]", reformulated):
            return raw_query
        return reformulated
    except Exception:
        return raw_query


def _llm_rerank(original_query: str, candidates: list, top_n: int):
    """
    Ask the LLM to rerank/filter `candidates` (list of (doc, meta, relevance)
    tuples already fetched from ChromaDB) for relevance to the ORIGINAL,
    non-reformulated customer query. Returns up to `top_n` tuples from
    `candidates`, most relevant first, or None if the LLM call/parse fails
    or selects nothing — callers should fall back to the existing
    cosine-relevance-threshold behavior in that case.
    """
    if not candidates:
        return None
    try:
        from agent_graph import llm
        from langchain_core.messages import SystemMessage, HumanMessage

        listing = "\n".join(
            f"{i}: {doc[:300].replace(chr(10), ' ')}"
            for i, (doc, _meta, _relevance) in enumerate(candidates)
        )
        messages = [
            SystemMessage(content=(
                "Tu classes des extraits de documentation technique par pertinence "
                "pour la question d'un client. Voici une liste numérotée d'extraits. "
                f"Renvoie UNIQUEMENT les indices des {top_n} extraits les plus "
                "pertinents pour la question, du plus au moins pertinent, séparés "
                "par des virgules (exemple: '2,0,4'). S'il y a moins d'extraits "
                "réellement pertinents, renvoie-en moins. N'écris rien d'autre, "
                "aucune explication."
            )),
            HumanMessage(content=f"Question du client: {original_query}\n\nExtraits:\n{listing}"),
        ]
        raw = llm.invoke(messages).content.strip()
        indices = [int(x) for x in re.findall(r"\d+", raw)]

        selected = []
        for idx in indices:
            if 0 <= idx < len(candidates) and idx not in selected:
                selected.append(idx)
            if len(selected) >= top_n:
                break

        if not selected:
            return None
        return [candidates[i] for i in selected]
    except Exception:
        return None


def _retrieve_candidates(collection, raw_query, n_fetch, top_n, min_relevance, gate_threshold):
    """
    Adaptive retrieval used by both search_troubleshooting_guide() and
    search_extended_knowledge(). Runs the raw (non-reformulated) semantic
    query first; if its top-candidate cosine relevance already clears
    `gate_threshold`, plain semantic search is trusted and reformulation +
    LLM reranking are skipped entirely (no LLM call). Otherwise, the query
    is reformulated and the reformulated candidates are reranked against the
    ORIGINAL query, falling back to cosine-threshold filtering if the LLM
    call/parse fails — same degrade-gracefully behavior as before gating.

    Returns a list of (doc, meta, relevance) tuples (possibly empty if
    nothing clears `min_relevance`), or None if the collection returned no
    documents at all for the raw query.
    """
    raw_results = collection.query(
        query_texts=[raw_query],
        n_results=n_fetch,
        include=["documents", "metadatas", "distances"],
    )
    if not raw_results["documents"] or not raw_results["documents"][0]:
        return None

    raw_candidates = [
        (doc, meta, round((1 - distance) * 100, 1))
        for doc, meta, distance in zip(
            raw_results["documents"][0],
            raw_results["metadatas"][0],
            raw_results["distances"][0],
        )
    ]

    top_relevance = raw_candidates[0][2]
    if top_relevance >= gate_threshold:
        return [c for c in raw_candidates if c[2] >= min_relevance][:top_n]

    search_query = _reformulate_query(raw_query)
    candidates = raw_candidates
    if search_query != raw_query:
        reform_results = collection.query(
            query_texts=[search_query],
            n_results=n_fetch,
            include=["documents", "metadatas", "distances"],
        )
        if reform_results["documents"] and reform_results["documents"][0]:
            candidates = [
                (doc, meta, round((1 - distance) * 100, 1))
                for doc, meta, distance in zip(
                    reform_results["documents"][0],
                    reform_results["metadatas"][0],
                    reform_results["distances"][0],
                )
            ]

    reranked = _llm_rerank(raw_query, candidates, top_n=top_n)
    if reranked is not None:
        return reranked
    return [c for c in candidates if c[2] >= min_relevance][:top_n]


# ─────────────────────────────────────────────
# Lazy initialization with thread safety
# ─────────────────────────────────────────────

_client     = None
_collection = None
_lock       = threading.Lock()


def _get_collection():
    """
    Return ChromaDB collection, initializing if needed.
    Thread-safe. Resets on failure so next call retries.
    """
    global _client, _collection
    with _lock:
        if _collection is None:
            _client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
            ef = embedding_functions.DefaultEmbeddingFunction()
            _collection = _client.get_or_create_collection(
                name=COLLECTION_NAME,
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
    return _collection


# ─────────────────────────────────────────────
# Tool 1: Semantic search (used before scenario known)
# ─────────────────────────────────────────────

@tool
def search_troubleshooting_guide(problem_description: str) -> str:
    """
    Search the ISP knowledge base for troubleshooting steps relevant to a problem.
    Use during initial diagnostics BEFORE the scenario has been identified.
    Good queries: "déconnexions Lost-Carrier répétées", "échecs Access-Reject",
    "Session-Timeout quota épuisé", "routeur offline", "lenteur débit insuffisant".
    Returns recommended diagnostic steps and escalation criteria.
    """
    global _collection
    try:
        collection = _get_collection()
        useful_results = _retrieve_candidates(
            collection, problem_description,
            n_fetch=6, top_n=2,
            min_relevance=MIN_RELEVANCE,
            gate_threshold=RERANK_GATE_THRESHOLD,
        )

        if useful_results is None:
            return (
                "Aucune procédure trouvée dans la base de connaissances. "
                "Appliquer la procédure générale: "
                "get_session_history → get_auth_failures → get_device → get_device_parameters."
            )

        if not useful_results:
            return (
                "Aucune procédure suffisamment pertinente trouvée. "
                "Appliquer la procédure générale: "
                "get_session_history → get_auth_failures → get_device → get_device_parameters."
            )

        parts = []
        for doc, meta, relevance in useful_results:
            category = meta.get("category", "général")
            parts.append(f"[Catégorie: {category} | Pertinence: {relevance}%]\n{doc}")

        return "\n\n".join(parts)

    except Exception as e:
        with _lock:
            _collection = None
        return (
            "[RAG_UNAVAILABLE] Base de connaissances inaccessible. "
            f"Détail: {str(e)}"
        )


# ─────────────────────────────────────────────
# Extended knowledge base — separate collection, additive only.
# Covers topics NOT in isp_troubleshooting: RFC/protocol details,
# vendor documentation, DSL line-quality thresholds, ISP offers/pricing,
# installation procedures.
# ─────────────────────────────────────────────

_ext_client     = None
_ext_collection = None
_ext_lock       = threading.Lock()


def _get_extended_collection():
    """
    Return the isp_knowledge_extended ChromaDB collection, initializing if needed.
    Thread-safe. Resets on failure so next call retries.
    Mirrors _get_collection() but targets the separate extended collection —
    never touches isp_troubleshooting.
    """
    global _ext_client, _ext_collection
    with _ext_lock:
        if _ext_collection is None:
            _ext_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
            ef = embedding_functions.DefaultEmbeddingFunction()
            _ext_collection = _ext_client.get_or_create_collection(
                name=EXTENDED_COLLECTION_NAME,
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
    return _ext_collection


@tool
def search_extended_knowledge(query: str, n_results: int = 3) -> str:
    """
    Search the extended ISP knowledge base for topics outside the standard
    diagnostic tree: RFC/protocol details, vendor documentation, DSL
    line-quality thresholds, ISP offers/pricing, and installation procedures.
    Use when a customer's question isn't covered by the standard diagnostic
    tools — e.g. pricing/offers, installation steps, or a technical concept
    the standard tools don't explain.
    Returns the most relevant extended knowledge base entries.
    """
    global _ext_collection
    try:
        collection = _get_extended_collection()
        n_fetch = max(n_results + 3, 6)
        useful_results = _retrieve_candidates(
            collection, query,
            n_fetch=n_fetch, top_n=n_results,
            min_relevance=MIN_RELEVANCE_EXTENDED,
            gate_threshold=RERANK_GATE_THRESHOLD_EXTENDED,
        )

        if useful_results is None:
            return "Aucune information trouvée dans la base de connaissances étendue."

        if not useful_results:
            return "Aucune information suffisamment pertinente trouvée dans la base de connaissances étendue."

        parts = []
        for doc, meta, relevance in useful_results:
            category = meta.get("category", "général")
            parts.append(f"[Catégorie: {category} | Pertinence: {relevance}%]\n{doc}")

        return "\n\n".join(parts)

    except Exception as e:
        with _ext_lock:
            _ext_collection = None
        return (
            "[RAG_UNAVAILABLE] Base de connaissances étendue inaccessible. "
            f"Détail: {str(e)}"
        )


# ─────────────────────────────────────────────
# EXPERIMENTAL — extended knowledge base, v2 collection with a multilingual
# embedding model. Side-by-side comparison only, not wired into any routing
# decision. Does not touch isp_troubleshooting or isp_knowledge_extended
# (v1) — separate collection, separate lazy client/lock, separate tool.
#
# Model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2.
# bge-m3 (~2.3GB) and multilingual-e5-large (similar size) were both
# considered, but a constrained network path could not reliably
# complete a multi-GB download (repeated mid-transfer SSL failures). This
# model (~470MB) needs no query/passage prefix convention (unlike e5) and
# is a well-established multilingual baseline.
#
# Uses a local copy at /models/... if one is mounted (optional, e.g. for
# offline machines); otherwise falls through to the plain HF repo id and
# SentenceTransformerEmbeddingFunction downloads it on first use.
# ─────────────────────────────────────────────

EXTENDED_V2_COLLECTION_NAME = "isp_knowledge_extended_v2"
_EXTENDED_V2_LOCAL_MODEL_PATH = "/models/paraphrase-multilingual-MiniLM-L12-v2"
_EXTENDED_V2_HF_MODEL_NAME    = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EXTENDED_V2_MODEL_NAME = (
    _EXTENDED_V2_LOCAL_MODEL_PATH
    if os.path.isdir(_EXTENDED_V2_LOCAL_MODEL_PATH)
    else _EXTENDED_V2_HF_MODEL_NAME
)
# Different embedding space than MiniLM — its cosine-distance distribution
# isn't comparable to MIN_RELEVANCE_EXTENDED. Left permissive (0) on purpose
# for this experiment so the comparison test in step 6 can see raw scores
# for every candidate rather than having some silently filtered out before
# we've established what a sane cutoff even looks like for this model.
MIN_RELEVANCE_EXTENDED_V2 = 0.0

_ext_v2_client     = None
_ext_v2_collection = None
_ext_v2_lock       = threading.Lock()


def _get_extended_v2_collection():
    """
    Return the isp_knowledge_extended_v2 ChromaDB collection, initializing
    if needed. Thread-safe. Resets on failure so next call retries.
    Mirrors _get_extended_collection() but targets the separate v2
    collection with a multilingual embedding model — never touches
    isp_troubleshooting or isp_knowledge_extended (v1).
    """
    global _ext_v2_client, _ext_v2_collection
    with _ext_v2_lock:
        if _ext_v2_collection is None:
            _ext_v2_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
            ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=EXTENDED_V2_MODEL_NAME
            )
            _ext_v2_collection = _ext_v2_client.get_or_create_collection(
                name=EXTENDED_V2_COLLECTION_NAME,
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
    return _ext_v2_collection


@tool
def search_extended_knowledge_v2(query: str, n_results: int = 3) -> str:
    """
    EXPERIMENTAL — same purpose as search_extended_knowledge (RFC/protocol,
    vendor docs, DSL thresholds, offers/pricing, installation procedures),
    but queries a separate collection embedded with a multilingual model
    (BAAI/bge-m3) instead of MiniLM, to test cross-lingual (French query vs
    English documents) retrieval quality. Not used by any routing decision
    yet — comparison only.
    """
    global _ext_v2_collection
    try:
        collection = _get_extended_v2_collection()
        results = collection.query(
            query_texts=[query],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )

        if not results["documents"] or not results["documents"][0]:
            return "Aucune information trouvée dans la base de connaissances étendue (v2)."

        useful_results = []
        for doc, meta, distance in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            relevance = round((1 - distance) * 100, 1)
            if relevance < MIN_RELEVANCE_EXTENDED_V2:
                continue
            useful_results.append((doc, meta, relevance))

        if not useful_results:
            return "Aucune information suffisamment pertinente trouvée dans la base de connaissances étendue (v2)."

        parts = []
        for doc, meta, relevance in useful_results:
            category = meta.get("category", "général")
            parts.append(f"[Catégorie: {category} | Pertinence: {relevance}%]\n{doc}")

        return "\n\n".join(parts)

    except Exception as e:
        with _ext_v2_lock:
            _ext_v2_collection = None
        return (
            "[RAG_UNAVAILABLE] Base de connaissances étendue v2 inaccessible. "
            f"Détail: {str(e)}"
        )


# ─────────────────────────────────────────────
# Tool 2: Targeted retrieval (used AFTER scenario known)
# ─────────────────────────────────────────────

def get_scenario_guide(scenario: str) -> str:
    """
    Load the specific troubleshooting guide for a detected scenario.
    Filters ChromaDB by the 'type' metadata field — no semantic search.
    Returns the exact procedure document for the scenario.

    This is more precise than semantic search because:
    - No noise from irrelevant documents
    - Always returns the correct procedure
    - Faster — metadata filter instead of vector search
    - Predictable — same scenario always loads same document
    """
    global _collection

    doc_type = SCENARIO_TO_DOC_TYPE.get(scenario)
    if not doc_type:
        return (
            f"Aucun guide spécifique pour le scénario '{scenario}'. "
            "Appliquer la procédure générale."
        )

    try:
        collection = _get_collection()

        # Targeted query: filter by 'type' metadata field
        # n_results=1 because we want exactly one document per scenario
        results = collection.get(
            where={"type": doc_type},
            include=["documents", "metadatas"],
        )

        if not results["documents"]:
            return (
                f"Guide '{doc_type}' non trouvé dans la base de connaissances. "
                "Appliquer la procédure générale."
            )

        # Return the first matching document
        doc      = results["documents"][0]
        meta     = results["metadatas"][0]
        category = meta.get("category", "général")

        return (
            f"[Guide ciblé: {doc_type} | Catégorie: {category}]\n"
            f"{doc}"
        )

    except Exception as e:
        with _lock:
            _collection = None
        return (
            "[RAG_UNAVAILABLE] Base de connaissances inaccessible. "
            f"Détail: {str(e)}"
        )