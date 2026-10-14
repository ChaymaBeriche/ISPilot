"""
review_tool.py — CLI tool for reviewing and correcting logged conversations.

Usage:
    python review_tool.py              # Review all pending sessions
    python review_tool.py --stats      # Show statistics
    python review_tool.py --export     # Export approved/corrected to JSONL
    python review_tool.py --file PATH  # Review a specific file
"""

import argparse
import json
import os
import sys
from pathlib import Path
from logger import ConversationLogger

LOG_DIR = os.getenv("LOG_DIR", "logs/conversations")

# ─────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────

def print_separator(char="═", width=70):
    print(char * width)


def print_section(title: str):
    print(f"\n{'─' * 70}")
    print(f"  {title}")
    print(f"{'─' * 70}")


def display_session(entry: dict):
    """Pretty-print a session log for review."""
    customer = entry.get("customer", {})
    flags    = entry.get("flags", {})

    print_separator()
    print(f"  SESSION: {entry['session_id']}")
    print(f"  Label:   {entry['label']}")
    print(f"  Time:    {entry['timestamp']}")
    print_separator()

    # Customer info
    print_section("CUSTOMER")
    print(f"  Name:     {customer.get('name')}")
    print(f"  Username: {customer.get('username')}")
    print(f"  Serial:   {customer.get('serial')}")
    print(f"  City:     {customer.get('city')} ({customer.get('region')})")
    print(f"  Status:   {customer.get('status')}")
    print(f"  Flags:    intervention_done={flags.get('intervention_done')} | snr_poor={flags.get('snr_poor')}")

    # Conversation
    print_section("CONVERSATION")
    for turn in entry.get("conversation", []):
        role    = turn.get("role", "?").upper()
        content = turn.get("content", "")
        prefix  = "  👤" if role == "USER" else "  🤖"
        print(f"{prefix} [{role}]: {content[:200]}")

    # Tool trace
    print_section("TOOL CALLS")
    trace = entry.get("tool_trace", [])
    if not trace:
        print("  (no tool calls recorded)")
    else:
        for i, tc in enumerate(trace, 1):
            status = "✅" if tc.get("success") else "❌"
            args   = json.dumps(tc.get("args", {}), ensure_ascii=False)
            result = str(tc.get("result", ""))[:150]
            print(f"  {i}. {status} {tc['tool_name']}({args})")
            print(f"     → {result}")

    # Final response
    print_section("FINAL RESPONSE")
    print(f"  {entry.get('final_response', '(none)')}")


def get_review_input(entry: dict) -> dict:
    """
    Prompt the reviewer for input on a session.
    Returns dict with review decision.
    """
    print("\n")
    print_separator("─")
    print("  REVIEW OPTIONS:")
    print("  [a] Approve   — response is correct, no changes needed")
    print("  [c] Correct   — response needs fixing")
    print("  [r] Reject    — too broken to use as training data")
    print("  [s] Skip      — review later")
    print("  [q] Quit      — stop reviewing")
    print_separator("─")

    while True:
        choice = input("  Your choice [a/c/r/s/q]: ").strip().lower()
        if choice in ("a", "c", "r", "s", "q"):
            break
        print("  Invalid choice. Enter a, c, r, s, or q.")

    if choice == "q":
        return {"action": "quit"}

    if choice == "s":
        return {"action": "skip"}

    if choice == "a":
        return {"action": "approve"}

    if choice == "r":
        reason = input("  Rejection reason: ").strip()
        return {"action": "reject", "reason": reason or "No reason given"}

    if choice == "c":
        print("\n  Enter the corrected response (press Enter twice to finish):")
        lines = []
        while True:
            line = input()
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
        corrected = "\n".join(lines[:-1]).strip()  # remove trailing empty line

        print("\n  Issues found (comma-separated, e.g. 'wrong tool order, English response'):")
        issues_raw = input("  > ").strip()
        issues = [i.strip() for i in issues_raw.split(",") if i.strip()]

        print("\n  Correct tool sequence (comma-separated tool names, or leave blank):")
        seq_raw = input("  > ").strip()
        tool_seq = [t.strip() for t in seq_raw.split(",") if t.strip()] or None

        print("\n  Reviewer notes (optional):")
        notes = input("  > ").strip() or None

        return {
            "action":               "correct",
            "corrected_response":   corrected,
            "issues_found":         issues,
            "correct_tool_sequence": tool_seq,
            "notes":                notes,
        }

    return {"action": "skip"}


# ─────────────────────────────────────────────
# Main commands
# ─────────────────────────────────────────────

def cmd_stats(logger: ConversationLogger):
    """Print statistics about the log directory."""
    stats = logger.stats()
    print_separator()
    print("  LOG STATISTICS")
    print_separator()
    print(f"  Total sessions:    {stats['total']}")
    print(f"  Pending review:    {stats['pending']}")
    print(f"  Approved:          {stats['approved']}")
    print(f"  Corrected:         {stats['corrected']}")
    print(f"  Rejected:          {stats['rejected']}")
    print(f"  Training ready:    {stats['training_ready']}")
    print_separator()

    # Progress bar
    total = max(stats["total"], 1)
    pct   = int(stats["training_ready"] / total * 100)
    bar   = "█" * (pct // 5) + "░" * (20 - pct // 5)
    print(f"  Progress: [{bar}] {pct}% training ready")
    print_separator()


def cmd_review_all(logger: ConversationLogger):
    """Review all pending sessions interactively."""
    pending = logger.get_pending_reviews()

    if not pending:
        print("  No pending sessions to review.")
        return

    print(f"\n  {len(pending)} session(s) pending review.")

    for i, p in enumerate(pending, 1):
        print(f"\n  [{i}/{len(pending)}] {p['label']} — {p['customer']} — {p['timestamp'][:10]}")

        filepath = p["file"]
        with open(filepath, encoding="utf-8") as f:
            entry = json.load(f)

        display_session(entry)
        decision = get_review_input(entry)

        action = decision["action"]

        if action == "quit":
            print("\n  Review stopped. Progress saved.")
            break
        elif action == "skip":
            print("  Skipped.")
            continue
        elif action == "approve":
            logger.mark_approved(filepath)
            print("  ✅ Approved.")
        elif action == "reject":
            logger.mark_rejected(filepath, decision["reason"])
            print("  ❌ Rejected.")
        elif action == "correct":
            logger.mark_corrected(
                log_file=filepath,
                corrected_response=decision["corrected_response"],
                issues_found=decision["issues_found"],
                correct_tool_sequence=decision.get("correct_tool_sequence"),
                notes=decision.get("notes"),
            )
            print("  ✏️  Corrected.")

    print("\n  Review session complete.")
    cmd_stats(logger)


def cmd_review_file(logger: ConversationLogger, filepath: str):
    """Review a single specific log file."""
    if not Path(filepath).exists():
        print(f"  File not found: {filepath}")
        return

    with open(filepath, encoding="utf-8") as f:
        entry = json.load(f)

    display_session(entry)
    decision = get_review_input(entry)
    action   = decision["action"]

    if action == "approve":
        logger.mark_approved(filepath)
        print("  ✅ Approved.")
    elif action == "reject":
        logger.mark_rejected(filepath, decision["reason"])
        print("  ❌ Rejected.")
    elif action == "correct":
        logger.mark_corrected(
            log_file=filepath,
            corrected_response=decision["corrected_response"],
            issues_found=decision["issues_found"],
            correct_tool_sequence=decision.get("correct_tool_sequence"),
            notes=decision.get("notes"),
        )
        print("  ✏️  Corrected.")
    else:
        print("  Skipped.")


def cmd_export(logger: ConversationLogger):
    """Export all training-ready sessions to JSONL."""
    MINIMAL_SYSTEM_PROMPT = """Tu es un agent support technique pour un FAI tunisien.
Tu réponds UNIQUEMENT en français. Tu es poli, professionnel et empathique.
Le client est déjà identifié — ne redemande jamais le CIN.
N'affiche jamais les noms des outils dans tes réponses.
N'invente aucune donnée — utilise uniquement ce que les outils retournent.
N'invente jamais un numéro de ticket — appelle toujours create_ticket().
factory_reset_device_safe nécessite une confirmation explicite du client.
"""
    output_file = "training_data.jsonl"
    count = logger.export_training_data(
        output_file=output_file,
        system_prompt=MINIMAL_SYSTEM_PROMPT,
    )
    if count > 0:
        print(f"\n  ✅ Exported {count} training examples → {output_file}")
    else:
        print("\n  ⚠️  No training-ready sessions found.")
        print("  Review and approve/correct sessions first.")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Review and export fine-tuning training data"
    )
    parser.add_argument("--stats",  action="store_true", help="Show log statistics")
    parser.add_argument("--export", action="store_true", help="Export training data to JSONL")
    parser.add_argument("--file",   type=str,            help="Review a specific log file")
    args = parser.parse_args()

    logger = ConversationLogger(log_dir=LOG_DIR)

    if args.stats:
        cmd_stats(logger)
    elif args.export:
        cmd_export(logger)
    elif args.file:
        cmd_review_file(logger, args.file)
    else:
        cmd_review_all(logger)