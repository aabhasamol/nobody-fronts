"""HTTP surface. The UI in /static drives the engine through these endpoints.

Run:  uvicorn app.main:app --reload --port 8000   then open http://localhost:8000

Everything a member or the organiser does in WhatsApp (or their UPI app) is one POST here; a channel
adapter would call the same engine methods. The two `/rails/*` endpoints are demo knobs on the mock rails.
"""
from __future__ import annotations
import base64
import os
import secrets
from datetime import date
from pathlib import Path
from typing import Callable, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .clock import clock
from .engine import Engine
from .rails import build_rails
from .scenario import five_friends, WEDDING, LEISURE, REPLIES

app = FastAPI(title="Quorum — trip agent prototype")
voice, payments, logistics = build_rails()
engine = Engine(voice, payments, logistics)
STATIC = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")
DEMO_PASSWORD = os.environ.get("QUORUM_DEMO_PASSWORD", "")


@app.middleware("http")
async def demo_password(request: Request, call_next):
    """When QUORUM_DEMO_PASSWORD is set (a public deploy), the whole app asks for it: any username, that password."""
    if DEMO_PASSWORD and request.url.path != "/clock":                 # /clock stays open as the health check
        header = request.headers.get("authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                _, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(pwd, DEMO_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response("Quorum demo — password required", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Quorum demo"'})
    return await call_next(request)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ------------------------------------------------------------------ organiser: kickoff
class TriggerIn(BaseModel):
    scenario: str = "wedding"                # wedding | leisure — presets in app/scenario.py
    budget: Optional[int] = None             # INR per head, all-in
    overshoot: Optional[float] = None        # 0.10 = 10 %
    auth_window_h: Optional[int] = None      # the organiser's deadline for blocking a share
    persona: Optional[str] = None            # bachelors | families; otherwise deduced from the parties
    organiser_index: int = 0


@app.post("/trips")
def trigger(body: TriggerIn):
    preset = dict(WEDDING if body.scenario == "wedding" else LEISURE)
    if body.budget:
        preset["budget"] = body.budget
    if body.overshoot is not None:
        preset["overshoot"] = body.overshoot
    if body.auth_window_h:
        preset["auth_window_h"] = body.auth_window_h
    if body.persona:
        preset["persona"] = body.persona
    for knob in (getattr(logistics, "drift", None), getattr(payments, "fail_capture_for", None)):
        if knob is not None:
            knob.clear()                     # a new trip starts with the world at rest
    members = five_friends()
    trip = engine.trigger(organiser=members[body.organiser_index], members=members, **preset)
    return _view(trip.id)


@app.get("/trips/{trip_id}")
def get_trip(trip_id: str):
    return _view(trip_id)


# ------------------------------------------------------------------ members, in their DMs
class GatherIn(BaseModel):
    canned: bool = True                      # use the scripted reply for this member (app/scenario.py)
    start_city: Optional[str] = None
    return_city: Optional[str] = None
    available: bool = True
    budget: Optional[int] = None             # this member's own ceiling, per head all-in
    party: int = 1                           # how many people they are paying for, themselves included
    party_names: list[str] = []
    interests: list[str] = []
    must_haves: list[str] = []
    text: Optional[str] = None


@app.post("/trips/{trip_id}/members/{member_id}/gather")
def gather(trip_id: str, member_id: str, body: GatherIn):
    trip = _trip(trip_id)
    kwargs = dict(REPLIES.get(trip.member(member_id).first, {})) if body.canned else body.model_dump(exclude={"canned"})
    _guard(lambda: engine.gather(trip, member_id, **kwargs))
    return _view(trip_id)


class DetailsIn(BaseModel):
    fields: dict[str, str]                   # names, dob, food, medical, pets — whatever the member sent
    text: Optional[str] = None


@app.post("/trips/{trip_id}/members/{member_id}/details")
def details(trip_id: str, member_id: str, body: DetailsIn):
    trip = _trip(trip_id)
    _guard(lambda: engine.details(trip, member_id, body.fields, body.text))
    return _view(trip_id)


@app.post("/trips/{trip_id}/members/{member_id}/flip")
def flip(trip_id: str, member_id: str):
    """A no-voter changes their mind inside the flip window."""
    trip = _trip(trip_id)
    _guard(lambda: engine.member_flips(trip, member_id))
    return _view(trip_id)


class VoteIn(BaseModel):
    yes: bool
    reason: str = ""


@app.post("/trips/{trip_id}/members/{member_id}/vote")
def vote(trip_id: str, member_id: str, body: VoteIn):
    trip = _trip(trip_id)
    _guard(lambda: engine.vote(trip, member_id, body.yes, body.reason))
    return _view(trip_id)


class ApproveIn(BaseModel):
    via: str = "UPI_RESERVE"                 # UPI_RESERVE | UPI_OTM | CARD_PREAUTH
    emi_months: Optional[int] = None         # card only


@app.post("/trips/{trip_id}/members/{member_id}/approve")
def approve(trip_id: str, member_id: str, body: Optional[ApproveIn] = None):
    """The member approved the block: in their UPI app, or on the card page — their share, or a top-up."""
    trip = _trip(trip_id)
    body = body or ApproveIn()
    _guard(lambda: engine.member_approves(trip, member_id, body.via, body.emi_months))
    return _view(trip_id)


@app.post("/trips/{trip_id}/members/{member_id}/withdraw")
def withdraw(trip_id: str, member_id: str):
    """The member asks Quorum to release their block. (A mandate or card hold can't be revoked from their own app.)"""
    trip = _trip(trip_id)
    _guard(lambda: engine.member_withdraws(trip, member_id))
    return _view(trip_id)


class ChooseIn(BaseModel):
    option: int


@app.post("/trips/{trip_id}/members/{member_id}/choose")
def choose(trip_id: str, member_id: str, body: ChooseIn):
    trip = _trip(trip_id)
    if member_id not in trip.pending:
        raise HTTPException(409, "nothing to choose")
    _guard(lambda: engine.member_chooses(trip, member_id, body.option))
    return _view(trip_id)


@app.post("/trips/{trip_id}/members/{member_id}/exit")
def member_exit(trip_id: str, member_id: str):
    trip = _trip(trip_id)
    _guard(lambda: engine.member_exits(trip, member_id))
    return _view(trip_id)


# ------------------------------------------------------------------ organiser, in their DM
class AdjustIn(BaseModel):
    budget: Optional[int] = None
    overshoot: Optional[float] = None
    end: Optional[date] = None


@app.post("/trips/{trip_id}/organiser/adjust")
def adjust(trip_id: str, body: AdjustIn):
    trip = _trip(trip_id)
    _guard(lambda: engine.organiser_adjusts(trip, body.budget, body.overshoot, body.end))
    return _view(trip_id)


class DecideIn(BaseModel):
    go: bool


@app.post("/trips/{trip_id}/organiser/decide")
def decide(trip_id: str, body: DecideIn):
    trip = _trip(trip_id)
    _guard(lambda: engine.organiser_decides(trip, body.go, text="go" if body.go else "close"))
    return _view(trip_id)


@app.post("/trips/{trip_id}/rerun")
def rerun(trip_id: str):
    trip = _trip(trip_id)
    _guard(lambda: engine.rerun(trip))
    return _view(trip_id)


@app.post("/trips/{trip_id}/close")
def close(trip_id: str):
    trip = _trip(trip_id)
    engine.close(trip)
    return _view(trip_id)


# ------------------------------------------------------------------ the world: carriers, fares, banks
@app.post("/trips/{trip_id}/disrupt/{leg_id}")
def disrupt(trip_id: str, leg_id: str):
    trip = _trip(trip_id)
    _guard(lambda: engine.disrupt(trip, leg_id))
    return _view(trip_id)


@app.post("/trips/{trip_id}/missed/{leg_id}")
def missed(trip_id: str, leg_id: str):
    """The member missed the departure: re-routing options, no refund, their money."""
    trip = _trip(trip_id)
    _guard(lambda: engine.missed(trip, leg_id))
    return _view(trip_id)


class DriftIn(BaseModel):
    city: str
    multiplier: float                        # 1.04 = fares from this city are 4 % up on the vote


@app.post("/rails/drift")
def drift(body: DriftIn):
    """Mock logistics only: move live fares so the booking re-price has something to absorb or escalate."""
    if not hasattr(logistics, "drift"):
        raise HTTPException(409, "live fares are not adjustable on this rail")
    logistics.drift[body.city] = body.multiplier
    return {"drift": logistics.drift}


class FailIn(BaseModel):
    member_id: str
    fail: bool = True


@app.post("/rails/fail-capture")
def fail_capture(body: FailIn):
    """Mock payments only: make this member's presentation bounce, to show the partial-capture wall."""
    if not hasattr(payments, "fail_capture_for"):
        raise HTTPException(409, "not a mock rail")
    (payments.fail_capture_for.add if body.fail else payments.fail_capture_for.discard)(body.member_id)
    return {"fail_capture_for": sorted(payments.fail_capture_for)}


# ------------------------------------------------------------------ clock
class ClockIn(BaseModel):
    hours: float


@app.post("/clock/advance")
def advance(body: ClockIn):
    clock.advance(body.hours)
    for t in engine.trips.values():
        engine.tick(t)
    return {"now": clock.now().isoformat()}


@app.get("/clock")
def now():
    return {"now": clock.now().isoformat()}


# ------------------------------------------------------------------ hooks for the real rails
@app.post("/webhooks/pinelabs")
async def pinelabs_webhook(req: Request):
    """Subscription activated / charged / expired events. TODO: verify signature, reconcile auth status."""
    payload = await req.json()
    return JSONResponse({"received": True, "event": payload.get("event") or payload.get("type")})


# ------------------------------------------------------------------ view
def _trip(trip_id: str):
    try:
        return engine.trips[trip_id]
    except KeyError:
        raise HTTPException(404, "no such trip")


def _guard(fn: Callable[[], object]) -> None:
    """Engine preconditions are asserts; the UI gets them back as 409s."""
    try:
        fn()
    except AssertionError as e:
        raise HTTPException(409, str(e) or "not allowed in this state")


def _view(trip_id: str) -> dict:
    t = _trip(trip_id)
    d = t.model_dump(mode="json")
    d["now"] = clock.now().isoformat()
    d["limit"] = t.limit()
    d["majority"] = t.majority()
    d["in_members"] = [m.id for m in t.in_members()]
    d["blocked_count"] = len(t.blocked())
    d["per_head"] = {p.id: {mid: p.per_head(mid) for mid in p.travellers} for p in t.plans}
    d["shares"] = {p.id: {mid: p.share(mid) for mid in p.travellers} for p in t.plans}
    d["heads"] = {p.id: p.heads() for p in t.plans}
    d["persona"] = t.persona()
    d["totals"] = {p.id: p.total() for p in t.plans}
    d["pool"] = payments.pool()
    d["ceilings"] = {m.id: t.ceiling(m.id) for m in t.members}
    d["emi_offers"] = {mid: payments.emi_offers(a.amount) for mid, a in list(t.authorisations.items()) + list(t.top_ups.items())
                       if a.status.value == "PENDING"}
    d["knobs"] = {"drift": getattr(logistics, "drift", {}), "fail_capture_for": sorted(getattr(payments, "fail_capture_for", []))}
    return d
