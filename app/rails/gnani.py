"""Gnani (Inya Agent Builder Platform API) — outbound verification calls.

Docs: https://docs.gnani.ai/Platform/platform-introduction  (base https://api.inya.ai/platform, header x-api-key)

Flow (from Trigger_Call docs):
    1. PUT  /v1/agents/{botId}                       configure agent  (done once, by hand or via setup_agent())
    2. POST /v1/agents/{botId}/trigger_call          place the call   (needs ?environment=development for dev keys)
    3. POST /v1/conversations/logs                   find conversationId by clientReferenceId
    4. GET  /v1/conversations/{id}/stats             disposition + transcript

Constraints that shape the demo:
  * Outbound calls go only to WHITELISTED numbers (400 otherwise). For Round 2 the hotel is a teammate's
    phone — whitelist it and answer as "Sea Breeze Beach Resort". That is a legitimate sandbox demo.
  * trigger_call carries only phone/countryCode/name/clientReferenceId. Hotel-specific context (listing claim,
    dates, party size) reaches the agent through the agent's pre-call / dynamic-variables API: configure the
    agent to GET {QUORUM_PUBLIC_URL}/gnani/precall?ref=<clientReferenceId> and this app serves the variables
    (see app/main.py). Prompt uses Jinja: {{ property_name }}, {{ photos_claim }}, {{ nights }}, {{ party_size }}.
  * Disposition prompt on the agent must emit one of: VERIFIED / MISMATCH / NO_ANSWER, and the agent's
    "actions/variables" must extract room_as_pictured, road_motorable, refund_terms, twin_sharing_available.

Environment:
    GNANI_API_KEY, GNANI_BOT_ID, GNANI_ENV=development, QUORUM_PUBLIC_URL (tunnel for pre-call variables)
"""
from __future__ import annotations
import os
import time
import httpx
from ..clock import clock
from ..models import Member, Stay, VerificationRecord, new_id
from .base import VoiceRail

BASE = "https://api.inya.ai/platform"

VERIFIER_SYSTEM_PROMPT = """You are calling {{ property_name }} on behalf of a group of {{ party_size }} friends
who are considering booking {{ nights }} nights. Speak in {{ language_name }}; switch to Hindi or English if the
person prefers. Be brief and polite. Ask, in order, and record the answers:
1. Is the room in your listing — "{{ photos_claim }}" — what guests actually get right now?
2. Is the approach road motorable for a taxi, including in rain?
3. What are your refund terms if the group cancels a week before?
4. Do you have twin-sharing rooms for the group?
Do not negotiate price. Thank them and end the call. Never claim a booking has been made."""

DISPOSITION_PROMPT = """Classify the call:
VERIFIED  — the property confirmed the listed room is available as described and the road is motorable.
MISMATCH  — the property contradicted the listing (room, road, or refund terms materially worse).
NO_ANSWER — nobody answered or the call did not reach a decision-maker."""


class GnaniVoice(VoiceRail):
    def __init__(self):
        self.key = os.environ["GNANI_API_KEY"]
        self.bot_id = os.environ["GNANI_BOT_ID"]
        self.env = os.environ.get("GNANI_ENV", "development")
        self.http = httpx.Client(timeout=30, headers={"x-api-key": self.key, "Content-Type": "application/json"})
        # The engine registers pre-call variables here; app/main.py serves them at /gnani/precall.
        self.precall_vars: dict[str, dict] = {}

    # ------------------------------------------------------------ one-time agent setup
    def setup_agent(self, language: str = "hi-IN") -> dict:
        """Push the verifier prompt onto the agent. Run once; re-run when the prompt changes."""
        r = self.http.put(f"{BASE}/v1/agents/{self.bot_id}", params={"environment": self.env},
                          json={"systemPrompt": VERIFIER_SYSTEM_PROMPT, "dispositionPrompt": DISPOSITION_PROMPT,
                                "language": language})
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------ verify
    def verify_property(self, stay: Stay, language: str, questions: list[str]) -> VerificationRecord:
        ref = new_id("verify")
        self.precall_vars[ref] = {"property_name": stay.name, "photos_claim": stay.photos_claim,
                                  "nights": stay.nights, "party_size": 5,
                                  "language_name": {"hi-IN": "Hindi", "kok-IN": "Konkani"}.get(language, "Hindi")}
        r = self.http.post(f"{BASE}/v1/agents/{self.bot_id}/trigger_call", params={"environment": self.env},
                           json={"phone": stay.phone[-10:], "countryCode": "+91", "name": stay.name,
                                 "clientReferenceId": ref})
        r.raise_for_status()
        conv_id = self._wait_for_conversation(ref)
        stats = self._stats(conv_id) if conv_id else {}
        disposition = (stats.get("overallCallDisposition") or "NO_ANSWER").upper()
        if disposition not in ("VERIFIED", "MISMATCH", "NO_ANSWER"):
            disposition = "NO_ANSWER"
        extracted = stats.get("extractedVariables") or stats.get("variables") or {}
        answers = {q: str(extracted.get(q, "")) for q in questions}
        transcript = stats.get("transcript") if isinstance(stats.get("transcript"), str) else \
            "\n".join(f"[{u.get('speaker', '?')}] {u.get('text', '')}" for u in stats.get("utteranceAnalytics", []))
        return VerificationRecord(property_name=stay.name, phone=stay.phone, language=language,
                                  disposition=disposition, answers=answers, transcript=transcript or "",
                                  call_ref=conv_id or ref, called_at=clock.now())

    def _wait_for_conversation(self, ref: str, timeout_s: int = 240) -> str | None:
        """Poll conversation logs until a record carrying our clientReferenceId appears and has ended."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            r = self.http.post(f"{BASE}/v1/conversations/logs", params={"environment": self.env},
                               json={"botId": self.bot_id, "clientReferenceId": ref, "page": 1, "limit": 5})
            if r.status_code == 200:
                rows = r.json().get("response") or r.json().get("data") or []
                rows = rows if isinstance(rows, list) else rows.get("conversations", [])
                for row in rows:
                    if row.get("clientReferenceId") == ref and row.get("callStatus", "").upper() not in ("", "IN PROGRESS", "RINGING"):
                        return row.get("conversationId") or row.get("id")
            time.sleep(6)
        return None

    def _stats(self, conv_id: str) -> dict:
        r = self.http.get(f"{BASE}/v1/conversations/{conv_id}/stats", params={"environment": self.env})
        r.raise_for_status()
        return r.json().get("response") or r.json().get("data") or r.json()

    # ------------------------------------------------------------ members who prefer a call
    def ask_member_by_call(self, member: Member, questions: list[str]) -> dict[str, str]:
        raise NotImplementedError("Round-2 stretch: a second agent that captures constraints by phone for parents.")
