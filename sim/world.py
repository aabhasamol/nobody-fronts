"""The world around the agent: the clock, the run log, and the person behind the curtain.

Nothing in here makes a decision for the agent. The curtain only answers what a called system would answer
(Pine Labs, Delhivery, a fare site, the phone line) and the operator only feeds in events from real sources.
"""
from __future__ import annotations
import copy
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

IST = timezone(timedelta(hours=5, minutes=30))
HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "curtain" / "templates.json"

# ------------------------------------------------------------------ terminal colours (the recording reads these)
_TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


C_EVENT, C_THINK, C_CALL, C_RESP, C_SAY, C_DEC, C_WARN, C_DIM = "36;1", "2", "35", "32", "1", "33", "31;1", "2"


def banner(label: str, body: str = "", colour: str = C_DIM) -> None:
    print(paint(colour, f"── {label} ") + (paint(colour, "─" * max(0, 70 - len(label)))))
    if body:
        print(body)


# ------------------------------------------------------------------ clock
class SimClock:
    """Wall-clock time plus an offset the operator advances (`/advance 24h`). Everything is shown in IST."""

    def __init__(self, start: Optional[str] = None):
        base = datetime.fromisoformat(start).astimezone(IST) if start else datetime.now(IST)
        self._offset = base - datetime.now(IST)

    def now(self) -> datetime:
        return (datetime.now(IST) + self._offset).replace(microsecond=0)

    def advance(self, spec: str) -> timedelta:
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([mhd])\s*", spec)
        if not m:
            raise ValueError("use e.g. 30m, 6h, 1d")
        n, unit = float(m.group(1)), m.group(2)
        delta = timedelta(minutes=n) if unit == "m" else timedelta(hours=n) if unit == "h" else timedelta(days=n)
        self._offset += delta
        return delta

    def stamp(self) -> str:
        return self.now().strftime("%a %d %b %Y, %H:%M IST")


# ------------------------------------------------------------------ run log
class RunLog:
    """Everything the submission needs, written as it happens: events, decisions, rail calls, messages."""

    def __init__(self, root: Optional[Path] = None):
        stamp = datetime.now(IST).strftime("%Y%m%d-%H%M%S")
        self.dir = (root or HERE / "runs") / stamp
        self.dir.mkdir(parents=True, exist_ok=True)
        self.n_rail = 0
        self.n_decision = 0

    def _append(self, name: str, row: dict) -> None:
        with (self.dir / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def event(self, row: dict) -> None:
        self._append("events.jsonl", row)

    def decision(self, row: dict) -> None:
        self.n_decision += 1
        self._append("decisions.jsonl", {"n": self.n_decision, **row})

    def rail(self, row: dict) -> int:
        self.n_rail += 1
        self._append("rail_calls.jsonl", {"n": self.n_rail, **row})
        return self.n_rail

    def message(self, row: dict) -> None:
        self._append("messages.jsonl", row)

    def model(self, row: dict) -> None:
        self._append("model_calls.jsonl", row)


# ------------------------------------------------------------------ the curtain
def _ids() -> dict[str, str]:
    return {"payment_link_id": f"pl-v1-{uuid.uuid4().hex[:20]}", "order_id": f"v1-{uuid.uuid4().hex[:24]}",
            "payment_id": f"v1-{uuid.uuid4().hex[:24]}", "refund_order_id": f"v1-{uuid.uuid4().hex[:24]}",
            "payout_id": f"po_{uuid.uuid4().hex[:16]}", "jwt": "eyJhbGciOiJSUzI1NiJ9." + secrets.token_urlsafe(24)}


def render(obj: Any, ctx: dict[str, Any]) -> Any:
    """Fill {{name}} placeholders. A string that is exactly one placeholder takes the value's type (amounts stay ints)."""
    if isinstance(obj, dict):
        return {k: render(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render(v, ctx) for v in obj]
    if isinstance(obj, str):
        whole = re.fullmatch(r"\{\{(\w+)\}\}", obj)
        if whole and whole.group(1) in ctx:
            return ctx[whole.group(1)]
        return re.sub(r"\{\{(\w+)\}\}", lambda m: str(ctx.get(m.group(1), m.group(0))), obj)
    return obj


class Curtain:
    """A teammate answering for Pine Labs, Delhivery, fare sites and the phone line.

    interactive (default): show the request, show the documented template filled in, and let the operator send it,
    edit it, paste their own JSON, or send the documented error. auto: send the template as-is (rehearsal only).
    scripted: answers come from a list of callables (tests)."""

    def __init__(self, clock: SimClock, mode: Optional[str] = None, templates: Path = TEMPLATES,
                 ask: Callable[[str], str] = input):
        self.clock = clock
        self.mode = mode or os.environ.get("SIM_CURTAIN", "interactive")
        self.templates = json.loads(templates.read_text(encoding="utf-8"))
        self.ask = ask
        self.scripted: list[Callable[[str, dict, dict], tuple[int, Any]]] = []

    def template(self, key: str) -> dict:
        return copy.deepcopy(self.templates[key])

    def context(self, extra: dict[str, Any]) -> dict[str, Any]:
        now = self.clock.now()
        return {**_ids(), "now_iso": now.isoformat(), "now_plus_1h_iso": (now + timedelta(hours=1)).isoformat(), **extra}

    def answer(self, key: str, request: dict, ctx: dict[str, Any]) -> tuple[int, Any]:
        t = self.template(key)
        status, body = t.get("status", 200), render(t["response"], self.context(ctx))
        if self.mode == "scripted" and self.scripted:
            return self.scripted.pop(0)(key, request, body)
        if self.mode != "interactive":
            return status, body
        banner(f"CURTAIN · {t['partner']} · {t['method']} {t['path']}", colour=C_WARN)
        print(paint(C_DIM, f"doc: {t.get('doc')}  verified={t.get('verified')}"))
        print(paint(C_DIM, "documented response, filled in:"))
        print(json.dumps(body, indent=2, ensure_ascii=False))
        opts = "[Enter] send · e edit · p paste JSON" + (" · x documented error" if "error" in t else "")
        while True:
            choice = self.ask(paint(C_WARN, f"curtain {opts} > ")).strip().lower()
            if choice == "":
                return status, body
            if choice == "x" and "error" in t:
                err = render(t["error"]["response"], self.context(ctx))
                return t["error"].get("status", 400), err
            if choice == "p":
                print("paste JSON, end with an empty line:")
                lines = []
                while (line := self.ask("")) != "":
                    lines.append(line)
                try:
                    return status, json.loads("\n".join(lines))
                except json.JSONDecodeError as e:
                    print(paint(C_WARN, f"not JSON: {e}"))
            if choice == "e":
                edited = _edit(json.dumps(body, indent=2, ensure_ascii=False))
                try:
                    return status, json.loads(edited)
                except json.JSONDecodeError as e:
                    print(paint(C_WARN, f"not JSON: {e}"))

    def ask_free(self, prompt: str) -> str:
        """For the phone line: the operator gives the path of the callee's recorded reply (or 'none')."""
        if self.mode == "scripted" and self.scripted:
            return str(self.scripted.pop(0)("phone", {}, {})[1])
        if self.mode != "interactive":
            return os.environ.get("SIM_AUTO_REPLY", "none")
        return self.ask(paint(C_WARN, prompt)).strip()


def _edit(text: str) -> str:
    editor = os.environ.get("EDITOR", "nano")
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write(text)
        path = f.name
    subprocess.call([editor, path])
    return Path(path).read_text(encoding="utf-8")
