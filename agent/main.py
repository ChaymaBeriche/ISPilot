"""
main.py — FastAPI layer for ISP support agent with LangGraph.
Handles HTTP, session management, and customer identification.
All agent logic delegated to agent_graph.py.
"""

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from tools.isp_tools import find_customer
from agent_graph import run_graph
import io
import re
import os
import voice
from typing import Dict, Optional

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# Session store
#
# Each session holds:
#   customer        — identified customer dict
#   messages        — full conversation history
#   turn_count      — how many turns have happened
#   graph_state     — persisted graph state across turns
#   problem_resolved — whether customer confirmed resolution
# ─────────────────────────────────────────────

sessions: Dict[str, dict] = {}


def _empty_graph_state() -> dict:
    """Initial persisted graph state for a new session."""
    return {
        "intervention_done":      False,
        "snr_poor":               False,
        "diagnostics_done":       False,
        "scenario":               None,
        "session_history":        None,
        "auth_failures":          None,
        "device_status":          None,
        "plan_info":              None,
        "device_parameters":      None,
        "network_metrics":        None,
        "awaiting_factory_reset": False,
        "awaiting_plan_upgrade":  False,
        "outage_notified":       False,
        "account_status":         None,
        "intervention_result":    None,
    }


# ── API models ────────────────────────────────────────────────────────────────

class Query(BaseModel):
    message:    str
    session_id: str


class ClearSession(BaseModel):
    session_id: str


class SynthesizeRequest(BaseModel):
    text: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def looks_like_identifier(text: str) -> Optional[str]:
    """Extract CIN or Tunisian phone number from message."""
    # Only collapse separators sitting *between digits* (e.g. spoken/transcribed
    # "26, 27, 28, 29" -> "26272829"). Separators between words and digits are left
    # alone so \b boundaries below still work against surrounding text.
    normalized = re.sub(r'(?<=\d)[\s\-\.,;]+(?=\d)', '', text.strip())

    cin = re.search(r'\b(\d{8})\b', normalized)
    if cin:
        return cin.group(1)

    intl = re.search(r'(?:\+216|00216)(\d{8})', normalized)
    if intl:
        return intl.group(1)

    local = re.search(r'\b([2457923]\d{7})\b', normalized)
    if local:
        return local.group(1)

    leading_zero = re.search(r'\b0([2457923]\d{7})\b', normalized)
    if leading_zero:
        return leading_zero.group(1)

    return None


def run_find_customer(identifier: str) -> Optional[dict]:
    """Run find_customer and parse result into a dict."""
    result = find_customer.invoke({"identifier": identifier})
    if "Aucun client" in result or "non trouvé" in result.lower():
        return None
    data = {"raw": result}
    for line in result.split("\n"):
        if "Username:" in line:
            data["username"] = line.split(":")[-1].strip()
        elif "Nom:" in line:
            data["name"] = line.split(":", 1)[-1].strip()
        elif "Ville:" in line:
            data["city"] = line.split(":")[-1].strip()
        elif "Serial" in line or "serial" in line:
            data["serial"] = line.split(":")[-1].strip()
        elif "Region ISP:" in line or "Région ISP:" in line:
            data["region"] = line.split(":")[-1].strip()
        elif "Statut du compte:" in line:
            data["status"] = line.split(":")[-1].strip()
    return data


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "mode": "langgraph"}


# ── Voice endpoints ──────────────────────────────────────────────────────────
#
# Pure audio<->text utilities — no session state, no LangGraph. The UI
# transcribes a recording here first, then sends the resulting text through
# /ask exactly like a typed message.

@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    audio_bytes = await file.read()
    try:
        text = voice.transcribe(audio_bytes)
    except voice.VoiceInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except voice.VoiceUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"text": text}


@app.post("/synthesize")
def synthesize_speech(body: SynthesizeRequest):
    try:
        wav_bytes = voice.synthesize(body.text)
    except voice.VoiceInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except voice.VoiceUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return StreamingResponse(io.BytesIO(wav_bytes), media_type="audio/wav")


@app.post("/ask")
def ask(query: Query):
    session_id = query.session_id

    # Initialize session if new
    if session_id not in sessions:
        sessions[session_id] = {
            "messages":         [],
            "customer":         None,
            "turn_count":       0,
            "graph_state":      _empty_graph_state(),
            "problem_resolved": False,
        }

    session = sessions[session_id]
    session["messages"].append({"role": "user", "content": query.message})
    session["turn_count"] += 1

    # ── PHASE 1: Customer not yet identified ──────────────────────────────────
    if session["customer"] is None:
        identifier = looks_like_identifier(query.message)

        if identifier is None:
            response = (
                "Bonjour, bienvenue au support technique. "
                "Pourriez-vous me communiquer votre numéro de CIN "
                "ou votre numéro de téléphone ?"
            )
            session["messages"].append({"role": "assistant", "content": response})
            return {"response": response, "session_id": session_id}

        # Identifier found — look up customer
        print(f"[GATEWAY] find_customer('{identifier}')")
        customer = run_find_customer(identifier)

        if customer is None:
            response = (
                "Je suis désolé, aucun compte n'est associé à cet identifiant. "
                "Veuillez contacter notre service commercial."
            )
            session["messages"].append({"role": "assistant", "content": response})
            return {"response": response, "session_id": session_id}

        session["customer"] = customer
        print(
            f"[GATEWAY] Identified: {customer.get('name')} | "
            f"username={customer.get('username')} | "
            f"serial={customer.get('serial')} | "
            f"city={customer.get('city')}"
        )

        # Run graph for Phase A — account check + outage check + greeting
        gs = session["graph_state"]

        try:
            result = run_graph(
                username=customer.get("username", ""),
                serial=customer.get("serial", "inconnu"),
                city=customer.get("city", ""),
                region=customer.get("region", ""),
                customer_name=customer.get("name", ""),
                user_message="[IDENTIFICATION]",
                conversation_history=[],
                turn_count=1,
                **{k: v for k, v in gs.items()},
            )
        except Exception as e:
            print(f"[GATEWAY] Graph error on identification: {e}")
            response = (
                f"Bonjour {customer.get('name', '')}. "
                "Votre compte a été identifié. Comment puis-je vous aider aujourd'hui ?"
            )
            session["messages"].append({"role": "assistant", "content": response})
            return {"response": response, "session_id": session_id}

        # Save updated graph state back to session
        session["graph_state"].update({
            k: result[k] for k in gs.keys() if k in result
        })

        response = result["response"]
        session["messages"].append({"role": "assistant", "content": response})
        return {"response": response, "session_id": session_id}

    # ── PHASE 2: Customer identified — handle problem ─────────────────────────
    customer   = session["customer"]
    gs         = session["graph_state"]
    turn_count = session["turn_count"]

    # Build conversation history for context (last 6 turns)
    conv_history = session["messages"][-6:]

    print(
        f"[GATEWAY] Turn {turn_count} | "
        f"scenario={gs.get('scenario')} | "
        f"intervention_done={gs.get('intervention_done')} | "
        f"awaiting_reset={gs.get('awaiting_factory_reset')} | "
        f"diagnostics_done={gs.get('diagnostics_done')}"
    )

    # Run graph with full persisted state
    try:
        result = run_graph(
            username=customer.get("username", ""),
            serial=customer.get("serial", "inconnu"),
            city=customer.get("city", ""),
            region=customer.get("region", ""),
            customer_name=customer.get("name", ""),
            user_message=query.message,
            conversation_history=conv_history,
            turn_count=turn_count,
            intervention_done=gs.get("intervention_done", False),
            snr_poor=gs.get("snr_poor", False),
            diagnostics_done=gs.get("diagnostics_done", False),
            scenario=gs.get("scenario"),
            session_history=gs.get("session_history"),
            auth_failures=gs.get("auth_failures"),
            device_status=gs.get("device_status"),
            plan_info=gs.get("plan_info"),
            device_parameters=gs.get("device_parameters"),
            network_metrics=gs.get("network_metrics"),
            awaiting_factory_reset=gs.get("awaiting_factory_reset", False),
            awaiting_plan_upgrade=gs.get("awaiting_plan_upgrade", False),
            outage_notified=gs.get("outage_notified", False),
            account_status=gs.get("account_status"),
            intervention_result=gs.get("intervention_result"),
        )
    except Exception as e:
        import traceback
        print(f"[GATEWAY] Graph error on turn {turn_count}: {e}")
        traceback.print_exc()
        response = (
            "Je suis désolé, une erreur technique est survenue. "
            "Veuillez réessayer ou contacter notre support au 71 XXX XXX."
        )
        session["messages"].append({"role": "assistant", "content": response})
        return {"response": response, "session_id": session_id}

    # Save updated graph state back to session
    session["graph_state"].update({
        k: result[k] for k in gs.keys() if k in result
    })

    # Track problem resolution at session level
    if result.get("problem_resolved"):
        session["problem_resolved"] = True
        print(f"[GATEWAY] Session {session_id} resolved and closed")

    response = result["response"]
    session["messages"].append({"role": "assistant", "content": response})
    return {"response": response, "session_id": session_id}


@app.post("/clear")
def clear_session(body: ClearSession):
    if body.session_id in sessions:
        del sessions[body.session_id]
    return {"status": "cleared", "session_id": body.session_id}


@app.get("/sessions")
def list_sessions():
    return {
        sid: {
            "turns":    len(s["messages"]),
            "customer": s["customer"].get("name") if s["customer"] else None,
            "scenario": s["graph_state"].get("scenario"),
            "resolved": s.get("problem_resolved", False),
        }
        for sid, s in sessions.items()
    }