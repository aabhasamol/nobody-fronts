"""HTTP surface. The UI in /static drives the engine through these endpoints.

Run:  uvicorn app.main:app --reload --port 8000   then open http://localhost:8000
"""
from __future__ import annotations
from datetime import date
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .clock import clock
from .engine import Engine
from .models import TripState
from .rails import build_rails
from .scenario import five_friends, TRIP

app = FastAPI(title="Quorum — trip agent prototype")
voice, payments, logistics = build_rails()
engine = Engine(voice, payments, logistics)
STATIC = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ------------------------------------------------------------------ trip lifecycle
class TriggerIn(BaseModel):
    name: str = TRIP["name"]
    destination: str = TRIP["destination"]
    start: date = TRIP["start"]
    end: date = TRIP["end"]
    rough_budget: int = TRIP["rough_budget"]
    quorum: int = TRIP["quorum"]
    organiser_index: int = 0


@app.post("/trips")
def trigger(body: TriggerIn):
    members = five_friends()
    trip = engine.trigger(body.name, members[body.organiser_index], members, body.destination,
                          body.start, body.end, body.rough_budget, body.quorum)
    return _view(trip.id)


@app.get("/trips/{trip_id}")
def get_trip(trip_id: str):
    return _view(trip_id)


class CaptureIn(BaseModel):
    available: bool = True
    budget_ceiling: Optional[int] = None
    must_haves: list[str] = []
    text: Optional[str] = None


@app.post("/trips/{trip_id}/members/{member_id}/capture")
def capture(trip_id: str, member_id: str, body: CaptureIn):
    trip = _trip(trip_id)
    engine.capture(trip, member_id, body.available, body.budget_ceiling, body.must_haves, text=body.text)
    return _view(trip_id)


@app.post("/trips/{trip_id}/members/{member_id}/approve")
def approve(trip_id: str, member_id: str):
    trip = _trip(trip_id)
    if trip.state != TripState.POSTED:
        raise HTTPException(409, f"trip is {trip.state}")
    engine.member_approves(trip, member_id)
    return _view(trip_id)


class SwitchIn(BaseModel):
    bundle_id: str


@app.post("/trips/{trip_id}/switch-default")
def switch_default(trip_id: str, body: SwitchIn):
    trip = _trip(trip_id)
    engine.switch_default(trip, body.bundle_id)
    return _view(trip_id)


class RerunIn(BaseModel):
    bundle_id: Optional[str] = None
    quorum: Optional[int] = None


@app.post("/trips/{trip_id}/rerun")
def rerun(trip_id: str, body: RerunIn):
    trip = _trip(trip_id)
    engine.rerun(trip, body.bundle_id, body.quorum)
    return _view(trip_id)


@app.post("/trips/{trip_id}/close")
def close(trip_id: str):
    trip = _trip(trip_id)
    engine.close(trip)
    return _view(trip_id)


@app.post("/trips/{trip_id}/disrupt/{leg_id}")
def disrupt(trip_id: str, leg_id: str):
    trip = _trip(trip_id)
    engine.disrupt(trip, leg_id)
    return _view(trip_id)


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
@app.get("/gnani/precall")
def gnani_precall(ref: str):
    """Gnani's pre-call / dynamic-variables API hits this with the clientReferenceId we passed."""
    vars_ = getattr(voice, "precall_vars", {}).get(ref)
    if not vars_:
        raise HTTPException(404, "unknown reference")
    return vars_


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


def _view(trip_id: str) -> dict:
    t = _trip(trip_id)
    d = t.model_dump(mode="json")
    d["now"] = clock.now().isoformat()
    d["per_head"] = {b.id: {m.id: b.per_head(m.id) for m in t.members} for b in t.bundles}
    d["blocked_count"] = len(t.blocked())
    return d
