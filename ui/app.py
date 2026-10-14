"""Minimal Streamlit chat UI for the ISP support agent (thin HTTP client)."""

import hashlib
import os
import uuid

import requests
import streamlit as st

AGENT_URL = os.environ.get("AGENT_URL", "http://langchain-agent:8000")

st.set_page_config(page_title="Support ISP - Assistant", page_icon="\U0001F4E1", layout="centered")


def new_session_id() -> str:
    return str(uuid.uuid4())


if "session_id" not in st.session_state:
    st.session_state.session_id = new_session_id()
if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_info" not in st.session_state:
    st.session_state.session_info = None
if "last_voice_hash" not in st.session_state:
    st.session_state.last_voice_hash = None
if "voice_widget_epoch" not in st.session_state:
    st.session_state.voice_widget_epoch = 0


def clear_conversation():
    old_session_id = st.session_state.session_id
    try:
        requests.post(f"{AGENT_URL}/clear", json={"session_id": old_session_id}, timeout=5)
    except requests.RequestException as exc:
        st.toast(f"Erreur lors du nettoyage de la session : {exc}")
    st.session_state.session_id = new_session_id()
    st.session_state.messages = []
    st.session_state.session_info = None
    st.session_state.last_voice_hash = None
    st.session_state.voice_widget_epoch = 0


def refresh_session_info():
    try:
        resp = requests.get(f"{AGENT_URL}/sessions", timeout=5)
        resp.raise_for_status()
        st.session_state.session_info = resp.json().get(st.session_state.session_id)
    except requests.RequestException:
        st.session_state.session_info = None


def send_message(text: str, audio_bytes: bytes | None = None) -> str:
    """Send `text` through /ask exactly as a typed chat message. Returns the reply."""
    user_msg = {"role": "user", "content": text}
    if audio_bytes is not None:
        user_msg["is_voice"] = True
        user_msg["audio_bytes"] = audio_bytes
    st.session_state.messages.append(user_msg)
    with st.chat_message("user"):
        if audio_bytes is not None:
            st.caption("\U0001F3A4 Message vocal")
            st.audio(audio_bytes, format="audio/wav")
        st.markdown(text)

    with st.chat_message("assistant"):
        with st.spinner("Le support réfléchit..."):
            try:
                resp = requests.post(
                    f"{AGENT_URL}/ask",
                    json={"message": text, "session_id": st.session_state.session_id},
                    timeout=60,
                )
                resp.raise_for_status()
                answer = resp.json().get("response", "(réponse vide)")
            except requests.RequestException as exc:
                answer = f"Erreur de connexion à l'agent : {exc}"
        st.markdown(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
    refresh_session_info()
    return answer


def transcribe_recording(audio_bytes: bytes) -> str:
    """POST a recording to /transcribe. Returns the transcribed text, or "" on failure."""
    try:
        resp = requests.post(
            f"{AGENT_URL}/transcribe",
            files={"file": ("recording.wav", audio_bytes, "audio/wav")},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("text", "").strip()
    except requests.RequestException as exc:
        st.toast(f"Erreur de transcription : {exc}")
        return ""


def synthesize_reply(text: str) -> bytes | None:
    """POST `text` to /synthesize. Returns the synthesized WAV bytes, or None on failure."""
    try:
        resp = requests.post(f"{AGENT_URL}/synthesize", json={"text": text}, timeout=60)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as exc:
        st.toast(f"Erreur de synthèse vocale : {exc}")
        return None


with st.sidebar:
    st.header("Session")
    st.button("\U0001F504 Nouvelle conversation", on_click=clear_conversation, use_container_width=True)
    st.caption(f"ID: `{st.session_state.session_id}`")

    st.divider()
    st.subheader("Statut client")

    info = st.session_state.session_info
    if info:
        customer = info.get("customer") or "Non identifié"
        scenario = info.get("scenario") or "—"
        resolved = info.get("resolved", False)
        turns = info.get("turns", 0)

        st.markdown(f"**Client :** {customer}")
        st.markdown(f"**Scénario :** {scenario}")
        st.markdown(f"**Tours de conversation :** {turns}")
        if resolved:
            st.success("Problème résolu")
        else:
            st.warning("En cours")
    else:
        st.caption("Aucune information de session pour le moment.")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg.get("audio_bytes") is not None:
            if msg.get("is_voice"):
                st.caption("\U0001F3A4 Message vocal")
            st.audio(msg["audio_bytes"], format="audio/wav")
        st.markdown(msg["content"])

user_input = st.chat_input("Écrivez votre message...")
voice_input = st.audio_input(
    "\U0001F3A4 Ou enregistrez un message vocal",
    key=f"voice_{st.session_state.session_id}_{st.session_state.voice_widget_epoch}",
)

pending_text = None
pending_audio_bytes = None
is_voice = False

if user_input:
    pending_text = user_input
elif voice_input is not None:
    audio_bytes = voice_input.getvalue()
    audio_hash = hashlib.md5(audio_bytes).hexdigest()
    if audio_hash != st.session_state.last_voice_hash:
        st.session_state.last_voice_hash = audio_hash
        with st.spinner("Transcription en cours..."):
            transcribed = transcribe_recording(audio_bytes)
        if transcribed:
            pending_text = transcribed
            pending_audio_bytes = audio_bytes
            is_voice = True
        else:
            st.toast("Aucune parole détectée, veuillez réessayer.")

if pending_text:
    reply = send_message(pending_text, audio_bytes=pending_audio_bytes)
    if is_voice:
        with st.spinner("Synthèse vocale en cours..."):
            reply_audio = synthesize_reply(reply)
        if reply_audio is not None:
            st.session_state.messages[-1]["audio_bytes"] = reply_audio
        st.session_state.voice_widget_epoch += 1
    st.rerun()
