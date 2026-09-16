"""Domain objects. Everything the engine reasons about lives here."""
from __future__ import annotations
from datetime import datetime, date
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class TripState(str, Enum):
    TRIGGERED = "TRIGGERED"      # organiser added the agent, dates named
    CAPTURING = "CAPTURING"      # private DMs out, waiting for constraints
    BUILDING = "BUILDING"        # bundles being assembled and verified
    POSTED = "POSTED"            # two bundles in the group, clock running
    BOOKED = "BOOKED"            # quorum met, debits fired, legs booked
    LAPSED = "LAPSED"            # quorum missed, every block released
    CLOSED = "CLOSED"            # organiser closed the trip after a lapse


class AuthStatus(str, Enum):
    PENDING = "PENDING"          # mandate created, member has not approved in their UPI app
    BLOCKED = "BLOCKED"          # member approved; funds blocked, not debited
    CAPTURED = "CAPTURED"        # quorum met; debit executed
    RELEASED = "RELEASED"        # quorum missed; block released / mandate cancelled
    EXPIRED = "EXPIRED"          # validity ran out without capture


class Member(BaseModel):
    id: str = Field(default_factory=lambda: new_id("m"))
    name: str
    phone: str
    home_city: str
    spend_ceiling: int                       # INR, the most they will ever let the agent commit
    past_trips: list[str] = Field(default_factory=list)
    pinelabs_customer_id: Optional[str] = None


class Constraint(BaseModel):
    member_id: str
    available: bool = True
    earliest: Optional[date] = None
    latest: Optional[date] = None
    budget_ceiling: Optional[int] = None     # INR per head for this trip
    must_haves: list[str] = Field(default_factory=list)
    replied_at: Optional[datetime] = None


class Leg(BaseModel):
    id: str = Field(default_factory=lambda: new_id("leg"))
    member_id: str
    mode: str                                # flight / train / bus
    carrier: str
    origin: str
    destination: str
    depart: datetime
    arrive: datetime
    price: int                               # INR per head
    pnr: Optional[str] = None
    status: str = "QUOTED"                   # QUOTED / BOOKED / CANCELLED / REBOOKED


class VerificationRecord(BaseModel):
    property_name: str
    phone: str
    language: str
    disposition: str                         # VERIFIED / MISMATCH / NO_ANSWER
    answers: dict[str, str]
    transcript: str
    call_ref: Optional[str] = None
    called_at: Optional[datetime] = None


class Stay(BaseModel):
    id: str = Field(default_factory=lambda: new_id("stay"))
    name: str
    city: str
    phone: str
    price_per_night: int                     # INR per head (twin-share)
    nights: int
    photos_claim: str                        # what the listing says
    verification: Optional[VerificationRecord] = None
    booking_ref: Optional[str] = None

    @property
    def price(self) -> int:
        return self.price_per_night * self.nights


class Bundle(BaseModel):
    id: str = Field(default_factory=lambda: new_id("b"))
    label: str                               # "Lean" / "Comfort"
    legs: list[Leg]                          # one out + one back per member
    stay: Stay
    is_default: bool = False

    def per_head(self, member_id: str) -> int:
        legs = sum(l.price for l in self.legs if l.member_id == member_id)
        return legs + self.stay.price

    def max_per_head(self) -> int:
        ids = {l.member_id for l in self.legs}
        return max(self.per_head(m) for m in ids) if ids else self.stay.price


class Authorisation(BaseModel):
    member_id: str
    bundle_id: str
    amount: int                              # INR — the member's share, also the cap for re-booking
    status: AuthStatus = AuthStatus.PENDING
    rail_ref: Optional[str] = None           # Pine Labs subscription_id (OT mandate)
    order_ref: Optional[str] = None          # Pine Labs order_id
    created_at: Optional[datetime] = None
    captured_amount: int = 0


class Event(BaseModel):
    at: datetime
    channel: str                             # group / dm:<member_id> / rail:<name> / system
    actor: str                               # agent / member name / rail
    text: str


class Trip(BaseModel):
    id: str = Field(default_factory=lambda: new_id("trip"))
    name: str
    organiser_id: str
    members: list[Member]
    destination: str
    start: date
    end: date
    rough_budget: int                        # INR per head, organiser's guess at trigger
    quorum: int                              # e.g. 4 of 5
    state: TripState = TripState.TRIGGERED
    constraints: dict[str, Constraint] = Field(default_factory=dict)
    capture_deadline: Optional[datetime] = None
    bundles: list[Bundle] = Field(default_factory=list)
    default_bundle_id: Optional[str] = None
    posted_at: Optional[datetime] = None
    deadline: Optional[datetime] = None
    authorisations: dict[str, Authorisation] = Field(default_factory=dict)
    events: list[Event] = Field(default_factory=list)

    # ---- helpers
    def member(self, member_id: str) -> Member:
        return next(m for m in self.members if m.id == member_id)

    def organiser(self) -> Member:
        return self.member(self.organiser_id)

    def nights(self) -> int:
        return (self.end - self.start).days

    def bundle(self, bundle_id: str) -> Bundle:
        return next(b for b in self.bundles if b.id == bundle_id)

    def default_bundle(self) -> Optional[Bundle]:
        return self.bundle(self.default_bundle_id) if self.default_bundle_id else None

    def blocked(self) -> list[Authorisation]:
        return [a for a in self.authorisations.values() if a.status == AuthStatus.BLOCKED]

    def quorum_met(self) -> bool:
        return len(self.blocked()) >= self.quorum
