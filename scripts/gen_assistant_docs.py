"""Regenerates the actions section of docs/assistant-tools.md from the live registry.

Run:  python scripts/gen_assistant_docs.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.assistant.actions import ACTIONS  # noqa: E402

DOC = Path(__file__).resolve().parents[1] / "docs" / "assistant-tools.md"
MARK = "## Actions (state-changing, separate registry)"

body = [MARK, """
The model can only **propose** these. A proposal stores a pending action bound to the user and changes nothing. The
assistant then asks the user to reply **Yes**, **No** or **Cancel** (buttons in the UI, or typed/spoken):

* **Yes** runs it. The decision is handled by the server from the user's own message, not by the model, so the model
  cannot confirm anything itself. Permission and pandal scope are re-checked at that moment with the user's session,
  and the same route function the HTTP API uses is called (same validation and audit rows, plus
  `assistant.action_confirmed`).
* **No** discards the most recent pending action. **Cancel** discards all of the user's pending actions.
* Pending actions live in memory for 10 minutes and are lost on restart.
* Permanent deletes and clears are marked **destructive**: the prompt starts with PERMANENT and the Yes button is red.

| Action | Same as route | Permission | Destructive | Arguments |
|---|---|---|---|---|"""]
for a in ACTIONS.values():
    props = a.args.model_json_schema().get("properties", {})
    body.append(f"| `{a.name}` | {a.source} | `{a.permission}` | {'yes' if a.destructive else 'no'} | "
                f"{', '.join(f'`{k}`' for k in props)} |")
body.append("""
Never available through the assistant: user or role management, registration, notification sending.
""")
text = DOC.read_text(encoding="utf-8")
tail_marker = "## Grounding rules given to the model"
cut = text.index(MARK) if MARK in text else text.index(tail_marker)
head = text[:cut]
tail = text[text.index(tail_marker):]
DOC.write_text(head + "\n".join(body) + "\n" + tail, encoding="utf-8")
print("updated", DOC)
