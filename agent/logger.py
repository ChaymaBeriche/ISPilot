"""
logger.py — Conversation logger for fine-tuning data collection.

Captures full session data including tool calls, tool results,
and agent decisions in a format ready for human review and correction.

Usage:
    from logger import ConversationLogger
    logger = ConversationLogger()
    logger.log_session(session_id, session, agent_messages)
"""

import os
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
LOG_DIR = os.getenv("LOG_DIR", "logs/conversations")


class ConversationLogger:
    def __init__(self, log_dir: str = LOG_DIR):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────
    # Main entry point
    # ─────────────────────────────────────────

    def log_session(
        self,
        session_id: str,
        session: dict,
        agent_messages: list,
        label: Optional[str] = None,
    ):
        """
        Log a complete conversation session to a JSON file.

        Args:
            session_id:      Unique session identifier
            session:         Session dict from main.py (messages, customer, flags)
            agent_messages:  Raw LangGraph message list from agent run
            label:           Optional label for review (e.g. "auth_failure", "snr_low")
        """
        customer  = session.get("customer") or {}
        timestamp = datetime.now(timezone.utc).isoformat()

        # Extract structured tool call trace from agent messages
        tool_trace = self._extract_tool_trace(agent_messages)

        # Extract final assistant response
        final_response = self._extract_final_response(agent_messages)

        # Build the log entry
        entry = {
            # ── Metadata ──────────────────────────────────────────────
            "session_id":        session_id,
            "timestamp":         timestamp,
            "label":             label or "unlabeled",
            "review_status":     "pending",   # pending | approved | corrected | rejected

            # ── Customer context ──────────────────────────────────────
            "customer": {
                "name":     customer.get("name"),
                "username": customer.get("username"),
                "serial":   customer.get("serial"),
                "city":     customer.get("city"),
                "region":   customer.get("region"),
                "status":   customer.get("status"),
            },

            # ── Session flags ─────────────────────────────────────────
            "flags": {
                "intervention_done": session.get("intervention_done", False),
                "snr_poor":          session.get("snr_poor", False),
            },

            # ── Full conversation (for human review) ──────────────────
            "conversation": self._extract_conversation(session),

            # ── Tool call trace (what the model actually did) ─────────
            "tool_trace": tool_trace,

            # ── Final response ────────────────────────────────────────
            "final_response": final_response,

            # ── Review fields (filled by human reviewer) ──────────────
            "review": {
                "correct_tool_sequence":  None,   # list of tool names in correct order
                "issues_found":           [],     # list of issues
                "corrected_response":     None,   # corrected final response if needed
                "notes":                  None,   # reviewer notes
            },

            # ── Training data (generated after review) ────────────────
            "training_ready": False,
        }

        # Write to file
        filename = self._build_filename(session_id, timestamp, label)
        filepath = self.log_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2, default=str)

        print(f"[LOGGER] Session {session_id} logged → {filepath}")
        return filepath

    # ─────────────────────────────────────────
    # Extraction helpers
    # ─────────────────────────────────────────

    def _extract_conversation(self, session: dict) -> list:
        """
        Extract the human-readable conversation from session messages.
        Returns list of {role, content} dicts.
        """
        conversation = []
        for m in session.get("messages", []):
            role    = m.get("role", "unknown")
            content = m.get("content", "")

            # Skip system injections and context blocks — not useful for review
            if "[CONTEXTE CLIENT" in str(content):
                continue
            if "[INSTRUCTION SYSTÈME]" in str(content):
                continue

            conversation.append({
                "role":    role,
                "content": str(content)[:500],  # truncate very long messages
            })
        return conversation

    def _extract_tool_trace(self, agent_messages: list) -> list:
        """
        Extract ordered list of tool calls and their results from agent messages.
        Returns list of {tool_name, args, result, success} dicts.
        """
        trace     = []
        tool_uses = {}  # tool_use_id → {name, args}

        for msg in agent_messages:
            # Tool call made by the model
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_uses[tc["id"]] = {
                        "tool_name": tc["name"],
                        "args":      tc["args"],
                    }
                    trace.append({
                        "type":      "tool_call",
                        "tool_name": tc["name"],
                        "args":      tc["args"],
                        "result":    None,   # filled when ToolMessage arrives
                        "success":   None,
                    })

            # Tool result returned
            elif isinstance(msg, ToolMessage):
                result_content = str(msg.content)[:300]
                success        = not any(
                    err in result_content
                    for err in ["[NOT_FOUND]", "[TIMEOUT]", "[GENIEACS_ERROR]",
                                "[DB_ERROR]", "[RADIUS_ERROR]", "[RAG_UNAVAILABLE]",
                                "Error:", "not found"]
                )

                # Match result back to the tool call
                for entry in reversed(trace):
                    if (entry["type"] == "tool_call"
                            and entry["tool_name"] == msg.name
                            and entry["result"] is None):
                        entry["result"]  = result_content
                        entry["success"] = success
                        break

        return trace

    def _extract_final_response(self, agent_messages: list) -> Optional[str]:
        """Extract the last AIMessage text content as the final response."""
        for msg in reversed(agent_messages):
            if isinstance(msg, AIMessage) and not msg.tool_calls:
                content = msg.content
                if isinstance(content, list):
                    # Handle list content (some LangChain versions)
                    texts = [
                        c.get("text", "") for c in content
                        if isinstance(c, dict) and c.get("type") == "text"
                    ]
                    return " ".join(texts).strip() or None
                return str(content).strip() or None
        return None

    def _build_filename(
        self,
        session_id: str,
        timestamp: str,
        label: Optional[str],
    ) -> str:
        """Build a descriptive filename for the log file."""
        date_str = timestamp[:10].replace("-", "")    # 20260517
        safe_id  = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)[:20]
        safe_lbl = re.sub(r"[^a-zA-Z0-9_-]", "_", label or "unlabeled")[:30]
        return f"{date_str}_{safe_id}_{safe_lbl}.json"

    # ─────────────────────────────────────────
    # Training data export
    # ─────────────────────────────────────────

    def export_training_data(
        self,
        output_file: str = "training_data.jsonl",
        system_prompt: str = "",
    ):
        """
        Export all approved/corrected sessions as Llama 3 tool-calling JSONL.
        Only exports sessions where review_status is 'approved' or 'corrected'.

        Args:
            output_file:   Path to output JSONL file
            system_prompt: The minimal system prompt to use in training data
        """
        exported = 0
        skipped  = 0

        with open(output_file, "w", encoding="utf-8") as out:
            for log_file in sorted(self.log_dir.glob("*.json")):
                with open(log_file, encoding="utf-8") as f:
                    entry = json.load(f)

                status = entry.get("review_status")
                if status not in ("approved", "corrected"):
                    skipped += 1
                    continue

                training_example = self._build_training_example(
                    entry, system_prompt
                )
                if training_example:
                    out.write(json.dumps(training_example, ensure_ascii=False))
                    out.write("\n")
                    exported += 1
                else:
                    skipped += 1

        print(f"[LOGGER] Export complete: {exported} examples → {output_file}")
        print(f"[LOGGER] Skipped: {skipped} (pending/rejected/malformed)")
        return exported

    def _build_training_example(self, entry: dict, system_prompt: str) -> Optional[dict]:
        """
        Convert a reviewed log entry into a Llama 3 tool-calling training example.
        Uses corrected_response if available, otherwise uses original final_response.
        """
        customer = entry.get("customer", {})
        if not customer.get("username"):
            return None

        messages = []

        # System message
        if system_prompt:
            messages.append({
                "role":    "system",
                "content": system_prompt,
            })

        # Customer context injection
        serial   = customer.get("serial", "inconnu")
        username = customer.get("username")
        messages.append({
            "role": "user",
            "content": (
                f"[CONTEXTE CLIENT]\n"
                f"Nom: {customer.get('name')} | "
                f"Username RADIUS: {username} | "
                f"SERIAL NUMBER DU ROUTEUR: {serial} | "
                f"Ville: {customer.get('city')} | "
                f"Région: {customer.get('region')}"
            ),
        })

        # Conversation turns
        for turn in entry.get("conversation", []):
            role    = turn.get("role")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        # Tool calls from trace
        for tc in entry.get("tool_trace", []):
            if tc["type"] != "tool_call":
                continue

            # Assistant tool call
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "type": "function",
                    "function": {
                        "name":      tc["tool_name"],
                        "arguments": json.dumps(tc["args"], ensure_ascii=False),
                    },
                }],
            })

            # Tool result
            if tc.get("result"):
                messages.append({
                    "role":    "tool",
                    "name":    tc["tool_name"],
                    "content": tc["result"],
                })

        # Final response — use corrected if available
        review           = entry.get("review", {})
        corrected        = review.get("corrected_response")
        final_response   = corrected or entry.get("final_response")

        if not final_response:
            return None

        messages.append({
            "role":    "assistant",
            "content": final_response,
        })

        return {"messages": messages}

    # ─────────────────────────────────────────
    # Review helpers
    # ─────────────────────────────────────────

    def get_pending_reviews(self) -> list:
        """Return list of log files pending review."""
        pending = []
        for log_file in sorted(self.log_dir.glob("*.json")):
            with open(log_file, encoding="utf-8") as f:
                entry = json.load(f)
            if entry.get("review_status") == "pending":
                pending.append({
                    "file":       str(log_file),
                    "session_id": entry.get("session_id"),
                    "label":      entry.get("label"),
                    "timestamp":  entry.get("timestamp"),
                    "customer":   entry.get("customer", {}).get("name"),
                })
        return pending

    def mark_approved(self, log_file: str):
        """Mark a session as approved (no corrections needed)."""
        self._update_review_status(log_file, "approved")

    def mark_corrected(
        self,
        log_file: str,
        corrected_response: str,
        issues_found: list,
        correct_tool_sequence: Optional[list] = None,
        notes: Optional[str] = None,
    ):
        """Mark a session as corrected with the fixed response."""
        with open(log_file, encoding="utf-8") as f:
            entry = json.load(f)

        entry["review_status"]                    = "corrected"
        entry["review"]["corrected_response"]     = corrected_response
        entry["review"]["issues_found"]           = issues_found
        entry["review"]["correct_tool_sequence"]  = correct_tool_sequence
        entry["review"]["notes"]                  = notes
        entry["training_ready"]                   = True

        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2, default=str)

        print(f"[LOGGER] Marked as corrected: {log_file}")

    def mark_rejected(self, log_file: str, reason: str):
        """Mark a session as rejected (too broken to use as training data)."""
        self._update_review_status(log_file, "rejected")
        with open(log_file, encoding="utf-8") as f:
            entry = json.load(f)
        entry["review"]["notes"] = f"REJECTED: {reason}"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2, default=str)

    def _update_review_status(self, log_file: str, status: str):
        with open(log_file, encoding="utf-8") as f:
            entry = json.load(f)
        entry["review_status"]  = status
        entry["training_ready"] = status in ("approved", "corrected")
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2, default=str)

    # ─────────────────────────────────────────
    # Stats
    # ─────────────────────────────────────────

    def stats(self) -> dict:
        """Return summary statistics of the log directory."""
        counts = {
            "pending":   0,
            "approved":  0,
            "corrected": 0,
            "rejected":  0,
            "total":     0,
        }
        for log_file in self.log_dir.glob("*.json"):
            with open(log_file, encoding="utf-8") as f:
                entry = json.load(f)
            status = entry.get("review_status", "pending")
            counts[status] = counts.get(status, 0) + 1
            counts["total"] += 1

        counts["training_ready"] = counts["approved"] + counts["corrected"]
        return counts