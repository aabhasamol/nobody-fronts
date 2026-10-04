"""The agent's tools. Tools act and report facts; they never decide.

Connectors:
    chat        send_group, send_dm                                  → real channel (sim/channels.py)
    Gnani       gnani_tts, gnani_stt                                  → real API (app/rails/gnani_speech.py)
    phone line  place_call                                            → curtain plays the line: plays the audio, the
                                                                         callee's recorded reply comes back as a file
    Pine Labs   pinelabs_*                                            → curtain (documented responses) or live UAT
    Delhivery   delhivery_geocode, delhivery_distance                 → curtain (documented responses)
    world       search_fares, search_stays                            → curtain pastes real results from real sites
    code        quote_plan, caps, log_decision                        → arithmetic and the decision log only
"""
from __future__ import annotations
import json
import math
import os
import shutil
import subprocess
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Optional
import httpx
from .world import Curtain, RunLog, SimClock, banner, paint, C_CALL, C_RESP, C_DEC, C_WARN, C_DIM
from .channels import Channel

# ------------------------------------------------------------------ schemas
DECISION = {
    "type": "object",
    "description": "Why you are doing this. Becomes one row of the decision log.",
    "properties": {
        "decided": {"type": "string", "description": "What you decided, in one sentence (the choice you made over the alternatives)."},
        "rule_id": {"type": "string", "description": "The rule(s) from your instructions that you followed, e.g. 'R9' or 'R14, R15'."},
        "trigger": {"type": "string", "description": "The input that triggered this decision (quote it briefly)."},
        "state": {"type": "string", "enum": ["INITIATED", "GATHERING", "PLANNING", "VOTING", "AUTHORISING", "BOOKING", "BOOKED", "CLOSED", "LAPSED"],
                  "description": "The trip's state after this action."},
    },
    "required": ["decided", "rule_id", "trigger", "state"],
}


def _tool(name: str, description: str, props: dict, required: list[str], decision: bool = True) -> dict:
    p = dict(props)
    req = list(required)
    if decision:
        p["decision"] = DECISION
        req.append("decision")
    return {"name": name, "description": description, "input_schema": {"type": "object", "properties": p, "required": req}}


MONEY = {"type": "integer", "description": "Indian rupees (whole rupees). The tool converts to paisa for Pine Labs."}

TOOLS: list[dict] = [
    _tool("send_group", "Post to the group chat. Only the kickoff, the vote tally, 'booked', and disruptions (R1).",
          {"text": {"type": "string"}}, ["text"]),
    _tool("send_dm", "Send a private message to one member.",
          {"member_id": {"type": "string"}, "text": {"type": "string"}}, ["member_id", "text"]),
    _tool("log_decision", "Record a decision that needs no other action right now (for example: wait for the deadline).",
          {}, []),
    _tool("search_fares", "Look up real fares for one leg on one date. Returns the options a real airline/OTA site shows now.",
          {"origin": {"type": "string"}, "destination": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD"}},
          ["origin", "destination", "date"], decision=False),
    _tool("search_stays", "Look up real stays (listed hotels and phone-only homestays) for the trip.",
          {"city": {"type": "string"}, "near": {"type": "string", "description": "venue or area to be near"},
           "check_in": {"type": "string"}, "nights": {"type": "integer"}, "guests": {"type": "integer"},
           "must_haves": {"type": "array", "items": {"type": "string"}}},
          ["city", "check_in", "nights", "guests"], decision=False),
    _tool("delhivery_geocode", "Delhivery Maps geocoding: an address or place name to coordinates.",
          {"address": {"type": "string"}}, ["address"], decision=False),
    _tool("delhivery_distance", "Delhivery Maps distance: road distance and drive time between two points.",
          {"origin": {"type": "object", "properties": {"lat": {"type": "number"}, "lng": {"type": "number"}}, "required": ["lat", "lng"]},
           "destination": {"type": "object", "properties": {"lat": {"type": "number"}, "lng": {"type": "number"}}, "required": ["lat", "lng"]}},
          ["origin", "destination"], decision=False),
    _tool("gnani_tts", "Gnani text-to-speech: turn one line you will say on a call into audio. Returns an audio file id.",
          {"text": {"type": "string"}, "language": {"type": "string", "description": "hi-IN or en-IN"}}, ["text", "language"]),
    _tool("place_call", "Dial and play audio you made with gnani_tts. Returns the callee's recorded reply (an audio file id) or no answer. Only for P0 reasons (R25).",
          {"callee": {"type": "string", "description": "stay or member name"}, "phone": {"type": "string"},
           "audio_file": {"type": "string"},
           "p0_reason": {"type": "string", "enum": ["SUPPLIER_AVAILABILITY", "LATE_ARRIVAL", "DISRUPTION_UNANSWERED"]}},
          ["callee", "phone", "audio_file", "p0_reason"]),
    _tool("gnani_stt", "Gnani speech-to-text on a recorded reply. Returns exactly what Gnani returns.",
          {"audio_file": {"type": "string"}, "language": {"type": "string"}}, ["audio_file", "language"], decision=False),
    _tool("pinelabs_create_payment_link",
          "Pine Labs: create a pre-authorised payment link for one payer for exactly their cap. Nothing is charged until capture.",
          {"member_id": {"type": "string"}, "amount_inr": MONEY, "valid_hours": {"type": "integer"},
           "description": {"type": "string"}}, ["member_id", "amount_inr", "valid_hours", "description"]),
    _tool("pinelabs_get_payment_link", "Pine Labs: status of a payment link, the order behind it and the method used.",
          {"payment_link_id": {"type": "string"}}, ["payment_link_id"], decision=False),
    _tool("pinelabs_capture", "Pine Labs: capture an amount (the payer's share, never above their cap) on an authorised order.",
          {"member_id": {"type": "string"}, "order_id": {"type": "string"}, "amount_inr": MONEY},
          ["member_id", "order_id", "amount_inr"]),
    _tool("pinelabs_cancel", "Pine Labs: cancel an order, releasing a hold or the uncaptured balance.",
          {"member_id": {"type": "string"}, "order_id": {"type": "string"}}, ["member_id", "order_id"]),
    _tool("pinelabs_refund", "Pine Labs: refund a captured amount.",
          {"member_id": {"type": "string"}, "order_id": {"type": "string"}, "amount_inr": MONEY},
          ["member_id", "order_id", "amount_inr"]),
    _tool("pinelabs_payout", "Pine Labs Payouts: pay a supplier from Quorum's account (the pool).",
          {"beneficiary_name": {"type": "string"}, "amount_inr": MONEY, "purpose": {"type": "string"},
           "reference": {"type": "string"}}, ["beneficiary_name", "amount_inr", "purpose", "reference"]),
    _tool("quote_plan",
          "Arithmetic only. Prices one plan: twin rooms = ceil(heads/2); stay per head = ceil(rooms × rate × nights / heads); "
          "per head = own legs + stay per head + essentials per head; share = per head × party.",
          {"rate_per_twin_room_night_inr": {"type": "integer"}, "nights": {"type": "integer"},
           "essentials_per_head_inr": {"type": "integer"},
           "payers": {"type": "array", "items": {"type": "object", "properties": {
               "member_id": {"type": "string"}, "party": {"type": "integer"},
               "leg_prices_per_seat_inr": {"type": "array", "items": {"type": "integer"}}},
               "required": ["member_id", "party", "leg_prices_per_seat_inr"]}}},
          ["rate_per_twin_room_night_inr", "nights", "payers"], decision=False),
    _tool("caps", "Arithmetic only. cap = party × ceiling (not rounded); headroom = cap − charged.",
          {"payers": {"type": "array", "items": {"type": "object", "properties": {
              "member_id": {"type": "string"}, "party": {"type": "integer"}, "ceiling_per_head_inr": {"type": "integer"},
              "charged_inr": {"type": "integer"}}, "required": ["member_id", "party", "ceiling_per_head_inr"]}}},
          ["payers"], decision=False),
]

CONNECTOR = {"send_group": "Chat (group)", "send_dm": "Chat (DM)", "log_decision": "—",
             "search_fares": "World: fare site (curtain)", "search_stays": "World: listings (curtain)",
             "delhivery_geocode": "Delhivery Maps", "delhivery_distance": "Delhivery Maps",
             "gnani_tts": "Gnani TTS", "gnani_stt": "Gnani STT", "place_call": "Phone line (curtain) + Gnani audio",
             "quote_plan": "calculator", "caps": "calculator"}


# ------------------------------------------------------------------ Pine Labs gateway
class PineLabsGateway:
    """Builds the request Pine Labs' docs describe. curtain: a teammate returns the documented response.
    uat: sends it to Pine Labs UAT (PINELABS_CLIENT_ID / _SECRET / _MERCHANT_ID; base PINELABS_BASE)."""

    def __init__(self, curtain: Curtain, log: RunLog, clock: SimClock, mode: Optional[str] = None):
        self.curtain, self.log, self.clock = curtain, log, clock
        self.mode = mode or os.environ.get("SIM_PINELABS", "curtain")
        self.base = os.environ.get("PINELABS_BASE", "https://pluraluat.v2.pinepg.in").rstrip("/")
        self.token: Optional[str] = None
        self.http = httpx.Client(timeout=30) if self.mode == "uat" else None
        self.links: dict[str, dict] = {}            # payment_link_id → member, amount, order_id (the operator's view)
        self.orders: dict[str, dict] = {}           # order_id → member, authorised, captured

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Request-ID": str(uuid.uuid4()), "Request-Timestamp": self.clock.now().isoformat()}
        if self.token:
            h["Authorization"] = "Bearer <access_token>"
        return h

    def call(self, key: str, method: str, path: str, body: Optional[dict], ctx: dict) -> tuple[int, Any]:
        if self.token is None and key != "pinelabs.token":
            self._token()
        request = {"method": method, "url": f"{self.base}{path}", "headers": self._headers(), "body": body}
        _show_request("Pine Labs", request)
        if self.mode == "uat":
            hdrs = dict(request["headers"])
            if self.token:
                hdrs["Authorization"] = f"Bearer {self.token}"
            r = self.http.request(method, request["url"], headers=hdrs, json=body)
            status, resp, played = r.status_code, (r.json() if r.content else {}), "Pine Labs UAT (live)"
        else:
            status, resp = self.curtain.answer(key, request, ctx)
            played = "curtain (documented response)"
        _show_response(status, resp)
        self.log.rail({"at": self.clock.now().isoformat(), "partner": "Pine Labs", "endpoint": f"{method} {path}",
                       "request": request, "status": status, "response": resp, "played_by": played})
        return status, resp

    def _token(self) -> None:
        body = {"client_id": "<PINELABS_CLIENT_ID>", "client_secret": "<PINELABS_CLIENT_SECRET>", "grant_type": "client_credentials"}
        if self.mode == "uat":
            body = {"client_id": os.environ["PINELABS_CLIENT_ID"], "client_secret": os.environ["PINELABS_CLIENT_SECRET"],
                    "grant_type": "client_credentials"}
        self.token = "pending"
        status, resp = self.call("pinelabs.token", "POST", "/api/auth/v1/token", body, {})
        self.token = (resp or {}).get("access_token") or "curtain-token"


def _data(resp: Any) -> dict:
    return resp.get("data", resp) if isinstance(resp, dict) else {}


def _show_request(partner: str, request: dict) -> None:
    banner(f"{partner} ▸ {request['method']} {request['url']}", colour=C_CALL)
    if request.get("body") is not None:
        print(paint(C_CALL, json.dumps(request["body"], indent=2, ensure_ascii=False)))


def _show_response(status: int, resp: Any) -> None:
    print(paint(C_RESP, f"◂ {status}\n" + json.dumps(resp, indent=2, ensure_ascii=False)))


# ------------------------------------------------------------------ the toolbox
class Toolbox:
    def __init__(self, cfg: dict, channel: Channel, curtain: Curtain, log: RunLog, clock: SimClock,
                 speech: Any = None, player: Optional[Callable[[Path], None]] = None):
        self.cfg, self.channel, self.curtain, self.log, self.clock = cfg, channel, curtain, log, clock
        self.members = {m["id"]: m for m in cfg["members"]}
        self.pinelabs = PineLabsGateway(curtain, log, clock)
        self.speech = speech                       # GnaniSpeech, built lazily so a run without calls needs no key
        self.player = player or _play
        self.audio: dict[str, Path] = {}           # audio file id → path
        self.pool = 0                              # operator's view of Quorum's account; the agent sees only responses
        self.current_event: dict = {}

    # dispatch -----------------------------------------------------
    def run(self, name: str, args: dict) -> tuple[Any, bool]:
        """Returns (result, is_error). Works on a copy: the tool_use input is part of history sent back to the API."""
        args = dict(args)
        decision = args.pop("decision", None)
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return f"unknown tool {name}", True
        try:
            result = fn(**args)
            err = False
        except Exception as e:                       # report to the model; never crash the run on a bad call
            result, err = f"{type(e).__name__}: {e}", True
        if decision:
            self._decision(name, args, decision, result)
        return result, err

    def _decision(self, name: str, args: dict, d: dict, result: Any) -> None:
        said = args.get("text") or json.dumps({k: v for k, v in args.items()}, ensure_ascii=False)
        to = ("group" if name == "send_group" else self.members.get(args.get("member_id", ""), {}).get("name")
              or args.get("callee") or args.get("beneficiary_name") or "—")
        ev = self.current_event
        row = {"when": self.clock.stamp(), "received": ev.get("summary", ""), "from": ev.get("source", ""),
               "decided": d.get("decided"), "why": d.get("rule_id"), "trigger": d.get("trigger"),
               "action": name, "to": to, "did_or_said": said, "through": CONNECTOR.get(name, "Pine Labs" if name.startswith("pinelabs") else name),
               "state": d.get("state")}
        self.log.decision(row)
        print(paint(C_DEC, f"◆ DECISION [{d.get('rule_id')}] {d.get('decided')}  → state {d.get('state')}"))

    # chat ----------------------------------------------------------
    def t_send_group(self, text: str) -> str:
        status = self.channel.send_group(text)
        self.log.message({"at": self.clock.now().isoformat(), "to": "group", "text": text, "status": status})
        return status

    def t_send_dm(self, member_id: str, text: str) -> str:
        if member_id not in self.members:
            raise ValueError(f"no member {member_id}; members: {', '.join(self.members)}")
        status = self.channel.send_dm(member_id, text)
        self.log.message({"at": self.clock.now().isoformat(), "to": self.members[member_id]["name"], "text": text, "status": status})
        return status

    def t_log_decision(self) -> str:
        return "logged"

    # world lookups (curtain pastes real data) ----------------------
    def _world(self, key: str, partner: str, request: dict, ctx: dict) -> Any:
        banner(f"{partner} ▸ {request}", colour=C_CALL)
        status, resp = self.curtain.answer(key, request, ctx)
        _show_response(status, resp)
        self.log.rail({"at": self.clock.now().isoformat(), "partner": partner, "endpoint": key, "request": request,
                       "status": status, "response": resp, "played_by": "curtain (real source)"})
        return resp

    def t_search_fares(self, origin: str, destination: str, date: str) -> Any:
        return self._world("world.fares", "Fare lookup", {"origin": origin, "destination": destination, "date": date},
                           {"origin": origin, "destination": destination, "date": date})

    def t_search_stays(self, city: str, check_in: str, nights: int, guests: int, near: str = "", must_haves: Optional[list] = None) -> Any:
        req = {"city": city, "near": near, "check_in": check_in, "nights": nights, "guests": guests, "must_haves": must_haves or []}
        return self._world("world.stays", "Stay lookup", req, {"city": city})

    # Delhivery (curtain, documented responses) ---------------------
    def t_delhivery_geocode(self, address: str) -> Any:
        return self._delhivery("delhivery.geocode", {"address": address}, {"address": address})

    def t_delhivery_distance(self, origin: dict, destination: dict) -> Any:
        return self._delhivery("delhivery.distance", {"origins": [origin], "destinations": [destination]}, {})

    def _delhivery(self, key: str, body: dict, ctx: dict) -> Any:
        t = self.curtain.template(key)
        request = {"method": t["method"], "url": t["path"], "body": body}
        _show_request("Delhivery", request)
        status, resp = self.curtain.answer(key, request, ctx)
        _show_response(status, resp)
        self.log.rail({"at": self.clock.now().isoformat(), "partner": "Delhivery", "endpoint": f"{t['method']} {t['path']}",
                       "request": request, "status": status, "response": resp, "played_by": "curtain (documented response)"})
        return resp

    # Gnani (real) + the phone line (curtain) -----------------------
    def _gnani(self):
        if self.speech is None:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from app.rails.gnani_speech import GnaniSpeech
            self.speech = GnaniSpeech()
        return self.speech

    def t_gnani_tts(self, text: str, language: str) -> dict:
        from app.rails.gnani_speech import TTS_URL
        request = {"method": "POST", "url": TTS_URL, "body": {"text": text, "voice": "sia", "model": "vachana-voice-v2",
                   "audio_config": {"sample_rate": 16000, "num_channels": 1, "sample_width": 2, "encoding": "linear_pcm", "container": "wav"}}}
        _show_request("Gnani", request)
        audio = self._gnani().tts(text)
        fid = f"tts-{len(self.audio) + 1}"
        path = self.log.dir / f"{fid}.wav"
        path.write_bytes(audio)
        self.audio[fid] = path
        resp = {"content_type": "audio/wav", "bytes": len(audio), "saved_as": path.name}
        _show_response(200, resp)
        self.log.rail({"at": self.clock.now().isoformat(), "partner": "Gnani", "endpoint": f"POST {TTS_URL}",
                       "request": request, "status": 200, "response": resp, "played_by": "Gnani API (real)"})
        return {"audio_file": fid, "bytes": len(audio)}

    def t_place_call(self, callee: str, phone: str, audio_file: str, p0_reason: str) -> dict:
        if audio_file not in self.audio:
            raise ValueError(f"unknown audio file {audio_file}; make it with gnani_tts first")
        banner(f"PHONE LINE ▸ dialling {callee} ({phone}) · {p0_reason}", colour=C_CALL)
        self.player(self.audio[audio_file])
        reply = self.curtain.ask_free(f"phone line: path to {callee}'s recorded reply (wav ≤60 s), or 'none' for no answer > ")
        self.log.rail({"at": self.clock.now().isoformat(), "partner": "Phone line", "endpoint": "dial + play",
                       "request": {"callee": callee, "phone": phone, "audio": self.audio[audio_file].name, "p0_reason": p0_reason},
                       "status": 200, "response": {"reply": reply}, "played_by": "curtain (teammate plays the callee, real recorded audio)"})
        if not reply or reply.lower() in ("none", "no answer", "-"):
            print(paint(C_RESP, "◂ no answer"))
            return {"disposition": "NO_ANSWER"}
        src = Path(reply).expanduser()
        if not src.exists():
            raise FileNotFoundError(f"reply audio not found: {src}")
        fid = f"reply-{len(self.audio) + 1}"
        dst = self.log.dir / f"{fid}{src.suffix or '.wav'}"
        shutil.copyfile(src, dst)
        self.audio[fid] = dst
        print(paint(C_RESP, f"◂ answered; reply recorded as {fid}"))
        return {"disposition": "ANSWERED", "reply_audio_file": fid}

    def t_gnani_stt(self, audio_file: str, language: str) -> dict:
        from app.rails.gnani_speech import STT_URL
        if audio_file not in self.audio:
            raise ValueError(f"unknown audio file {audio_file}")
        request = {"method": "POST", "url": STT_URL, "body": {"audio_file": self.audio[audio_file].name, "language_code": language}}
        _show_request("Gnani", request)
        transcript = self._gnani().stt(self.audio[audio_file], language_code=language)
        resp = {"success": True, "transcript": transcript}
        _show_response(200, resp)
        self.log.rail({"at": self.clock.now().isoformat(), "partner": "Gnani", "endpoint": f"POST {STT_URL}",
                       "request": request, "status": 200, "response": resp, "played_by": "Gnani API (real)"})
        return resp

    # Pine Labs -----------------------------------------------------
    def t_pinelabs_create_payment_link(self, member_id: str, amount_inr: int, valid_hours: int, description: str) -> Any:
        m = self.members[member_id]
        first, _, last = m["name"].partition(" ")
        ref = f"quorum-{member_id}-{uuid.uuid4().hex[:6]}"
        expire_by = (self.clock.now() + timedelta(hours=valid_hours)).isoformat()
        body = {"amount": {"value": amount_inr * 100, "currency": "INR"}, "description": description,
                "expire_by": expire_by, "allowed_payment_methods": ["CARD", "UPI", "BNPL"], "pre_auth": "true",
                "merchant_payment_link_reference": ref,
                "customer": {"first_name": first, "last_name": last or "-", "mobile_number": m.get("phone", ""),
                             "country_code": "91", "email_id": m.get("email", f"{member_id}@example.invalid"),
                             "merchant_customer_reference": f"quorum-{member_id}"},
                "callback_url": os.environ.get("PINELABS_CALLBACK_URL", "https://<tunnel>/webhooks/pinelabs"),
                "merchant_metadata": {"source": "quorum", "member": member_id}}
        ctx = {"amount_paisa": amount_inr * 100, "merchant_payment_link_reference": ref, "description": description,
               "expire_by": expire_by}
        status, resp = self.pinelabs.call("pinelabs.create_payment_link", "POST", "/api/pay/v1/paymentlink", body, ctx)
        d = _data(resp)
        link_id = d.get("payment_link_id") or d.get("id")
        if link_id:
            self.pinelabs.links[link_id] = {"member_id": member_id, "amount_inr": amount_inr, "ref": ref, "order_id": None}
        return {"status": status, "response": resp}

    def t_pinelabs_get_payment_link(self, payment_link_id: str) -> Any:
        link = self.pinelabs.links.get(payment_link_id, {})
        ctx = {"payment_link_id": payment_link_id, "amount_paisa": link.get("amount_inr", 0) * 100}
        if link.get("order_id"):
            ctx["order_id"] = link["order_id"]
        status, resp = self.pinelabs.call("pinelabs.get_payment_link", "GET", f"/api/pay/v1/paymentlink/{payment_link_id}", None, ctx)
        self._note_order(payment_link_id, _data(resp))
        return {"status": status, "response": resp}

    def _note_order(self, link_id: str, d: dict) -> None:
        oid = d.get("order_id")
        if oid and link_id in self.pinelabs.links:
            self.pinelabs.links[link_id]["order_id"] = oid
            link = self.pinelabs.links[link_id]
            self.pinelabs.orders.setdefault(oid, {"member_id": link["member_id"], "authorised_inr": link["amount_inr"], "captured_inr": 0})

    def t_pinelabs_capture(self, member_id: str, order_id: str, amount_inr: int) -> Any:
        o = self.pinelabs.orders.get(order_id)
        if o and o["captured_inr"] + amount_inr > o["authorised_inr"]:
            print(paint(C_WARN, f"(operator) capture ₹{amount_inr} exceeds the ₹{o['authorised_inr']} authorised — Pine Labs would reject; consider 'x'"))
        body = {"merchant_capture_reference": f"cap-{uuid.uuid4().hex[:10]}", "capture_amount": {"value": amount_inr * 100, "currency": "INR"}}
        ctx = {"order_id": order_id, "amount_paisa": amount_inr * 100, "merchant_capture_reference": body["merchant_capture_reference"]}
        method = os.environ.get("PINELABS_CAPTURE_METHOD", "PUT")
        status, resp = self.pinelabs.call("pinelabs.capture", method, f"/api/pay/v1/orders/{order_id}/capture", body, ctx)
        if status < 400:
            self.pool += amount_inr
            if o:
                o["captured_inr"] += amount_inr
        return {"status": status, "response": resp}

    def t_pinelabs_cancel(self, member_id: str, order_id: str) -> Any:
        method = os.environ.get("PINELABS_CANCEL_METHOD", "PUT")
        status, resp = self.pinelabs.call("pinelabs.cancel", method, f"/api/pay/v1/orders/{order_id}/cancel", None, {"order_id": order_id})
        return {"status": status, "response": resp}

    def t_pinelabs_refund(self, member_id: str, order_id: str, amount_inr: int) -> Any:
        body = {"merchant_order_reference": f"refund-{uuid.uuid4().hex[:10]}", "order_amount": {"value": amount_inr * 100, "currency": "INR"}}
        status, resp = self.pinelabs.call("pinelabs.refund", "POST", f"/api/pay/v1/refunds/{order_id}", body,
                                          {"order_id": order_id, "amount_paisa": amount_inr * 100})
        if status < 400:
            self.pool -= amount_inr
        return {"status": status, "response": resp}

    def t_pinelabs_payout(self, beneficiary_name: str, amount_inr: int, purpose: str, reference: str) -> Any:
        if amount_inr > self.pool:
            print(paint(C_WARN, f"(operator) payout ₹{amount_inr} exceeds the ₹{self.pool} in the pool — Pine Labs would reject"))
        body = {"beneficiary": {"name": beneficiary_name}, "amount": {"value": amount_inr * 100, "currency": "INR"},
                "mode": "IMPS", "purpose": purpose, "reference": reference}
        status, resp = self.pinelabs.call("pinelabs.payout", "POST", "/api/payouts/v1/payouts", body,
                                          {"amount_paisa": amount_inr * 100, "beneficiary_name": beneficiary_name, "reference": reference})
        if status < 400:
            self.pool -= amount_inr
        return {"status": status, "response": resp}

    # calculators ----------------------------------------------------
    def t_quote_plan(self, rate_per_twin_room_night_inr: int, nights: int, payers: list[dict], essentials_per_head_inr: int = 0) -> dict:
        heads = sum(int(p["party"]) for p in payers)
        if heads <= 0:
            raise ValueError("no heads")
        rooms = math.ceil(heads / 2)
        stay_total = rooms * rate_per_twin_room_night_inr * nights
        stay_per_head = math.ceil(stay_total / heads)
        out = []
        for p in payers:
            per_head = sum(p["leg_prices_per_seat_inr"]) + stay_per_head + essentials_per_head_inr
            out.append({"member_id": p["member_id"], "party": p["party"], "per_head_inr": per_head, "share_inr": per_head * p["party"]})
        return {"heads": heads, "twin_rooms": rooms, "stay_total_inr": stay_total, "stay_per_head_inr": stay_per_head,
                "payers": out, "total_inr": sum(o["share_inr"] for o in out)}

    def t_caps(self, payers: list[dict]) -> dict:
        out = []
        for p in payers:
            cap = int(p["party"]) * int(p["ceiling_per_head_inr"])
            out.append({"member_id": p["member_id"], "cap_inr": cap, "headroom_inr": cap - int(p.get("charged_inr", 0))})
        return {"payers": out}


def _play(path: Path) -> None:
    for cmd in (["afplay", str(path)], ["aplay", "-q", str(path)], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]):
        if shutil.which(cmd[0]):
            subprocess.call(cmd)
            return
    print(paint(C_DIM, f"(no audio player found; audio saved at {path})"))
