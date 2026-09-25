"""Gnani (Inya Agent Builder Platform API) — the two voice jobs, as outbound calls.

Docs: https://docs.gnani.ai/Platform/platform-introduction  (base https://api.inya.ai/platform, header x-api-key)

Flow (from the Trigger_Call docs):
    1. PUT  /v1/agents/{botId}                       configure the agent (once, by hand or via setup_agent())
    2. POST /v1/agents/{botId}/trigger_call          place the call   (needs ?environment=development for dev keys)
    3. POST /v1/conversations/logs                   find conversationId by clientReferenceId
    4. GET  /v1/conversations/{id}/stats             disposition + extracted variables + transcript

Where voice is used, and where it is not (docs/round2-answers.md, "Where do we actually need to call"):
  * job 1, supplier calls: stays with no online inventory. Availability, group rate, refund terms, a 48-hour
    hold; on the travel day, a late-arrival notice. Listed stays are never called — the listing is the data.
  * job 2, escalation: a member whose flight was cancelled has not answered the options text within
    ESCALATE_AFTER_MIN. The call reads the options and captures the choice as a variable.
  Members are never called to chase a vote or an approval. That is the chasing the product removes.

Constraints that shape the sandbox demo:
  * Outbound calls go only to WHITELISTED numbers (400 otherwise). A teammate's phone plays the homestay.
  * trigger_call carries only phone/countryCode/name/clientReferenceId. Everything else reaches the agent
    through its pre-call / dynamic-variables API: configure the agent to GET
    {QUORUM_PUBLIC_URL}/gnani/precall?ref=<clientReferenceId>; this app serves the variables (app/main.py).
    The prompt is one Jinja template branching on {{ purpose }}   [verify: Jinja `if` on the platform; if not,
    use three bots — GNANI_BOT_ID, GNANI_BOT_ID_LATE, GNANI_BOT_ID_MEMBER].
  * The agent's "actions / variables" must extract the fields in EXTRACT for each purpose, and the
    disposition prompt must emit CONFIRMED / UNAVAILABLE / NO_ANSWER. That is the ask: fields, not prose.

Environment:
    GNANI_API_KEY, GNANI_BOT_ID, GNANI_ENV=development, QUORUM_PUBLIC_URL (tunnel for pre-call variables)
"""
from __future__ import annotations
import os
import re
import time
from datetime import date, datetime, timedelta
import httpx
from ..clock import clock
from ..models import Member, Stay, CallRecord, new_id
from .base import VoiceRail

BASE = "https://api.inya.ai/platform"

SYSTEM_PROMPT = """{% if purpose == "AVAILABILITY" %}
You are calling {{ property_name }} in {{ area }} on behalf of a group of {{ party_size }} friends who want
{{ rooms }} twin-sharing rooms for {{ nights }} nights from {{ check_in }}. Speak in {{ language_name }}; switch to
Hindi, Konkani or English if the person prefers. Be brief and polite. Find out, in order:
1. Do you have {{ rooms }} twin rooms for those dates?
2. What is the rate per room per night for this group, and does it include breakfast?
3. What are your refund terms if the group cancels a week before?
4. Can you hold the rooms for 48 hours while the group confirms? Until when exactly?
Do not negotiate. Never claim a booking has been made. Thank them and end the call.
{% elif purpose == "LATE_ARRIVAL" %}
You are calling {{ property_name }} about a confirmed group booking (ref {{ booking_ref }}). One guest,
{{ guest }}, now arrives at {{ eta }} because their flight was cancelled. Ask them to keep the room and confirm
someone will be there to let the guest in. Speak in {{ language_name }}. Be brief.
{% else %}
You are calling {{ member }} from Quorum, the trip agent for their friends' group. {{ question }}
Read out the options: {{ options }}. Ask which one they want, repeat the number back, and say a UPI request for
the extra amount will follow on WhatsApp. If they want to think, say the options hold for twenty minutes.
{% endif %}"""

DISPOSITION_PROMPT = """Classify the call:
CONFIRMED   — rooms and a hold were confirmed (supplier call), the late arrival was acknowledged, or the member chose an option.
UNAVAILABLE — the property has no rooms for those dates.
NO_ANSWER   — nobody answered, or the call did not reach someone who could decide."""

EXTRACT = {                                  # variables the agent must fill, per purpose
    "AVAILABILITY": ["available", "rate_per_room_night", "rooms", "twin_sharing", "refund_terms", "hold_until"],
    "LATE_ARRIVAL": [],
    "ESCALATION": ["choice"],
}
LANG_NAME = {"hi-IN": "Hindi", "kok-IN": "Konkani", "en-IN": "English"}


def _bool(v) -> bool | None:
    if v is None or v == "":
        return None
    return str(v).strip().lower() in ("true", "yes", "haan", "ha", "1", "y")


def _int(v) -> int | None:
    m = re.search(r"\d[\d,]*", str(v or ""))
    return int(m.group().replace(",", "")) if m else None


def _dt(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


class GnaniVoice(VoiceRail):
    def __init__(self):
        self.key = os.environ["GNANI_API_KEY"]
        self.bot_id = os.environ["GNANI_BOT_ID"]
        self.env = os.environ.get("GNANI_ENV", "development")
        self.http = httpx.Client(timeout=30, headers={"x-api-key": self.key, "Content-Type": "application/json"})
        # The engine's per-call context lives here; app/main.py serves it at /gnani/precall?ref=…
        self.precall_vars: dict[str, dict] = {}

    # ------------------------------------------------------------ one-time agent setup
    def setup_agent(self, language: str = "hi-IN") -> dict:
        """Push the prompts onto the agent. Run once; re-run when the prompts change."""
        r = self.http.put(f"{BASE}/v1/agents/{self.bot_id}", params={"environment": self.env},
                          json={"systemPrompt": SYSTEM_PROMPT, "dispositionPrompt": DISPOSITION_PROMPT,
                                "language": language})
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------ the call itself
    def _call(self, phone: str, name: str, purpose: str, language: str, variables: dict) -> tuple[str, dict]:
        ref = new_id(purpose.lower()[:5])
        self.precall_vars[ref] = {"purpose": purpose, "language_name": LANG_NAME.get(language, "Hindi"), **variables}
        r = self.http.post(f"{BASE}/v1/agents/{self.bot_id}/trigger_call", params={"environment": self.env},
                           json={"phone": phone[-10:], "countryCode": "+91", "name": name, "clientReferenceId": ref})
        r.raise_for_status()
        conv_id = self._wait_for_conversation(ref)
        return conv_id or ref, (self._stats(conv_id) if conv_id else {})

    @staticmethod
    def _record(stats: dict, **fixed) -> CallRecord:
        disposition = (stats.get("overallCallDisposition") or "NO_ANSWER").upper()
        if disposition not in ("CONFIRMED", "UNAVAILABLE", "NO_ANSWER"):
            disposition = "NO_ANSWER"
        x = stats.get("extractedVariables") or stats.get("variables") or {}
        transcript = stats.get("transcript") if isinstance(stats.get("transcript"), str) else \
            "\n".join(f"[{u.get('speaker', '?')}] {u.get('text', '')}" for u in stats.get("utteranceAnalytics", []))
        return CallRecord(disposition=disposition, available=_bool(x.get("available")),
                          rate_per_room_night=_int(x.get("rate_per_room_night")), rooms=_int(x.get("rooms")),
                          twin_sharing=_bool(x.get("twin_sharing")), refund_terms=x.get("refund_terms") or None,
                          hold_until=_dt(x.get("hold_until")), choice=_int(x.get("choice")),
                          transcript=transcript or "", called_at=clock.now(), **fixed)

    # ------------------------------------------------------------ job 1: suppliers
    def call_supplier(self, stay: Stay, party_size: int, check_in: date, nights: int, language: str) -> CallRecord:
        rooms = Stay.rooms_for(party_size)
        ref, stats = self._call(stay.phone, stay.name, "AVAILABILITY", language,
                                {"property_name": stay.name, "area": stay.area, "party_size": party_size,
                                 "rooms": rooms, "nights": nights, "check_in": check_in.strftime("%d %B"),
                                 "known": stay.listing_note})
        rec = self._record(stats, to=stay.name, phone=stay.phone, language=language, purpose="AVAILABILITY", call_ref=ref)
        if rec.disposition == "CONFIRMED" and rec.hold_until is None:
            rec.hold_until = clock.now() + timedelta(hours=48)      # the script asks for 48h; default if not extracted
        return rec

    def notify_late_arrival(self, stay: Stay, member: Member, eta: datetime) -> CallRecord:
        ref, stats = self._call(stay.phone, stay.name, "LATE_ARRIVAL", "hi-IN",
                                {"property_name": stay.name, "booking_ref": stay.booking_ref or "", "guest": member.first,
                                 "eta": eta.strftime("%H:%M on %d %B")})
        return self._record(stats, to=stay.name, phone=stay.phone, language="hi-IN", purpose="LATE_ARRIVAL", call_ref=ref)

    # ------------------------------------------------------------ job 2: a member, in a live disruption
    def escalate_member(self, member: Member, question: str, options: list[str]) -> CallRecord:
        ref, stats = self._call(member.phone, member.name, "ESCALATION", "en-IN",
                                {"member": member.first, "question": question,
                                 "options": "; ".join(f"option {i}: {o}" for i, o in enumerate(options, 1))})
        return self._record(stats, to=member.name, phone=member.phone, language="en-IN", purpose="ESCALATION", call_ref=ref)

    # ------------------------------------------------------------ polling
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
